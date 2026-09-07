"""The Add Trial extraction must land in the canonical engine without inventing.

These cover the join added so that a trial created through the live Add Trial flow
produces a UCTSM draft schedule version instead of a Mongo-only template.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.models import QualifierCategory, UniversalSchedule
from app.domain.schedule.timing import (
    NominalWindowTiming, OffsetTiming, ProtocolDefinedTiming, RangeTiming, UnresolvedTiming,
)
from app.domain.schedule.validator import ScheduleValidator
from app.services.canonical_import import (
    extraction_result_from_plan, universal_schedule_from_plan,
)


def evidence_fact(evidence_id: str, claim: str) -> dict:
    return {
        "evidence_id": evidence_id, "page_evidence_id": "page-1", "claim": claim,
        "source_location": "Table 1, page 12", "source_quote": claim, "confidence": 0.9,
    }


def simple_plan() -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{
            "id": "anchor-baseline", "name": "First Dose", "anchor_type": "first_dose",
            "evidence_ids": ["e1"],
        }],
        "phases": [{"id": "phase-treat", "name": "Treatment", "phase_type": "treatment"}],
        "branches": [
            {"id": "arm-a", "name": "Arm A", "branch_type": "arm"},
            {"id": "seq-ab", "name": "Sequence AB", "branch_type": "sequence"},
        ],
        "activities": [
            {"id": "act-labs", "name": "Safety Labs", "evidence_ids": ["e1"]},
            {"id": "act-ecg", "name": "ECG", "conditional_text": "Footnote a: pre-dose only",
             "evidence_ids": ["e1"]},
        ],
        "events": [
            {
                "id": "event-screening", "name": "Screening", "event_type": "screening",
                "phase_id": "phase-treat",
                "timing": {"kind": "range", "anchor_id": "anchor-baseline",
                           "range_start": {"value": -28, "unit": "day"},
                           "range_end": {"value": -1, "unit": "day"},
                           "source_label": "Day -28 to -1"},
                "window": {"scope": "visit", "state": "not_stated"},
                "activity_ids": ["act-labs"], "evidence_ids": ["e1"],
            },
            {
                "id": "event-c1d1", "name": "C1D1", "event_type": "treatment",
                "arm_id": "arm-a",
                "timing": {"kind": "offset", "offset": {"value": 1, "unit": "day"},
                           "source_label": "Day 1"},
                "window": {"scope": "visit", "state": "stated",
                           "early": {"value": 1, "unit": "day"},
                           "late": {"value": 1, "unit": "day"}},
                "activity_ids": ["act-labs", "act-ecg"], "evidence_ids": ["e1"],
            },
            {
                "id": "event-unscheduled", "name": "Unscheduled Safety Visit",
                "event_type": "unscheduled",
                "timing": {"kind": "unresolved", "source_label": "As needed"},
                "window": {"scope": "visit", "state": "not_stated"},
                "evidence_ids": ["e1"],
            },
        ],
        "recurrences": [{
            "id": "rec-1", "event_ids": ["event-c1d1"],
            "frequency": {"value": 21, "unit": "day"}, "start_occurrence": 1,
            "evidence_ids": ["e1"],
        }],
        "transitions": [],
        "conditions": [],
        "conflicts": [],
    }


def build(plan=None, facts=None):
    return universal_schedule_from_plan(
        plan or simple_plan(), name="Primary",
        evidence_facts=facts if facts is not None else [evidence_fact("e1", "Day 1")],
    )


def test_events_activities_and_groups_are_translated_not_flattened():
    schedule, _ = build()
    assert isinstance(schedule, UniversalSchedule)
    assert [event.code for event in schedule.events] == [
        "SCREENING", "C1D1", "UNSCHEDULED_SAFETY_VISIT"]
    assert [arm.code for arm in schedule.arms] == ["ARM_A"]
    # A grouping the canonical model has no dedicated table for stays itself
    # instead of being forced into "arm".
    assert [(item.dimension_type, item.code) for item in schedule.dimensions] == [
        ("SEQUENCE", "SEQUENCE_AB")]
    assert [epoch.code for epoch in schedule.epochs] == ["TREATMENT"]
    c1d1 = schedule.events[1]
    assert [activity.display_name for activity in c1d1.activities] == ["Safety Labs", "ECG"]


def test_stated_window_becomes_nominal_window_and_unstated_stays_bare():
    schedule, _ = build()
    screening, c1d1 = schedule.events[0], schedule.events[1]
    assert isinstance(screening.timing, RangeTiming)
    assert isinstance(c1d1.timing, NominalWindowTiming)
    assert c1d1.timing.window.before.value == 1 and c1d1.timing.window.after.value == 1
    assert isinstance(c1d1.timing.nominal, OffsetTiming)
    assert c1d1.timing.nominal.offset.value == 1


def test_no_window_is_manufactured_when_the_protocol_did_not_state_one():
    plan = simple_plan()
    plan["events"][1]["window"] = {"scope": "visit", "state": "not_stated"}
    schedule, _ = build(plan)
    assert isinstance(schedule.events[1].timing, OffsetTiming)


def test_unclear_window_becomes_a_qualifier_a_reviewer_must_settle():
    plan = simple_plan()
    plan["events"][1]["window"] = {
        "scope": "visit", "state": "unclear", "source_label": "window unclear in table"}
    schedule, _ = build(plan)
    qualifiers = schedule.events[1].qualifiers
    assert [item.category for item in qualifiers] == [QualifierCategory.WINDOW]
    assert qualifiers[0].resolved is False


def test_unresolved_timing_is_kept_unresolved_rather_than_guessed():
    plan = simple_plan()
    plan["events"][1]["timing"] = {"kind": "unresolved", "source_label": "see footnote"}
    schedule, _ = build(plan)
    assert isinstance(schedule.events[1].timing, UnresolvedTiming)


def test_fractional_offsets_are_not_rounded_into_a_date():
    plan = simple_plan()
    plan["events"][1]["timing"] = {
        "kind": "offset", "offset": {"value": 1.5, "unit": "day"}, "source_label": "Day 1.5"}
    schedule, _ = build(plan)
    assert isinstance(schedule.events[1].timing, UnresolvedTiming)


def test_unscheduled_visit_is_on_demand_not_an_extraction_failure():
    schedule, _ = build()
    unscheduled = schedule.events[2]
    assert unscheduled.event_type == "UNSCHEDULED"
    assert unscheduled.activation == "ON_DEMAND"
    assert isinstance(unscheduled.timing, ProtocolDefinedTiming)


def cycling_plan() -> dict:
    """C1D1 on the baseline day itself, then every 21 days - the ordinary q21d."""
    plan = simple_plan()
    plan["events"][1]["timing"] = {
        "kind": "offset", "anchor_id": "anchor-baseline",
        "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"}
    return plan


def test_open_ended_repeat_uses_the_horizon_not_an_invented_cycle_count():
    schedule, _ = build(cycling_plan())
    recurrence = schedule.events[1].recurrence
    assert recurrence is not None
    assert recurrence.interval.value == 21
    assert recurrence.termination.type == "HORIZON"
    assert recurrence.termination.count is None


def test_stated_maximum_cycles_is_carried_across():
    plan = cycling_plan()
    plan["recurrences"][0]["end_occurrence"] = 35
    schedule, _ = build(plan)
    termination = schedule.events[1].recurrence.termination
    assert termination.type == "COUNT" and termination.count == 35


def test_a_range_cadence_recurrence_is_flagged_as_an_unresolved_repeat_qualifier():
    """The engine's interval field holds one whole-unit value, not a range.

    When extraction captured a stated range ('every 3-4 months' ->
    frequency.value_max=4), silently keeping only the lower bound as the
    engine's interval would look like a confirmed fixed cadence. It must
    instead surface as the same unresolved-until-reviewed qualifier every
    other footnote/condition uses, which also blocks approval.
    """
    plan = cycling_plan()
    plan["recurrences"][0]["frequency"] = {"value": 3, "unit": "month", "value_max": 4}
    plan["recurrences"][0]["source_label"] = "Bone marrow aspirate every 3-4 months"
    schedule, _ = build(plan)

    c1d1 = schedule.events[1]
    assert c1d1.recurrence is not None
    assert c1d1.recurrence.interval.value == 3
    repeat_qualifiers = [q for q in c1d1.qualifiers if q.category == QualifierCategory.REPEAT]
    assert len(repeat_qualifiers) == 1
    qualifier = repeat_qualifiers[0]
    assert qualifier.resolved is False
    assert "3-4 months" in qualifier.text
    assert "Bone marrow aspirate every 3-4 months" in qualifier.text
    assert qualifier.details == {"value": 3, "value_max": 4, "unit": "MONTH"}
    assert c1d1.requires_review is True


def test_a_fixed_cadence_recurrence_gets_no_repeat_qualifier():
    schedule, _ = build(cycling_plan())
    c1d1 = schedule.events[1]
    assert not [q for q in c1d1.qualifiers if q.category == QualifierCategory.REPEAT]


def test_a_repeat_starting_away_from_its_anchor_is_refused_not_mis_placed():
    """Placing it on the anchor anyway would move every cycle of a real patient."""
    schedule, issues = build()

    assert schedule.events[1].recurrence is None
    blocking = [item for item in issues if item.blocking]
    assert any("needs a reviewed start rule" in item.message for item in blocking)


def test_activity_footnote_is_kept_unresolved_and_targeted():
    schedule, _ = build()
    ecg = schedule.events[1].activities[1]
    assert [item.text for item in ecg.qualifiers] == ["Footnote a: pre-dose only"]
    assert ecg.qualifiers[0].resolved is False
    assert ecg.qualifiers[0].target_codes == ["ECG"]


def test_condition_attaches_to_its_target_visit():
    plan = simple_plan()
    plan["conditions"] = [{
        "id": "cond-1", "expression": "Only if measurable disease",
        "applies_to_ids": ["event-c1d1"], "evidence_ids": ["e1"],
    }]
    schedule, _ = build(plan)
    texts = [item.text for item in schedule.events[1].qualifiers]
    assert "Only if measurable disease" in texts


def test_condition_with_no_known_target_is_reported_not_dropped():
    plan = simple_plan()
    plan["conditions"] = [{
        "id": "cond-1", "expression": "Hold dose if ANC <1000", "applies_to_ids": ["nope"],
    }]
    _, issues = build(plan)
    assert any("Hold dose if ANC <1000" in issue.message for issue in issues)


def test_extraction_conflicts_become_blocking_validation_issues():
    plan = simple_plan()
    plan["conflicts"] = [{
        "id": "cf-1", "field_path": "events.C1D1.window",
        "description": "Table says +/-1 day, text says +/-3 days", "status": "unresolved",
    }]
    _, issues = build(plan)
    conflict = next(item for item in issues if item.issue_code == "CONFLICTING_EVIDENCE")
    assert conflict.blocking is True


def test_dependency_is_recorded_with_no_mode_so_a_reviewer_decides():
    plan = simple_plan()
    plan["transitions"] = [{
        "id": "t1", "from_event_id": "event-c1d1", "to_event_id": "event-screening",
        "relation": "after",
    }]
    schedule, _ = build(plan)
    screening = schedule.events[0]
    assert [item.source_event_code for item in screening.dependencies] == ["C1D1"]
    # Doc 4 s7: planned-vs-actual is a clinical decision, never a default.
    assert screening.dependency_mode is None


def test_evidence_claims_are_carried_so_the_validator_can_pass():
    schedule, issues = build()
    codes = {issue.issue_code for issue in ScheduleValidator().validate(schedule)}
    assert "MISSING_EVIDENCE" not in codes
    assert not any(issue.issue_code == "CONFLICTING_EVIDENCE" for issue in issues)


def test_missing_evidence_blocks_approval_rather_than_being_asserted():
    schedule, _ = build(facts=[])
    issues = ScheduleValidator().validate(schedule)
    assert any(issue.issue_code == "MISSING_EVIDENCE" and issue.blocking for issue in issues)


def test_result_is_packaged_for_the_existing_extraction_service():
    result = extraction_result_from_plan(
        simple_plan(), name="Primary", evidence_facts=[evidence_fact("e1", "Day 1")],
        source={"schedule_definition_id": "abc"},
    )
    assert result.schedule is not None
    assert result.extraction_trace[0]["node"] == "canonical_import"
    assert result.extraction_trace[0]["counts"]["events"] == 3
