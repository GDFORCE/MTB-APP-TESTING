"""The per-patient status of one anchor (requirement doc 1 section 22).

Doc 1 asks for a specific vocabulary, and the distinctions it draws are the whole
point of the document:

    NOT_REQUIRED    this anchor does not apply to this patient at all
    AWAITING_EVENT  the anchoring event has not happened, so there is no date
    PLANNED         a date is expected but the event has NOT happened yet
    ACTUAL          the event happened and its real date is recorded
    CONFIRMED       a human confirmed the recorded date
    CORRECTED       a confirmed date was later changed

PLANNED and ACTUAL are the pair that matters. A planned surgery date is not
evidence that surgery occurred; scheduling a 30-day follow-up off it, and then
presenting that follow-up as if it were anchored to reality, is the failure this
requirement exists to prevent. So PLANNED never silently becomes ACTUAL - only a
recorded actual date does that.

This module is pure: it maps stored anchor records to the vocabulary and never
touches the database or computes a date.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from .timing import StrictModel

Temporal = date | datetime


class AnchorStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    AWAITING_EVENT = "AWAITING_EVENT"
    PLANNED = "PLANNED"
    ACTUAL = "ACTUAL"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"


#: Record-level statuses that mean "a human has accepted this value".
CONFIRMED_RECORD_STATUSES = frozenset({"CONFIRMED"})

#: Record-level statuses that carry a usable date but no human decision yet.
PROVISIONAL_RECORD_STATUSES = frozenset({"PROVISIONAL", "PENDING_CONFIRMATION"})

#: Source types that assert the event actually happened, as opposed to being
#: expected. Anything not listed is treated as PLANNED, because guessing the
#: other way would let an expected date be read as a real one.
ACTUAL_SOURCE_TYPES = frozenset({
    "ACTUAL", "ACTUAL_EVENT", "RECORDED_VISIT", "EDC", "SOURCE_DOCUMENT",
    "DOSE_ADMINISTRATION", "PROCEDURE_RECORD",
})


class AnchorRecord(StrictModel):
    """One stored value for one anchor, in the order it was recorded."""

    value: Temporal | None = None
    record_status: str
    source_type: str | None = None
    recorded_at: datetime | None = None
    superseded: bool = False


class AnchorStatusView(StrictModel):
    """What a Patient Profile shows for one anchor."""

    anchor_code: str
    display_name: str
    status: AnchorStatus
    value: Temporal | None = None
    reason: str
    awaiting_event_code: str | None = None
    confirmed: bool = False


def resolve_anchor_status(
    *,
    anchor_code: str,
    display_name: str,
    records: list[AnchorRecord],
    applicable: bool = True,
    source_event_code: str | None = None,
    source_event_has_actual: bool = False,
) -> AnchorStatusView:
    """Derive doc 1 section 22's status for one anchor and one patient.

    ``records`` must be in recording order, oldest first. Superseded records are
    what make CORRECTED distinguishable from CONFIRMED: a value that replaced a
    previously confirmed one is a correction, and reviewers need to see that it
    was changed rather than just see the new date.
    """
    base: dict[str, object] = {
        "anchor_code": anchor_code, "display_name": display_name,
    }

    if not applicable:
        return AnchorStatusView(
            status=AnchorStatus.NOT_REQUIRED,
            reason="this anchor does not apply to this patient",
            **base,
        )

    live = [item for item in records if not item.superseded and item.value is not None]
    if not live:
        # Doc 1 sections 3 and 8: no anchor means no date. Naming the event the
        # anchor waits on is what makes the empty schedule row explicable.
        if source_event_code is not None and not source_event_has_actual:
            return AnchorStatusView(
                status=AnchorStatus.AWAITING_EVENT,
                reason=f"waiting for {source_event_code} to occur",
                awaiting_event_code=source_event_code, **base,
            )
        return AnchorStatusView(
            status=AnchorStatus.AWAITING_EVENT,
            reason="no date has been recorded for this anchor yet", **base,
        )

    current = live[-1]
    was_confirmed_before = any(
        item.superseded and item.record_status in CONFIRMED_RECORD_STATUSES
        for item in records
    )

    if current.record_status in CONFIRMED_RECORD_STATUSES:
        if was_confirmed_before:
            return AnchorStatusView(
                status=AnchorStatus.CORRECTED, value=current.value, confirmed=True,
                reason="a previously confirmed date was corrected", **base,
            )
        return AnchorStatusView(
            status=AnchorStatus.CONFIRMED, value=current.value, confirmed=True,
            reason="confirmed by a reviewer", **base,
        )

    if _is_actual(current):
        return AnchorStatusView(
            status=AnchorStatus.ACTUAL, value=current.value,
            reason="recorded from what actually happened, awaiting confirmation",
            **base,
        )
    # Everything else is expected, not evidence. Doc 1 section 22 keeps PLANNED
    # separate so a follow-up anchored to it is never shown as anchored to a real
    # event.
    return AnchorStatusView(
        status=AnchorStatus.PLANNED, value=current.value,
        reason="an expected date; the event has not been recorded as having happened",
        **base,
    )


def _is_actual(record: AnchorRecord) -> bool:
    source = (record.source_type or "").strip().upper()
    return source in ACTUAL_SOURCE_TYPES
