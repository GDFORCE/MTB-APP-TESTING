"""One schedule origin, shared by every consumer (requirement doc s4).

Day 0 is a clinical fact. Before this, the flat projection picked it by anchor
type while the UCTSM adapter took whichever anchor happened to be listed first,
so the same protocol row could resolve to two different calendar dates; and
patient enrolment looked for an anchor literally NAMED "Baseline", which stranded
every protocol whose origin is printed as "First Dose" or "Randomisation".

These tests pin the three properties that matter:

  1. the origin is chosen from the anchor's semantic TYPE and from what the
     schedule's own events count from - never from its printed name;
  2. the flat projection and the canonical adapter always agree;
  3. a genuinely ambiguous origin is refused, not guessed.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schedule_schema import (  # noqa: E402
    CanonicalSchedulePlan, project_canonical_plan, select_baseline_anchor,
)
from app.domain.schedule.models import schedule_origin_anchor  # noqa: E402
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402


def plan_with(anchors: list[dict], events: list[dict] | None = None) -> dict:
    """A minimal but complete plan; only the anchor structure varies per test."""
    return {
        "schema_version": "2.0",
        "anchors": anchors,
        "branches": [], "phases": [],
        "activities": [{"id": "act", "name": "Labs", "evidence_ids": ["e1"]}],
        "events": events if events is not None else [{
            "id": "ev1", "name": "Day 8 Visit", "event_type": "site visit",
            "timing": {"kind": "offset", "anchor_id": anchors[0]["id"],
                       "offset": {"value": 8, "unit": "day"},
                       "source_label": "Day 8"},
            "activity_ids": ["act"], "evidence_ids": ["e1"],
        }],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def origin_legacy_id(plan: dict) -> str | None:
    return select_baseline_anchor(CanonicalSchedulePlan.model_validate(plan)).anchor_id


def origin_canonical_code(plan: dict) -> str | None:
    schedule, _issues = universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])
    anchor = schedule_origin_anchor(schedule)
    return anchor.code if anchor else None


# --- the origin is a semantic type, not a name -------------------------------

@pytest.mark.parametrize("name", ["Baseline", "First Dose", "Randomisation", "Day 1"])
def test_origin_does_not_depend_on_what_the_anchor_is_called(name):
    """Renaming the anchor must not change which anchor is Day 0."""
    plan = plan_with([
        {"id": "a1", "name": name, "anchor_type": "first_dose", "evidence_ids": ["e1"]},
        {"id": "a2", "name": "Last Dose", "anchor_type": "last_dose", "evidence_ids": ["e1"]},
    ])
    resolution = select_baseline_anchor(CanonicalSchedulePlan.model_validate(plan))

    assert resolution.status == "RESOLVED"
    assert resolution.anchor_id == "a1"
    # The semantic type survives; it is never rewritten to a synthetic BASELINE.
    assert resolution.anchor_type == "first_dose"


@pytest.mark.parametrize("anchor_type", ["randomization", "first_dose", "cycle_start", "period_start"])
def test_each_origin_type_resolves(anchor_type):
    plan = plan_with([
        {"id": "a1", "name": "Origin", "anchor_type": anchor_type, "evidence_ids": ["e1"]},
        {"id": "a2", "name": "Progression", "anchor_type": "progression", "evidence_ids": ["e1"]},
    ])
    resolution = select_baseline_anchor(CanonicalSchedulePlan.model_validate(plan))

    assert resolution.status == "RESOLVED"
    assert resolution.anchor_id == "a1"


def test_a_non_origin_anchor_is_never_promoted_to_day_zero():
    """Consent and screening measure something else; they are not Day 0."""
    plan = plan_with([
        {"id": "consent", "name": "Informed Consent", "anchor_type": "consent",
         "evidence_ids": ["e1"]},
        {"id": "rand", "name": "Randomisation", "anchor_type": "randomization",
         "evidence_ids": ["e1"]},
    ])
    resolution = select_baseline_anchor(CanonicalSchedulePlan.model_validate(plan))

    assert resolution.anchor_id == "rand"


def test_a_single_enrolment_anchor_resolves_even_though_it_is_not_a_typed_origin():
    """One anchor means there is nothing to choose between."""
    plan = plan_with([
        {"id": "enrol", "name": "Enrollment", "anchor_type": "other",
         "source_label": "Day 1 (enrollment)", "evidence_ids": ["e1"]},
    ])
    resolution = select_baseline_anchor(CanonicalSchedulePlan.model_validate(plan))

    assert resolution.status == "RESOLVED"
    assert resolution.anchor_id == "enrol"


# --- ambiguity is refused, never guessed -------------------------------------

def test_two_equally_plausible_origins_are_unresolved_with_alternatives():
    """Doc s4: if several baselines are plausible, mark UNRESOLVED and show them."""
    plan = plan_with(
        [
            {"id": "r1", "name": "Randomisation (Part A)", "anchor_type": "randomization",
             "evidence_ids": ["e1"]},
            {"id": "r2", "name": "Randomisation (Part B)", "anchor_type": "randomization",
             "evidence_ids": ["e1"]},
        ],
        events=[],  # nothing counts from either, and neither prints a study day
    )
    resolution = select_baseline_anchor(CanonicalSchedulePlan.model_validate(plan))

    assert resolution.status == "UNRESOLVED"
    assert resolution.requires_review is True
    assert resolution.anchor_id is None, "an unresolved origin must carry no anchor"
    assert {item["id"] for item in resolution.alternatives} == {"r1", "r2"}


def test_an_ambiguous_origin_leaves_the_flat_projection_undated_and_says_why():
    plan = plan_with(
        [
            {"id": "r1", "name": "Randomisation A", "anchor_type": "randomization"},
            {"id": "r2", "name": "Randomisation B", "anchor_type": "randomization"},
        ],
        events=[{
            "id": "ev1", "name": "Day 8 Visit", "event_type": "site visit",
            "timing": {"kind": "offset", "offset": {"value": 8, "unit": "day"},
                       "source_label": "Day 8"},
            "activity_ids": [], "evidence_ids": ["e1"],
        }],
    )
    rows, warnings = project_canonical_plan(CanonicalSchedulePlan.model_validate(plan))

    assert rows[0]["day_offset"] is None, "an unresolved origin must not produce a date"
    assert any("Schedule origin (Day 0) could not be determined" in item
               for item in warnings)


def test_an_ambiguous_origin_blocks_the_canonical_draft():
    """The adapter must refuse to date a schedule nobody has anchored."""
    plan = plan_with(
        [
            {"id": "r1", "name": "Randomisation A", "anchor_type": "randomization",
             "evidence_ids": ["e1"]},
            {"id": "r2", "name": "Randomisation B", "anchor_type": "randomization",
             "evidence_ids": ["e1"]},
        ],
        events=[],
    )
    _schedule, issues = universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "SoA")])

    blocking = [item for item in issues if item.issue_code == "AMBIGUOUS_BASELINE"]
    assert blocking, "an ambiguous Day 0 must raise a blocking validation issue"
    assert blocking[0].blocking is True
    assert len(blocking[0].details["alternatives"]) == 2


# --- the two paths agree, which is the whole point ---------------------------

AGREEMENT_CASES = {
    "baseline_named_anchor": [
        {"id": "a1", "name": "Baseline", "anchor_type": "first_dose", "evidence_ids": ["e1"]},
        {"id": "a2", "name": "Last Dose", "anchor_type": "last_dose", "evidence_ids": ["e1"]},
    ],
    "first_dose": [
        {"id": "a1", "name": "First Dose", "anchor_type": "first_dose", "evidence_ids": ["e1"]},
        {"id": "a2", "name": "EOT", "anchor_type": "end_of_treatment", "evidence_ids": ["e1"]},
    ],
    "randomisation_after_consent": [
        {"id": "a1", "name": "Informed Consent", "anchor_type": "consent", "evidence_ids": ["e1"]},
        {"id": "a2", "name": "Randomisation", "anchor_type": "randomization", "evidence_ids": ["e1"]},
    ],
    "enrolment_only": [
        {"id": "a1", "name": "Enrollment", "anchor_type": "other", "evidence_ids": ["e1"]},
    ],
    "randomisation_and_first_dose": [
        {"id": "a1", "name": "Randomisation", "anchor_type": "randomization",
         "source_label": "Day 1", "evidence_ids": ["e1"]},
        {"id": "a2", "name": "First Dose", "anchor_type": "first_dose",
         "source_label": "Day 3", "evidence_ids": ["e1"]},
    ],
}


@pytest.mark.parametrize("case", sorted(AGREEMENT_CASES))
def test_legacy_and_canonical_resolve_the_same_day_zero(case):
    """Legacy projection Day 0 == canonical Day 0, for the same plan.

    Both call the one resolver, so this holds by construction; the test exists
    so that reintroducing a second, local rule fails loudly.
    """
    anchors = AGREEMENT_CASES[case]
    plan = plan_with(anchors)

    legacy_id = origin_legacy_id(plan)
    canonical_code = origin_canonical_code(plan)

    assert legacy_id is not None and canonical_code is not None
    chosen = next(item for item in anchors if item["id"] == legacy_id)
    expected_code = chosen["name"].upper().replace(" ", "_")

    assert canonical_code == expected_code, (
        f"{case}: flat projection chose {chosen['name']!r} but the canonical "
        f"adapter chose {canonical_code!r}")


def test_the_canonical_origin_records_how_it_was_chosen():
    """A reviewer must be able to see why this anchor is Day 0."""
    plan = plan_with([
        {"id": "a1", "name": "First Dose", "anchor_type": "first_dose", "evidence_ids": ["e1"]},
        {"id": "a2", "name": "Last Dose", "anchor_type": "last_dose", "evidence_ids": ["e1"]},
    ])
    schedule, _issues = universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "SoA")])
    origin = schedule_origin_anchor(schedule)

    assert origin is not None
    assert origin.derivation_rule["role"] == "SCHEDULE_ORIGIN"
    assert origin.derivation_rule["resolver"] == "schedule_schema.select_baseline_anchor"
    assert origin.derivation_rule["protocol_anchor_type"] == "first_dose", (
        "the protocol's own anchor type must survive; FIRST_DOSE is not BASELINE")
    assert origin.anchor_type == "FIRST_DOSE"


def test_ambiguous_baseline_blocks_approval_even_after_the_symptom_is_patched():
    """The block must be durable, not a one-time note the adapter attached.

    ``ScheduleValidator().validate()`` runs fresh on every validate/submit/
    approve call and replaces ``validation_issues`` each time. A blocking
    issue that exists ONLY in the adapter's one-time output would vanish the
    moment anyone revalidates - including a reviewer who "fixes" one event's
    unresolved timing by pointing it at ONE of the ambiguous anchors, without
    ever actually deciding which anchor the schedule's Day 0 is. This proves
    the block survives that exact patch-the-symptom path, because it is
    re-derived from ``Anchor.status`` every time, not remembered from import.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
    from app.db.repositories import ScheduleRepository
    from app.services.canonical_bridge import import_schedule_definitions
    from app.services.schedule_service import ScheduleReviewService

    import test_canonical_journey as journey

    plan = plan_with(
        [
            {"id": "r1", "name": "Randomisation A", "anchor_type": "randomization",
             "evidence_ids": ["e1"]},
            {"id": "r2", "name": "Randomisation B", "anchor_type": "randomization",
             "evidence_ids": ["e1"]},
        ],
        events=[{
            "id": "ev1", "name": "Day 8 Visit", "event_type": "site visit",
            "timing": {"kind": "offset", "offset": {"value": 8, "unit": "day"},
                       "source_label": "Day 8"},
            "activity_ids": ["act"], "evidence_ids": ["e1"],
        }],
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _trial, rows = import_schedule_definitions(
            session, organization_id=journey.uuid4(), actor_id=journey.REVIEWER,
            external_trial_id="t1", protocol_number="ABC", study_title="S",
            definitions=[{
                "id": "sd-1", "canonical_plan": plan,
                "evidence_facts": [evidence_fact("e1", "SoA")],
                "option_label": "Primary", "file_name": "p.pdf",
            }],
        )
        session.commit()
        row = rows[0]
        service = ScheduleReviewService(session)

        # Patch only the symptom: give the event an explicit anchor, without
        # ever resolving which of the two RANDOMIZATION anchors is Day 0.
        schedule = ScheduleRepository(session).get(row.schedule_version_id)
        event = schedule.events[0]
        event.timing = OffsetTiming(
            reference=AnchorReference(code="RANDOMISATION_A"),
            offset=TemporalAmount(value=8, unit="DAY"))
        service.correct_event(
            row.schedule_version_id, event, reviewer_id=journey.REVIEWER,
            reason="Picked an anchor so timing resolves")

        fresh = ScheduleValidator().validate(
            ScheduleRepository(session).get(row.schedule_version_id))
        assert any(item.issue_code == "AMBIGUOUS_BASELINE" for item in fresh), (
            "the ambiguity must survive being patched around")

        service.submit_for_review(row.schedule_version_id, actor_id=journey.REVIEWER)
        journey.review_every_field(session, row.schedule_version_id)
        with pytest.raises(ValueError, match="approval blocked"):
            service.approve(row.schedule_version_id, reviewer_id=journey.REVIEWER)


def test_a_plan_with_no_anchors_states_its_origin_explicitly():
    """The live product counts from the patient's baseline date; say so."""
    plan = plan_with([], events=[{
        "id": "ev1", "name": "Day 8 Visit", "event_type": "site visit",
        "timing": {"kind": "offset", "offset": {"value": 8, "unit": "day"},
                   "source_label": "Day 8"},
        "activity_ids": [], "evidence_ids": ["e1"],
    }])
    schedule, issues = universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "SoA")])
    origin = schedule_origin_anchor(schedule)

    assert origin is not None and origin.anchor_type == "BASELINE"
    assert origin.derivation_rule["protocol_anchor_type"] == "none"
    assert not [item for item in issues if item.issue_code == "AMBIGUOUS_BASELINE"]
