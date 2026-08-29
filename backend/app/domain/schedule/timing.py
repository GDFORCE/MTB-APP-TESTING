from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeUnit(StrEnum):
    MINUTE = "MINUTE"
    HOUR = "HOUR"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


class Precision(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    CONSTRAINT = "CONSTRAINT"


class TemporalAmount(StrictModel):
    value: int
    unit: TimeUnit


class NonNegativeTemporalAmount(StrictModel):
    value: int = Field(ge=0)
    unit: TimeUnit


class PositiveTemporalAmount(StrictModel):
    value: int = Field(gt=0)
    unit: TimeUnit


class AnchorReference(StrictModel):
    kind: Literal["ANCHOR"] = "ANCHOR"
    code: str = Field(min_length=1)


class EventReference(StrictModel):
    kind: Literal["EVENT"] = "EVENT"
    event_code: str = Field(min_length=1)
    occurrence: Literal["FIRST", "LAST", "CURRENT"] = "CURRENT"


TemporalReference = Annotated[
    Union[AnchorReference, EventReference], Field(discriminator="kind")
]


class Window(StrictModel):
    before: NonNegativeTemporalAmount
    after: NonNegativeTemporalAmount

    @model_validator(mode="after")
    def same_unit(self) -> "Window":
        if self.before.unit != self.after.unit:
            raise ValueError("window before and after must use the same unit")
        return self


class AbsoluteTiming(StrictModel):
    type: Literal["ABSOLUTE"] = "ABSOLUTE"
    value: date | datetime


class OffsetTiming(StrictModel):
    type: Literal["OFFSET"] = "OFFSET"
    reference: TemporalReference
    offset: TemporalAmount


class ProtocolDayTiming(StrictModel):
    """A numbered clinical day; unlike OFFSET, Day 1 resolves to the anchor."""
    type: Literal["PROTOCOL_DAY"] = "PROTOCOL_DAY"
    reference: AnchorReference
    day: int

    @model_validator(mode="after")
    def no_day_zero(self) -> "ProtocolDayTiming":
        if self.day == 0:
            raise ValueError("clinical day numbering has no Day 0")
        return self


class RangeTiming(StrictModel):
    type: Literal["RANGE"] = "RANGE"
    reference: TemporalReference
    start: TemporalAmount
    end: TemporalAmount

    @model_validator(mode="after")
    def ordered_compatible_bounds(self) -> "RangeTiming":
        if self.start.unit != self.end.unit:
            raise ValueError("range bounds must use the same unit")
        if self.start.value > self.end.value:
            raise ValueError("range start must be less than or equal to range end")
        return self


class NominalWindowTiming(StrictModel):
    type: Literal["NOMINAL_WITH_WINDOW"] = "NOMINAL_WITH_WINDOW"
    nominal: OffsetTiming | AbsoluteTiming
    window: Window


class WithinTiming(StrictModel):
    type: Literal["WITHIN"] = "WITHIN"
    reference: TemporalReference
    duration: PositiveTemporalAmount
    direction: Literal["AFTER", "BEFORE"] = "AFTER"


class NoLaterThanTiming(StrictModel):
    type: Literal["NO_LATER_THAN"] = "NO_LATER_THAN"
    reference: TemporalReference
    duration: TemporalAmount


class ApproximateTiming(StrictModel):
    type: Literal["APPROXIMATE"] = "APPROXIMATE"
    reference: TemporalReference
    offset: TemporalAmount
    requires_review: bool = True


class TriggerOffset(StrictModel):
    type: Literal["OFFSET"] = "OFFSET"
    offset: TemporalAmount


class TriggerWithin(StrictModel):
    type: Literal["WITHIN"] = "WITHIN"
    duration: PositiveTemporalAmount
    direction: Literal["AFTER", "BEFORE"] = "AFTER"


class TriggerNoLaterThan(StrictModel):
    type: Literal["NO_LATER_THAN"] = "NO_LATER_THAN"
    duration: TemporalAmount


class TriggeredTiming(StrictModel):
    type: Literal["TRIGGERED"] = "TRIGGERED"
    trigger: TemporalReference
    timing_after_trigger: Annotated[
        Union[TriggerOffset, TriggerWithin, TriggerNoLaterThan],
        Field(discriminator="type"),
    ]


class CycleSelector(StrictModel):
    type: Literal["CURRENT", "NUMBER"]
    number: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def number_matches_type(self) -> "CycleSelector":
        if (self.type == "NUMBER") != (self.number is not None):
            raise ValueError("cycle number is required only for NUMBER selector")
        return self


class CycleDayTiming(StrictModel):
    type: Literal["CYCLE_DAY"] = "CYCLE_DAY"
    cycle_reference: str = Field(min_length=1)
    cycle: CycleSelector
    day: int = Field(gt=0)


class ProtocolExtension(StrictModel):
    type: str = Field(min_length=1)
    value: object


class ProtocolDefinedTiming(StrictModel):
    type: Literal["PROTOCOL_DEFINED"] = "PROTOCOL_DEFINED"
    handler: str | None = None
    extensions: list[ProtocolExtension] = Field(default_factory=list)
    requires_review: bool = True


class UnresolvedTiming(StrictModel):
    type: Literal["UNRESOLVED"] = "UNRESOLVED"
    reason: str = Field(min_length=1)
    source_reference: dict[str, object] = Field(default_factory=dict)
    requires_review: Literal[True] = True


TimingExpression = Annotated[
    Union[
        AbsoluteTiming,
        OffsetTiming,
        ProtocolDayTiming,
        RangeTiming,
        NominalWindowTiming,
        WithinTiming,
        NoLaterThanTiming,
        ApproximateTiming,
        TriggeredTiming,
        CycleDayTiming,
        ProtocolDefinedTiming,
        UnresolvedTiming,
    ],
    Field(discriminator="type"),
]
