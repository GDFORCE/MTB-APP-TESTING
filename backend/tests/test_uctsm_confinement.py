"""Multi-day / inpatient / confinement handling (MTB requirement doc 6).

The worked example throughout: the patient checks in on Day -1, is dosed on Day 1,
gives PK through Day 3, and is discharged on Day 3. That is ONE episode with four
study days, not four hospital visits.
"""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Activity, Anchor, ApplicabilityRule, ClaimEvidence, ConditionalAction,
    ConditionalDefinition, ConfinementDayDefinition, ConfinementEpisodeDefinition,
    ConditionState, ConfinementStatus, Event, Evidence, PatientConditionStatus,
    PatientContext, ScheduleMetadata, ScheduleStatus, StudyDimension, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount, TimeUnit
from app.domain.schedule.validator import ScheduleValidator

HORIZON = date(2027, 6, 30)
BASELINE = date(2026, 9, 11)  # Day 1 of the confinement


def visit(code: str, offset: int, activities: list[Activity] | None = None) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.replace("_", " ").title(),
        event_type="INPATIENT_CONFINEMENT",
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=offset, unit=TimeUnit.DAY),
        ),
        activities=activities or [],
    )


def confinement_schedule(*, conditionals=None, applicability=None) -> UniversalSchedule:
    dose = Activity(
        code="DOSE", protocol_label="Dose", display_name="Study drug administration",
        activity_type="TREATMENT", sequence_number=1,
    )
    pk = Activity(
        code="PK_48H", protocol_label="PK +48h", display_name="PK sample at 48 hours",
        activity_type="SAMPLE", sequence_number=2,
    )
    events = [
        visit("ADMISSION", -1),
        visit("DOSING_DAY", 0, [dose]),
        visit("PK_DAY", 1),
        visit("DISCHARGE", 2, [pk]),
    ]
    episode = ConfinementEpisodeDefinition(
        code="CONFINEMENT_1", protocol_label="Confinement Period 1",
        display_name="Confinement Period 1",
        admission_event_code="ADMISSION",
        dose_event_codes=["DOSING_DAY"],
        discharge_event_code="DISCHARGE",
        applicability=applicability or [],
        days=[
            ConfinementDayDefinition(day_label="Day -1", relative_day=-1),
            ConfinementDayDefinition(day_label="Day 1", relative_day=1, activity_codes=["DOSE"]),
            ConfinementDayDefinition(day_label="Day 2", relative_day=2),
            ConfinementDayDefinition(day_label="Day 3", relative_day=3, activity_codes=["PK_48H"]),
        ],
    )
    evidence = Evidence(
        evidence_type="SECTION", page_number=152,
        source_text="Subjects remain confined until completion of the 48-hour sample.",
    )
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path,
            ))
        for activity in event.activities:
            activity.evidence_refs = [evidence.id]
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="ACTIVITY", claim_entity_type="ACTIVITY",
                claim_entity_id=activity.id, claim_path="display_name",
            ))
    episode.evidence_refs = [evidence.id]
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="CONFINEMENT",
        claim_entity_type="CONFINEMENT_EPISODE",
        claim_entity_id=episode.id, claim_path="admission_event_code/days/discharge_event_code",
    ))
    for definition in conditionals or []:
        definition.evidence_refs = [evidence.id]
        claims.extend([
            ClaimEvidence(
                evidence_id=evidence.id, claim_type="CONDITION",
                claim_entity_type="CONDITION", claim_entity_id=definition.id,
                claim_path="condition",
            ),
            ClaimEvidence(
                evidence_id=evidence.id, claim_type="CONDITIONAL_ACTION",
                claim_entity_type="CONDITION", claim_entity_id=definition.id,
                claim_path="actions",
            ),
        ])
        if definition.resolution_condition is not None:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="CONDITIONAL_RESOLUTION",
                claim_entity_type="CONDITION", claim_entity_id=definition.id,
                claim_path="resolution_condition",
            ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, confinement_episodes=[episode],
        conditional_definitions=conditionals or [],
        evidence=[evidence], claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": BASELINE},
    }
    return PatientContext(**{**values, **changes})


def evaluate(schedule, ctx):
    return ScheduleEvaluator().evaluate(schedule, ctx, horizon=HORIZON)


def episode_of(result):
    return result.confinements[0]


def test_continuous_stay_is_one_episode_with_child_study_days():
    schedule = confinement_schedule()
    episode = episode_of(evaluate(schedule, context(schedule)))

    assert episode.episode_code == "CONFINEMENT_1"
    assert episode.status == ConfinementStatus.UPCOMING
    assert episode.planned_admission == date(2026, 9, 10)
    assert episode.planned_discharge == date(2026, 9, 13)
    # Four study days inside one parent stay, not four separate hospital visits.
    assert [day.day_label for day in episode.days] == ["Day -1", "Day 1", "Day 2", "Day 3"]
    assert [day.scheduled_date for day in episode.days] == [
        date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13),
    ]
    assert episode.days[1].activity_codes == ["DOSE"]


def test_actual_admission_puts_the_patient_in_confinement():
    schedule = confinement_schedule()
    ctx = context(schedule, actual_event_values={"ADMISSION": [date(2026, 9, 10)]})
    episode = episode_of(evaluate(schedule, ctx))

    assert episode.status == ConfinementStatus.IN_CONFINEMENT
    assert episode.actual_admission == date(2026, 9, 10)
    assert episode.actual_discharge is None


def test_planned_discharge_passing_does_not_close_the_episode():
    """Doc 6 section 18: the patient stays confined until an ACTUAL discharge."""
    schedule = confinement_schedule()
    ctx = context(schedule, actual_event_values={"ADMISSION": [date(2026, 9, 10)]})
    episode = episode_of(evaluate(schedule, ctx))

    assert episode.planned_discharge == date(2026, 9, 13)
    # No actual discharge recorded, so the episode is still open regardless of date.
    assert episode.status == ConfinementStatus.IN_CONFINEMENT


def test_actual_discharge_closes_the_episode():
    schedule = confinement_schedule()
    ctx = context(schedule, actual_event_values={
        "ADMISSION": [date(2026, 9, 10)], "DISCHARGE": [date(2026, 9, 14)],
    })
    episode = episode_of(evaluate(schedule, ctx))

    assert episode.status == ConfinementStatus.DISCHARGED
    assert episode.actual_discharge == date(2026, 9, 14)
    # Planned and actual stay distinct so the extra day is visible as a deviation.
    assert episode.planned_discharge == date(2026, 9, 13)


def test_study_days_follow_the_actual_admission_when_it_differs_from_plan():
    schedule = confinement_schedule()
    ctx = context(schedule, actual_event_values={"ADMISSION": [date(2026, 9, 11)]})
    episode = episode_of(evaluate(schedule, ctx))

    assert episode.planned_admission == date(2026, 9, 10)
    assert [day.scheduled_date for day in episode.days][0] == date(2026, 9, 11)


def test_conditional_extension_moves_discharge_without_creating_a_second_episode():
    """Doc 6 sections 19-22: an extension changes the expected end, not the identity."""
    extension_condition = ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="additional_observation"),
        right=LiteralOperand(value=True),
    )
    schedule = confinement_schedule(conditionals=[ConditionalDefinition(
        code="ADDITIONAL_OBSERVATION", protocol_label="Additional observation required",
        display_name="Additional observation required", condition=extension_condition,
        actions=[ConditionalAction(
            action_type="EXTEND_CONFINEMENT", target_code="CONFINEMENT_1",
            condition=extension_condition,
            parameters={"duration": {"value": 24, "unit": "HOUR"}},
        )],
    )])
    ctx = context(
        schedule,
        actual_event_values={"ADMISSION": [date(2026, 9, 10)]},
        conditions={"ADDITIONAL_OBSERVATION": PatientConditionStatus(
            condition_code="ADDITIONAL_OBSERVATION", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 9, 13),
        )},
    )
    result = evaluate(schedule, ctx)
    episode = episode_of(result)

    assert len(result.confinements) == 1
    assert episode.episode_code == "CONFINEMENT_1"
    assert episode.planned_discharge == date(2026, 9, 14)
    assert episode.status == ConfinementStatus.EXTENDED
    assert episode.extended_by == ["ADDITIONAL_OBSERVATION"]


def test_confinement_applies_only_to_its_cohort():
    schedule = confinement_schedule(
        applicability=[ApplicabilityRule(dimension="COHORT", values=["PK_COHORT"])])
    schedule.cohorts = [
        StudyDimension(code="PK_COHORT", protocol_label="PK cohort", display_name="PK cohort"),
        StudyDimension(code="MAIN", protocol_label="Main", display_name="Main"),
    ]
    assert episode_of(evaluate(schedule, context(schedule, cohort_code="PK_COHORT"))).status == (
        ConfinementStatus.UPCOMING)
    assert episode_of(evaluate(schedule, context(schedule, cohort_code="MAIN"))).status == (
        ConfinementStatus.NOT_APPLICABLE)


def test_admission_dose_and_discharge_must_stay_distinct_events():
    schedule = confinement_schedule()
    schedule.confinement_episodes[0].dose_event_codes = ["ADMISSION"]
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(schedule))}
    assert "INVALID_CONFINEMENT" in codes


def test_confinement_referencing_unknown_events_is_blocked():
    schedule = confinement_schedule()
    schedule.confinement_episodes[0].discharge_event_code = "GHOST_VISIT"
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(schedule))}
    assert "UNRESOLVED_REFERENCE" in codes


def test_confinement_day_referencing_an_unknown_activity_is_blocked():
    schedule = confinement_schedule()
    schedule.confinement_episodes[0].days[0].activity_codes = ["NOT_A_REAL_ACTIVITY"]
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(schedule))}
    assert "UNRESOLVED_REFERENCE" in codes


def test_duplicate_study_day_numbers_are_blocked():
    schedule = confinement_schedule()
    schedule.confinement_episodes[0].days[2].relative_day = 1
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(schedule))}
    assert "INVALID_CONFINEMENT" in codes


def test_confinement_without_source_evidence_is_blocked():
    schedule = confinement_schedule()
    schedule.confinement_episodes[0].evidence_refs = []
    codes = {item.issue_code for item in ScheduleValidator.blocking(
        ScheduleValidator().validate(schedule))}
    assert "MISSING_EVIDENCE" in codes


# --- doc 6 s26 rules 1-3: impossible stays are reported, never accepted ---------

def test_discharge_before_admission_is_flagged_for_review():
    """Rule 1: a stay that ends before it starts cannot be scheduled."""
    schedule = confinement_schedule()
    # Move discharge a week BEFORE admission.
    discharge = next(item for item in schedule.events if item.code == "DISCHARGE")
    discharge.timing = OffsetTiming(
        reference=AnchorReference(code="BASELINE"),
        offset=TemporalAmount(value=-8, unit=TimeUnit.DAY),
    )
    episode = episode_of(evaluate(schedule, context(schedule)))

    assert episode.status == ConfinementStatus.WAITING_FOR_ANCHOR
    assert "discharge precedes" in episode.explanation["reason"]


def test_actual_discharge_before_actual_admission_is_flagged():
    schedule = confinement_schedule()
    ctx = context(schedule, actual_event_values={
        "ADMISSION": [date(2026, 9, 12)], "DISCHARGE": [date(2026, 9, 10)],
    })
    episode = episode_of(evaluate(schedule, ctx))
    assert episode.status == ConfinementStatus.WAITING_FOR_ANCHOR
    assert "actual discharge precedes actual admission" in episode.explanation["reason"]


def test_two_overlapping_confinement_periods_are_flagged():
    """Rules 2 and 3: a patient cannot be admitted to two stays at once."""
    schedule = confinement_schedule()
    first = schedule.confinement_episodes[0]
    overlapping = first.model_copy(deep=True)
    overlapping.id = uuid4()
    overlapping.code = "CONFINEMENT_2"
    overlapping.display_name = "Confinement Period 2"
    schedule.confinement_episodes.append(overlapping)
    schedule.claim_evidence.append(ClaimEvidence(
        evidence_id=schedule.evidence[0].id, claim_type="CONFINEMENT",
        claim_entity_type="CONFINEMENT_EPISODE", claim_entity_id=overlapping.id,
    ))

    result = evaluate(schedule, context(schedule))
    assert len(result.confinements) == 2
    for episode in result.confinements:
        assert episode.status == ConfinementStatus.WAITING_FOR_ANCHOR
        assert "overlaps another confinement episode" in episode.explanation["reason"]


def test_sequential_confinement_periods_do_not_overlap():
    """A crossover study with a washout between periods is legitimate."""
    schedule = confinement_schedule()
    period_2_events = []
    for code, offset in (("ADMISSION_2", 27), ("DOSING_DAY_2", 28), ("DISCHARGE_2", 30)):
        event = visit(code, offset)
        event.evidence_refs = schedule.events[0].evidence_refs
        period_2_events.append(event)
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            schedule.claim_evidence.append(ClaimEvidence(
                evidence_id=schedule.evidence[0].id, claim_type=claim_type,
                claim_entity_type="EVENT", claim_entity_id=event.id, claim_path=path,
            ))
    schedule.events.extend(period_2_events)
    period_2 = ConfinementEpisodeDefinition(
        code="CONFINEMENT_2", protocol_label="Confinement Period 2",
        display_name="Confinement Period 2",
        admission_event_code="ADMISSION_2", dose_event_codes=["DOSING_DAY_2"],
        discharge_event_code="DISCHARGE_2",
        evidence_refs=[schedule.evidence[0].id],
        days=[
            ConfinementDayDefinition(day_label="Day -1", relative_day=-1),
            ConfinementDayDefinition(day_label="Day 1", relative_day=1),
            ConfinementDayDefinition(day_label="Day 3", relative_day=3),
        ],
    )
    schedule.confinement_episodes.append(period_2)
    schedule.claim_evidence.append(ClaimEvidence(
        evidence_id=schedule.evidence[0].id, claim_type="CONFINEMENT",
        claim_entity_type="CONFINEMENT_EPISODE", claim_entity_id=period_2.id,
    ))

    result = evaluate(schedule, context(schedule))
    assert len(result.confinements) == 2
    for episode in result.confinements:
        assert episode.status == ConfinementStatus.UPCOMING
        assert "overlapping_episodes" not in episode.explanation
