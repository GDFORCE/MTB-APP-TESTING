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
    Anchor, ApplicabilityRule, ClaimEvidence, Dependency, Event, Evidence, PatientContext,
    PatientEventStatus, RecurrenceRule, RecurrenceTermination,
    ScheduleMetadata, ScheduleStatus, StudyDimension, UniversalSchedule,
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


def test_interleaved_independent_recurrences_already_work_with_no_new_schema():
    """Confirms "interleaved recurrence" (two overlapping regimens on
    independent rhythms) is already fully handled: recurrence is a per-event
    property, expanded independently by the evaluator. This is a correctly
    extracted-document question, not a scheduling-engine gap -- if a real
    protocol fails here, the fix is in extraction/prompting, not new code."""
    drug_a = Event(
        code="DRUG_A", protocol_label="Drug A dose", display_name="Drug A dose", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=0, unit="DAY")),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=14, unit="DAY"),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="COUNT", count=3),
        ),
    )
    drug_b = Event(
        code="DRUG_B", protocol_label="Drug B dose", display_name="Drug B dose", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=0, unit="DAY")),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=21, unit="DAY"),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="COUNT", count=2),
        ),
    )
    schedule = schedule_with(drug_a)
    schedule.events.append(drug_b)
    evidence_id = schedule.evidence[0].id
    drug_b.evidence_refs = [evidence_id]
    schedule.claim_evidence += [
        ClaimEvidence(evidence_id=evidence_id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                      claim_entity_id=drug_b.id, claim_path="display_name"),
        ClaimEvidence(evidence_id=evidence_id, claim_type="TIMING", claim_entity_type="EVENT",
                      claim_entity_id=drug_b.id, claim_path="timing"),
        ClaimEvidence(evidence_id=evidence_id, claim_type="RECURRENCE", claim_entity_type="EVENT",
                      claim_entity_id=drug_b.id, claim_path="recurrence"),
    ]
    results = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 4, 1)).events
    drug_a_dates = sorted(item.timing.nominal_start for item in results if item.event_code == "DRUG_A")
    drug_b_dates = sorted(item.timing.nominal_start for item in results if item.event_code == "DRUG_B")
    assert drug_a_dates == [date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 29)]
    assert drug_b_dates == [date(2026, 1, 1), date(2026, 1, 22)]
    # The two independent series genuinely interleave on the calendar, not
    # merged, not collapsed, not requiring the other's cadence to divide evenly.
    all_dates = sorted(item.timing.nominal_start for item in results)
    assert all_dates == [date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 22), date(2026, 1, 29)]


def test_regimen_switch_by_patient_state_already_works_with_no_new_schema():
    """Confirms "regimen/template selection" is already fully handled via
    ApplicabilityRule's existing PATIENT_ATTRIBUTE dimension: an event can be
    scoped to apply only once a recorded clinical fact (e.g. achieving CR)
    sets a state field -- no new applicability dimension or handler needed.
    If a real protocol's regimen switch isn't extracted this way, the fix is
    in extraction/prompting, not the schema."""
    maintenance_dose = Event(
        code="MAINTENANCE_DOSE", protocol_label="Maintenance regimen dose",
        display_name="Maintenance regimen dose", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=30, unit="DAY")),
        applicability=[ApplicabilityRule(
            dimension="PATIENT_ATTRIBUTE", field="regimen_code", values=["MAINTENANCE"],
        )],
    )
    schedule = schedule_with(maintenance_dose)

    still_induction = context(schedule, state={"regimen_code": "INDUCTION"})
    result = ScheduleEvaluator().evaluate(schedule, still_induction, horizon=date(2026, 3, 1)).events[0]
    assert result.applicability_result == "FALSE"

    switched_to_maintenance = context(schedule, state={"regimen_code": "MAINTENANCE"})
    result = ScheduleEvaluator().evaluate(schedule, switched_to_maintenance, horizon=date(2026, 3, 1)).events[0]
    assert result.applicability_result == "TRUE"
    assert result.timing.nominal_start == date(2026, 1, 31)


def test_asymmetric_calendar_windows_already_work_with_no_new_schema():
    """Confirms "asymmetric/special calendar window" is already fully
    handled: Window.before and Window.after are independent values, not
    forced symmetric. If a real protocol's asymmetric window (e.g. -2/+7
    days) isn't extracted this way, the fix is in extraction, not the schema."""
    event = Event(
        code="FOLLOW_UP", protocol_label="Follow-up", display_name="Follow-up", event_type="VISIT",
        timing=NominalWindowTiming(
            nominal=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=30, unit="DAY")),
            window=Window(
                before=NonNegativeTemporalAmount(value=2, unit="DAY"),
                after=NonNegativeTemporalAmount(value=7, unit="DAY"),
            ),
        ),
    )
    schedule = schedule_with(event)
    result = ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 3, 1)).events[0]
    assert result.timing.nominal_start == date(2026, 1, 31)
    assert result.timing.earliest == date(2026, 1, 29)  # -2 days
    assert result.timing.latest == date(2026, 2, 7)      # +7 days, genuinely asymmetric


def test_time_of_day_inheritance_already_works_with_no_new_schema():
    """Confirms "time-of-day inheritance" (e.g. PK samples at the exact
    clock time of the dosing anchor) is already fully handled: anchors may
    carry a full datetime, and HOUR/MINUTE offset units compose on top of it
    with ordinary calendar arithmetic -- no separate time-of-day concept
    needed. If a real protocol's PK time-of-day rule isn't extracted this
    way, the fix is in extraction, not the schema."""
    pk_anchor_time = Event(
        code="PK_2H", protocol_label="PK sample at 2h post-dose", display_name="PK sample at 2h post-dose",
        event_type="PROCEDURE",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=2, unit="HOUR")),
    )
    schedule = schedule_with(pk_anchor_time)
    dosed_at = context(schedule, anchors={"BASELINE": datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)})
    result = ScheduleEvaluator().evaluate(schedule, dosed_at, horizon=date(2026, 3, 1)).events[0]
    assert result.timing.nominal_start == datetime(2026, 1, 1, 11, 30, tzinfo=timezone.utc)


def test_lab_result_gated_scheduling_already_works_with_no_new_schema():
    """Confirms "lab/result-gated scheduling" (e.g. repeat only if ANC <
    1000) is already fully handled: event.conditions already evaluates
    arbitrary ComparisonCondition expressions against PatientContext.state
    with Kleene three-valued logic -- an unknown lab value is UNKNOWN, never
    treated as false. If a real protocol's lab-gated rule isn't extracted
    this way, the fix is in extraction, not the schema."""
    repeat_if_low_anc = Event(
        code="REPEAT_CBC", protocol_label="Repeat CBC if ANC low", display_name="Repeat CBC if ANC low",
        event_type="PROCEDURE",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=7, unit="DAY")),
        conditions=[ComparisonCondition(
            operator="LT", left=FieldOperand(field="ANC"), right=LiteralOperand(value=1000),
        )],
    )
    schedule = schedule_with(repeat_if_low_anc)

    unknown_anc = context(schedule, state={})
    result = ScheduleEvaluator().evaluate(schedule, unknown_anc, horizon=date(2026, 3, 1)).events[0]
    assert result.condition_result == "UNKNOWN"

    normal_anc = context(schedule, state={"ANC": 1500})
    result = ScheduleEvaluator().evaluate(schedule, normal_anc, horizon=date(2026, 3, 1)).events[0]
    assert result.condition_result == "FALSE"

    low_anc = context(schedule, state={"ANC": 800})
    result = ScheduleEvaluator().evaluate(schedule, low_anc, horizon=date(2026, 3, 1)).events[0]
    assert result.condition_result == "TRUE"
    assert result.timing.nominal_start == date(2026, 1, 8)


def test_cohort_triggered_timing_already_works_with_no_new_schema():
    """Confirms "cohort-triggered timing" (different visit cadence per
    cohort) is already fully handled: two events, each applicability-gated
    to its own cohort via the existing COHORT dimension, each with its own
    independent timing -- no cohort-aware timing variant needed in the
    engine itself. If a real protocol's per-cohort cadence isn't extracted
    this way, the fix is in extraction, not the schema."""
    cohort_a_visit = Event(
        code="VISIT", protocol_label="Cohort A visit", display_name="Cohort A visit", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=14, unit="DAY")),
        applicability=[ApplicabilityRule(dimension="COHORT", values=["COHORT_A"])],
    )
    schedule = schedule_with(cohort_a_visit)
    schedule.cohorts = [
        StudyDimension(code="COHORT_A", protocol_label="Cohort A", display_name="Cohort A"),
        StudyDimension(code="COHORT_B", protocol_label="Cohort B", display_name="Cohort B"),
    ]

    cohort_b_context = context(schedule, cohort_code="COHORT_B")
    result = ScheduleEvaluator().evaluate(schedule, cohort_b_context, horizon=date(2026, 3, 1)).events[0]
    assert result.applicability_result == "FALSE"

    cohort_a_context = context(schedule, cohort_code="COHORT_A")
    result = ScheduleEvaluator().evaluate(schedule, cohort_a_context, horizon=date(2026, 3, 1)).events[0]
    assert result.applicability_result == "TRUE"
    assert result.timing.nominal_start == date(2026, 1, 15)


def test_non_approved_schedule_cannot_generate_patient_dates():
    event = Event(
        code="A", protocol_label="A", display_name="A", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=1, unit="DAY")),
    )
    schedule = schedule_with(event, status=ScheduleStatus.IN_REVIEW)
    with pytest.raises(ScheduleNotApprovedError):
        ScheduleEvaluator().evaluate(schedule, context(schedule), horizon=date(2026, 12, 31))
