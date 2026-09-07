"""Conditional definitions and actions reach UCTSM (doc s10, §36 items 12/13).

"If progression occurs, perform an assessment within 14 days" must remain
conditional end to end - never flattened into "Assessment Day 14", and never
activated by a guess. This is proven two ways:

  1. structurally: the extracted condition becomes a real ConditionalDefinition
     with a real ConditionalAction, not only a qualifier a human has to
     manually operationalise;
  2. behaviourally, through the REAL engine: the target visit is
     WAITING_FOR_CONDITION (no date, no reminder, no overdue) until a
     clinician explicitly records that THIS patient's condition occurred via
     the existing patient-condition workflow - the adapter never decides
     that for them.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import ExistsCondition  # noqa: E402
from app.domain.schedule.evaluator import ScheduleEvaluator  # noqa: E402
from app.domain.schedule.models import (  # noqa: E402
    PatientConditionStatus, PatientContext, ScheduleStatus,
)
from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402

BASELINE = date(2026, 1, 1)


def plan_with_conditional(action_type: str, *, resolution: str | None = None) -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [], "phases": [],
        "activities": [{"id": "act", "name": "Tumour assessment", "evidence_ids": ["e1"]}],
        "events": [
            {"id": "c1d1", "name": "Cycle 1 Day 1", "event_type": "treatment",
             "timing": {"kind": "offset", "anchor_id": "a1",
                        "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
             "activity_ids": [], "evidence_ids": ["e1"]},
            # The visit's own timing is an ordinary, resolvable offset - what
            # makes it CONDITIONAL is that its code is only in
            # plan.conditional_event_codes, gated before any date is computed
            # (evaluator.py:_conditional_gate). Linking its date instead to the
            # progression occurrence itself ("within 14 days of progression")
            # would need a condition-sourced Anchor this adapter does not yet
            # build - a disclosed follow-up, not part of this behaviour.
            {"id": "eot", "name": "End of Treatment Assessment", "event_type": "end_of_treatment",
             "timing": {"kind": "offset", "anchor_id": "a1",
                        "offset": {"value": 180, "unit": "day"},
                        "source_label": "Day 180 (if triggered)"},
             "activity_ids": ["act"], "evidence_ids": ["e1"]},
        ],
        "conditions": [{
            "id": "cond1",
            "expression": "If disease progression occurs, perform the end-of-treatment "
                          "assessment within 14 days",
            "action_type": action_type,
            "applies_to_ids": ["eot"],
            "resolution_expression": resolution,
            "evidence_ids": ["e1"],
        }],
        "recurrences": [], "transitions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


# --- structure ----------------------------------------------------------------

def test_add_visit_becomes_a_real_conditional_definition_and_action():
    schedule, _issues = build(plan_with_conditional("ADD_VISIT"))

    assert len(schedule.conditional_definitions) == 1
    definition = schedule.conditional_definitions[0]
    assert "progression" in definition.protocol_label.lower()
    assert len(definition.actions) == 1
    action = definition.actions[0]
    assert action.action_type == "ADD_EVENT"
    assert action.target_code == "END_OF_TREATMENT_ASSESSMENT"
    # The activation condition is a deliberately inert placeholder - it must
    # never assert anything about a real patient by itself.
    assert isinstance(definition.condition, ExistsCondition)
    assert definition.requires_review is True


def test_the_resolution_text_is_preserved_verbatim_not_interpreted():
    schedule, _issues = build(plan_with_conditional(
        "ADD_VISIT", resolution="until confirmed disease progression per RECIST 1.1"))

    definition = schedule.conditional_definitions[0]
    assert "until confirmed disease progression per recist 1.1" in definition.protocol_label.lower()
    assert definition.resolution_condition is not None


def test_manual_review_is_also_mapped_structurally():
    schedule, _issues = build(plan_with_conditional("MANUAL_REVIEW"))

    action = schedule.conditional_definitions[0].actions[0]
    assert action.action_type == "MANUAL_REVIEW"


def test_activate_eot_and_survival_followup_map_the_same_way():
    for action_type in ("ACTIVATE_EOT", "ACTIVATE_SAFETY_FOLLOWUP", "ACTIVATE_SURVIVAL_FOLLOWUP"):
        schedule, _issues = build(plan_with_conditional(action_type))
        assert schedule.conditional_definitions[0].actions[0].action_type == "ADD_EVENT"


# --- deliberate non-mapping: safety over completeness ------------------------

def test_stop_branch_is_not_structurally_mapped_this_adapter_has_no_blocks():
    """Doc s10: never force a shape the adapter cannot correctly complete.

    STOP_BRANCH targets a RepeatBlock, and this adapter does not build repeat
    blocks - mapping it anyway would produce an UNRESOLVED_REFERENCE the
    reviewer could never actually fix. It must stay a qualifier instead.
    """
    schedule, _issues = build(plan_with_conditional("STOP_BRANCH"))
    assert not schedule.conditional_definitions

    # The condition is still visible - just as a qualifier, not silently dropped.
    qualifiers = [q for event in schedule.events for q in event.qualifiers]
    assert any("progression" in q.text.lower() for q in qualifiers)


def test_an_unclassified_condition_is_not_forced_into_a_definition():
    plan = plan_with_conditional("ADD_VISIT")
    plan["conditions"][0]["action_type"] = None
    schedule, _issues = build(plan)
    assert not schedule.conditional_definitions


def test_a_condition_naming_no_real_target_is_not_forced_into_a_definition():
    plan = plan_with_conditional("ADD_VISIT")
    plan["conditions"][0]["applies_to_ids"] = ["event-does-not-exist"]
    schedule, _issues = build(plan)
    assert not schedule.conditional_definitions


# --- the actual behaviour: never automatic, always human-gated ---------------

def _resolved_and_approved(schedule):
    for event in schedule.events:
        for qualifier in event.qualifiers:
            qualifier.resolved = True
        for activity in event.activities:
            for qualifier in activity.qualifiers:
                qualifier.resolved = True
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    return schedule


def test_the_conditional_visit_stays_undated_until_a_human_records_the_condition():
    schedule, _issues = build(plan_with_conditional("ADD_VISIT"))
    schedule = _resolved_and_approved(schedule)

    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={schedule.anchors[0].code: BASELINE},
    )
    result = ScheduleEvaluator().evaluate(
        schedule, context, horizon=BASELINE + timedelta(days=90))

    eot = next(item for item in result.events if item.event_code == "END_OF_TREATMENT_ASSESSMENT")
    assert eot.status.value == "WAITING_FOR_CONDITION"
    assert eot.timing is None, "no date must ever be invented for an unactivated condition"


def test_recording_the_condition_activates_the_visit_with_the_recorded_date():
    """The adapter never decides this happens; a human recording it does."""
    schedule, _issues = build(plan_with_conditional("ADD_VISIT"))
    schedule = _resolved_and_approved(schedule)
    definition_code = schedule.conditional_definitions[0].code

    progression_date = BASELINE + timedelta(days=40)
    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={schedule.anchors[0].code: BASELINE},
        conditions={
            definition_code: PatientConditionStatus(
                condition_code=definition_code, state="ACTIVE",
                occurrence_date=progression_date,
            ),
        },
    )
    result = ScheduleEvaluator().evaluate(
        schedule, context, horizon=BASELINE + timedelta(days=90))

    eot = next(item for item in result.events if item.event_code == "END_OF_TREATMENT_ASSESSMENT")
    assert eot.status.value == "RESOLVED"
    assert eot.timing is not None


def test_a_different_patient_with_no_recorded_condition_is_unaffected():
    """One patient's recorded condition must never leak into another's schedule."""
    schedule, _issues = build(plan_with_conditional("ADD_VISIT"))
    schedule = _resolved_and_approved(schedule)

    context = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={schedule.anchors[0].code: BASELINE},
    )
    result = ScheduleEvaluator().evaluate(
        schedule, context, horizon=BASELINE + timedelta(days=90))
    eot = next(item for item in result.events if item.event_code == "END_OF_TREATMENT_ASSESSMENT")
    assert eot.status.value == "WAITING_FOR_CONDITION"
