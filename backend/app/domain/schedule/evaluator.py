from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from .condition import TruthValue, evaluate_condition
from .conditional import ConditionalPlan, build_conditional_plan
from .exceptions import ScheduleNotApprovedError, UnsupportedTimingError
from .models import (
    Activity,
    ApplicabilityRule,
    BlockState,
    ConfinementEpisodeDefinition,
    ConfinementStatus,
    EvaluatedConfinement,
    EvaluatedConfinementDay,
    DependencyMode,
    EvaluatedActivity,
    EvaluatedEvent,
    EvaluationResult,
    Event,
    PatientActivityStatus,
    PatientContext,
    PatientEventStatus,
    RepeatSummary,
    Requiredness,
    ResolvedTiming,
    RollingHorizon,
    ScheduleStatus,
    UniversalSchedule,
    UnscheduledOccurrence,
)
from .timing import (
    AbsoluteTiming,
    ActivityReference,
    AnchorReference,
    ApproximateTiming,
    CycleDayTiming,
    EventReference,
    NoLaterThanTiming,
    NominalWindowTiming,
    OffsetTiming,
    ProtocolDefinedTiming,
    ProtocolDayTiming,
    RangeTiming,
    TemporalAmount,
    TemporalReference,
    TimeUnit,
    TriggerNoLaterThan,
    TriggerOffset,
    TriggeredTiming,
    TriggerWithin,
    UnresolvedTiming,
    WithinTiming,
)
from .validator import ScheduleValidator, topological_event_codes


Temporal = date | datetime


def _confinement_integrity(
    planned_admission: "Temporal | None",
    planned_discharge: "Temporal | None",
    actual_admission: "Temporal | None",
    actual_discharge: "Temporal | None",
) -> list[str]:
    """Doc 6 s26 rule 1: discharge must not precede admission."""
    findings: list[str] = []
    for label, admission, discharge in (
        ("planned", planned_admission, planned_discharge),
        ("actual", actual_admission, actual_discharge),
    ):
        if admission is not None and discharge is not None and not _before_or_equal(
                admission, discharge):
            findings.append(
                f"{label} discharge precedes {label} admission; the episode dates "
                "need review before the stay can be scheduled"
            )
    return findings


def _flag_overlapping_episodes(
    episodes: list[EvaluatedConfinement],
) -> list[EvaluatedConfinement]:
    """Doc 6 s26 rules 2 and 3: a patient cannot be in two stays at once.

    Overlap is judged on the dates the patient is actually committed to, so an
    episode that is NOT_APPLICABLE or still waiting on its anchor is ignored.
    """
    datable = [
        item for item in episodes
        if item.status not in {
            ConfinementStatus.NOT_APPLICABLE, ConfinementStatus.CANCELLED,
            ConfinementStatus.WAITING_FOR_ANCHOR,
        }
        and (item.actual_admission or item.planned_admission) is not None
    ]

    def span(item: EvaluatedConfinement) -> tuple[Temporal, Temporal]:
        start = item.actual_admission or item.planned_admission
        end = item.actual_discharge or item.planned_discharge or start
        assert start is not None
        return start, end

    conflicts: dict[str, list[str]] = {}
    for index, left in enumerate(datable):
        left_start, left_end = span(left)
        for right in datable[index + 1:]:
            right_start, right_end = span(right)
            if _before_or_equal(left_start, right_end) and _before_or_equal(
                    right_start, left_end):
                conflicts.setdefault(left.episode_code, []).append(right.episode_code)
                conflicts.setdefault(right.episode_code, []).append(left.episode_code)
    if not conflicts:
        return episodes

    updated: list[EvaluatedConfinement] = []
    for item in episodes:
        overlapping = conflicts.get(item.episode_code)
        if not overlapping:
            updated.append(item)
            continue
        explanation = dict(item.explanation)
        explanation["overlapping_episodes"] = sorted(set(overlapping))
        explanation["reason"] = (
            "this stay overlaps another confinement episode; a patient cannot be "
            "admitted to two stays at the same time"
        )
        updated.append(item.model_copy(update={
            "status": ConfinementStatus.WAITING_FOR_ANCHOR,
            "explanation": explanation,
        }))
    return updated


def _evaluate_on_demand(
    event: Event,
    context: PatientContext,
    base: dict[str, object],
    explanation: dict[str, object],
) -> list[EvaluatedEvent]:
    """Doc 10 s13-s15: an unscheduled definition, plus whatever was created.

    The definition itself is always returned so the schedule shows it exists and
    a site can create one. It is AVAILABLE_ON_DEMAND, so nothing downstream can
    read it as due, overdue, or missed. Each occurrence a site actually created
    is returned as a dated, completed visit carrying the definition's activities.
    """
    created = [
        item for item in context.unscheduled_occurrences
        if item.event_code == event.code
    ]
    definition_explanation = dict(explanation)
    definition_explanation["reason"] = (
        "this visit is defined by the protocol but occurs only when clinically "
        "indicated; it becomes due when a site creates one"
    )
    definition_explanation["on_demand"] = True
    output = [EvaluatedEvent(
        **{**base, "explanation": definition_explanation},
        status=PatientEventStatus.AVAILABLE_ON_DEMAND,
    )]
    for index, occurrence in enumerate(
            sorted(created, key=lambda item: _sort_value(item.occurred_on))):
        occurrence_explanation = dict(explanation)
        occurrence_explanation["reason"] = (
            f"unscheduled visit created by the site: {occurrence.reason}"
        )
        occurrence_explanation["unscheduled"] = {
            "reason": occurrence.reason,
            "recorded_at": occurrence.recorded_at.isoformat(),
        }
        mode = occurrence.visit_mode or event.visit_mode
        output.append(EvaluatedEvent(
            **{
                **base, "explanation": occurrence_explanation,
                "visit_mode": mode,
            },
            occurrence_index=index,
            status=PatientEventStatus.RESOLVED,
            unscheduled_reason=occurrence.reason,
            timing=ResolvedTiming(
                nominal_start=occurrence.occurred_on,
                nominal_end=occurrence.occurred_on,
                precision="EXACT",
            ),
            activities=evaluate_activities(event, context, occurrence_index=index),
        ))
    return output


def _with_block_reason(
    item: EvaluatedEvent,
    block_code: str,
    status: PatientEventStatus,
    condition_code: str,
    reason: str,
    block_state: BlockState,
) -> EvaluatedEvent:
    explanation = dict(item.explanation)
    explanation["block_state"] = {
        "block": block_code, "state": block_state.value, "condition": condition_code,
    }
    explanation["reason"] = reason
    return item.model_copy(update={"status": status, "explanation": explanation})


def _containing_pause(
    value: Temporal,
    pauses: list[tuple[Temporal, Temporal | None]],
) -> tuple[Temporal, Temporal | None] | None:
    """The pause period this date falls in, treating an open pause as unbounded."""
    for start, end in pauses:
        if _before_or_equal(value, start):
            continue
        if end is None or not _before_or_equal(end, value):
            return start, end
    return None


def _pause_days_before(
    value: Temporal,
    pauses: list[tuple[Temporal, Temporal | None]],
) -> int:
    """Total days the block was paused before this occurrence."""
    total = 0
    for start, end in pauses:
        if end is None or not _before_or_equal(end, value):
            continue
        left = start.date() if isinstance(start, datetime) else start
        right = end.date() if isinstance(end, datetime) else end
        total += (right - left).days
    return total


def _shift(value: Temporal | None, days: int) -> Temporal | None:
    return None if value is None else value + timedelta(days=days)


def _apply_resume_mode(
    item: EvaluatedEvent,
    block: "RepeatBlock",
    pauses: list[tuple[Temporal, Temporal | None]],
    resumed: Temporal,
) -> EvaluatedEvent:
    """Doc 3 s20: what the cadence does after a pause ends."""
    explanation = dict(item.explanation)
    explanation["resume"] = {
        "block": block.code, "mode": block.resume_mode,
        "resumed_on": resumed.isoformat(),
    }
    if block.resume_mode == "NOMINAL":
        # The grid never moved; the paused occurrences are simply gone.
        explanation["reason"] = (
            f"repeating block {block.code} resumed on its original schedule; "
            "occurrences during the pause were not made up"
        )
        return item.model_copy(update={"explanation": explanation})

    if block.resume_mode == "ACTUAL_RESUME":
        offset = _pause_days_before(
            item.timing.nominal_start if item.timing else resumed, pauses)
        if offset == 0 or item.timing is None:
            explanation["reason"] = (
                f"repeating block {block.code} restarted from the actual resume date"
            )
            return item.model_copy(update={"explanation": explanation})
        timing = item.timing.model_copy(update={
            "nominal_start": _shift(item.timing.nominal_start, offset),
            "nominal_end": _shift(item.timing.nominal_end, offset),
            "earliest": _shift(item.timing.earliest, offset),
            "latest": _shift(item.timing.latest, offset),
        })
        explanation["resume"]["shifted_days"] = offset
        explanation["reason"] = (
            f"repeating block {block.code} restarted from the actual resume date, "
            f"moving this occurrence {offset} days later"
        )
        return item.model_copy(update={"timing": timing, "explanation": explanation})

    # MANUAL or UNCLEAR: the protocol did not say, so no date is invented.
    explanation["reason"] = (
        f"repeating block {block.code} resumed but the protocol does not state "
        "whether the cadence continues on its original schedule or restarts from "
        "the resume date; confirm before scheduling"
    )
    return item.model_copy(update={
        "status": PatientEventStatus.UNRESOLVED,
        "timing": None,
        "explanation": explanation,
    })


def _cadence_text(amount: object) -> str:
    value = getattr(amount, "value", None)
    unit = getattr(amount, "unit", None)
    if value is None or unit is None:
        return "on a repeating schedule"
    word = str(unit).lower()
    if value == 1:
        return f"every {word[:-1] if word.endswith('s') else word}"
    return f"every {value} {word if word.endswith('s') else word + 's'}"


def _termination_text(termination: object) -> str:
    """Plain wording for how a repeat ends, never implying a cap we invented."""
    kind = getattr(termination, "type", None)
    if kind == "COUNT":
        count = getattr(termination, "count", None)
        return f"a protocol maximum of {count} occurrences"
    if kind == "DATE":
        value = getattr(termination, "termination_date", None)
        return f"until {value.isoformat()}" if value else "until a stated date"
    if kind == "EVENT":
        return f"until {getattr(termination, 'event_code', 'a later event')}"
    if kind == "CONDITION":
        return "until the protocol stopping condition is met"
    return "no stated maximum; the protocol continues while the patient remains on study"


def _apply_rolling_horizon(
    events: list[EvaluatedEvent],
    schedule: UniversalSchedule,
    plan: ConditionalPlan,
    rolling: RollingHorizon | None,
    horizon: date,
) -> tuple[list[EvaluatedEvent], list[RepeatSummary]]:
    """Doc 3 s7-s8: keep a bounded rolling set of upcoming occurrences.

    The cap governs what is MATERIALIZED, never how long the protocol runs. Every
    repeating rule that has more to come is reported in a RepeatSummary, so the
    caller can say "continues" rather than showing the last kept row as the end
    (doc 3 s2, s4, s36).
    """
    blocks_by_event = {
        code: block.code
        for block in schedule.repeat_blocks for code in block.event_codes
    }
    repeating = {
        event.code: event for event in schedule.events
        if event.recurrence is not None
    }
    for code in plan.activations:
        if plan.activations[code].repeat_interval is not None and code in {
                event.code for event in schedule.events}:
            repeating.setdefault(code, next(
                event for event in schedule.events if event.code == code))

    as_of = (rolling.as_of if rolling and rolling.as_of else None) or date.today()
    limit = rolling.upcoming_limit if rolling else None

    summaries: list[RepeatSummary] = []
    kept: list[EvaluatedEvent] = []
    by_code: dict[str, list[EvaluatedEvent]] = {}
    for item in events:
        by_code.setdefault(item.event_code, []).append(item)

    for item_code, group in by_code.items():
        event = repeating.get(item_code)
        if event is None:
            kept.extend(group)
            continue

        activation = plan.activations.get(item_code)
        rule = event.recurrence
        future_dated = [
            item for item in group
            if item.timing is not None and item.timing.nominal_start is not None
            and not _before_or_equal(item.timing.nominal_start, as_of)
            and item.status == PatientEventStatus.RESOLVED
        ]
        dropped: list[EvaluatedEvent] = []
        if limit is not None and len(future_dated) > limit:
            dropped = future_dated[limit:]
        dropped_ids = {id(item) for item in dropped}
        kept.extend(item for item in group if id(item) not in dropped_ids)

        # "Continues" is true when the protocol has more to give: either we chose
        # not to write it down, or the date horizon - not the protocol - ended it.
        open_ended = (
            rule is not None and rule.termination.type == "HORIZON"
        ) or (
            activation is not None and activation.repeat_until_resolved
            and activation.resolution_date is None
        )
        last = group[-1].timing.nominal_start if group and group[-1].timing else None
        horizon_bounded = bool(
            open_ended and last is not None and not dropped
            and _before_or_equal(_as_date(last), horizon)
        )
        if not dropped and not horizon_bounded:
            continue
        cadence_source = (
            rule.interval if rule is not None
            else (activation.repeat_interval if activation else None)
        )
        summaries.append(RepeatSummary(
            event_code=item_code,
            block_code=blocks_by_event.get(item_code),
            cadence=_cadence_text(cadence_source),
            termination=(
                _termination_text(rule.termination) if rule is not None
                else "until the condition is marked resolved"
            ),
            materialized_count=len(group) - len(dropped),
            continues=True,
            next_unmaterialized=(
                dropped[0].timing.nominal_start if dropped and dropped[0].timing else None
            ),
        ))
    kept.sort(key=lambda item: (
        _sort_value(item.timing.nominal_start if item.timing else None),
        item.event_code, item.occurrence_index,
    ))
    return kept, summaries


def _as_date(value: Temporal) -> date:
    return value.date() if isinstance(value, datetime) else value


def _sort_value(value: Temporal | None) -> tuple[int, date]:
    if value is None:
        return (1, date.max)
    return (0, _as_date(value))


def _elapsed_day(clinical_day: int) -> int:
    """Convert a clinical day number to days elapsed from Day 1 (there is no Day 0)."""
    return clinical_day - 1 if clinical_day > 0 else clinical_day


def _first_nominal(results: "list[EvaluatedEvent] | None") -> Temporal | None:
    for item in results or []:
        if item.timing is not None and item.timing.nominal_start is not None:
            return item.timing.nominal_start
    return None


def _first_actual(context: PatientContext, event_code: str) -> Temporal | None:
    values = context.actual_event_values.get(event_code) or []
    return min(values) if values else None


def _before_or_equal(left: Temporal, right: Temporal) -> bool:
    """Compare values that may mix dates and datetimes."""
    if isinstance(left, datetime) and not isinstance(right, datetime):
        return left.date() <= right
    if isinstance(right, datetime) and not isinstance(left, datetime):
        return left <= right.date()
    return left <= right


def add_amount(value: Temporal, amount: TemporalAmount, multiplier: int = 1) -> Temporal:
    count = amount.value * multiplier
    if amount.unit == TimeUnit.MINUTE:
        return value + timedelta(minutes=count)
    if amount.unit == TimeUnit.HOUR:
        return value + timedelta(hours=count)
    if amount.unit == TimeUnit.DAY:
        return value + timedelta(days=count)
    if amount.unit == TimeUnit.WEEK:
        return value + timedelta(weeks=count)
    months = count if amount.unit == TimeUnit.MONTH else count * 12
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def activity_actual_key(event_code: str, occurrence_index: int, activity_code: str) -> str:
    """Composite key for an intra-day actual time inside one visit occurrence."""
    return f"{event_code}#{occurrence_index}#{activity_code}"


def _resolve_reference(
    reference: TemporalReference,
    context: PatientContext,
    *,
    activity_scope: tuple[str, int] | None = None,
) -> Temporal | None:
    if isinstance(reference, AnchorReference):
        return context.anchors.get(reference.code)
    if isinstance(reference, ActivityReference):
        if activity_scope is None:
            return None
        event_code, occurrence_index = activity_scope
        return context.activity_actuals.get(
            activity_actual_key(event_code, occurrence_index, reference.activity_code)
        )
    values = context.event_values.get(reference.event_code, [])
    if not values:
        return None
    if reference.occurrence == "FIRST":
        return min(values)
    if reference.occurrence == "LAST":
        return max(values)
    return values[-1]


def evaluate_timing(
    expression: object,
    context: PatientContext,
    *,
    activity_scope: tuple[str, int] | None = None,
) -> ResolvedTiming | None:
    if isinstance(expression, AbsoluteTiming):
        return ResolvedTiming(nominal_start=expression.value, nominal_end=expression.value, precision="EXACT")
    if isinstance(expression, OffsetTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        value = add_amount(reference, expression.offset)
        return ResolvedTiming(nominal_start=value, nominal_end=value, precision="EXACT")
    if isinstance(expression, ProtocolDayTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        elapsed_days = expression.day - 1 if expression.day > 0 else expression.day
        value = reference + timedelta(days=elapsed_days)
        return ResolvedTiming(
            nominal_start=value, nominal_end=value, precision="EXACT",
            constraints=[{"type": "CLINICAL_DAY", "protocol_day": expression.day}],
        )
    if isinstance(expression, RangeTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        return ResolvedTiming(
            nominal_start=add_amount(reference, expression.start),
            nominal_end=add_amount(reference, expression.end),
            earliest=add_amount(reference, expression.start),
            latest=add_amount(reference, expression.end),
            precision="CONSTRAINT",
        )
    if isinstance(expression, NominalWindowTiming):
        nominal = evaluate_timing(expression.nominal, context, activity_scope=activity_scope)
        if nominal is None or nominal.nominal_start is None:
            return None
        before = TemporalAmount(value=-expression.window.before.value, unit=expression.window.before.unit)
        after = TemporalAmount(value=expression.window.after.value, unit=expression.window.after.unit)
        return ResolvedTiming(
            nominal_start=nominal.nominal_start,
            nominal_end=nominal.nominal_end,
            earliest=add_amount(nominal.nominal_start, before),
            latest=add_amount(nominal.nominal_start, after),
            precision="EXACT",
        )
    if isinstance(expression, WithinTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        end = add_amount(reference, expression.duration, 1 if expression.direction == "AFTER" else -1)
        earliest, latest = sorted((reference, end))
        return ResolvedTiming(
            earliest=earliest, latest=latest, precision="CONSTRAINT",
            constraints=[{"type": "WITHIN", "direction": expression.direction}],
        )
    if isinstance(expression, NoLaterThanTiming):
        reference = _resolve_reference(expression.reference, context, activity_scope=activity_scope)
        if reference is None:
            return None
        return ResolvedTiming(
            latest=add_amount(reference, expression.duration), precision="CONSTRAINT",
            constraints=[{"type": "NO_LATER_THAN"}],
        )
    if isinstance(expression, TriggeredTiming):
        trigger_value = _resolve_reference(expression.trigger, context, activity_scope=activity_scope)
        if trigger_value is None:
            return None
        nested = expression.timing_after_trigger
        if isinstance(nested, TriggerOffset):
            result = add_amount(trigger_value, nested.offset)
            return ResolvedTiming(nominal_start=result, nominal_end=result, precision="EXACT")
        if isinstance(nested, TriggerWithin):
            end = add_amount(trigger_value, nested.duration, 1 if nested.direction == "AFTER" else -1)
            earliest, latest = sorted((trigger_value, end))
            return ResolvedTiming(earliest=earliest, latest=latest, precision="CONSTRAINT")
        if isinstance(nested, TriggerNoLaterThan):
            return ResolvedTiming(
                latest=add_amount(trigger_value, nested.duration), precision="CONSTRAINT",
                constraints=[{"type": "NO_LATER_THAN"}],
            )
    if isinstance(expression, CycleDayTiming):
        start = context.anchors.get(expression.cycle_reference)
        cycle_length = context.state.get("cycle_lengths", {}).get(expression.cycle_reference) if isinstance(context.state.get("cycle_lengths"), dict) else None
        if start is None or not isinstance(cycle_length, int) or cycle_length <= 0:
            return None
        cycle_number = expression.cycle.number
        if expression.cycle.type == "CURRENT":
            current = context.state.get("current_cycles", {})
            cycle_number = current.get(expression.cycle_reference) if isinstance(current, dict) else None
        if not isinstance(cycle_number, int) or cycle_number <= 0:
            return None
        value = start + timedelta(days=(cycle_number - 1) * cycle_length + expression.day - 1)
        return ResolvedTiming(nominal_start=value, nominal_end=value, precision="EXACT")
    if isinstance(expression, ApproximateTiming):
        raise UnsupportedTimingError("approximate timing requires an approved precision policy")
    if isinstance(expression, (ProtocolDefinedTiming, UnresolvedTiming)):
        raise UnsupportedTimingError("timing is not deterministically executable")
    raise UnsupportedTimingError(f"unsupported timing expression {type(expression).__name__}")


def _evaluate_applicability(rules: list[ApplicabilityRule], context: PatientContext) -> TruthValue:
    if not rules:
        return TruthValue.TRUE
    dimensions = {
        "ARM": context.arm_code,
        "COHORT": context.cohort_code,
        "POPULATION": context.population_code,
        **context.dimension_values,
    }
    results: list[TruthValue] = []
    for rule in rules:
        if rule.condition is not None:
            results.append(evaluate_condition(rule.condition, context.state))
            continue
        value = dimensions.get(rule.dimension)
        if rule.dimension in {"PATIENT_ATTRIBUTE", "CUSTOM"}:
            value = context.state.get(rule.field) if rule.field else None
        elif rule.dimension == "EPOCH":
            value = context.state.get("epoch")
        if value is None:
            results.append(TruthValue.UNKNOWN)
            continue
        values = value if isinstance(value, list) else [value]
        included = any(str(item) in rule.values for item in values)
        if rule.operator == "NOT_IN":
            included = not included
        results.append(TruthValue.TRUE if included else TruthValue.FALSE)
    if TruthValue.FALSE in results:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in results else TruthValue.TRUE


def _missing_activity_anchor(timing: object) -> str | None:
    """Name the intra-day activity a timing expression waits on, if any."""
    for reference in (
        getattr(timing, "reference", None),
        getattr(timing, "trigger", None),
        getattr(getattr(timing, "nominal", None), "reference", None),
    ):
        if isinstance(reference, ActivityReference):
            return reference.activity_code
    return None


def evaluate_activities(
    event: Event,
    context: PatientContext,
    *,
    occurrence_index: int,
) -> list[EvaluatedActivity]:
    """Resolve one visit occurrence's activities.

    Activity times resolve progressively: an activity anchored on another activity
    (dose, infusion end, previous sample) stays WAITING_FOR_ANCHOR with a named
    reason until that activity's ACTUAL time is recorded. No time is ever invented,
    and a recorded actual time is never overwritten by a recalculated planned time.
    """
    scope = (event.code, occurrence_index)
    # Doc s8: "MRI every second cycle" is an activity CONDITION referencing which
    # occurrence this is, not a different recurrence rule and not a second copy
    # of the event. occurrence_number is 1-based (cycle 1, cycle 2, ...) to match
    # how a protocol numbers cycles; it is visible only to the condition
    # evaluated for THIS occurrence's activities and is never written back onto
    # the shared patient context, so it cannot leak into another occurrence's
    # evaluation or into applicability/timing, which do not use it.
    condition_state = {**context.state, "occurrence_number": occurrence_index + 1}
    results: list[EvaluatedActivity] = []
    for activity in event.activities:
        key = (
            activity_actual_key(event.code, occurrence_index, activity.code)
            if activity.code else None
        )
        actual = context.activity_actuals.get(key) if key else None
        recorded_status = context.activity_statuses.get(key) if key else None
        base: dict[str, object] = {
            "activity_definition_id": activity.id,
            "activity_code": activity.code,
            "display_name": activity.display_name,
            "requiredness": activity.requiredness,
            "sequence_number": activity.sequence_number,
        }
        applicability = _evaluate_applicability(activity.applicability, context)
        condition = (
            TruthValue.TRUE if not activity.conditions
            else _combine([evaluate_condition(item, condition_state) for item in activity.conditions])
        )
        base["applicability_result"] = applicability.value
        base["condition_result"] = condition.value
        if activity.requiredness == Requiredness.NOT_APPLICABLE or applicability == TruthValue.FALSE:
            results.append(EvaluatedActivity(status=PatientActivityStatus.NOT_APPLICABLE, **base))
            continue
        if recorded_status == "NOT_DONE":
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.NOT_DONE, actual_time=actual, **base))
            continue
        explanation: dict[str, object] = {
            "evidence_refs": [str(item) for item in activity.evidence_refs],
        }
        if activity.timing is not None:
            explanation["timing"] = activity.timing.model_dump(mode="json")
        timing: ResolvedTiming | None = None
        if activity.timing is not None:
            try:
                timing = evaluate_timing(activity.timing, context, activity_scope=scope)
            except UnsupportedTimingError as error:
                explanation["reason"] = str(error)
                results.append(EvaluatedActivity(
                    status=PatientActivityStatus.UNRESOLVED, actual_time=actual,
                    explanation=explanation, **base,
                ))
                continue
        if actual is not None:
            explanation["result"] = timing.model_dump(mode="json") if timing else None
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.COMPLETED, timing=timing,
                actual_time=actual, explanation=explanation, **base,
            ))
            continue
        if condition == TruthValue.FALSE:
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.NOT_APPLICABLE, explanation=explanation, **base))
            continue
        if applicability == TruthValue.UNKNOWN or condition == TruthValue.UNKNOWN:
            explanation["reason"] = "applicability or condition is not yet known"
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.WAITING_FOR_CONDITION,
                explanation=explanation, **base,
            ))
            continue
        if activity.timing is not None and timing is None:
            waiting_on = _missing_activity_anchor(activity.timing)
            explanation["reason"] = (
                f"waiting for the actual time of {waiting_on}" if waiting_on
                else "required anchor or event reference is not available"
            )
            if waiting_on:
                explanation["waiting_for_activity"] = waiting_on
            results.append(EvaluatedActivity(
                status=PatientActivityStatus.WAITING_FOR_ANCHOR,
                explanation=explanation, **base,
            ))
            continue
        if timing is not None:
            explanation["result"] = timing.model_dump(mode="json")
        results.append(EvaluatedActivity(
            status=PatientActivityStatus.RESOLVED, timing=timing,
            explanation=explanation, **base,
        ))
    return sorted(
        results,
        key=lambda item: (item.sequence_number is None, item.sequence_number or 0),
    )


def _combine(values: list[TruthValue]) -> TruthValue:
    if TruthValue.FALSE in values:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in values else TruthValue.TRUE


def _evaluate_conditions(event: Event, context: PatientContext) -> TruthValue:
    if not event.conditions:
        return TruthValue.TRUE
    results = [evaluate_condition(condition, context.state) for condition in event.conditions]
    if TruthValue.FALSE in results:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in results else TruthValue.TRUE


class ScheduleEvaluator:
    version = "uctsm-evaluator.v1"

    def evaluate(
        self,
        schedule: UniversalSchedule,
        context: PatientContext,
        *,
        horizon: date,
        rolling: RollingHorizon | None = None,
    ) -> EvaluationResult:
        if schedule.schedule_metadata.status != ScheduleStatus.APPROVED:
            raise ScheduleNotApprovedError("patient schedules require an approved schedule version")
        if context.schedule_version_id != schedule.schedule_version_id:
            raise ValueError("patient context is pinned to a different schedule version")
        blocking = ScheduleValidator.blocking(ScheduleValidator().validate(schedule))
        if blocking:
            raise ValueError("approved schedule failed integrity validation")

        events_by_code = {event.code: event for event in schedule.events}
        evaluated: list[EvaluatedEvent] = []
        results_by_code: dict[str, list[EvaluatedEvent]] = {}
        working_context = context.model_copy(deep=True)

        # WHAT becomes applicable is decided first; WHEN it happens is decided below.
        plan = build_conditional_plan(schedule, working_context)
        for anchor in schedule.anchors:
            if anchor.source_condition_code is None:
                continue
            value = plan.condition_anchors.get(anchor.source_condition_code)
            if value is not None:
                working_context.anchors[anchor.code] = value
        blocks_by_event = {
            code: block
            for block in schedule.repeat_blocks for code in block.event_codes
        }

        for code in topological_event_codes(schedule):
            event = events_by_code[code]
            event_results = self._evaluate_event(
                event, working_context, results_by_code, horizon, plan=plan,
            )
            event_results = self._apply_block_state(
                event_results, plan, blocks_by_event.get(code))
            # Dependent events below see the FULL series; only what is written
            # down is trimmed, and that happens once at the end.
            evaluated.extend(event_results)
            results_by_code[code] = event_results
            dates = [item.timing.nominal_start for item in event_results if item.timing and item.timing.nominal_start]
            if dates:
                working_context.event_values[code] = dates
                for anchor in schedule.anchors:
                    if anchor.source_event_code != code:
                        continue
                    selection = str((anchor.derivation_rule or {}).get("selection", "LAST")).upper()
                    working_context.anchors[anchor.code] = min(dates) if selection == "FIRST" else max(dates)
        evaluated, repeat_summaries = _apply_rolling_horizon(
            evaluated, schedule, plan, rolling, horizon)
        return EvaluationResult(
            repeat_summaries=repeat_summaries,
            schedule_version_id=schedule.schedule_version_id,
            patient_id=context.patient_id,
            evaluator_version=self.version,
            input_snapshot=context.model_dump(mode="json"),
            events=evaluated,
            confinements=self._evaluate_confinements(
                schedule, working_context, results_by_code, plan),
        )

    def _evaluate_confinements(
        self,
        schedule: UniversalSchedule,
        context: PatientContext,
        results: dict[str, list[EvaluatedEvent]],
        plan: ConditionalPlan,
    ) -> list[EvaluatedConfinement]:
        """Resolve continuous inpatient stays as one parent episode each.

        A protocol that lists Day -1 through Day 3 while the patient stays admitted
        is ONE episode with four study days, not four hospital visits. Admission,
        dosing, and discharge stay distinct events so the dosing anchor is never
        replaced by the admission time.
        """
        output: list[EvaluatedConfinement] = []
        for definition in schedule.confinement_episodes:
            output.append(self._evaluate_confinement(definition, context, results, plan))
        return _flag_overlapping_episodes(output)

    def _evaluate_confinement(
        self,
        definition: ConfinementEpisodeDefinition,
        context: PatientContext,
        results: dict[str, list[EvaluatedEvent]],
        plan: ConditionalPlan,
    ) -> EvaluatedConfinement:
        base = {
            "episode_definition_id": definition.id,
            "episode_code": definition.code,
            "display_name": definition.display_name,
        }
        if _evaluate_applicability(definition.applicability, context) != TruthValue.TRUE:
            return EvaluatedConfinement(status=ConfinementStatus.NOT_APPLICABLE, **base)
        conditions = [evaluate_condition(item, context.state) for item in definition.conditions]
        if TruthValue.FALSE in conditions:
            return EvaluatedConfinement(status=ConfinementStatus.NOT_APPLICABLE, **base)

        planned_admission = _first_nominal(results.get(definition.admission_event_code))
        planned_discharge = _first_nominal(results.get(definition.discharge_event_code))
        actual_admission = _first_actual(context, definition.admission_event_code)
        actual_discharge = _first_actual(context, definition.discharge_event_code)

        # A conditional extension moves the expected discharge; it never creates a
        # second episode and never closes the current one.
        extended_by: list[str] = []
        for extension in plan.confinement_extensions:
            if extension.episode_code != definition.code or planned_discharge is None:
                continue
            planned_discharge = add_amount(planned_discharge, extension.extension)
            extended_by.append(extension.condition_code)

        explanation: dict[str, object] = {
            "admission_event": definition.admission_event_code,
            "discharge_event": definition.discharge_event_code,
            "dose_events": list(definition.dose_event_codes),
            "evidence_refs": [str(item) for item in definition.evidence_refs],
        }
        if actual_discharge is not None:
            status = ConfinementStatus.DISCHARGED
        elif actual_admission is not None:
            # The patient stays confined until an actual discharge is recorded, even
            # if the planned discharge date has already passed.
            status = ConfinementStatus.EXTENDED if extended_by else ConfinementStatus.IN_CONFINEMENT
        elif planned_admission is None:
            status = ConfinementStatus.WAITING_FOR_ANCHOR
            explanation["reason"] = (
                f"admission event {definition.admission_event_code} has no resolved date")
        else:
            status = ConfinementStatus.UPCOMING

        day_origin = actual_admission or planned_admission
        ordered = sorted(definition.days, key=lambda item: item.relative_day)
        # Clinical day numbering has no Day 0, so Day -1 is one day before Day 1.
        first_elapsed = _elapsed_day(ordered[0].relative_day) if ordered else 0
        days = []
        for day in ordered:
            value = None
            if day_origin is not None:
                offset = _elapsed_day(day.relative_day) - first_elapsed
                value = add_amount(
                    day_origin, TemporalAmount(value=offset, unit=TimeUnit.DAY))
            days.append(EvaluatedConfinementDay(
                day_label=day.day_label, relative_day=day.relative_day,
                scheduled_date=value, activity_codes=list(day.activity_codes),
            ))
        # Doc 6 s26 rule 1: an impossible stay is reported, never silently accepted.
        integrity = _confinement_integrity(
            planned_admission, planned_discharge, actual_admission, actual_discharge)
        if integrity:
            explanation["integrity_findings"] = integrity
            status = ConfinementStatus.WAITING_FOR_ANCHOR
            explanation["reason"] = integrity[0]
        return EvaluatedConfinement(
            status=status, planned_admission=planned_admission,
            actual_admission=actual_admission, planned_discharge=planned_discharge,
            actual_discharge=actual_discharge, extended_by=extended_by,
            days=days, explanation=explanation, **base,
        )

    def _conditional_gate(
        self,
        event: Event,
        plan: ConditionalPlan,
        base: dict[str, object],
        explanation: dict[str, object],
    ) -> list[EvaluatedEvent] | None:
        """Apply conditional consequences before any date is calculated.

        An untriggered conditional visit must never receive a date, because a dated
        visit becomes due, then overdue, and generates reminders for something the
        protocol may never require of this patient.
        """
        condition_code = plan.cancelled_event_codes.get(event.code)
        if condition_code is not None:
            explanation["reason"] = f"cancelled by condition {condition_code}"
            explanation["cancelled_by_condition"] = condition_code
            return [EvaluatedEvent(status=PatientEventStatus.CANCELLED, **base)]
        condition_code = plan.manual_review_event_codes.get(event.code)
        if condition_code is not None:
            explanation["reason"] = (
                f"condition {condition_code} requires manual review before scheduling")
            explanation["manual_review_for_condition"] = condition_code
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        if plan.is_conditional(event.code) and not plan.is_active(event.code):
            explanation["reason"] = "protocol condition has not occurred for this patient"
            explanation["conditional"] = True
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_CONDITION, **base)]
        return None

    def _expand_conditional_repeats(
        self,
        event: Event,
        plan: ConditionalPlan,
        horizon: date,
        base: dict[str, object],
    ) -> list[EvaluatedEvent]:
        """Generate 'repeat every N days until resolved' occurrences (doc 2 s17).

        Generation stops at the recorded resolution date, so once the PI/CRC marks
        the condition resolved no further repeat visit is produced. While the
        condition remains active, generation is bounded by the evaluation horizon.
        """
        activation = plan.activations[event.code]
        interval = activation.repeat_interval
        current: Temporal = activation.occurrence_date
        stop: Temporal = horizon
        if isinstance(current, datetime) and isinstance(stop, date) and not isinstance(stop, datetime):
            stop = datetime.combine(stop, datetime.max.time(), tzinfo=current.tzinfo)
        resolution = activation.resolution_date
        if resolution is not None:
            if isinstance(current, datetime) and not isinstance(resolution, datetime):
                resolution = datetime.combine(resolution, datetime.max.time(), tzinfo=current.tzinfo)
            elif isinstance(resolution, datetime) and not isinstance(current, datetime):
                resolution = resolution.date()
            stop = min(stop, resolution)
        output: list[EvaluatedEvent] = []
        index = 0
        current = add_amount(current, interval)
        while current <= stop and index < 1000:
            timing = ResolvedTiming(
                nominal_start=current, nominal_end=current, precision="EXACT")
            explanation = dict(base["explanation"])
            explanation["conditional_repeat"] = {
                "condition": activation.condition_code,
                "index": index,
                "result": timing.model_dump(mode="json"),
            }
            output.append(EvaluatedEvent(
                **{**base, "explanation": explanation},
                occurrence_index=index, status=PatientEventStatus.RESOLVED, timing=timing,
                activities=[],
            ))
            index += 1
            current = add_amount(current, interval)
        if not output:
            explanation = dict(base["explanation"])
            explanation["reason"] = "condition is resolved; no further repeat is due"
            return [EvaluatedEvent(
                **{**base, "explanation": explanation},
                status=PatientEventStatus.NOT_APPLICABLE,
            )]
        return output

    @staticmethod
    def _apply_block_state(
        results: list[EvaluatedEvent],
        plan: ConditionalPlan,
        block: "RepeatBlock | None",
    ) -> list[EvaluatedEvent]:
        """Apply stop, pause and resume to a repeating block (doc 3 s19-s20).

        Occurrences already in the past are untouched: stopping a treatment branch
        cancels what has not happened, it does not erase completed cycles.

        Resume is the part the protocol has to answer. Once a paused block comes
        back, either the cadence stayed on its original grid (NOMINAL) or it
        restarts from the day the patient actually resumed (ACTUAL_RESUME). When
        the protocol does not say, no date is invented: the occurrences after the
        resume go to UNRESOLVED for a reviewer.
        """
        if block is None:
            return results
        directive = plan.block_directives.get(block.code)
        if directive is None:
            return results

        stopped_from: Temporal | None = None
        for change in directive.changes:
            if change.state == BlockState.STOPPED:
                stopped_from = change.effective_date
                break
        pauses = directive.paused_intervals()
        resumed = directive.resumed_on()
        if stopped_from is None and not pauses:
            return results

        updated: list[EvaluatedEvent] = []
        for item in results:
            start_date = item.timing.nominal_start if item.timing else None
            if start_date is None:
                updated.append(item)
                continue

            if stopped_from is not None and not _before_or_equal(start_date, stopped_from):
                updated.append(_with_block_reason(
                    item, block.code, PatientEventStatus.CANCELLED,
                    directive.condition_code,
                    f"repeating block {block.code} was stopped by condition "
                    f"{directive.condition_code}",
                    BlockState.STOPPED,
                ))
                continue

            paused_in = _containing_pause(start_date, pauses)
            if paused_in is not None:
                updated.append(_with_block_reason(
                    item, block.code, PatientEventStatus.PAUSED, directive.condition_code,
                    f"repeating block {block.code} is paused; this occurrence is not "
                    "due while the patient is off the cadence",
                    BlockState.PAUSED,
                ))
                continue

            if resumed is None or _before_or_equal(start_date, resumed):
                updated.append(item)
                continue
            updated.append(_apply_resume_mode(item, block, pauses, resumed))
        return updated

    def _evaluate_event(
        self,
        event: Event,
        context: PatientContext,
        prior: dict[str, list[EvaluatedEvent]],
        horizon: date,
        *,
        plan: ConditionalPlan | None = None,
    ) -> list[EvaluatedEvent]:
        plan = plan or ConditionalPlan()
        applicability = _evaluate_applicability(event.applicability, context)
        condition = _evaluate_conditions(event, context)
        explanation: dict[str, object] = {
            "rule": event.code,
            "timing": event.timing.model_dump(mode="json"),
            "evidence_refs": [str(item) for item in event.evidence_refs],
        }
        base = {
            "event_definition_id": event.id,
            "event_code": event.code,
            "applicability_result": applicability.value,
            "condition_result": condition.value,
            "explanation": explanation,
            "visit_mode": event.visit_mode,
            "allowed_visit_modes": list(event.allowed_visit_modes),
        }
        if applicability == TruthValue.FALSE or condition == TruthValue.FALSE:
            return [EvaluatedEvent(status=PatientEventStatus.NOT_APPLICABLE, **base)]
        if event.activation == "ON_DEMAND":
            # Doc 10 s13-s15: a protocol-defined unscheduled visit is available,
            # never due. It gets a date only when the site creates one.
            return _evaluate_on_demand(event, context, base, explanation)
        conditional_result = self._conditional_gate(event, plan, base, explanation)
        if conditional_result is not None:
            return conditional_result
        if applicability == TruthValue.UNKNOWN or condition == TruthValue.UNKNOWN:
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_CONDITION, **base)]
        blocked = []
        for dependency in event.dependencies:
            source = prior.get(dependency.source_event_code, [])
            if not source or all(item.status != PatientEventStatus.RESOLVED for item in source):
                blocked.append(dependency.source_event_code)
        if blocked:
            return [EvaluatedEvent(
                status=PatientEventStatus.BLOCKED,
                dependency_result={"blocked_by": blocked}, **base,
            )]
        timing_context = context
        dependency_result: dict[str, object] = {
            "mode": event.dependency_mode.value if event.dependency_mode else DependencyMode.NOMINAL.value,
        }
        if event.dependency_mode == DependencyMode.ACTUAL_PREVIOUS_EVENT:
            missing_actuals = [
                dependency.source_event_code for dependency in event.dependencies
                if not context.actual_event_values.get(dependency.source_event_code)
            ]
            if missing_actuals:
                return [EvaluatedEvent(
                    status=PatientEventStatus.BLOCKED,
                    dependency_result={
                        **dependency_result, "waiting_for_actual": missing_actuals,
                    }, **base,
                )]
            timing_context = context.model_copy(deep=True)
            for dependency in event.dependencies:
                timing_context.event_values[dependency.source_event_code] = list(
                    context.actual_event_values[dependency.source_event_code])
        elif event.dependency_mode == DependencyMode.MANUAL:
            manual = context.state.get("manual_dependency_dates")
            manual_values = manual if isinstance(manual, dict) else {}
            missing_manual = [
                dependency.source_event_code for dependency in event.dependencies
                if dependency.source_event_code not in manual_values
            ]
            if missing_manual:
                return [EvaluatedEvent(
                    status=PatientEventStatus.WAITING_FOR_ANCHOR,
                    dependency_result={
                        **dependency_result, "waiting_for_manual_date": missing_manual,
                    }, **base,
                )]
            timing_context = context.model_copy(deep=True)
            for dependency in event.dependencies:
                value = manual_values[dependency.source_event_code]
                if isinstance(value, str):
                    try:
                        value = datetime.fromisoformat(value)
                    except ValueError:
                        value = date.fromisoformat(value)
                timing_context.event_values[dependency.source_event_code] = [value]
        try:
            timing = evaluate_timing(event.timing, timing_context)
        except UnsupportedTimingError as error:
            explanation["reason"] = str(error)
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        if timing is None:
            explanation["reason"] = "required anchor or event reference is not available"
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        activation = plan.activations.get(event.code)
        if activation is not None and activation.repeat_interval is not None:
            return self._expand_conditional_repeats(
                event, plan, horizon, {**base, "dependency_result": dependency_result})
        if event.recurrence is None:
            explanation["result"] = timing.model_dump(mode="json")
            return [EvaluatedEvent(
                status=PatientEventStatus.RESOLVED, timing=timing,
                dependency_result=dependency_result,
                activities=evaluate_activities(event, timing_context, occurrence_index=0),
                **base,
            )]
        recurrence_base = {**base, "dependency_result": dependency_result}
        return self._expand_recurrence(event, timing, timing_context, horizon, recurrence_base)

    def _expand_recurrence(
        self,
        event: Event,
        first: ResolvedTiming,
        context: PatientContext,
        horizon: date,
        base: dict[str, object],
    ) -> list[EvaluatedEvent]:
        rule = event.recurrence
        assert rule is not None
        current = _resolve_reference(rule.start_reference, context)
        if current is None:
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        stop_date: Temporal = horizon
        max_count = 10000
        termination = rule.termination
        if termination.type == "COUNT":
            max_count = termination.count or 0
        elif termination.type == "DATE" and termination.termination_date is not None:
            stop_date = min(horizon, termination.termination_date)
        elif termination.type == "EVENT" and termination.event_code:
            values = context.event_values.get(termination.event_code, [])
            if values:
                stop_date = min(stop_date, min(values))
        elif termination.type == "CONDITION" and termination.condition is not None:
            result = evaluate_condition(termination.condition, context.state)
            if result == TruthValue.UNKNOWN:
                return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_CONDITION, **base)]
            if result == TruthValue.TRUE:
                effective = context.state_effective_at.get(f"recurrence:{event.code}")
                if effective is None:
                    explanation = dict(base["explanation"])
                    explanation["reason"] = "termination condition is true but its effective time is unresolved"
                    return [EvaluatedEvent(
                        **{**base, "explanation": explanation},
                        status=PatientEventStatus.UNRESOLVED,
                    )]
                effective_value: Temporal = effective
                if isinstance(current, date) and not isinstance(current, datetime):
                    effective_value = effective.date()
                stop_date = min(stop_date, effective_value)
        output: list[EvaluatedEvent] = []
        index = 0
        if not rule.include_start:
            current = add_amount(current, rule.interval)
        while index < max_count and current <= stop_date:
            earliest = latest = None
            if isinstance(event.timing, NominalWindowTiming):
                before = TemporalAmount(value=-event.timing.window.before.value, unit=event.timing.window.before.unit)
                after = TemporalAmount(value=event.timing.window.after.value, unit=event.timing.window.after.unit)
                earliest, latest = add_amount(current, before), add_amount(current, after)
            timing = ResolvedTiming(
                nominal_start=current, nominal_end=current, earliest=earliest,
                latest=latest, precision="EXACT",
            )
            explanation = dict(base["explanation"])
            explanation["recurrence"] = {"index": index, "result": timing.model_dump(mode="json")}
            output.append(EvaluatedEvent(
                **{**base, "explanation": explanation},
                occurrence_index=index, status=PatientEventStatus.RESOLVED, timing=timing,
                activities=evaluate_activities(event, context, occurrence_index=index),
            ))
            index += 1
            current = add_amount(current, rule.interval)
        return output
