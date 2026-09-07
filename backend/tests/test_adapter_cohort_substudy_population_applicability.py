"""Cohort/substudy/population applicability through the adapter (§36 item 19).

The scorecard previously listed this as a gap needing "extraction-level
support". Reading the adapter shows that is not quite right: the extraction
schema's own ``branch_type`` field description already tells the model to use
free text like 'sub_study' for anything that is not an arm/cohort/population,
and ``universal_schedule_from_plan`` already has a fallback
(``GenericDimension`` + ``branch_dimension`` map) for exactly that free text -
the engine-level generic-dimension applicability match is already proven by
test_uctsm_generic_dimensions.py. What was never proven, end to end through
the adapter itself, is that a plan naming a cohort, a population, or a
free-text sub-study branch actually reaches a real ``ApplicabilityRule`` - not
a fabrication check, a closing-the-loop one.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402


def plan_with_branch(branch_type: str, branch_name: str) -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [{"id": "b1", "name": branch_name, "branch_type": branch_type,
                      "evidence_ids": ["e1"]}],
        "phases": [],
        "activities": [{"id": "act", "name": "Echocardiogram", "evidence_ids": ["e1"]}],
        "events": [{
            "id": "ev1", "name": "Cardiac Substudy Visit", "event_type": "site visit",
            "arm_id": "b1",
            "timing": {"kind": "offset", "anchor_id": "a1",
                       "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
            "activity_ids": ["act"], "evidence_ids": ["e1"],
        }],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


def test_a_cohort_branch_becomes_a_real_cohort_dimension_and_applicability_rule():
    schedule, _issues = build(plan_with_branch("cohort", "Dose Expansion Cohort A"))

    assert len(schedule.cohorts) == 1
    cohort_code = schedule.cohorts[0].code
    event = schedule.events[0]
    assert any(
        rule.dimension == "COHORT" and rule.values == [cohort_code]
        for rule in event.applicability
    )


def test_a_population_branch_becomes_a_real_population_dimension_and_applicability_rule():
    schedule, _issues = build(plan_with_branch("population", "Cardiac Risk Population"))

    assert len(schedule.populations) == 1
    population_code = schedule.populations[0].code
    event = schedule.events[0]
    assert any(
        rule.dimension == "POPULATION" and rule.values == [population_code]
        for rule in event.applicability
    )


def test_a_free_text_sub_study_branch_is_not_dropped_it_becomes_a_generic_dimension():
    """The extraction schema explicitly allows 'sub_study' as free text for a
    structure that is not an arm/cohort/population. This proves that text
    survives as a real, engine-evaluable dimension rather than being silently
    absorbed into one of the fixed categories or lost."""
    schedule, _issues = build(plan_with_branch("sub_study", "Cardiac Substudy"))

    assert not schedule.cohorts and not schedule.populations and not schedule.arms
    assert len(schedule.dimensions) == 1
    dimension = schedule.dimensions[0]
    assert dimension.dimension_type == "SUB_STUDY"
    event = schedule.events[0]
    assert any(
        rule.dimension == "SUB_STUDY" and rule.values == [dimension.code]
        for rule in event.applicability
    )


def test_a_condition_restricting_an_activity_to_a_cohort_also_resolves():
    """Doc s9: a condition can restrict an ACTIVITY to a cohort without the
    parent event itself being cohort-specific - the same branch-applicability
    path a condition already uses for arms."""
    plan = plan_with_branch("cohort", "Expansion Cohort B")
    plan["events"][0].pop("arm_id")
    plan["conditions"] = [{
        "id": "c1", "expression": "Only for Expansion Cohort B",
        "applies_to_ids": ["act"], "applies_to_branch_ids": ["b1"],
        "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    cohort_code = schedule.cohorts[0].code
    activity = schedule.events[0].activities[0]
    assert any(
        rule.dimension == "COHORT" and rule.values == [cohort_code]
        for rule in activity.applicability
    )
