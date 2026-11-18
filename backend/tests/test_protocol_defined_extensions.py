"""The PROTOCOL_DEFINED extension-handler registry: short-turnaround process
for accommodating protocol patterns the built-in timing kinds cannot express.

`timing.py`'s `ProtocolDefinedTiming.handler` field was already designed for
this (the architecture doc describes it as "preserved structured extension
that is not executable without an installed deterministic handler"), but
nothing previously looked that name up: every such timing unconditionally
blocked approval and raised on evaluation, regardless of what `handler` said.

These tests prove the registry (`extension_handlers.py`) and the one
real handler built on it (`builtin_extensions.py`'s MINIMUM_GAP_AFTER_EVENT --
the washout/non-visit-interval pattern that failed in 8-9 of 11 real
protocols per docs/protocol-schedule-schema-analysis.md) do what's claimed:

  * an UNREGISTERED handler name still fails exactly as before -- blocked at
    validation, raises on evaluation, never silently accepted;
  * a REGISTERED handler's schedule is no longer blocked at validation and
    resolves deterministically when its reference is known;
  * an unresolved reference is a genuine waiting state (None), never a
    fabricated nominal date;
  * registering a brand-new handler for a hypothetical future pattern needs
    no change anywhere in `evaluate_timing` or the validator's dispatch --
    this is the actual "short span" proof, not just the one shipped handler.
"""
from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.builtin_extensions import (
    MINIMUM_GAP_AFTER_EVENT, SAME_DAY_AS_EVENT, TREATMENT_DELAY_AFTER_HOLD,
)
from app.domain.schedule.evaluator import evaluate_timing
from app.domain.schedule.exceptions import UnsupportedTimingError
from app.domain.schedule.extension_handlers import (
    get_timing_extension, register_timing_extension, registered_timing_extensions,
)
from app.domain.schedule.models import Event, PatientContext, ScheduleStatus
from app.domain.schedule.timing import ProtocolDefinedTiming, ProtocolExtension
from app.domain.schedule.validator import ScheduleValidator

from test_uctsm_domain import context, schedule_with


def washout_timing(*, handler=MINIMUM_GAP_AFTER_EVENT, reference_code="LAST_DOSE", gap_days=21):
    return ProtocolDefinedTiming(
        handler=handler,
        extensions=[ProtocolExtension(type=MINIMUM_GAP_AFTER_EVENT, value={
            "reference": {"kind": "ANCHOR", "code": reference_code},
            "minimum_gap": {"value": gap_days, "unit": "DAY"},
        })],
    )


def test_builtin_washout_handler_is_registered_at_package_import():
    assert MINIMUM_GAP_AFTER_EVENT in registered_timing_extensions()
    assert get_timing_extension(MINIMUM_GAP_AFTER_EVENT) is not None
    assert SAME_DAY_AS_EVENT in registered_timing_extensions()
    assert get_timing_extension(SAME_DAY_AS_EVENT) is not None
    assert TREATMENT_DELAY_AFTER_HOLD in registered_timing_extensions()
    assert get_timing_extension(TREATMENT_DELAY_AFTER_HOLD) is not None


def test_registered_handler_resolves_a_deterministic_lower_bound_with_no_invented_nominal_date():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(),
        anchors={"LAST_DOSE": date(2026, 1, 1)},
    )
    result = evaluate_timing(washout_timing(), patient_context)
    assert result.earliest == date(2026, 1, 22)
    assert result.nominal_start is None  # lower bound only -- never a fabricated exact date
    assert result.precision == "CONSTRAINT"


def test_registered_handler_waits_instead_of_guessing_when_its_reference_is_unknown():
    patient_context = PatientContext(patient_id=uuid4(), schedule_version_id=uuid4(), anchors={})
    assert evaluate_timing(washout_timing(), patient_context) is None


def test_unregistered_handler_name_still_fails_safely_exactly_as_before():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(), anchors={"LAST_DOSE": date(2026, 1, 1)},
    )
    unknown = washout_timing(handler="SOME_FUTURE_PATTERN_NOBODY_HAS_BUILT_YET")
    with pytest.raises(UnsupportedTimingError, match="no installed deterministic logic"):
        evaluate_timing(unknown, patient_context)


def test_protocol_defined_timing_with_no_handler_name_still_fails_safely():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(), anchors={"LAST_DOSE": date(2026, 1, 1)},
    )
    with pytest.raises(UnsupportedTimingError, match="names no handler"):
        evaluate_timing(ProtocolDefinedTiming(handler=None, extensions=[]), patient_context)


def test_validator_blocks_approval_only_while_the_named_handler_is_unregistered():
    event = Event(
        code="PERIOD2_DOSE", protocol_label="Period II dosing", display_name="Period II dosing",
        event_type="VISIT", timing=washout_timing(handler="NOT_YET_BUILT_PATTERN"), requires_review=True,
    )
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    issues = ScheduleValidator().validate(schedule)
    assert any(
        item.issue_code == "UNSUPPORTED_TIMING" and item.blocking
        and item.details.get("handler") == "NOT_YET_BUILT_PATTERN"
        for item in issues
    )


def test_validator_allows_approval_once_the_named_handler_is_registered():
    event = Event(
        code="PERIOD2_DOSE", protocol_label="Period II dosing", display_name="Period II dosing",
        event_type="VISIT", timing=washout_timing(), requires_review=True,
    )
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    issues = ScheduleValidator().validate(schedule)
    assert not any(item.issue_code == "UNSUPPORTED_TIMING" for item in issues)


def test_a_brand_new_hypothetical_pattern_needs_zero_core_dispatch_changes():
    """The actual "short span" claim: registering a new handler for a pattern
    this module has never heard of makes it executable immediately, with no
    change anywhere in evaluate_timing or the validator. Uses a name distinct
    from any shipped handler (SAME_DAY_AS_EVENT is now real production code,
    not a stand-in) so this test never silently overwrites it."""
    def resolve_latest_lab_plus_offset(expression, patient_context):
        extension = next(item for item in expression.extensions if item.type == "LATEST_LAB_PLUS_OFFSET")
        target_code = extension.value["event_code"]
        values = patient_context.event_values.get(target_code, [])
        if not values:
            return None
        from app.domain.schedule.models import ResolvedTiming
        return ResolvedTiming(nominal_start=values[-1], nominal_end=values[-1], precision="EXACT")

    register_timing_extension("LATEST_LAB_PLUS_OFFSET", resolve_latest_lab_plus_offset)
    timing = ProtocolDefinedTiming(
        handler="LATEST_LAB_PLUS_OFFSET",
        extensions=[ProtocolExtension(type="LATEST_LAB_PLUS_OFFSET", value={"event_code": "LABS"})],
    )
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(),
        event_values={"LABS": [date(2026, 3, 4)]},
    )
    result = evaluate_timing(timing, patient_context)
    assert result.nominal_start == date(2026, 3, 4)


def same_day_timing(*, handler=SAME_DAY_AS_EVENT, reference_kind="EVENT", event_code="DRUG_A_DOSE"):
    reference = (
        {"kind": "EVENT", "event_code": event_code, "occurrence": "CURRENT"}
        if reference_kind == "EVENT" else {"kind": "ANCHOR", "code": event_code}
    )
    return ProtocolDefinedTiming(
        handler=handler,
        extensions=[ProtocolExtension(type=SAME_DAY_AS_EVENT, value={"reference": reference})],
    )


def test_same_day_merge_pins_to_the_exact_other_event_date_not_its_own_timing():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(),
        event_values={"DRUG_A_DOSE": [date(2026, 2, 10)]},
    )
    result = evaluate_timing(same_day_timing(), patient_context)
    assert result.nominal_start == date(2026, 2, 10)
    assert result.nominal_end == date(2026, 2, 10)
    assert result.precision == "EXACT"


def test_same_day_merge_waits_instead_of_guessing_when_its_reference_is_unknown():
    patient_context = PatientContext(patient_id=uuid4(), schedule_version_id=uuid4(), event_values={})
    assert evaluate_timing(same_day_timing(), patient_context) is None


def test_same_day_merge_can_also_pin_to_an_anchor_not_only_another_event():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(), anchors={"SURGERY": date(2026, 5, 1)},
    )
    result = evaluate_timing(same_day_timing(reference_kind="ANCHOR", event_code="SURGERY"), patient_context)
    assert result.nominal_start == date(2026, 5, 1)


def test_same_day_merge_validator_gating_matches_every_other_builtin_handler():
    event = Event(
        code="IMAGING", protocol_label="Imaging on day of surgery", display_name="Imaging on day of surgery",
        event_type="VISIT", requires_review=True, timing=same_day_timing(),
    )
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    assert not any(item.issue_code == "UNSUPPORTED_TIMING" for item in ScheduleValidator().validate(schedule))

    event.timing = same_day_timing(handler="NOT_YET_BUILT_SAME_DAY_VARIANT")
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    assert any(
        item.issue_code == "UNSUPPORTED_TIMING" and item.blocking
        for item in ScheduleValidator().validate(schedule)
    )


def delay_timing(*, handler=TREATMENT_DELAY_AFTER_HOLD, delay_field="treatment_delay_days", base_days=21):
    return ProtocolDefinedTiming(
        handler=handler,
        extensions=[ProtocolExtension(type=TREATMENT_DELAY_AFTER_HOLD, value={
            "reference": {"kind": "ANCHOR", "code": "CYCLE2_DOSE"},
            "base_offset": {"value": base_days, "unit": "DAY"},
            "delay_state_field": delay_field,
        })],
    )


def test_treatment_delay_shifts_by_the_recorded_accrued_delay_not_a_guess():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(),
        anchors={"CYCLE2_DOSE": date(2026, 1, 1)},
        state={"treatment_delay_days": 7},
    )
    result = evaluate_timing(delay_timing(), patient_context)
    assert result.nominal_start == date(2026, 1, 29)  # 21 (base) + 7 (recorded hold delay)
    assert result.precision == "EXACT"


def test_treatment_delay_defaults_to_zero_when_no_hold_has_been_recorded():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(),
        anchors={"CYCLE2_DOSE": date(2026, 1, 1)}, state={},
    )
    result = evaluate_timing(delay_timing(), patient_context)
    assert result.nominal_start == date(2026, 1, 22)  # base offset only, zero delay


def test_treatment_delay_waits_instead_of_guessing_when_its_reference_is_unknown():
    patient_context = PatientContext(patient_id=uuid4(), schedule_version_id=uuid4(), state={"treatment_delay_days": 5})
    assert evaluate_timing(delay_timing(), patient_context) is None


def test_treatment_delay_rejects_a_malformed_recorded_delay_rather_than_silently_ignoring_it():
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=uuid4(),
        anchors={"CYCLE2_DOSE": date(2026, 1, 1)}, state={"treatment_delay_days": -3},
    )
    with pytest.raises(UnsupportedTimingError, match="non-negative integer"):
        evaluate_timing(delay_timing(), patient_context)

    patient_context.state["treatment_delay_days"] = "a week"
    with pytest.raises(UnsupportedTimingError, match="non-negative integer"):
        evaluate_timing(delay_timing(), patient_context)


def test_treatment_delay_validator_gating_matches_every_other_builtin_handler():
    event = Event(
        code="CYCLE2_VISIT", protocol_label="Cycle 2 visit", display_name="Cycle 2 visit",
        event_type="VISIT", requires_review=True, timing=delay_timing(),
    )
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    assert not any(item.issue_code == "UNSUPPORTED_TIMING" for item in ScheduleValidator().validate(schedule))

    event.timing = delay_timing(handler="NOT_YET_BUILT_DELAY_VARIANT")
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    assert any(
        item.issue_code == "UNSUPPORTED_TIMING" and item.blocking
        for item in ScheduleValidator().validate(schedule)
    )
