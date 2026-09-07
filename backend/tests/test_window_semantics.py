"""Window semantics survive the import, one type at a time (doc s7).

"Lab must be within 28 days" and "visit tolerance +/- 28 days" are different
clinical rules: one is a validity constraint on reusing a prior result, the
other is a scheduling tolerance around a nominal date. Before this, every
stated window - whatever kind the protocol described - was flattened into the
same before/after tolerance shape.

The fix does not teach the engine to schedule off a validity/lookback/gap
constraint (that would be new clinical behaviour, not translation). It refuses
to force the wrong shape: a genuine tolerance still produces a tolerance
Window, and every other window type is instead preserved as a reviewed,
structured qualifier - visible, evidenced, and blocking until a reviewer
disposes of it, exactly like an unresolved footnote.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.timing import NominalWindowTiming  # noqa: E402
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402


def plan_with_window(window: dict, *, source_label: str = "") -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [], "phases": [],
        "activities": [{"id": "act", "name": "Haematology", "evidence_ids": ["e1"]}],
        "events": [{
            "id": "ev1", "name": "Cycle 1 Day 1", "event_type": "site visit",
            "timing": {"kind": "offset", "anchor_id": "a1",
                       "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
            "window": {"scope": "visit", "state": "stated",
                       "source_label": source_label, **window},
            "activity_ids": ["act"], "evidence_ids": ["e1"],
        }],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


def event_timing(schedule):
    return schedule.events[0].timing


def event_qualifiers(schedule):
    return schedule.events[0].qualifiers


def unresolved_qualifier_issues(schedule):
    """The real approval gate: ScheduleValidator, not the import's own issues.

    ``universal_schedule_from_plan`` returns only the conflicts/conditions it
    saw during translation; whether a qualifier actually blocks approval is a
    structural question the validator answers, same as the live review flow.
    """
    return [
        item for item in ScheduleValidator().validate(schedule)
        if item.issue_code == "UNRESOLVED_QUALIFIER"
    ]


# --- an actual tolerance is still a tolerance --------------------------------

def test_a_stated_tolerance_still_produces_a_real_tolerance_window():
    plan = plan_with_window({
        "window_type": "tolerance",
        "early": {"value": 3, "unit": "day"}, "late": {"value": 3, "unit": "day"},
    })
    schedule, _issues = build(plan)

    timing = event_timing(schedule)
    assert isinstance(timing, NominalWindowTiming)
    assert timing.window.before.value == 3 and timing.window.after.value == 3
    assert not event_qualifiers(schedule), "a real tolerance needs no reviewer qualifier"
    assert not unresolved_qualifier_issues(schedule)


def test_an_unspecified_window_type_defaults_to_tolerance():
    """window_type is optional on real extractions; absence means the ordinary case."""
    plan = plan_with_window({
        "early": {"value": 2, "unit": "day"}, "late": {"value": 2, "unit": "day"},
    })
    schedule, _issues = build(plan)

    assert isinstance(event_timing(schedule), NominalWindowTiming)


# --- every other window type is preserved, never flattened -------------------

@pytest.mark.parametrize("window_type,label,wording", [
    ("validity", "Screening labs valid for 28 days",
     "validity window"),
    ("lookback", "Prior imaging may be used if performed within 90 days",
     "lookback window"),
    ("minimum_gap", "At least 21 days must elapse since the last infusion",
     "minimum gap"),
    ("maximum_gap", "No more than 14 days between consent and screening",
     "maximum gap"),
])
def test_a_non_tolerance_window_is_never_forced_into_a_tolerance(window_type, label, wording):
    plan = plan_with_window({
        "window_type": window_type,
        "early": {"value": 28, "unit": "day"},
    }, source_label=label)
    schedule, _issues = build(plan)

    # The clinical mistake this whole change prevents: no NominalWindowTiming,
    # so nothing downstream can compute "the visit may occur N days late" from
    # what the protocol actually stated as a different kind of rule.
    assert not isinstance(event_timing(schedule), NominalWindowTiming)

    qualifiers = event_qualifiers(schedule)
    assert len(qualifiers) == 1
    qualifier = qualifiers[0]
    assert qualifier.category.value == "WINDOW"
    assert qualifier.resolved is False
    assert wording in qualifier.text.lower()
    assert label in qualifier.text

    # The numbers survive as DATA, not only as a sentence a human must re-parse.
    assert qualifier.details["window_type"] == window_type
    assert qualifier.details["lower_bound"] == {"value": 28, "unit": "day"}
    assert qualifier.details["source_label"] == label

    blocking = unresolved_qualifier_issues(schedule)
    assert blocking, "an unclassified window type must block approval like any footnote"


def test_a_non_tolerance_window_is_resolvable_through_the_same_workflow():
    """The qualifier resolution endpoint (doc s6) is the one path to clear this."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.db.repositories import ScheduleRepository
    from app.domain.schedule.validator import ScheduleValidator
    from app.services.canonical_bridge import import_schedule_definitions
    from app.services.schedule_service import ScheduleReviewService

    import test_canonical_journey as journey

    plan = plan_with_window({
        "window_type": "validity",
        "early": {"value": 28, "unit": "day"},
    }, source_label="Screening labs valid for 28 days")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _trial, rows = import_schedule_definitions(
            session, organization_id=journey.uuid4(), actor_id=journey.REVIEWER,
            external_trial_id="trial-w", protocol_number="ABC-123",
            study_title="A study",
            definitions=[{
                "id": "sd-w", "canonical_plan": plan,
                "evidence_facts": [evidence_fact("e1", "SoA")],
                "option_label": "Primary", "file_name": "protocol.pdf",
            }],
        )
        session.commit()
        row = rows[0]

        schedule = ScheduleRepository(session).get(row.schedule_version_id)
        qualifier = schedule.events[0].qualifiers[0]
        blocking_before = [
            item for item in ScheduleValidator().validate(schedule)
            if item.issue_code == "UNRESOLVED_QUALIFIER"
        ]
        assert blocking_before

        ScheduleReviewService(session).resolve_qualifier(
            row.schedule_version_id, qualifier.id, reviewer_id=journey.REVIEWER,
            decision="ACCEPT", category="WINDOW",
            reason="Confirmed: 28-day validity window for screening labs, "
                   "not a visit tolerance.",
        )
        session.commit()

        after = ScheduleRepository(session).get(row.schedule_version_id)
        blocking_after = [
            item for item in ScheduleValidator().validate(after)
            if item.issue_code == "UNRESOLVED_QUALIFIER"
        ]
        assert not blocking_after
        resolved = after.events[0].qualifiers[0]
        assert resolved.resolved is True
        # The original window facts are untouched by resolving it.
        assert resolved.details["window_type"] == "validity"


def test_an_unclear_window_still_preserves_its_source_label():
    plan = plan_with_window({"state": "unclear"}, source_label="see footnote c")
    plan["events"][0]["window"]["state"] = "unclear"
    schedule, _issues = build(plan)

    qualifier = event_qualifiers(schedule)[0]
    assert qualifier.details.get("source_label") == "see footnote c"
    assert "unclear" in qualifier.text.lower()
