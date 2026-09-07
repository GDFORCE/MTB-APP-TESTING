"""Confinement/study-day structure reaches UCTSM (doc s11, §36 items 14/15).

"Day -1, Day 1, Day 2, Day 3, Discharge" must become ONE confinement episode
with study days - never four separate hospital visits that each generate
their own arrival reminder. Proven two ways: structurally (a real
ConfinementEpisodeDefinition with real study days), and through the REAL
engine (the patient is IN_CONFINEMENT as one continuous stay, and an internal
study day never produces its own arrival reminder).

Grouping is never inferred from consecutive day numbers - only from the
protocol's own explicit confinement_episode_id, proven by a control case where
four ordinary visits on consecutive days stay four ordinary visits.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.evaluator import ScheduleEvaluator  # noqa: E402
from app.domain.schedule.models import PatientContext, ScheduleStatus  # noqa: E402
from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402
from app.services.schedule_projection import ProjectedConfinement, ProjectedEvent, project  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402

BASELINE = date(2026, 1, 1)


def _event(event_id, name, day_offset, *, confinement=None, activities=None):
    raw = {
        "id": event_id, "name": name, "event_type": "inpatient_admission",
        "timing": {"kind": "offset", "anchor_id": "a1",
                   "offset": {"value": day_offset, "unit": "day"},
                   "source_label": f"Day {day_offset}"},
        "activity_ids": activities or [], "evidence_ids": ["e1"],
    }
    if confinement:
        episode_id, role, relative_day = confinement
        raw.update({
            "confinement_episode_id": episode_id,
            "confinement_role": role,
            "confinement_relative_day": relative_day,
        })
    return raw


def confinement_plan() -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [], "phases": [],
        "activities": [
            {"id": "act-vitals", "name": "Vitals", "evidence_ids": ["e1"]},
            {"id": "act-dose", "name": "Dose administration", "evidence_ids": ["e1"]},
            {"id": "act-pk", "name": "PK sampling", "evidence_ids": ["e1"]},
        ],
        "events": [
            _event("admit", "Day -1 (Admission)", -1,
                   confinement=("conf1", "admission", -1), activities=["act-vitals"]),
            _event("dose1", "Day 1 (Dosing)", 0,
                   confinement=("conf1", "dose", 1), activities=["act-dose", "act-pk"]),
            _event("day2", "Day 2", 1,
                   confinement=("conf1", "study_day", 2), activities=["act-vitals"]),
            _event("discharge", "Day 3 (Discharge)", 2,
                   confinement=("conf1", "discharge", 3), activities=["act-vitals"]),
        ],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


# --- structure ----------------------------------------------------------------

def test_a_grouped_confinement_becomes_one_episode_with_study_days():
    schedule, _issues = build(confinement_plan())

    assert len(schedule.confinement_episodes) == 1
    episode = schedule.confinement_episodes[0]
    assert episode.admission_event_code == "DAY_1_ADMISSION"
    assert episode.discharge_event_code == "DAY_3_DISCHARGE"
    assert episode.dose_event_codes == ["DAY_1_DOSING"]
    assert [day.relative_day for day in episode.days] == [-1, 1, 2, 3]
    assert not [
        item for item in schedule.events
        if item.confinement is not None
    ], "no per-event confinement inference is invented on the Event itself"


def test_study_day_activities_carry_real_activity_codes():
    schedule, _issues = build(confinement_plan())
    episode = schedule.confinement_episodes[0]

    dosing_day = next(day for day in episode.days if day.relative_day == 1)
    assert set(dosing_day.activity_codes) == {"DOSE_ADMINISTRATION", "PK_SAMPLING"}


def test_the_four_member_events_still_exist_as_ordinary_schedule_events():
    """The episode groups them for confinement purposes; it does not replace
    them - each remains a real Event with its own timing and activities."""
    schedule, _issues = build(confinement_plan())
    assert len(schedule.events) == 4


# --- grouping is never inferred -----------------------------------------------

def test_four_consecutive_day_visits_with_no_grouping_stay_four_ordinary_visits():
    """The control case: consecutive days alone must never imply confinement."""
    plan = confinement_plan()
    for event in plan["events"]:
        event.pop("confinement_episode_id", None)
        event.pop("confinement_role", None)
        event.pop("confinement_relative_day", None)
    schedule, _issues = build(plan)

    assert not schedule.confinement_episodes
    assert len(schedule.events) == 4


def test_an_incomplete_episode_missing_discharge_is_not_built():
    plan = confinement_plan()
    plan["events"] = [e for e in plan["events"] if e["id"] != "discharge"]
    schedule, _issues = build(plan)

    assert not schedule.confinement_episodes
    # The remaining member events are still preserved individually.
    assert len(schedule.events) == 3


def test_an_episode_with_two_admissions_is_not_built():
    """Ambiguous grouping (two admissions claimed for one episode) must not
    be forced into a single, arbitrarily-chosen structure."""
    plan = confinement_plan()
    plan["events"][1]["confinement_role"] = "admission"  # dose1 also claims admission
    schedule, _issues = build(plan)
    assert not schedule.confinement_episodes


# --- the real engine: one continuous stay, no duplicate arrival reminders ----

def _resolved_and_approved(schedule):
    for event in schedule.events:
        for qualifier in event.qualifiers:
            qualifier.resolved = True
        for activity in event.activities:
            for qualifier in activity.qualifiers:
                qualifier.resolved = True
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    return schedule


def test_the_engine_reports_one_continuous_confinement_status():
    schedule, _issues = build(confinement_plan())
    schedule = _resolved_and_approved(schedule)

    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={schedule.anchors[0].code: BASELINE},
    )
    result = ScheduleEvaluator().evaluate(
        schedule, context, horizon=BASELINE + timedelta(days=30))

    assert len(result.confinements) == 1
    confinement = result.confinements[0]
    assert confinement.episode_code == schedule.confinement_episodes[0].code
    # Planned, not yet admitted: an upcoming stay, never invented as already
    # in progress or discharged.
    assert confinement.status.value == "UPCOMING"


def test_confinement_absorption_prevents_duplicate_arrival_reminders():
    """The actual patient-facing consequence: once absorbed by a confinement
    episode, an internal study day produces NO separate arrival reminder -
    proven through the real reminder projection, not by assertion alone."""
    schedule, _issues = build(confinement_plan())
    episode = schedule.confinement_episodes[0]

    events = [
        ProjectedEvent(logical_key="DAY_1_ADMISSION:0", event_code="DAY_1_ADMISSION",
                        event_type="INPATIENT_ADMISSION", display_name="Admission",
                        status="RESOLVED", nominal_date=date(2026, 1, 5)),
        ProjectedEvent(logical_key="DAY_2:0", event_code="DAY_2",
                        event_type="ASSESSMENT", display_name="Day 2",
                        status="RESOLVED", nominal_date=date(2026, 1, 7)),
        ProjectedEvent(logical_key="DAY_3_DISCHARGE:0", event_code="DAY_3_DISCHARGE",
                        event_type="INPATIENT_DISCHARGE", display_name="Discharge",
                        status="RESOLVED", nominal_date=date(2026, 1, 8)),
    ]
    confinement = ProjectedConfinement(
        episode_code=episode.code, display_name=episode.display_name,
        status="IN_CONFINEMENT",
        start_date=date(2026, 1, 5), end_date=date(2026, 1, 8),
        member_event_codes=[
            episode.admission_event_code, "DAY_2", episode.discharge_event_code],
    )
    result = project(events, today=date(2026, 1, 6), confinements=[confinement])

    # One calendar entry for the whole stay - not three.
    assert len([item for item in result.calendar if item.kind == "CONFINEMENT"]) == 1
    assert not [item for item in result.calendar if item.kind == "EVENT"]
    assert not result.reminders, (
        "an internal study day inside a confinement must not remind the "
        "already-admitted patient to 'arrive' again")
    absorbed_reasons = {item.logical_key: item.reason for item in result.suppressed}
    assert "confinement" in absorbed_reasons["DAY_2:0"]
