"""The adapter fails, not silently, when a plan reference resolves to nothing
(doc s29).

A schedule-of-assessments row that names an activity the plan never defined a
template for, a dependency naming a visit that does not exist, or a
recurrence rule naming a visit that does not exist - none of these may
silently vanish into a schedule that LOOKS complete. Each becomes a blocking
``INFORMATION_LOSS`` issue, and - the actual point of the requirement -
that block is durable: it survives every later validate/submit/approve call,
not only the first read right after import.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402


def base_plan() -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [], "phases": [],
        "activities": [{"id": "act-real", "name": "Labs", "evidence_ids": ["e1"]}],
        "events": [
            {"id": "ev1", "name": "Cycle 1 Day 1", "event_type": "site visit",
             "timing": {"kind": "offset", "anchor_id": "a1",
                        "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
             "activity_ids": ["act-real"], "evidence_ids": ["e1"]},
            {"id": "ev2", "name": "Cycle 1 Day 8", "event_type": "site visit",
             "timing": {"kind": "offset", "anchor_id": "a1",
                        "offset": {"value": 7, "unit": "day"}, "source_label": "Day 8"},
             "activity_ids": ["act-real"], "evidence_ids": ["e1"]},
        ],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


def durable_issues(schedule):
    """What every future validate/submit/approve call would ACTUALLY see."""
    return [
        item for item in ScheduleValidator().validate(schedule)
        if item.issue_code == "INFORMATION_LOSS"
    ]


# --- a clean plan produces no loss issues at all -----------------------------

def test_a_complete_plan_produces_no_information_loss_issues():
    schedule, issues = build(base_plan())

    assert not [item for item in issues if item.issue_code == "INFORMATION_LOSS"]
    assert not durable_issues(schedule)


# --- a dangling activity reference -------------------------------------------

def test_a_dangling_activity_reference_is_detected_and_blocks():
    plan = base_plan()
    plan["events"][0]["activity_ids"] = ["act-real", "act-never-defined"]
    schedule, issues = build(plan)

    at_import = [item for item in issues if item.issue_code == "INFORMATION_LOSS"]
    assert at_import and at_import[0].blocking is True

    event = next(item for item in schedule.events if item.display_name == "Cycle 1 Day 1")
    assert event.metadata["unresolved_activity_refs"] == ["act-never-defined"]

    survives = durable_issues(schedule)
    assert survives, "the loss must be re-derivable, not only returned once at import"
    assert survives[0].blocking is True


def test_the_activity_that_does_exist_is_unaffected_by_the_one_that_does_not():
    plan = base_plan()
    plan["events"][0]["activity_ids"] = ["act-real", "act-never-defined"]
    schedule, _issues = build(plan)

    event = next(item for item in schedule.events if item.display_name == "Cycle 1 Day 1")
    assert len(event.activities) == 1
    assert event.activities[0].display_name == "Labs"


# --- a dangling dependency ----------------------------------------------------

def test_a_dependency_naming_a_nonexistent_visit_is_detected_and_blocks():
    plan = base_plan()
    plan["transitions"] = [{
        "id": "t1", "from_event_id": "ev-does-not-exist", "to_event_id": "ev2",
        "relation": "after",
    }]
    schedule, issues = build(plan)

    assert [item for item in issues if item.issue_code == "INFORMATION_LOSS"]
    event = next(item for item in schedule.events if item.display_name == "Cycle 1 Day 8")
    assert event.metadata["unresolved_dependency_refs"] == ["ev-does-not-exist"]
    assert not event.dependencies, "a dangling dependency must not become a real one"

    assert durable_issues(schedule)


# --- a dangling recurrence target ---------------------------------------------

def test_a_recurrence_naming_a_nonexistent_visit_is_detected_and_blocks():
    plan = base_plan()
    plan["recurrences"] = [{
        "id": "r1", "event_ids": ["ev1", "ev-does-not-exist"],
        "frequency": {"value": 21, "unit": "day"}, "start_occurrence": 1,
    }]
    schedule, issues = build(plan)

    assert [item for item in issues if item.issue_code == "INFORMATION_LOSS"]
    extensions = schedule.schedule_metadata.extensions
    assert any(item.get("type") == "INFORMATION_LOSS" for item in extensions)

    assert durable_issues(schedule)


def test_a_valid_recurrence_member_is_unaffected_by_a_dangling_sibling():
    plan = base_plan()
    plan["recurrences"] = [{
        "id": "r1", "event_ids": ["ev1", "ev-does-not-exist"],
        "frequency": {"value": 21, "unit": "day"}, "start_occurrence": 1,
    }]
    schedule, _issues = build(plan)

    ev1 = next(item for item in schedule.events if item.display_name == "Cycle 1 Day 1")
    assert ev1.recurrence is not None, "the real event's own recurrence must still resolve"


# --- the block is durable across the exact lifecycle that matters ------------

def test_the_block_survives_a_full_import_review_and_approve_cycle():
    """The requirement's actual test: not 'is an issue returned once', but
    'does approval actually stay blocked' after going through the same
    review lifecycle a real reviewer would use."""
    from uuid import uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.db.repositories import ScheduleRepository
    from app.services.canonical_bridge import import_schedule_definitions
    from app.services.schedule_service import ScheduleReviewService

    import test_canonical_journey as journey

    plan = base_plan()
    plan["events"][0]["activity_ids"] = ["act-real", "act-never-defined"]

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _trial, rows = import_schedule_definitions(
            session, organization_id=uuid4(), actor_id=journey.REVIEWER,
            external_trial_id="t-loss", protocol_number="ABC", study_title="S",
            definitions=[{
                "id": "sd-loss", "canonical_plan": plan,
                "evidence_facts": [evidence_fact("e1", "SoA")],
                "option_label": "Primary", "file_name": "p.pdf",
            }],
        )
        session.commit()
        row = rows[0]
        service = ScheduleReviewService(session)

        service.submit_for_review(row.schedule_version_id, actor_id=journey.REVIEWER)
        journey.review_every_field(session, row.schedule_version_id)

        try:
            service.approve(row.schedule_version_id, reviewer_id=journey.REVIEWER)
            raise AssertionError(
                "a schedule with a dangling activity reference must never approve")
        except ValueError as error:
            assert "approval blocked" in str(error)

        # And it is not merely the first check that catches it - a second,
        # independent validate pass sees it too, proving it was never a
        # one-time note.
        reloaded = ScheduleRepository(session).get(row.schedule_version_id)
        assert any(
            item.issue_code == "INFORMATION_LOSS"
            for item in ScheduleValidator().validate(reloaded)
        )
