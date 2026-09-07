"""Conditional / triggered scheduling (MTB requirement doc 2).

Separation of concerns, per the requirement:

    the Conditional Engine decides WHAT becomes applicable,
    the Anchor Engine decides WHEN it happens.

This module is the first half. It reads the approved conditional definitions plus
the patient's condition states and produces a ``ConditionalPlan`` describing which
events are activated, cancelled, or held for manual review, and which repeating
blocks are paused or stopped. It never computes dates - the evaluator does that.

Safety rules encoded here:
  * A conditional event that has not been activated for this patient stays
    inactive. It gets no date, and therefore no reminder, due, or overdue status.
  * A condition that is merely PENDING_CONFIRMATION does not act. Clinically
    significant pathway changes require confirmation first.
  * Resolution stops further repeats but never deletes what already happened.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from .condition import TruthValue, evaluate_condition
from .models import (
    ApplicabilityRule,
    BlockState,
    ConditionalDefinition,
    ConditionState,
    PatientConditionStatus,
    UniversalSchedule,
)
from .timing import PositiveTemporalAmount, StrictModel

# Conditions in these states are driving the patient's schedule right now.
# A recurrence acts exactly as the first occurrence does: the protocol response
# to "ANC below 1000" does not change because it has happened before.
ACTING_STATES = {ConditionState.ACTIVE, ConditionState.RECURRED}
# Conditions in these states have acted at some point, so anchors they produced
# (a progression date, a discontinuation date) remain valid references.
DATED_STATES = {
    ConditionState.ACTIVE, ConditionState.RECURRED, ConditionState.RESOLVED,
}


class EventActivation(StrictModel):
    """Why a conditional event is active, and how it repeats."""

    event_code: str
    condition_code: str
    occurrence_date: date | datetime
    resolution_date: date | datetime | None = None
    repeat_interval: PositiveTemporalAmount | None = None
    repeat_until_resolved: bool = False


class BlockStateChange(StrictModel):
    """One dated transition of a repeating block, kept in protocol order.

    The current state alone is not enough for doc 3 s20: a block that was paused
    and later resumed needs the pause INTERVAL, because whether the cadence keeps
    its original grid or restarts depends on how long the patient was off it.
    """

    state: BlockState
    condition_code: str
    effective_date: date | datetime | None = None


class BlockDirective(StrictModel):
    block_code: str
    state: BlockState
    condition_code: str
    effective_date: date | datetime | None = None
    changes: list[BlockStateChange] = Field(default_factory=list)

    def paused_intervals(self) -> list[tuple[date | datetime, date | datetime | None]]:
        """Closed pause periods and, last, any pause still open (end ``None``)."""
        intervals: list[tuple[date | datetime, date | datetime | None]] = []
        pause_start: date | datetime | None = None
        for change in self.changes:
            if change.effective_date is None:
                continue
            if change.state == BlockState.PAUSED and pause_start is None:
                pause_start = change.effective_date
            elif change.state == BlockState.ACTIVE and pause_start is not None:
                intervals.append((pause_start, change.effective_date))
                pause_start = None
            elif change.state == BlockState.STOPPED:
                break
        if pause_start is not None:
            intervals.append((pause_start, None))
        return intervals

    def resumed_on(self) -> date | datetime | None:
        """The date the block last came back, or ``None`` if it never paused."""
        resumed: date | datetime | None = None
        seen_pause = False
        for change in self.changes:
            if change.state == BlockState.PAUSED:
                seen_pause = True
            elif change.state == BlockState.ACTIVE and seen_pause:
                resumed = change.effective_date
        return resumed


class ConfinementExtension(StrictModel):
    episode_code: str
    condition_code: str
    extension: PositiveTemporalAmount


class ConditionalPlan(StrictModel):
    """The conditional consequences that currently apply to one patient."""

    conditional_event_codes: set[str] = Field(default_factory=set)
    activations: dict[str, EventActivation] = Field(default_factory=dict)
    cancelled_event_codes: dict[str, str] = Field(default_factory=dict)
    manual_review_event_codes: dict[str, str] = Field(default_factory=dict)
    block_directives: dict[str, BlockDirective] = Field(default_factory=dict)
    confinement_extensions: list[ConfinementExtension] = Field(default_factory=list)
    condition_anchors: dict[str, date | datetime] = Field(default_factory=dict)

    def is_conditional(self, event_code: str) -> bool:
        return event_code in self.conditional_event_codes

    def is_active(self, event_code: str) -> bool:
        return event_code in self.activations

    def block_state(self, block_code: str) -> BlockState:
        directive = self.block_directives.get(block_code)
        return directive.state if directive else BlockState.ACTIVE


def _applies(rules: list[ApplicabilityRule], context: object) -> TruthValue:
    from .evaluator import _evaluate_applicability  # local import avoids a cycle

    return _evaluate_applicability(rules, context)


def _status(
    definition: ConditionalDefinition,
    conditions: dict[str, PatientConditionStatus],
) -> PatientConditionStatus:
    return conditions.get(
        definition.code,
        PatientConditionStatus(condition_code=definition.code),
    )


def _repeat_interval(parameters: dict[str, object]) -> PositiveTemporalAmount | None:
    interval = parameters.get("interval")
    if isinstance(interval, PositiveTemporalAmount):
        return interval
    if isinstance(interval, dict):
        return PositiveTemporalAmount.model_validate(interval)
    return None


def build_conditional_plan(schedule: UniversalSchedule, context: object) -> ConditionalPlan:
    """Resolve every conditional definition against this patient's condition states."""
    plan = ConditionalPlan()
    conditions: dict[str, PatientConditionStatus] = getattr(context, "conditions", {}) or {}

    for definition in schedule.conditional_definitions:
        # Every event a conditional definition can create or repeat is conditional,
        # regardless of whether this patient has triggered it. That is what keeps
        # untriggered pathways out of the normal dated schedule.
        for action in definition.actions:
            if action.action_type in {"ADD_EVENT", "REPEAT_EVENT"}:
                plan.conditional_event_codes.add(action.target_code)

        if _applies(definition.applicability, context) != TruthValue.TRUE:
            # Not this patient's arm/cohort: the condition is not even offered.
            continue

        status = _status(definition, conditions)
        if status.occurrence_date is not None and status.state in DATED_STATES:
            plan.condition_anchors[definition.code] = status.occurrence_date
        if status.state not in ACTING_STATES:
            continue

        assert status.occurrence_date is not None  # guaranteed by PatientConditionStatus
        for action in definition.actions:
            parameters = dict(action.parameters)
            if action.action_type in {"ADD_EVENT", "REPEAT_EVENT"}:
                plan.activations[action.target_code] = EventActivation(
                    event_code=action.target_code,
                    condition_code=definition.code,
                    occurrence_date=status.occurrence_date,
                    resolution_date=status.resolution_date,
                    repeat_interval=(
                        _repeat_interval(parameters)
                        if action.action_type == "REPEAT_EVENT" else None
                    ),
                    repeat_until_resolved=action.action_type == "REPEAT_EVENT",
                )
            elif action.action_type == "CANCEL_EVENT":
                plan.cancelled_event_codes[action.target_code] = definition.code
            elif action.action_type == "MANUAL_REVIEW":
                plan.manual_review_event_codes[action.target_code] = definition.code
            elif action.action_type in {"STOP_BLOCK", "PAUSE_BLOCK", "RESUME_BLOCK"}:
                state = {
                    "STOP_BLOCK": BlockState.STOPPED,
                    "PAUSE_BLOCK": BlockState.PAUSED,
                    "RESUME_BLOCK": BlockState.ACTIVE,
                }[action.action_type]
                existing = plan.block_directives.get(action.target_code)
                # A stop is terminal: a later pause or resume cannot revive a branch
                # the protocol has ended.
                if existing is not None and existing.state == BlockState.STOPPED:
                    continue
                change = BlockStateChange(
                    state=state, condition_code=definition.code,
                    effective_date=status.occurrence_date,
                )
                # Transitions are kept in protocol order, so a pause followed by a
                # resume stays legible as a pause interval rather than collapsing
                # into a bare "ACTIVE".
                history = list(existing.changes) if existing is not None else []
                history.append(change)
                history.sort(key=lambda item: (
                    item.effective_date is None,
                    _sort_key(item.effective_date),
                ))
                trimmed: list[BlockStateChange] = []
                for item in history:
                    trimmed.append(item)
                    if item.state == BlockState.STOPPED:
                        break
                plan.block_directives[action.target_code] = BlockDirective(
                    block_code=action.target_code, state=trimmed[-1].state,
                    condition_code=trimmed[-1].condition_code,
                    effective_date=trimmed[-1].effective_date,
                    changes=trimmed,
                )
            elif action.action_type == "EXTEND_CONFINEMENT":
                extension = _repeat_interval({"interval": parameters.get("duration")})
                if extension is not None:
                    plan.confinement_extensions.append(ConfinementExtension(
                        episode_code=action.target_code,
                        condition_code=definition.code,
                        extension=extension,
                    ))
    return plan


def _sort_key(value: date | datetime | None) -> datetime:
    """Order dates and datetimes together without inventing a time of day."""
    if value is None:
        return datetime.max
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.combine(value, datetime.min.time())


def condition_requires_attention(
    definition: ConditionalDefinition,
    conditions: dict[str, PatientConditionStatus],
) -> bool:
    """True when a dashboard should surface this condition for PI/CRC action."""
    status = _status(definition, conditions)
    return status.state in {
        ConditionState.ACTIVE, ConditionState.RECURRED,
        ConditionState.PENDING_CONFIRMATION,
    }


def resolution_is_due(
    definition: ConditionalDefinition,
    status: PatientConditionStatus,
    state: dict[str, object],
) -> TruthValue:
    """Evaluate the protocol resolution rule (for example 'ANC >= 1000').

    UNKNOWN means the site has not supplied the value yet, so the condition stays
    active and repeats continue - the engine never assumes recovery.
    """
    if definition.resolution_condition is None or status.state not in ACTING_STATES:
        return TruthValue.UNKNOWN
    return evaluate_condition(definition.resolution_condition, state)
