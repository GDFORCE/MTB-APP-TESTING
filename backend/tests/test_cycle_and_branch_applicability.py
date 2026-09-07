"""Cycle-specific and arm/cohort applicability survive the import (doc s8/s9).

"MRI every second cycle" must remain a recurrence/applicability restriction on
MRI, never a rule that gives every cycle an MRI. "MRI only in Arm B" must stay
structurally restricted to Arm B, never applied to every patient because the
restriction turned into a sentence nobody could act on.

Both are proven two ways: the STRUCTURE survives the import (an
ApplicabilityRule, a MembershipCondition), and the ENGINE actually enforces it
end to end - gating a real patient's activity by which cycle they are in and by
which arm they were assigned to.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import MembershipCondition  # noqa: E402
from app.domain.schedule.evaluator import ScheduleEvaluator  # noqa: E402
from app.domain.schedule.models import (  # noqa: E402
    PatientContext, ScheduleStatus,
)
from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402

BASELINE = date(2026, 1, 1)


def cyclic_plan(*, occurrence_numbers: list[int] | None = None,
                arm_branch_id: str | None = None) -> dict:
    """C1D1 repeats q21d; MRI is one of its activities, per the given rule."""
    mri = {"id": "act-mri", "name": "MRI", "evidence_ids": ["e1"]}
    labs = {"id": "act-labs", "name": "Labs", "evidence_ids": ["e1"]}
    conditions = []
    if occurrence_numbers is not None or arm_branch_id is not None:
        conditions.append({
            "id": "c1", "expression": "MRI every second cycle" if occurrence_numbers
                else "MRI only in Arm B",
            "applies_to_ids": ["act-mri"],
            "occurrence_numbers": occurrence_numbers or [],
            "applies_to_branch_ids": [arm_branch_id] if arm_branch_id else [],
            "evidence_ids": ["e1"],
        })
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [
            {"id": "arm-a", "name": "Arm A", "branch_type": "arm"},
            {"id": "arm-b", "name": "Arm B", "branch_type": "arm"},
        ],
        "phases": [],
        "activities": [mri, labs],
        "events": [{
            "id": "c1d1", "name": "Cycle 1 Day 1", "event_type": "treatment",
            "timing": {"kind": "offset", "anchor_id": "a1",
                       "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
            "activity_ids": ["act-mri", "act-labs"], "evidence_ids": ["e1"],
        }],
        "recurrences": [{
            "id": "r1", "event_ids": ["c1d1"],
            "frequency": {"value": 21, "unit": "day"}, "start_occurrence": 1,
            "evidence_ids": ["e1"],
        }],
        "conditions": conditions, "transitions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


def mri_activity(schedule):
    event = schedule.events[0]
    return next(item for item in event.activities if item.display_name == "MRI")


# --- structure survives the import -------------------------------------------

def test_cycle_restriction_becomes_a_real_evaluable_condition_not_a_note():
    schedule, _issues = build(cyclic_plan(occurrence_numbers=[2, 4, 6]))
    mri = mri_activity(schedule)

    assert len(mri.conditions) == 1
    condition = mri.conditions[0]
    assert isinstance(condition, MembershipCondition)
    assert condition.operator == "IN"
    assert condition.values == [2, 4, 6]
    assert condition.value.field == "occurrence_number"

    labs = next(item for item in schedule.events[0].activities if item.display_name == "Labs")
    assert not labs.conditions, "an activity with no stated rule must stay unrestricted"


def test_cycle_restriction_is_still_visible_to_a_reviewer_as_structured_detail():
    schedule, _issues = build(cyclic_plan(occurrence_numbers=[2, 4, 6]))
    mri = mri_activity(schedule)

    qualifiers = [item for item in mri.qualifiers if item.details.get("occurrence_numbers")]
    assert qualifiers, "the cycle list must be visible to a reviewer, not only to the engine"
    assert qualifiers[0].details["occurrence_numbers"] == [2, 4, 6]


def test_arm_restriction_on_an_activity_becomes_a_real_applicability_rule():
    schedule, _issues = build(cyclic_plan(arm_branch_id="arm-b"))
    mri = mri_activity(schedule)

    assert len(mri.applicability) == 1
    rule = mri.applicability[0]
    assert rule.dimension == "ARM"
    assert rule.values == ["ARM_B"]


def test_an_unmapped_branch_id_is_never_silently_dropped_or_applied_globally():
    """A condition naming a branch id this plan does not declare must not vanish."""
    plan = cyclic_plan(arm_branch_id="arm-does-not-exist")
    schedule, _issues = build(plan)
    mri = mri_activity(schedule)

    assert not mri.applicability, "an unresolved branch id must not become a rule"
    qualifiers = [item for item in mri.qualifiers if item.details.get("applies_to_branch_ids")]
    assert qualifiers and qualifiers[0].details["applies_to_branch_ids"] == ["arm-does-not-exist"]


def test_a_condition_can_restrict_a_shared_visit_to_one_arm():
    """Doc s9: a factorial visit that is not itself arm-tagged can still be
    restricted to an arm by a condition naming it."""
    plan = cyclic_plan(arm_branch_id="arm-b")
    plan["conditions"][0]["applies_to_ids"] = ["c1d1"]
    schedule, _issues = build(plan)

    event = schedule.events[0]
    assert any(rule.dimension == "ARM" and rule.values == ["ARM_B"]
               for rule in event.applicability)


# --- the engine actually enforces it ------------------------------------------

def _resolved_and_approved(schedule):
    """Mark every qualifier reviewed so the engine's integrity gate passes.

    Qualifier resolution (doc s6) is its own workflow, covered in
    test_uctsm_qualifier_resolution.py. These tests are about what the ENGINE
    does with a clean, approved schedule, so a fixture-level "already reviewed"
    is the right simplification rather than re-driving that workflow here.
    """
    for event in schedule.events:
        for qualifier in event.qualifiers:
            qualifier.resolved = True
        for activity in event.activities:
            for qualifier in activity.qualifiers:
                qualifier.resolved = True
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    return schedule


def _context(schedule, *, arm_code: str | None = None) -> PatientContext:
    return PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={schedule.anchors[0].code: BASELINE},
        arm_code=arm_code,
    )


def test_the_engine_gives_mri_only_on_the_listed_cycles():
    schedule, _issues = build(cyclic_plan(occurrence_numbers=[2, 4]))
    schedule = _resolved_and_approved(schedule)
    context = _context(schedule)

    result = ScheduleEvaluator().evaluate(
        schedule, context, horizon=BASELINE + timedelta(days=21 * 6))

    by_occurrence: dict[int, str] = {}
    for event in result.events:
        for activity in event.activities:
            if activity.display_name == "MRI":
                by_occurrence[event.occurrence_index] = activity.status.value

    # Cycle 1 = occurrence_index 0 -> occurrence_number 1 (not listed) -> NOT_APPLICABLE.
    # Cycle 2 = occurrence_index 1 -> occurrence_number 2 (listed)     -> RESOLVED.
    assert by_occurrence[0] == "NOT_APPLICABLE"
    assert by_occurrence[1] == "RESOLVED"
    assert by_occurrence[2] == "NOT_APPLICABLE"
    assert by_occurrence[3] == "RESOLVED"


def test_the_engine_never_gives_mri_to_every_cycle_by_mistake():
    """The specific failure the requirement calls out: MRI -> every cycle."""
    schedule, _issues = build(cyclic_plan(occurrence_numbers=[2, 4, 6]))
    schedule = _resolved_and_approved(schedule)
    context = _context(schedule)

    result = ScheduleEvaluator().evaluate(
        schedule, context, horizon=BASELINE + timedelta(days=21 * 8))

    mri_statuses = [
        activity.status.value
        for event in result.events for activity in event.activities
        if activity.display_name == "MRI"
    ]
    assert mri_statuses.count("RESOLVED") < len(mri_statuses), (
        "MRI resolved on every occurrence - the cycle restriction was not enforced")


def test_the_engine_restricts_an_arm_specific_activity_to_that_arm():
    schedule, _issues = build(cyclic_plan(arm_branch_id="arm-b"))
    schedule = _resolved_and_approved(schedule)

    arm_a = ScheduleEvaluator().evaluate(
        schedule, _context(schedule, arm_code="ARM_A"),
        horizon=BASELINE + timedelta(days=21))
    arm_b = ScheduleEvaluator().evaluate(
        schedule, _context(schedule, arm_code="ARM_B"),
        horizon=BASELINE + timedelta(days=21))

    def mri_status(result):
        for event in result.events:
            for activity in event.activities:
                if activity.display_name == "MRI":
                    return activity.status.value
        raise AssertionError("MRI activity not found in evaluation")

    assert mri_status(arm_a) == "NOT_APPLICABLE"
    assert mri_status(arm_b) == "RESOLVED"
