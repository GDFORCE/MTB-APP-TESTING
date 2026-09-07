"""Reminder and calendar projection rules.

Sources: doc 1 sections 23-25, doc 2 sections 23-24 and 37-38, doc 6 sections 14-16
and 40-41, doc 10 sections 21-23.
"""

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.schedule_projection import (
    ProjectedConfinement, ProjectedEvent, project, reconcile,
)

TODAY = date(2026, 9, 5)


def event(code: str, **changes) -> ProjectedEvent:
    values = {
        "logical_key": f"{code}:0", "event_code": code, "display_name": code.title(),
        "status": "RESOLVED", "event_type": "SITE_VISIT",
        "nominal_date": date(2026, 9, 10),
    }
    values.update(changes)
    return ProjectedEvent(**values)


def keys(items) -> set:
    return {item.logical_key for item in items}


def suppressed_reason(result, logical_key: str) -> str:
    return next(item.reason for item in result.suppressed if item.logical_key == logical_key)


def test_only_resolved_dated_events_produce_reminders():
    result = project([
        event("C1D1"),
        event("SAFETY_FOLLOWUP", status="WAITING_FOR_ANCHOR", nominal_date=None),
        event("REPEAT_CBC", status="WAITING_FOR_CONDITION", nominal_date=None),
        event("CANCELLED_CYCLE", status="CANCELLED", nominal_date=date(2026, 10, 1)),
        event("PAUSED_CYCLE", status="PAUSED", nominal_date=date(2026, 10, 1)),
    ], today=TODAY)

    assert keys(result.reminders) == {"C1D1:0"}
    # An awaiting-anchor visit cannot become overdue, because it has no date at all.
    assert "WAITING_FOR_ANCHOR" in suppressed_reason(result, "SAFETY_FOLLOWUP:0")
    assert "WAITING_FOR_CONDITION" in suppressed_reason(result, "REPEAT_CBC:0")
    assert "CANCELLED" in suppressed_reason(result, "CANCELLED_CYCLE:0")
    assert "PAUSED" in suppressed_reason(result, "PAUSED_CYCLE:0")


def test_unscheduled_visits_stay_inert_until_a_site_creates_them():
    result = project([event("UNSCHEDULED_SAFETY", is_unscheduled=True)], today=TODAY)
    assert result.reminders == []
    assert result.calendar == []
    assert "unscheduled" in suppressed_reason(result, "UNSCHEDULED_SAFETY:0")


def test_reminder_wording_follows_the_event_type():
    """Doc s13: exactly the vocabulary the extractor/adapter actually write
    (CORE_EVENT_TYPES), not a second, similar-looking set of strings.

    Before this was unified, this test asserted against "PHONE_CONTACT" and
    "IMAGING_ONLY" - values nothing in the real pipeline ever produces (it
    writes TELEPHONE_CONTACT and IMAGING). The test passed while the real
    code path silently fell through to generic wording for every actual
    telephone contact and imaging visit. Asserting on the real vocabulary is
    the fix; a passing test on the wrong strings was worse than no test.
    """
    result = project([
        event("C1D1", event_type="SITE_VISIT", display_name="Cycle 1 Day 1"),
        event("DAY7", event_type="TELEPHONE_CONTACT", display_name="Day 7 safety follow-up"),
        event("WEEK12", event_type="HOME_VISIT", display_name="Home assessment"),
        event("CT", event_type="IMAGING", display_name="CT assessment"),
    ], today=TODAY)
    messages = {item.event_code: item.message for item in result.reminders}
    attendance = {item.event_code: item.requires_attendance for item in result.reminders}

    assert messages["C1D1"].startswith("Your study visit is scheduled")
    assert messages["DAY7"].startswith("Your study team is expected to contact you")
    assert messages["WEEK12"].startswith("A study-related home visit is scheduled")
    assert messages["CT"].startswith("Your imaging assessment is scheduled")
    # A phone call must never be presented as travel to the site.
    assert attendance["C1D1"] is True and attendance["DAY7"] is False


def test_the_vocabulary_is_the_one_thing_shared_across_the_pipeline():
    """The concrete guarantee doc s13 asks for: no second vocabulary exists.

    Every key this module recognises, and every type it treats as
    "attendance required", must be a member of the SAME set the adapter and
    validator use (CORE_EVENT_TYPES) - not a parallel list that happens to
    look similar.
    """
    from app.domain.schedule.validator import CORE_EVENT_TYPES
    from app.services.schedule_projection import ATTENDANCE_EVENT_TYPES, PATIENT_WORDING

    assert set(PATIENT_WORDING) <= CORE_EVENT_TYPES
    assert ATTENDANCE_EVENT_TYPES <= CORE_EVENT_TYPES
    # The legacy strings must not reappear anywhere in this module's vocabulary.
    retired = {"PHONE_CONTACT", "REMOTE_VISIT", "LAB_ONLY", "IMAGING_ONLY",
               "PROCEDURE_ONLY", "INPATIENT_CONFINEMENT", "ONSITE"}
    assert not retired & set(PATIENT_WORDING)
    assert not retired & ATTENDANCE_EVENT_TYPES


def test_policy_state_uses_the_protocol_window():
    result = project([
        event("EARLY", nominal_date=date(2026, 9, 20),
              earliest_date=date(2026, 9, 18), latest_date=date(2026, 9, 22)),
        event("DUE", nominal_date=date(2026, 9, 5),
              earliest_date=date(2026, 9, 3), latest_date=date(2026, 9, 7)),
        event("LATE", nominal_date=date(2026, 8, 20),
              earliest_date=date(2026, 8, 18), latest_date=date(2026, 8, 22)),
    ], today=TODAY)
    states = {item.event_code: item.policy_state for item in result.reminders}
    assert states == {"EARLY": "UPCOMING", "DUE": "DUE_WINDOW", "LATE": "OVERDUE"}


def test_confinement_is_one_calendar_entry_and_absorbs_its_study_days():
    events = [
        event("ADMISSION", nominal_date=date(2026, 9, 10),
              event_type="INPATIENT_CONFINEMENT"),
        event("DOSING_DAY", nominal_date=date(2026, 9, 11),
              event_type="INPATIENT_CONFINEMENT"),
        event("DISCHARGE", nominal_date=date(2026, 9, 13),
              event_type="INPATIENT_CONFINEMENT"),
        event("WEEK4", nominal_date=date(2026, 10, 9)),
    ]
    result = project(events, today=TODAY, confinements=[ProjectedConfinement(
        episode_code="CONFINEMENT_1", display_name="Confinement Period 1",
        status="IN_CONFINEMENT", start_date=date(2026, 9, 10), end_date=date(2026, 9, 13),
        member_event_codes=["ADMISSION", "DOSING_DAY", "DISCHARGE"],
    )])

    confinement_entries = [item for item in result.calendar if item.kind == "CONFINEMENT"]
    assert len(confinement_entries) == 1
    assert confinement_entries[0].start_date == date(2026, 9, 10)
    assert confinement_entries[0].end_date == date(2026, 9, 13)
    # No separate calendar appointment or arrival reminder per internal study day.
    assert keys(result.reminders) == {"WEEK4:0"}
    assert "CONFINEMENT_1" in suppressed_reason(result, "DOSING_DAY:0")


def test_reconciliation_is_idempotent_when_nothing_changed():
    reminders = project([event("C1D1"), event("C1D8", nominal_date=date(2026, 9, 17))],
                        today=TODAY).reminders
    existing = [item.key for item in reminders]
    result = reconcile(existing, reminders)

    assert result.added == [] and result.cancelled == [] and result.updated == []
    assert len(result.unchanged) == 2


def test_moved_visit_cancels_the_old_reminder_instead_of_duplicating_it():
    """Doc 1 section 24: the patient must not be reminded of both dates."""
    before = project([event("POSTOP_30", nominal_date=date(2026, 10, 18))], today=TODAY)
    after = project([event("POSTOP_30", nominal_date=date(2026, 10, 20))], today=TODAY)

    result = reconcile([item.key for item in before.reminders], after.reminders)
    assert result.updated == ["reminder:POSTOP_30:0:2026-10-20"]
    assert result.cancelled == ["reminder:POSTOP_30:0:2026-10-18"]
    assert result.added == []


def test_cancelled_pathway_withdraws_its_reminders():
    before = project([event("C2D1"), event("C3D1", nominal_date=date(2026, 10, 1))],
                     today=TODAY)
    after = project([event("C2D1"),
                     event("C3D1", status="CANCELLED", nominal_date=date(2026, 10, 1))],
                    today=TODAY)

    result = reconcile([item.key for item in before.reminders], after.reminders)
    assert result.cancelled == ["reminder:C3D1:0:2026-10-01"]
    assert result.unchanged == ["reminder:C2D1:0:2026-09-10"]
