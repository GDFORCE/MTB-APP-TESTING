from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import (
    AllCondition, ComparisonCondition, FieldOperand, LiteralOperand,
    TruthValue, evaluate_condition,
)
from app.domain.schedule.evaluator import ScheduleEvaluator, add_amount, evaluate_timing
from app.domain.schedule.exceptions import ScheduleNotApprovedError
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Dependency, Event, Evidence, PatientContext,
    PatientEventStatus, RecurrenceRule, RecurrenceTermination,
    ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, EventReference, NominalWindowTiming, NonNegativeTemporalAmount,
    OffsetTiming, PositiveTemporalAmount, RangeTiming, TemporalAmount,
    ProtocolDayTiming, TimeUnit, TriggeredTiming, TriggerWithin,
    UnresolvedTiming, Window, WithinTiming,
)
from app.domain.schedule.validator import ScheduleValidator


def condition(field: str, value: object) -> ComparisonCondition:
    return ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field=field), right=LiteralOperand(value=value),
    )


def schedule_with(event: Event, *, status=ScheduleStatus.APPROVED) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=12, source_text="Day 30")
    event.evidence_refs = [evidence.id]
    claims = [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path="display_name", confidence=.9,
        ),
        ClaimEvidence(
            evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path="timing", confidence=.9,
        ),
    ]
    for present, claim_type, path in (
        (event.conditions, "CONDITION", "conditions"),
        (event.applicability, "APPLICABILITY", "applicability"),
        (event.recurrence, "RECURRENCE", "recurrence"),
    ):
        if present:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path, confidence=.9,
            ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=status),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], evidence=[evidence],
        claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": date(2026, 1, 1)}, "state": {},
    }
    values.update(changes)
    return PatientContext(**values)


def test_three_valued_condition_logic_does_not_coerce_missing_to_false():
    expression = AllCondition(conditions=[
        condition("patient.discontinued", True), condition("patient.reason", "TOXICITY"),
    ])
    assert evaluate_condition(expression, {"discontinued": True}) == TruthValue.UNKNOWN
    assert evaluate_condition(expression, {"discontinued": False}) == TruthValue.FALSE
    assert evaluate_condition(expression, {"discontinued": True, "reason": "TOXICITY"}) == TruthValue.TRUE


def test_calendar_month_policy_clamps_end_of_month():
    assert add_amount(date(2026, 1, 31), TemporalAmount(value=1, unit=TimeUnit.MONTH)) == date(2026, 2, 28)
    assert add_amount(date(2024, 1, 31), TemporalAmount(value=1, unit=TimeUnit.MONTH)) == date(2024, 2, 29)


def test_protocol_day_numbering_is_explicit_and_distinct_from_elapsed_offset():
    event = Event(
        code="DAY_1", protocol_label="Day 1", display_name="Day 1", event_type="VISIT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=1),
    )
    schedule = schedule_with(event)
    result = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31)).events[0]
    assert result.timing.nominal_start == date(2026, 1, 1)
    assert result.timing.constraints[0]["type"] == "CLINICAL_DAY"


def test_event_relative_intraday_and_triggered_constraints_preserve_datetime_precision():
    schedule_id = uuid4()
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule_id,
        event_values={"DOSE": [datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)]},
        anchors={"PROGRESSION": date(2026, 9, 12)},
    )
    pk = evaluate_timing(OffsetTiming(
        reference=EventReference(event_code="DOSE"),
        offset=TemporalAmount(value=4, unit="HOUR"),
    ), patient_context)
    assert pk.nominal_start == datetime(2026, 1, 10, 13, 0, tzinfo=timezone.utc)
    triggered = evaluate_timing(TriggeredTiming(
        trigger=AnchorReference(code="PROGRESSION"),
        timing_after_trigger=TriggerWithin(duration=PositiveTemporalAmount(value=7, unit="DAY")),
    ), patient_context)
    assert triggered.nominal_start is None
    assert triggered.earliest == date(2026, 9, 12)
    assert triggered.latest == date(2026, 9, 19)


def test_within_constraint_never_manufactures_a_nominal_date():
    schedule_id = uuid4()
    patient_context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule_id,
        anchors={"BASELINE": date(2026, 1, 1)},
    )
    result = evaluate_timing(WithinTiming(
        reference=AnchorReference(code="BASELINE"),
        duration=PositiveTemporalAmount(value=7, unit="DAY"),
    ), patient_context)
    assert result.nominal_start is None
    assert result.earliest == date(2026, 1, 1)
    assert result.latest == date(2026, 1, 8)


def test_nominal_asymmetric_window_is_deterministic():
    event = Event(
        code="DAY_24", protocol_label="Visit 5", display_name="Visit 5", event_type="VISIT",
        timing=NominalWindowTiming(
            nominal=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=24, unit="DAY")),
            window=Window(
                before=NonNegativeTemporalAmount(value=0, unit="DAY"),
                after=NonNegativeTemporalAmount(value=1, unit="DAY"),
            ),
        ),
    )
    schedule = schedule_with(event)
    output = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31))
    result = output.events[0]
    assert result.status == PatientEventStatus.RESOLVED
    assert result.timing.nominal_start == date(2026, 1, 25)
    assert result.timing.earliest == date(2026, 1, 25)
    assert result.timing.latest == date(2026, 1, 26)


def test_range_preserves_range_semantics():
    event = Event(
        code="RANGE", protocol_label="Days 12-14", display_name="Days 12-14", event_type="ASSESSMENT",
        timing=RangeTiming(
            reference=AnchorReference(code="BASELINE"),
            start=TemporalAmount(value=12, unit="DAY"), end=TemporalAmount(value=14, unit="DAY"),
        ),
    )
    schedule = schedule_with(event)
    result = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31)).events[0]
    assert result.timing.nominal_start == date(2026, 1, 13)
    assert result.timing.nominal_end == date(2026, 1, 15)
    assert result.timing.precision == "CONSTRAINT"


def test_unknown_anchor_and_unknown_condition_have_distinct_pending_states():
    event = Event(
        code="FOLLOW_UP", protocol_label="Follow-up", display_name="Follow-up", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=30, unit="DAY")),
        conditions=[condition("patient.progression", True)],
    )
    schedule = schedule_with(event)
    no_anchor = context(schedule, anchors={})
    no_anchor.state = {"progression": True}
    assert ScheduleEvaluator().evaluate(schedule, no_anchor, horizon=date(2026, 12, 31)).events[0].status == PatientEventStatus.WAITING_FOR_ANCHOR
    assert ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31)).events[0].status == PatientEventStatus.WAITING_FOR_CONDITION


def test_recurrence_is_bounded_by_horizon():
    event = Event(
        code="Q28", protocol_label="Every 28 days", display_name="Treatment", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=0, unit="DAY")),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=28, unit="DAY"),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="HORIZON"),
        ),
    )
    schedule = schedule_with(event)
    results = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 3, 1)).events
    assert [item.timing.nominal_start for item in results] == [date(2026, 1, 1), date(2026, 1, 29), date(2026, 2, 26)]


def test_unresolved_timing_blocks_validation_and_never_generates_date():
    event = Event(
        code="UNKNOWN", protocol_label="Approximately later", display_name="Unknown", event_type="VISIT",
        timing=UnresolvedTiming(reason="External schedule missing"), requires_review=True,
    )
    schedule = schedule_with(event, status=ScheduleStatus.DRAFT)
    issues = ScheduleValidator().validate(schedule)
    assert any(item.issue_code == "AMBIGUOUS_TIMING" and item.blocking for item in issues)


def test_dependency_cycle_is_blocking():
    a = Event(
        code="A", protocol_label="A", display_name="A", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=1, unit="DAY")),
        dependencies=[Dependency(source_event_code="B", dependency_type="SEQUENCE")],
    )
    b = Event(
        code="B", protocol_label="B", display_name="B", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=2, unit="DAY")),
        dependencies=[Dependency(source_event_code="A", dependency_type="SEQUENCE")],
    )
    schedule = schedule_with(a, status=ScheduleStatus.DRAFT)
    schedule.events.append(b)
    assert any(item.issue_code == "CIRCULAR_DEPENDENCY" for item in ScheduleValidator().validate(schedule))


def test_non_approved_schedule_cannot_generate_patient_dates():
    event = Event(
        code="A", protocol_label="A", display_name="A", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=1, unit="DAY")),
    )
    schedule = schedule_with(event, status=ScheduleStatus.IN_REVIEW)
    with pytest.raises(ScheduleNotApprovedError):
        ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31))
