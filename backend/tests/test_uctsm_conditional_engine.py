"""Conditional / triggered scheduling (MTB requirement doc 2).

These tests walk the requirement's own worked examples: the ANC <1000 repeat-CBC
rule, the disease-progression pathway switch, the Grade 3 toxicity hold, and the
QTcF repeat-ECG rule that must stay invisible until it actually triggers.
"""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.conditional import build_conditional_plan
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ApplicabilityRule, BlockState, ClaimEvidence, ConditionalAction,
    ConditionalDefinition, ConditionState, Event, Evidence, PatientConditionStatus,
    PatientContext, PatientEventStatus, RecurrenceRule, RecurrenceTermination,
    RepeatBlock, ScheduleMetadata, ScheduleStatus, StudyDimension, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, PositiveTemporalAmount, ProtocolDayTiming,
    TemporalAmount, TimeUnit,
)

HORIZON = date(2027, 6, 30)
BASELINE = date(2026, 9, 1)


def anc_below(value: int) -> ComparisonCondition:
    return ComparisonCondition(
        operator="LT", left=FieldOperand(field="anc"), right=LiteralOperand(value=value),
    )


def anc_at_least(value: int) -> ComparisonCondition:
    return ComparisonCondition(
        operator="GTE", left=FieldOperand(field="anc"), right=LiteralOperand(value=value),
    )


def day_event(code: str, day: int, **kwargs) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.replace("_", " ").title(),
        event_type="SITE_VISIT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=day),
        **kwargs,
    )


def anchored_event(code: str, anchor_code: str, days: int, **kwargs) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.replace("_", " ").title(),
        event_type="SITE_VISIT",
        timing=OffsetTiming(
            reference=AnchorReference(code=anchor_code),
            offset=TemporalAmount(value=days, unit=TimeUnit.DAY),
        ),
        **kwargs,
    )


def approved(
    events: list[Event],
    *,
    conditionals: list[ConditionalDefinition] | None = None,
    anchors: list[Anchor] | None = None,
    repeat_blocks: list[RepeatBlock] | None = None,
    arms: list[StudyDimension] | None = None,
) -> UniversalSchedule:
    evidence = Evidence(evidence_type="SECTION", page_number=41, source_text="Protocol rule")
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path, confidence=0.9,
            ))
        if event.recurrence is not None:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="RECURRENCE", claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path="recurrence", confidence=0.9,
            ))
        if event.applicability:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="APPLICABILITY", claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path="applicability", confidence=0.9,
            ))
    for definition in conditionals or []:
        definition.evidence_refs = [evidence.id]
        claims.extend(_conditional_claims(definition, evidence.id))
    for block in repeat_blocks or []:
        block.evidence_refs = [evidence.id]
        claims.extend(
            ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="REPEAT_BLOCK", claim_entity_id=block.id,
                confidence=0.9,
            )
            for claim_type in ("REPEAT_BLOCK", "DEPENDENCY_MODE")
        )
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE"),
                 *(anchors or [])],
        events=events, conditional_definitions=conditionals or [],
        repeat_blocks=repeat_blocks or [], arms=arms or [],
        evidence=[evidence], claim_evidence=claims,
    )


def _conditional_claims(
    definition: ConditionalDefinition, evidence_id,
) -> list[ClaimEvidence]:
    """Claim-level evidence the validator requires for a conditional requirement."""
    claim_types = ["CONDITION", "CONDITIONAL_ACTION"]
    if definition.resolution_condition is not None:
        claim_types.append("CONDITIONAL_RESOLUTION")
    return [
        ClaimEvidence(
            evidence_id=evidence_id, claim_type=claim_type,
            claim_entity_type="CONDITION", claim_entity_id=definition.id,
            confidence=0.9,
        )
        for claim_type in claim_types
    ]


def _with_evidence(
    schedule: UniversalSchedule, definition: ConditionalDefinition,
) -> ConditionalDefinition:
    definition.evidence_refs = [schedule.evidence[0].id]
    schedule.claim_evidence.extend(
        _conditional_claims(definition, schedule.evidence[0].id))
    return definition


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": BASELINE},
    }
    return PatientContext(**{**values, **changes})


def evaluate(schedule, ctx):
    return ScheduleEvaluator().evaluate(schedule, ctx, horizon=HORIZON)


def by_code(result) -> dict:
    output: dict[str, list] = {}
    for item in result.events:
        output.setdefault(item.event_code, []).append(item)
    return output


# --- doc 2 s23/s24: an untriggered conditional visit is inert -------------------

def repeat_cbc_schedule() -> UniversalSchedule:
    """'If ANC <1000, withhold treatment and repeat CBC weekly until ANC >=1000.'"""
    return approved(
        [day_event("C1D1", 1), day_event("REPEAT_CBC", 1)],
        conditionals=[ConditionalDefinition(
            code="ANC_LOW", protocol_label="ANC <1000/mm3",
            display_name="Neutrophil count below 1000/mm3",
            condition=anc_below(1000),
            resolution_condition=anc_at_least(1000),
            actions=[ConditionalAction(
                action_type="REPEAT_EVENT", target_code="REPEAT_CBC",
                condition=anc_below(1000),
                parameters={"interval": {"value": 7, "unit": "DAY"}},
            )],
        )],
    )


def test_untriggered_conditional_visit_gets_no_date_and_cannot_become_overdue():
    schedule = repeat_cbc_schedule()
    events = by_code(evaluate(schedule, context(schedule)))

    cbc = events["REPEAT_CBC"][0]
    assert cbc.status == PatientEventStatus.WAITING_FOR_CONDITION
    assert cbc.timing is None
    assert cbc.explanation["conditional"] is True
    # The ordinary schedule is untouched by an inactive conditional pathway.
    assert events["C1D1"][0].status == PatientEventStatus.RESOLVED


def test_pending_confirmation_does_not_change_the_schedule():
    """A detected but unconfirmed condition must not restructure the schedule."""
    schedule = repeat_cbc_schedule()
    ctx = context(schedule, conditions={"ANC_LOW": PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.PENDING_CONFIRMATION,
        occurrence_date=date(2026, 9, 10),
    )})
    assert by_code(evaluate(schedule, ctx))["REPEAT_CBC"][0].status == (
        PatientEventStatus.WAITING_FOR_CONDITION)


def test_active_condition_repeats_weekly_from_the_occurrence_date():
    schedule = repeat_cbc_schedule()
    ctx = context(schedule, conditions={"ANC_LOW": PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.ACTIVE,
        occurrence_date=date(2026, 9, 10),
    )})
    repeats = by_code(evaluate(schedule, ctx))["REPEAT_CBC"]

    assert all(item.status == PatientEventStatus.RESOLVED for item in repeats)
    assert [item.timing.nominal_start for item in repeats[:3]] == [
        date(2026, 9, 17), date(2026, 9, 24), date(2026, 10, 1),
    ]
    assert repeats[0].explanation["conditional_repeat"]["condition"] == "ANC_LOW"


def test_resolution_stops_further_repeats_without_deleting_earlier_ones():
    schedule = repeat_cbc_schedule()
    ctx = context(schedule, conditions={"ANC_LOW": PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.ACTIVE,
        occurrence_date=date(2026, 9, 10), resolution_date=date(2026, 9, 24),
    )})
    repeats = by_code(evaluate(schedule, ctx))["REPEAT_CBC"]

    # 17-Sep and 24-Sep were due; nothing is generated past resolution.
    assert [item.timing.nominal_start for item in repeats] == [
        date(2026, 9, 17), date(2026, 9, 24),
    ]


def test_resolved_condition_generates_no_new_repeat_visit():
    schedule = repeat_cbc_schedule()
    ctx = context(schedule, conditions={"ANC_LOW": PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.RESOLVED,
        occurrence_date=date(2026, 9, 10), resolution_date=date(2026, 9, 24),
    )})
    assert by_code(evaluate(schedule, ctx))["REPEAT_CBC"][0].status == (
        PatientEventStatus.WAITING_FOR_CONDITION)


# --- doc 2 s20: disease progression switches the schedule pathway --------------

def progression_schedule() -> UniversalSchedule:
    treatment = Event(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment cycle",
        event_type="SITE_VISIT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=1),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="HORIZON"),
        ),
    )
    return approved(
        [
            treatment,
            anchored_event("EOT", "PROGRESSION_DATE", 7),
            anchored_event("SURVIVAL_FOLLOWUP", "PROGRESSION_DATE", 84),
        ],
        anchors=[Anchor(
            code="PROGRESSION_DATE", display_name="Disease progression",
            anchor_type="CONDITION", source_condition_code="DISEASE_PROGRESSION",
        )],
        repeat_blocks=[RepeatBlock(
            code="TREATMENT_BLOCK", protocol_label="Treatment", display_name="Treatment",
            event_codes=["TREATMENT"], dependency_mode="NOMINAL",
        )],
        conditionals=[ConditionalDefinition(
            code="DISEASE_PROGRESSION", protocol_label="Disease progression confirmed",
            display_name="Disease progression confirmed",
            condition=ComparisonCondition(
                operator="EQUALS", left=FieldOperand(field="disease_status"),
                right=LiteralOperand(value="PROGRESSION"),
            ),
            actions=[
                ConditionalAction(
                    action_type="STOP_BLOCK", target_code="TREATMENT_BLOCK",
                    condition=ComparisonCondition(
                        operator="EQUALS", left=FieldOperand(field="disease_status"),
                        right=LiteralOperand(value="PROGRESSION")),
                ),
                ConditionalAction(
                    action_type="ADD_EVENT", target_code="EOT",
                    condition=ComparisonCondition(
                        operator="EQUALS", left=FieldOperand(field="disease_status"),
                        right=LiteralOperand(value="PROGRESSION")),
                ),
                ConditionalAction(
                    action_type="ADD_EVENT", target_code="SURVIVAL_FOLLOWUP",
                    condition=ComparisonCondition(
                        operator="EQUALS", left=FieldOperand(field="disease_status"),
                        right=LiteralOperand(value="PROGRESSION")),
                ),
            ],
        )],
    )


def test_before_progression_treatment_repeats_and_followup_stays_inactive():
    schedule = progression_schedule()
    events = by_code(evaluate(schedule, context(schedule)))

    assert len(events["TREATMENT"]) > 5
    assert all(item.status == PatientEventStatus.RESOLVED for item in events["TREATMENT"])
    for code in ("EOT", "SURVIVAL_FOLLOWUP"):
        assert events[code][0].status == PatientEventStatus.WAITING_FOR_CONDITION
        assert events[code][0].timing is None


def test_progression_stops_treatment_and_anchors_followup_on_the_progression_date():
    schedule = progression_schedule()
    progression = date(2026, 11, 14)
    ctx = context(schedule, conditions={"DISEASE_PROGRESSION": PatientConditionStatus(
        condition_code="DISEASE_PROGRESSION", state=ConditionState.ACTIVE,
        occurrence_date=progression,
    )})
    events = by_code(evaluate(schedule, ctx))

    # Cycles already delivered keep their dates; only future ones are cancelled.
    treatment = events["TREATMENT"]
    assert [item.status for item in treatment if item.timing.nominal_start <= progression] == (
        [PatientEventStatus.RESOLVED] * len(
            [i for i in treatment if i.timing.nominal_start <= progression]))
    future = [item for item in treatment if item.timing.nominal_start > progression]
    assert future and all(item.status == PatientEventStatus.CANCELLED for item in future)
    assert future[0].explanation["block_state"]["state"] == "STOPPED"

    # The next pathway is activated and dated from the progression anchor.
    assert events["EOT"][0].status == PatientEventStatus.RESOLVED
    assert events["EOT"][0].timing.nominal_start == date(2026, 11, 21)
    assert events["SURVIVAL_FOLLOWUP"][0].timing.nominal_start == date(2027, 2, 6)


# --- doc 2 s21 / doc 3 s19: pause is not stop ---------------------------------

def test_toxicity_hold_pauses_treatment_rather_than_ending_it():
    schedule = progression_schedule()
    schedule.conditional_definitions.append(_with_evidence(schedule, ConditionalDefinition(
        code="GRADE_3_TOXICITY", protocol_label="Grade >=3 toxicity",
        display_name="Grade 3 or higher toxicity",
        condition=ComparisonCondition(
            operator="GTE", left=FieldOperand(field="toxicity_grade"),
            right=LiteralOperand(value=3)),
        actions=[ConditionalAction(
            action_type="PAUSE_BLOCK", target_code="TREATMENT_BLOCK",
            condition=ComparisonCondition(
                operator="GTE", left=FieldOperand(field="toxicity_grade"),
                right=LiteralOperand(value=3)),
        )],
    )))
    hold = date(2026, 10, 15)
    ctx = context(schedule, conditions={"GRADE_3_TOXICITY": PatientConditionStatus(
        condition_code="GRADE_3_TOXICITY", state=ConditionState.ACTIVE, occurrence_date=hold,
    )})
    future = [item for item in by_code(evaluate(schedule, ctx))["TREATMENT"]
              if item.timing.nominal_start > hold]

    assert future and all(item.status == PatientEventStatus.PAUSED for item in future)
    # Paused, not cancelled: treatment can resume later.
    assert all(item.status != PatientEventStatus.CANCELLED for item in future)


def test_stop_is_terminal_and_a_later_pause_cannot_revive_the_branch():
    schedule = progression_schedule()
    schedule.conditional_definitions.append(_with_evidence(schedule, ConditionalDefinition(
        code="TOXICITY", protocol_label="Toxicity", display_name="Toxicity",
        condition=anc_below(500),
        actions=[ConditionalAction(
            action_type="RESUME_BLOCK", target_code="TREATMENT_BLOCK",
            condition=anc_below(500)),
        ],
    )))
    ctx = context(schedule, conditions={
        "DISEASE_PROGRESSION": PatientConditionStatus(
            condition_code="DISEASE_PROGRESSION", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 11, 14)),
        "TOXICITY": PatientConditionStatus(
            condition_code="TOXICITY", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 11, 20)),
    })
    plan = build_conditional_plan(schedule, ctx)
    assert plan.block_state("TREATMENT_BLOCK") == BlockState.STOPPED


# --- doc 8 s27: a conditional rule can itself be arm restricted ----------------

def test_conditional_rule_is_not_offered_outside_its_applicable_arm():
    schedule = repeat_cbc_schedule()
    schedule.arms = [
        StudyDimension(code="ARM_A", protocol_label="Arm A", display_name="Arm A"),
        StudyDimension(code="ARM_B", protocol_label="Arm B", display_name="Arm B"),
    ]
    schedule.conditional_definitions[0].applicability = [
        ApplicabilityRule(dimension="ARM", values=["ARM_B"]),
    ]
    active = {"ANC_LOW": PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.ACTIVE,
        occurrence_date=date(2026, 9, 10))}

    arm_a = build_conditional_plan(schedule, context(schedule, arm_code="ARM_A", conditions=active))
    arm_b = build_conditional_plan(schedule, context(schedule, arm_code="ARM_B", conditions=active))

    assert not arm_a.is_active("REPEAT_CBC")
    assert arm_b.is_active("REPEAT_CBC")
    # The event stays conditional for both, so neither patient gets an invented date.
    assert arm_a.is_conditional("REPEAT_CBC") and arm_b.is_conditional("REPEAT_CBC")


# --- doc 2 s22: some conditions cannot be made deterministic -------------------

def test_manual_review_condition_never_invents_an_assessment_date():
    schedule = approved(
        [day_event("C1D1", 1), day_event("EXTRA_ASSESSMENT", 1)],
        conditionals=[ConditionalDefinition(
            code="CLINICALLY_INDICATED", protocol_label="As clinically indicated",
            display_name="Additional assessment as clinically indicated",
            condition=ComparisonCondition(
                operator="EQUALS", left=FieldOperand(field="clinically_indicated"),
                right=LiteralOperand(value=True)),
            actions=[ConditionalAction(
                action_type="MANUAL_REVIEW", target_code="EXTRA_ASSESSMENT",
                condition=ComparisonCondition(
                    operator="EQUALS", left=FieldOperand(field="clinically_indicated"),
                    right=LiteralOperand(value=True)),
            )],
        )],
    )
    ctx = context(schedule, conditions={"CLINICALLY_INDICATED": PatientConditionStatus(
        condition_code="CLINICALLY_INDICATED", state=ConditionState.ACTIVE,
        occurrence_date=date(2026, 10, 1),
    )})
    assessment = by_code(evaluate(schedule, ctx))["EXTRA_ASSESSMENT"][0]

    assert assessment.status == PatientEventStatus.UNRESOLVED
    assert assessment.timing is None
    assert assessment.explanation["manual_review_for_condition"] == "CLINICALLY_INDICATED"


def test_condition_activation_is_patient_specific():
    """Patient 001 progressing must not touch patient 002's schedule."""
    schedule = progression_schedule()
    progressed = context(schedule, conditions={"DISEASE_PROGRESSION": PatientConditionStatus(
        condition_code="DISEASE_PROGRESSION", state=ConditionState.ACTIVE,
        occurrence_date=date(2026, 11, 14))})
    untouched = context(schedule)

    assert by_code(evaluate(schedule, progressed))["EOT"][0].status == PatientEventStatus.RESOLVED
    assert by_code(evaluate(schedule, untouched))["EOT"][0].status == (
        PatientEventStatus.WAITING_FOR_CONDITION)
