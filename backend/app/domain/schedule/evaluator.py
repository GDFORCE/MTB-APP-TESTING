from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from .condition import TruthValue, evaluate_condition
from .exceptions import ScheduleNotApprovedError, UnsupportedTimingError
from .models import (
    ApplicabilityRule,
    EvaluatedEvent,
    EvaluationResult,
    Event,
    PatientContext,
    PatientEventStatus,
    ResolvedTiming,
    ScheduleStatus,
    UniversalSchedule,
)
from .timing import (
    AbsoluteTiming,
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


def _resolve_reference(reference: TemporalReference, context: PatientContext) -> Temporal | None:
    if isinstance(reference, AnchorReference):
        return context.anchors.get(reference.code)
    values = context.event_values.get(reference.event_code, [])
    if not values:
        return None
    if reference.occurrence == "FIRST":
        return min(values)
    if reference.occurrence == "LAST":
        return max(values)
    return values[-1]


def evaluate_timing(expression: object, context: PatientContext) -> ResolvedTiming | None:
    if isinstance(expression, AbsoluteTiming):
        return ResolvedTiming(nominal_start=expression.value, nominal_end=expression.value, precision="EXACT")
    if isinstance(expression, OffsetTiming):
        reference = _resolve_reference(expression.reference, context)
        if reference is None:
            return None
        value = add_amount(reference, expression.offset)
        return ResolvedTiming(nominal_start=value, nominal_end=value, precision="EXACT")
    if isinstance(expression, ProtocolDayTiming):
        reference = _resolve_reference(expression.reference, context)
        if reference is None:
            return None
        elapsed_days = expression.day - 1 if expression.day > 0 else expression.day
        value = reference + timedelta(days=elapsed_days)
        return ResolvedTiming(
            nominal_start=value, nominal_end=value, precision="EXACT",
            constraints=[{"type": "CLINICAL_DAY", "protocol_day": expression.day}],
        )
    if isinstance(expression, RangeTiming):
        reference = _resolve_reference(expression.reference, context)
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
        nominal = evaluate_timing(expression.nominal, context)
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
        reference = _resolve_reference(expression.reference, context)
        if reference is None:
            return None
        end = add_amount(reference, expression.duration, 1 if expression.direction == "AFTER" else -1)
        earliest, latest = sorted((reference, end))
        return ResolvedTiming(
            earliest=earliest, latest=latest, precision="CONSTRAINT",
            constraints=[{"type": "WITHIN", "direction": expression.direction}],
        )
    if isinstance(expression, NoLaterThanTiming):
        reference = _resolve_reference(expression.reference, context)
        if reference is None:
            return None
        return ResolvedTiming(
            latest=add_amount(reference, expression.duration), precision="CONSTRAINT",
            constraints=[{"type": "NO_LATER_THAN"}],
        )
    if isinstance(expression, TriggeredTiming):
        trigger_value = _resolve_reference(expression.trigger, context)
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
        included = str(value) in rule.values
        if rule.operator == "NOT_IN":
            included = not included
        results.append(TruthValue.TRUE if included else TruthValue.FALSE)
    if TruthValue.FALSE in results:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in results else TruthValue.TRUE


def _evaluate_conditions(event: Event, context: PatientContext) -> TruthValue:
    if not event.conditions:
        return TruthValue.TRUE
    results = [evaluate_condition(condition, context.state) for condition in event.conditions]
    if TruthValue.FALSE in results:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in results else TruthValue.TRUE


class ScheduleEvaluator:
    version = "uctsm-evaluator.v1"

    def evaluate(self, schedule: UniversalSchedule, context: PatientContext, *, horizon: date) -> EvaluationResult:
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
        for code in topological_event_codes(schedule):
            event = events_by_code[code]
            event_results = self._evaluate_event(event, working_context, results_by_code, horizon)
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
        return EvaluationResult(
            schedule_version_id=schedule.schedule_version_id,
            patient_id=context.patient_id,
            evaluator_version=self.version,
            input_snapshot=context.model_dump(mode="json"),
            events=evaluated,
        )

    def _evaluate_event(
        self,
        event: Event,
        context: PatientContext,
        prior: dict[str, list[EvaluatedEvent]],
        horizon: date,
    ) -> list[EvaluatedEvent]:
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
        }
        if applicability == TruthValue.FALSE or condition == TruthValue.FALSE:
            return [EvaluatedEvent(status=PatientEventStatus.NOT_APPLICABLE, **base)]
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
        try:
            timing = evaluate_timing(event.timing, context)
        except UnsupportedTimingError as error:
            explanation["reason"] = str(error)
            return [EvaluatedEvent(status=PatientEventStatus.UNRESOLVED, **base)]
        if timing is None:
            explanation["reason"] = "required anchor or event reference is not available"
            return [EvaluatedEvent(status=PatientEventStatus.WAITING_FOR_ANCHOR, **base)]
        if event.recurrence is None:
            explanation["result"] = timing.model_dump(mode="json")
            return [EvaluatedEvent(status=PatientEventStatus.RESOLVED, timing=timing, **base)]
        return self._expand_recurrence(event, timing, context, horizon, base)

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
            ))
            index += 1
            current = add_amount(current, rule.interval)
        return output
