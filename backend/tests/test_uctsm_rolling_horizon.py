"""Rolling horizon and resume semantics (MTB requirement doc 3).

Doc 3 sections 2, 4, 7, 8, 19, 20 and 36. Two rules that are easy to get wrong in
opposite directions:

  * an open-ended protocol must not put hundreds of visits on a patient, but the
    number we choose to write down must never be presented as the protocol
    maximum - the rule keeps running and the system has to say so;
  * when a paused block resumes, the cadence either kept its original grid or
    restarts from the resume date. If the protocol did not say which, no date may
    be invented.
"""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, ConditionState, ConditionalAction, ConditionalDefinition,
    Event, Evidence, PatientConditionStatus, PatientContext, PatientEventStatus,
    RecurrenceRule, RecurrenceTermination, RepeatBlock, RollingHorizon,
    ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, PositiveTemporalAmount, ProtocolDayTiming, TimeUnit,
)

BASELINE = date(2026, 9, 1)
HORIZON = date(2029, 12, 31)


def cycle_event(code: str, *, termination: RecurrenceTermination) -> Event:
    """C1D1 repeating every 21 days - the doc 3 s2 q21d example."""
    return Event(
        code=code, protocol_label=code, display_name="Treatment Cycle Day 1",
        event_type="SITE_VISIT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=1),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            start_reference=AnchorReference(code="BASELINE"),
            termination=termination,
        ),
    )


def approved(
    events: list[Event],
    *,
    conditionals: list[ConditionalDefinition] | None = None,
    repeat_blocks: list[RepeatBlock] | None = None,
) -> UniversalSchedule:
    evidence = Evidence(evidence_type="SECTION", page_number=12, source_text="Protocol rule")
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
    for definition in conditionals or []:
        definition.evidence_refs = [evidence.id]
        claims.extend(
            ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="CONDITION", claim_entity_id=definition.id,
                confidence=0.9,
            )
            for claim_type in ("CONDITION", "CONDITIONAL_ACTION")
        )
    for block in repeat_blocks or []:
        block.evidence_refs = [evidence.id]
        claims.extend(
            ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="REPEAT_BLOCK", claim_entity_id=block.id, confidence=0.9,
            )
            for claim_type in ("REPEAT_BLOCK", "DEPENDENCY_MODE")
        )
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, conditional_definitions=conditionals or [],
        repeat_blocks=repeat_blocks or [], evidence=[evidence], claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": BASELINE},
    }
    values.update(changes)
    return PatientContext(**values)


def evaluate(schedule, ctx, *, rolling=None):
    return ScheduleEvaluator().evaluate(schedule, ctx, horizon=HORIZON, rolling=rolling)


def cycles(result, code="CYCLE"):
    return [item for item in result.events if item.event_code == code]


# --- doc 3 s7-s8: a bounded rolling set of upcoming occurrences -----------------

def test_an_open_ended_protocol_materializes_only_a_few_upcoming_cycles():
    """q21d with no stated end must not put 50 visits on the patient."""
    schedule = approved([cycle_event(
        "CYCLE", termination=RecurrenceTermination(type="HORIZON"))])
    result = evaluate(
        schedule, context(schedule),
        rolling=RollingHorizon(upcoming_limit=4, as_of=BASELINE),
    )

    upcoming = [
        item for item in cycles(result)
        if item.timing and item.timing.nominal_start > BASELINE
    ]
    assert len(upcoming) == 4
    # Unbounded, this run would have produced well over forty occurrences.
    assert len(cycles(result)) < 10


def test_the_materialization_cap_is_never_presented_as_the_protocol_maximum():
    """Doc 3 s2 and s36: the preview cap is not the protocol own limit."""
    schedule = approved([cycle_event(
        "CYCLE", termination=RecurrenceTermination(type="HORIZON"))])
    result = evaluate(
        schedule, context(schedule),
        rolling=RollingHorizon(upcoming_limit=3, as_of=BASELINE),
    )

    summary = next(item for item in result.repeat_summaries if item.event_code == "CYCLE")
    assert summary.continues is True
    assert summary.cadence == "every 21 days"
    assert "no stated maximum" in summary.termination
    assert summary.next_unmaterialized is not None


def test_history_is_kept_even_when_it_exceeds_the_upcoming_limit():
    """The cap bounds the FUTURE. Past cycles are protected history."""
    schedule = approved([cycle_event(
        "CYCLE", termination=RecurrenceTermination(type="HORIZON"))])
    much_later = date(2027, 3, 1)
    result = evaluate(
        schedule, context(schedule),
        rolling=RollingHorizon(upcoming_limit=2, as_of=much_later),
    )

    past = [
        item for item in cycles(result)
        if item.timing and item.timing.nominal_start <= much_later
    ]
    assert len(past) > 2
    assert all(item.status == PatientEventStatus.RESOLVED for item in past)


def test_a_protocol_stated_maximum_is_honoured_and_reported_as_finished():
    """Doc 3 s5-s6: a real maximum ends the series; nothing continues."""
    schedule = approved([cycle_event(
        "CYCLE", termination=RecurrenceTermination(type="COUNT", count=6))])
    result = evaluate(
        schedule, context(schedule),
        rolling=RollingHorizon(upcoming_limit=10, as_of=BASELINE),
    )

    assert len(cycles(result)) == 6
    assert result.repeat_summaries == []


def test_a_stated_maximum_larger_than_the_cap_still_reports_the_real_maximum():
    schedule = approved([cycle_event(
        "CYCLE", termination=RecurrenceTermination(type="COUNT", count=12))])
    result = evaluate(
        schedule, context(schedule),
        rolling=RollingHorizon(upcoming_limit=3, as_of=BASELINE),
    )

    summary = next(item for item in result.repeat_summaries if item.event_code == "CYCLE")
    assert summary.materialized_count < 12
    assert "maximum of 12" in summary.termination
    assert summary.continues is True


def test_no_rolling_policy_keeps_the_previous_behaviour():
    """Backward compatibility: callers that pass no policy see every occurrence."""
    schedule = approved([cycle_event(
        "CYCLE", termination=RecurrenceTermination(type="COUNT", count=8))])
    assert len(cycles(evaluate(schedule, context(schedule)))) == 8


# --- doc 3 s19-s20: what a resumed cadence does ---------------------------------

def toxicity() -> ComparisonCondition:
    return ComparisonCondition(
        operator="GTE", left=FieldOperand(field="toxicity_grade"),
        right=LiteralOperand(value=3),
    )


def recovered() -> ComparisonCondition:
    return ComparisonCondition(
        operator="LT", left=FieldOperand(field="toxicity_grade"),
        right=LiteralOperand(value=2),
    )


def paused_then_resumed(resume_mode: str):
    """A block held on 20 Sep and resumed 14 days later on 4 Oct."""
    block = RepeatBlock(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment",
        event_codes=["CYCLE"], dependency_mode="NOMINAL", resume_mode=resume_mode,
    )
    hold = ConditionalDefinition(
        code="TOX_HOLD", protocol_label="TOX_HOLD",
        display_name="Grade 3 toxicity hold", condition=toxicity(),
        actions=[ConditionalAction(
            action_type="PAUSE_BLOCK", target_code="TREATMENT", condition=toxicity())],
    )
    resume = ConditionalDefinition(
        code="TOX_RECOVERED", protocol_label="TOX_RECOVERED",
        display_name="Toxicity recovered", condition=recovered(),
        actions=[ConditionalAction(
            action_type="RESUME_BLOCK", target_code="TREATMENT", condition=recovered())],
    )
    schedule = approved(
        [cycle_event("CYCLE", termination=RecurrenceTermination(type="COUNT", count=8))],
        conditionals=[hold, resume], repeat_blocks=[block],
    )
    ctx = context(schedule, conditions={
        "TOX_HOLD": PatientConditionStatus(
            condition_code="TOX_HOLD", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 9, 20)),
        "TOX_RECOVERED": PatientConditionStatus(
            condition_code="TOX_RECOVERED", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 10, 4)),
    })
    return schedule, ctx


def test_a_nominal_resume_keeps_the_original_cadence_grid():
    schedule, ctx = paused_then_resumed("NOMINAL")
    after = [
        item for item in cycles(evaluate(schedule, ctx))
        if item.timing and item.timing.nominal_start > date(2026, 10, 4)
    ]

    assert after
    first = after[0]
    assert first.status == PatientEventStatus.RESOLVED
    # 1 Sep + 21n stays on its original grid: 13 Oct, not a shifted date.
    assert first.timing.nominal_start == date(2026, 10, 13)
    assert "original schedule" in first.explanation["reason"]


def test_an_actual_resume_shifts_the_cadence_by_the_time_off_treatment():
    schedule, ctx = paused_then_resumed("ACTUAL_RESUME")
    after = [
        item for item in cycles(evaluate(schedule, ctx))
        if item.timing and item.timing.nominal_start > date(2026, 10, 4)
    ]

    assert after
    first = after[0]
    assert first.status == PatientEventStatus.RESOLVED
    # 14 days of hold push the 13 Oct cycle to 27 Oct.
    assert first.timing.nominal_start == date(2026, 10, 27)
    assert first.explanation["resume"]["shifted_days"] == 14


def test_an_unclear_resume_rule_refuses_to_invent_a_date():
    """The protocol did not say. UNRESOLVED with a reason beats a plausible guess."""
    schedule, ctx = paused_then_resumed("UNCLEAR")
    after = [
        item for item in cycles(evaluate(schedule, ctx))
        if item.status == PatientEventStatus.UNRESOLVED
    ]

    assert after
    assert all(item.timing is None for item in after)
    assert "does not state" in after[0].explanation["reason"]


def test_occurrences_during_the_pause_are_reported_as_paused_not_missed():
    schedule, ctx = paused_then_resumed("NOMINAL")
    during = [
        item for item in cycles(evaluate(schedule, ctx))
        if item.timing and date(2026, 9, 20) < item.timing.nominal_start <= date(2026, 10, 4)
    ]

    assert during
    assert all(item.status == PatientEventStatus.PAUSED for item in during)
    assert all(item.explanation["block_state"]["state"] == "PAUSED" for item in during)


def test_cycles_before_the_pause_are_untouched():
    schedule, ctx = paused_then_resumed("ACTUAL_RESUME")
    before = [
        item for item in cycles(evaluate(schedule, ctx))
        if item.timing and item.timing.nominal_start <= date(2026, 9, 20)
    ]

    assert before
    assert all(item.status == PatientEventStatus.RESOLVED for item in before)
    assert all("resume" not in item.explanation for item in before)
