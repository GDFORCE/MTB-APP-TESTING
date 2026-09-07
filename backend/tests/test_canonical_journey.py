"""The production journey, end to end, on one engine.

Upload -> extract -> canonical draft -> Sponsor/PI review -> approve -> add
patient -> patient schedule. Each step is exercised against the real services,
because the value of consolidating on UCTSM is precisely that these steps share
one model; a test that stubbed the middle would prove nothing about that.

The two behaviours that matter most here are refusals: an AI draft cannot reach a
patient unapproved, and an anchor-based visit stays undated until its anchor
exists rather than being given a plausible date.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.services.canonical_bridge import import_schedule_definitions
from app.services.operational_bridge import OperationalBridgeService
from app.services.schedule_service import ScheduleReviewService

from test_canonical_import import evidence_fact

ORG = uuid4()
REVIEWER = uuid4()
BASELINE = date(2026, 9, 1)
HORIZON = BASELINE + timedelta(days=400)


def plan() -> dict:
    """A protocol with a fixed visit, an open-ended repeat and an anchored visit."""
    return {
        "schema_version": "2.0",
        "protocol_version": "v1.0",
        "anchors": [
            {"id": "a-base", "name": "Baseline", "anchor_type": "first_dose",
             "evidence_ids": ["e1"]},
            {"id": "a-last", "name": "Last Dose", "anchor_type": "last_dose",
             "evidence_ids": ["e1"]},
        ],
        "branches": [],
        "phases": [],
        "activities": [
            {"id": "act-labs", "name": "Labs", "evidence_ids": ["e1"]},
            {"id": "act-dose", "name": "Dose", "evidence_ids": ["e1"]},
        ],
        "events": [
            {"id": "e-c1d1", "name": "C1D1", "event_type": "treatment",
             # Day 1 IS the baseline day in this protocol's numbering, which is
             # what makes the q21d repeat expressible from the same anchor.
             "timing": {"kind": "offset", "anchor_id": "a-base",
                        "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
             "window": {"scope": "visit", "state": "stated",
                        "early": {"value": 1, "unit": "day"},
                        "late": {"value": 1, "unit": "day"}},
             "activity_ids": ["act-labs", "act-dose"], "evidence_ids": ["e1"]},
            {"id": "e-safety", "name": "Safety Follow-up", "event_type": "site visit",
             "timing": {"kind": "offset", "anchor_id": "a-last",
                        "offset": {"value": 30, "unit": "day"},
                        "relation": "after",
                        "source_label": "30 days after Last Dose"},
             "window": {"scope": "visit", "state": "stated",
                        "early": {"value": 3, "unit": "day"},
                        "late": {"value": 3, "unit": "day"}},
             "activity_ids": ["act-labs"], "evidence_ids": ["e1"]},
        ],
        "recurrences": [{
            "id": "r-1", "event_ids": ["e-c1d1"],
            "frequency": {"value": 21, "unit": "day"}, "start_occurrence": 1,
            "evidence_ids": ["e1"],
        }],
        "transitions": [], "conditions": [], "conflicts": [],
    }


def definition(external_id: str = "sd-1", *, payload=None) -> dict:
    return {
        "id": external_id, "canonical_plan": payload or plan(),
        "evidence_facts": [evidence_fact("e1", "Schedule of Assessments")],
        "option_label": "Primary", "file_name": "protocol.pdf",
    }


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        yield active


def imported(session, definitions=None):
    trial, rows = import_schedule_definitions(
        session, organization_id=ORG, actor_id=REVIEWER,
        external_trial_id="trial-1", protocol_number="ABC-123",
        study_title="A study", definitions=definitions or [definition()],
    )
    session.commit()
    return trial, rows[0]


def review_every_field(session, version_id, reviewer=REVIEWER):
    """Doc 10: approval requires a person to have looked at each field."""
    schedule = ScheduleRepository(session).get(version_id)
    for event in schedule.events:
        paths = ["display_name", "timing"]
        if event.activities:
            paths.append("activities")
        if event.recurrence:
            paths.append("recurrence")
        if event.applicability:
            paths.append("applicability")
        if event.conditions:
            paths.append("conditions")
        for path in paths:
            session.add(db.ReviewDecision(
                schedule_version_id=version_id, decision="CONFIRM",
                reviewer_id=reviewer, entity_type="EVENT", entity_id=event.id,
                field_path=path,
            ))
    session.flush()


def approve(session, version_id, *, effective_from=None):
    service = ScheduleReviewService(session)
    service.submit_for_review(version_id, actor_id=REVIEWER)
    review_every_field(session, version_id)
    service.approve(version_id, reviewer_id=REVIEWER, effective_from=effective_from)
    session.commit()


def enrol(session, trial, row, *, external_patient_id="patient-1"):
    enrollment = OperationalBridgeService(session).enroll_patient(
        organization_id=ORG, actor_id=REVIEWER, canonical_trial_id=trial.id,
        schedule_definition_id=row.schedule_definition_id,
        external_patient_id=external_patient_id, patient_code="P-001",
        baseline_date=BASELINE, horizon=HORIZON,
    )
    session.commit()
    return enrollment


# --- the gate ----------------------------------------------------------------

def test_an_extracted_draft_cannot_reach_a_patient_before_approval(session):
    trial, row = imported(session)

    assert row.status == "VALIDATION_REQUIRED"
    with pytest.raises(ValueError, match="no approved canonical schedule"):
        enrol(session, trial, row)


def test_approval_requires_a_reviewer_to_have_confirmed_each_field(session):
    _, row = imported(session)
    service = ScheduleReviewService(session)
    service.submit_for_review(row.schedule_version_id, actor_id=REVIEWER)

    with pytest.raises(ValueError, match="outstanding field review"):
        service.approve(row.schedule_version_id, reviewer_id=REVIEWER)


def test_an_unresolved_footnote_blocks_approval_rather_than_being_applied(session):
    """Case 8: a detected qualifier is never silently applied or dropped."""
    payload = plan()
    payload["activities"][0]["conditional_text"] = "Footnote a: only if clinically indicated"
    _, row = imported(session, [definition("sd-foot", payload=payload)])
    service = ScheduleReviewService(session)
    service.submit_for_review(row.schedule_version_id, actor_id=REVIEWER)
    review_every_field(session, row.schedule_version_id)

    with pytest.raises(ValueError, match="approval blocked"):
        service.approve(row.schedule_version_id, reviewer_id=REVIEWER)

    codes = {
        issue.issue_code for issue in session.scalars(select(db.ValidationIssue).where(
            db.ValidationIssue.schedule_version_id == row.schedule_version_id))
    }
    assert "UNRESOLVED_QUALIFIER" in codes


# --- the journey -------------------------------------------------------------

def test_approved_schedule_produces_a_patient_schedule_from_the_baseline(session):
    trial, row = imported(session)
    approve(session, row.schedule_version_id)

    enrollment = enrol(session, trial, row)
    by_code: dict[str, list] = {}
    for event in enrollment.events:
        by_code.setdefault(event.event_code, []).append(event)

    c1d1 = by_code["C1D1"][0]
    assert c1d1.nominal_date == BASELINE
    # The window the protocol stated, not a default.
    assert c1d1.earliest_date == c1d1.nominal_date - timedelta(days=1)
    assert c1d1.latest_date == c1d1.nominal_date + timedelta(days=1)
    assert c1d1.activities == ("Labs", "Dose")


def test_an_anchor_based_visit_stays_undated_until_its_anchor_exists(session):
    """Case 2. The one thing it must never do is borrow the baseline date."""
    trial, row = imported(session)
    approve(session, row.schedule_version_id)

    enrollment = enrol(session, trial, row)
    safety = next(
        event for event in enrollment.events if event.event_code == "SAFETY_FOLLOW_UP")

    assert safety.nominal_date is None
    assert safety.earliest_date is None and safety.latest_date is None
    assert safety.status == "WAITING_FOR_ANCHOR"


def test_an_open_ended_repeat_rolls_forward_instead_of_stopping_at_a_guess(session):
    """Case 4: cycles continue to the horizon; no invented maximum."""
    trial, row = imported(session)
    approve(session, row.schedule_version_id)

    enrollment = enrol(session, trial, row)
    cycles = sorted(
        (event.nominal_date for event in enrollment.events
         if event.event_code == "C1D1" and event.nominal_date),
    )

    assert len(cycles) > 4
    assert cycles[0] == BASELINE
    assert cycles[1] - cycles[0] == timedelta(days=21)
    assert cycles[-1] <= HORIZON


def test_a_patient_is_pinned_to_the_version_they_enrolled_under(session):
    """Case 10: an amendment must never re-date a patient already on study."""
    trial, first = imported(session)
    approve(session, first.schedule_version_id)
    enrollment = enrol(session, trial, first)

    amended = plan()
    amended["events"][0]["timing"]["offset"] = {"value": 2, "unit": "day"}
    _, second = imported(session, [definition("sd-2", payload=amended)])
    approve(session, second.schedule_version_id)

    patient = session.get(db.Patient, enrollment.patient_id)
    assert patient.current_schedule_version_id == first.schedule_version_id
    assignments = list(session.scalars(select(db.PatientScheduleAssignment).where(
        db.PatientScheduleAssignment.patient_id == patient.id)))
    assert [item.schedule_version_id for item in assignments] == [first.schedule_version_id]


def test_a_version_approved_for_a_future_date_does_not_take_enrolments_yet(session):
    """Doc 9 s8: approved is not the same as in force."""
    trial, row = imported(session)
    approve(session, row.schedule_version_id, effective_from=date(2999, 1, 1))

    with pytest.raises(ValueError, match="no approved canonical schedule"):
        enrol(session, trial, row)


def test_reenrolling_the_same_patient_is_idempotent(session):
    trial, row = imported(session)
    approve(session, row.schedule_version_id)

    first = enrol(session, trial, row)
    second = enrol(session, trial, row)

    assert second.patient_id == first.patient_id
    assert len(list(session.scalars(select(db.Patient)))) == 1
