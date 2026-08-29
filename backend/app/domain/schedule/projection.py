from __future__ import annotations

from pydantic import Field

from .models import Event, UniversalSchedule
from .timing import (
    AbsoluteTiming, ApproximateTiming, CycleDayTiming, NoLaterThanTiming,
    NominalWindowTiming, OffsetTiming, ProtocolDefinedTiming, RangeTiming,
    ProtocolDayTiming, TemporalAmount, TriggeredTiming, UnresolvedTiming, WithinTiming,
)
from .timing import StrictModel


def _amount(value: TemporalAmount) -> str:
    unit = value.unit.value.lower()
    suffix = "" if abs(value.value) == 1 else "s"
    return f"{value.value} {unit}{suffix}"


def _reference(value: object) -> str:
    code = getattr(value, "code", None) or getattr(value, "event_code", None)
    return str(code).replace("_", " ").title() if code else "reference event"


def timing_display(event: Event) -> tuple[str, str | None]:
    timing = event.timing
    if isinstance(timing, AbsoluteTiming):
        return str(timing.value), None
    if isinstance(timing, OffsetTiming):
        direction = "after" if timing.offset.value >= 0 else "before"
        amount = timing.offset.model_copy(update={"value": abs(timing.offset.value)})
        return f"{_amount(amount)} {direction} {_reference(timing.reference)}", None
    if isinstance(timing, ProtocolDayTiming):
        return f"Day {timing.day} relative to {_reference(timing.reference)}", None
    if isinstance(timing, RangeTiming):
        return f"{_amount(timing.start)} to {_amount(timing.end)} from {_reference(timing.reference)}", None
    if isinstance(timing, NominalWindowTiming):
        temporary = event.model_copy(update={"timing": timing.nominal})
        nominal, _ = timing_display(temporary)
        return nominal, f"-{_amount(timing.window.before)}/+{_amount(timing.window.after)}"
    if isinstance(timing, WithinTiming):
        return f"Within {_amount(timing.duration)} {timing.direction.lower()} {_reference(timing.reference)}", None
    if isinstance(timing, NoLaterThanTiming):
        return f"No later than {_amount(timing.duration)} from {_reference(timing.reference)}", None
    if isinstance(timing, TriggeredTiming):
        nested = timing.timing_after_trigger
        amount = getattr(nested, "duration", None) or getattr(nested, "offset", None)
        return f"{nested.type.replace('_', ' ').title()} {_amount(amount)} after {_reference(timing.trigger)}", None
    if isinstance(timing, CycleDayTiming):
        cycle = timing.cycle.number or "current"
        return f"Cycle {cycle}, Day {timing.day}", None
    if isinstance(timing, ApproximateTiming):
        return f"Approximately {_amount(timing.offset)} from {_reference(timing.reference)}", None
    if isinstance(timing, (UnresolvedTiming, ProtocolDefinedTiming)):
        return "Timing requires site/sponsor confirmation", None
    return "Timing unavailable", None


class ScheduleDisplayProjection(StrictModel):
    event_id: str
    event_code: str
    title: str
    timing_display: str
    window_display: str | None = None
    event_type_display: str
    activities_display: list[str] = Field(default_factory=list)
    condition_display: str | None = None
    status: str
    requires_review: bool
    evidence_refs: list[str] = Field(default_factory=list)


def project_schedule(schedule: UniversalSchedule) -> list[ScheduleDisplayProjection]:
    output = []
    for event in schedule.events:
        timing, window = timing_display(event)
        output.append(ScheduleDisplayProjection(
            event_id=str(event.id), event_code=event.code, title=event.display_name,
            timing_display=timing, window_display=window,
            event_type_display=event.event_type.replace("_", " ").title(),
            activities_display=[item.display_name for item in event.activities],
            condition_display="Conditional" if event.conditions else None,
            status=event.interpretation_status.value,
            requires_review=event.requires_review,
            evidence_refs=[str(item) for item in event.evidence_refs],
        ))
    return output
