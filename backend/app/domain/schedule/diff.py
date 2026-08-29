from __future__ import annotations

from pydantic import Field

from .models import UniversalSchedule
from .timing import StrictModel


class FieldChange(StrictModel):
    event_code: str
    field_path: str
    before: object | None = None
    after: object | None = None


class ScheduleDiff(StrictModel):
    added_events: list[str] = Field(default_factory=list)
    removed_events: list[str] = Field(default_factory=list)
    changes: list[FieldChange] = Field(default_factory=list)


def compare_schedule_versions(left: UniversalSchedule, right: UniversalSchedule) -> ScheduleDiff:
    left_events = {event.code: event for event in left.events}
    right_events = {event.code: event for event in right.events}
    result = ScheduleDiff(
        added_events=sorted(right_events.keys() - left_events.keys()),
        removed_events=sorted(left_events.keys() - right_events.keys()),
    )
    fields = (
        "protocol_label", "display_name", "event_type", "timing", "applicability",
        "conditions", "dependencies", "recurrence", "activities",
    )
    for code in sorted(left_events.keys() & right_events.keys()):
        before = left_events[code].model_dump(mode="json")
        after = right_events[code].model_dump(mode="json")
        for field in fields:
            if before.get(field) != after.get(field):
                result.changes.append(FieldChange(
                    event_code=code, field_path=field,
                    before=before.get(field), after=after.get(field),
                ))
    return result

