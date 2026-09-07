"""Activity-level / intra-day timing (MTB requirement doc 5, sections 35-47).

The clinical safety rule under test: an activity anchored on another activity
resolves only from that activity's ACTUAL recorded time. Patient arrival is never
a default anchor, and a recorded actual time is never rewritten.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.evaluator import ScheduleEvaluator, activity_actual_key
from app.domain.schedule.models import (
    Activity, Anchor, ApplicabilityRule, ClaimEvidence, Event, Evidence,
    PatientActivityStatus, PatientContext, RecurrenceRule, RecurrenceTermination,
    ScheduleMetadata, ScheduleStatus, StudyDimension, UniversalSchedule,
)
from app.domain.schedule.timing import (
    ActivityReference, AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount,
    OffsetTiming, PositiveTemporalAmount, ProtocolDayTiming, TemporalAmount, TimeUnit, Window,
)
from app.domain.schedule.validator import ScheduleValidator


def offset_from_activity(code: str, value: int, unit: TimeUnit = TimeUnit.HOUR) -> OffsetTiming:
    return OffsetTiming(
        reference=ActivityReference(activity_code=code),
        offset=TemporalAmount(value=value, unit=unit),
    )


def c1d1() -> Event:
    """Cycle 1 Day 1 as written in the requirement: arrival, dose, then serial PK."""
    return Event(
        code="C1D1", protocol_label="C1D1", display_name="Cycle 1 Day 1",
        event_type="SITE_VISIT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=1),
        activities=[
            Activity(
                code="PATIENT_ARRIVAL", protocol_label="Arrival", display_name="Patient arrival",
                activity_type="ADMINISTRATIVE", sequence_number=1,
            ),
            Activity(
                code="VITALS", protocol_label="Vital signs", display_name="Vital signs",
                activity_type="ASSESSMENT", sequence_number=2,
                timing=offset_from_activity("PATIENT_ARRIVAL", 30, TimeUnit.MINUTE),
            ),
            Activity(
                code="DOSE", protocol_label="Dose", display_name="Study drug administration",
                activity_type="TREATMENT", sequence_number=3,
            ),
            Activity(
                code="PK_1H", protocol_label="PK +1h", display_name="PK sample 1 hour post-dose",
                activity_type="SAMPLE", sequence_number=4, timing=offset_from_activity("DOSE", 1),
            ),
            Activity(
                code="PK_2H", protocol_label="PK +2h", display_name="PK sample 2 hours post-dose",
                activity_type="SAMPLE", sequence_number=5,
                timing=NominalWindowTiming(
                    nominal=offset_from_activity("DOSE", 2),
                    window=Window(
                        before=NonNegativeTemporalAmount(value=10, unit=TimeUnit.MINUTE),
                        after=NonNegativeTemporalAmount(value=10, unit=TimeUnit.MINUTE),
                    ),
                ),
            ),
        ],
    )


def approved(event: Event) -> UniversalSchedule:
    """Wrap one event in an approved schedule with the claim-level evidence the
    validator requires, so these tests exercise timing rather than traceability."""
    evidence = Evidence(
        evidence_type="TABLE_CELL", page_number=42,
        source_text="PK samples at 1, 2 and 4 hours following administration of study drug.",
    )
    event.evidence_refs = [evidence.id]
    claims = [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path=path, confidence=0.9,
        )
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ]
    if event.recurrence is not None:
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type="RECURRENCE", claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path="recurrence", confidence=0.9,
        ))
    for activity in event.activities:
        activity.evidence_refs = [evidence.id]
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type="ACTIVITY", claim_entity_type="ACTIVITY",
            claim_entity_id=activity.id, claim_path="display_name", confidence=0.9,
        ))
        if activity.timing is not None:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="ACTIVITY_TIMING",
                claim_entity_type="ACTIVITY", claim_entity_id=activity.id,
                claim_path="timing", confidence=0.9,
            ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], evidence=[evidence], claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": date(2026, 9, 1)},
    }
    return PatientContext(**{**values, **changes})


def by_code(activities) -> dict:
    return {item.activity_code: item for item in activities}


def evaluate(schedule, ctx):
    return ScheduleEvaluator().evaluate(schedule, ctx, horizon=date(2026, 12, 31))


def test_dose_dependent_activities_wait_for_the_actual_dose_time():
    schedule = approved(c1d1())
    ctx = context(schedule, activity_actuals={
        activity_actual_key("C1D1", 0, "PATIENT_ARRIVAL"): datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc),
    })
    activities = by_code(evaluate(schedule, ctx).events[0].activities)

    # Arrival-anchored work resolves immediately from the recorded arrival time.
    assert activities["VITALS"].status == PatientActivityStatus.RESOLVED
    assert activities["VITALS"].timing.nominal_start == datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    # Dose-anchored work must not borrow the arrival time.
    for code in ("PK_1H", "PK_2H"):
        assert activities[code].status == PatientActivityStatus.WAITING_FOR_ANCHOR
        assert activities[code].timing is None
        assert activities[code].explanation["waiting_for_activity"] == "DOSE"
        assert "DOSE" in activities[code].explanation["reason"]


def test_day_schedule_is_generated_progressively_once_dose_time_is_recorded():
    schedule = approved(c1d1())
    ctx = context(schedule, activity_actuals={
        activity_actual_key("C1D1", 0, "PATIENT_ARRIVAL"): datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc),
        activity_actual_key("C1D1", 0, "DOSE"): datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc),
    })
    activities = by_code(evaluate(schedule, ctx).events[0].activities)

    assert activities["DOSE"].status == PatientActivityStatus.COMPLETED
    assert activities["PK_1H"].timing.nominal_start == datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
    assert activities["PK_2H"].timing.nominal_start == datetime(2026, 9, 1, 11, 30, tzinfo=timezone.utc)
    # The +/- 10 minute activity window survives into the resolved timing.
    assert activities["PK_2H"].timing.earliest == datetime(2026, 9, 1, 11, 20, tzinfo=timezone.utc)
    assert activities["PK_2H"].timing.latest == datetime(2026, 9, 1, 11, 40, tzinfo=timezone.utc)


def test_correcting_the_dose_time_moves_pending_activities_but_not_recorded_ones():
    schedule = approved(c1d1())
    ctx = context(schedule, activity_actuals={
        activity_actual_key("C1D1", 0, "DOSE"): datetime(2026, 9, 1, 9, 45, tzinfo=timezone.utc),
        activity_actual_key("C1D1", 0, "PK_1H"): datetime(2026, 9, 1, 10, 38, tzinfo=timezone.utc),
    })
    activities = by_code(evaluate(schedule, ctx).events[0].activities)

    # The already-collected sample keeps its real collection time.
    assert activities["PK_1H"].status == PatientActivityStatus.COMPLETED
    assert activities["PK_1H"].actual_time == datetime(2026, 9, 1, 10, 38, tzinfo=timezone.utc)
    # Its protocol-expected time is still recalculated for deviation analysis.
    assert activities["PK_1H"].timing.nominal_start == datetime(2026, 9, 1, 10, 45, tzinfo=timezone.utc)
    # A future dependent activity follows the corrected dose time.
    assert activities["PK_2H"].timing.nominal_start == datetime(2026, 9, 1, 11, 45, tzinfo=timezone.utc)


def test_activity_applicability_filters_cohort_specific_samples():
    event = c1d1()
    event.activities.append(Activity(
        code="PK_6H", protocol_label="PK +6h", display_name="PK sample 6 hours post-dose",
        activity_type="SAMPLE", sequence_number=6, timing=offset_from_activity("DOSE", 6),
        applicability=[ApplicabilityRule(dimension="COHORT", values=["COHORT_2"])],
    ))
    schedule = approved(event)
    schedule.cohorts = [
        StudyDimension(code="COHORT_1", protocol_label="Cohort 1", display_name="Cohort 1"),
        StudyDimension(code="COHORT_2", protocol_label="Cohort 2", display_name="Cohort 2"),
    ]
    actuals = {activity_actual_key("C1D1", 0, "DOSE"): datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)}

    cohort_1 = by_code(evaluate(
        schedule, context(schedule, cohort_code="COHORT_1", activity_actuals=actuals)).events[0].activities)
    cohort_2 = by_code(evaluate(
        schedule, context(schedule, cohort_code="COHORT_2", activity_actuals=actuals)).events[0].activities)

    assert cohort_1["PK_6H"].status == PatientActivityStatus.NOT_APPLICABLE
    assert cohort_2["PK_6H"].status == PatientActivityStatus.RESOLVED
    # The shared visit itself still applies to both patients.
    assert cohort_1["PK_1H"].status == PatientActivityStatus.RESOLVED


def test_activities_are_ordered_by_protocol_sequence():
    schedule = approved(c1d1())
    codes = [item.activity_code for item in evaluate(schedule, context(schedule)).events[0].activities]
    assert codes == ["PATIENT_ARRIVAL", "VITALS", "DOSE", "PK_1H", "PK_2H"]


def test_activity_marked_not_done_is_preserved_and_not_rescheduled():
    schedule = approved(c1d1())
    ctx = context(
        schedule,
        activity_actuals={activity_actual_key("C1D1", 0, "DOSE"): datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)},
        activity_statuses={activity_actual_key("C1D1", 0, "PK_1H"): "NOT_DONE"},
    )
    activities = by_code(evaluate(schedule, ctx).events[0].activities)
    assert activities["PK_1H"].status == PatientActivityStatus.NOT_DONE


def test_activity_timing_referencing_an_unknown_activity_blocks_approval():
    event = c1d1()
    event.activities.append(Activity(
        code="PK_LATE", protocol_label="PK late", display_name="Late PK",
        activity_type="SAMPLE", timing=offset_from_activity("INFUSION_END", 2),
    ))
    blocking = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(approved(event)))}
    assert "UNRESOLVED_REFERENCE" in blocking


def test_activity_cannot_be_timed_relative_to_itself():
    event = c1d1()
    event.activities.append(Activity(
        code="LOOP", protocol_label="Loop", display_name="Self referencing sample",
        activity_type="SAMPLE", timing=offset_from_activity("LOOP", 1),
    ))
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(approved(event)))}
    assert "CIRCULAR_DEPENDENCY" in codes


def test_duplicate_activity_codes_inside_one_visit_are_blocked():
    event = c1d1()
    event.activities.append(Activity(
        code="DOSE", protocol_label="Dose", display_name="Duplicate dose",
        activity_type="TREATMENT",
    ))
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(approved(event)))}
    assert "DUPLICATE_ACTIVITY" in codes


def test_recurring_visit_keeps_intra_day_actuals_separated_per_occurrence():
    event = c1d1()
    event.code = "CXD1"
    event.recurrence = RecurrenceRule(
        interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
        start_reference=AnchorReference(code="BASELINE"),
        termination=RecurrenceTermination(type="COUNT", count=3),
    )
    schedule = approved(event)
    ctx = context(schedule, activity_actuals={
        activity_actual_key("CXD1", 0, "DOSE"): datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        activity_actual_key("CXD1", 1, "DOSE"): datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc),
    })
    occurrences = evaluate(schedule, ctx).events

    assert by_code(occurrences[0].activities)["PK_1H"].timing.nominal_start.hour == 10
    assert by_code(occurrences[1].activities)["PK_1H"].timing.nominal_start.hour == 12
    # Cycle 3 has no recorded dose yet, so its PK samples stay unresolved.
    assert by_code(occurrences[2].activities)["PK_1H"].status == PatientActivityStatus.WAITING_FOR_ANCHOR
