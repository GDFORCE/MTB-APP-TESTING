"""Protocol deviation detection.

Requirement sources: doc 1 sections 18 and 26, doc 2 section 39, doc 3 section 31,
doc 4 section 25, doc 5 section 17, doc 6 section 38, doc 9 section 27.

Deviation is computed, never stored. The protocol-expected date is recalculated
whenever an anchor is corrected, so a stored deviation would go stale the moment a
site fixes a surgery date. Computing it from the current plan plus the recorded
actual keeps the two consistent by construction.

Two magnitudes are reported because the requirement uses both:

  * ``delta_from_planned`` - actual minus the protocol-expected value. Doc 1
    section 18 calls this out as "early by 2 days" even when the visit was inside
    its window, so it is informational on its own.
  * ``outside_window`` - how far past the allowed window the actual fell. Doc 1
    section 26 measures "1 day late" from the window edge, not from the target.

A visit is only assessable if it was an ACTIVE, dated requirement for that patient.
Doc 2 section 39 is explicit: before a condition is activated the visit "did not
exist as an active patient requirement and therefore could not be considered
overdue or deviated".
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field

from .timing import StrictModel

# Statuses that mean the occurrence was a real, dated requirement for this patient.
ASSESSABLE_STATUSES = {"RESOLVED", "COMPLETED", "MISSED", "DUE", "OVERDUE"}

# Statuses that mean the protocol never required this of this patient.
NOT_A_REQUIREMENT = {
    "WAITING_FOR_CONDITION": "the protocol condition was never activated for this patient",
    "NOT_APPLICABLE": "not applicable to this patient",
    "CANCELLED": "cancelled before it became due",
    "PAUSED": "the schedule block was paused",
    "WAITING_FOR_ANCHOR": "no protocol-expected date could be calculated yet",
    "BLOCKED": "waiting on a preceding actual date",
    "UNRESOLVED": "protocol timing needs reviewer confirmation",
    # Doc 10 s13-s15: an unscheduled visit that was never created was never due,
    # so it can be neither early, late, nor missed.
    "AVAILABLE_ON_DEMAND": "an unscheduled visit that was never required to occur",
}


class DeviationType(StrEnum):
    NONE = "NONE"
    EARLY = "EARLY"
    LATE = "LATE"
    MISSED = "MISSED"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class VisitDeviation(StrictModel):
    logical_key: str
    event_code: str
    display_name: str
    deviation_type: DeviationType
    planned_date: date | None = None
    earliest_date: date | None = None
    latest_date: date | None = None
    actual_date: date | None = None
    #: actual - planned, in days. Negative is early. Reported even when in window.
    delta_from_planned_days: int | None = None
    #: days beyond the allowed window; 0 when the visit fell inside it.
    outside_window_days: int | None = None
    within_window: bool | None = None
    reason: str


class ActivityDeviation(StrictModel):
    logical_key: str
    activity_code: str | None
    display_name: str
    deviation_type: DeviationType
    planned_time: datetime | None = None
    earliest_time: datetime | None = None
    latest_time: datetime | None = None
    actual_time: datetime | None = None
    delta_from_planned_minutes: int | None = None
    outside_window_minutes: int | None = None
    within_window: bool | None = None
    reason: str


class ConfinementDeviation(StrictModel):
    episode_code: str
    display_name: str
    admission_deviation_days: int | None = None
    discharge_deviation_days: int | None = None
    extended: bool = False
    reason: str


class DeviationReport(StrictModel):
    """Everything a monitor needs, with the version the windows came from."""

    patient_id: str
    schedule_version_id: str
    assessed_on: date
    visits: list[VisitDeviation] = Field(default_factory=list)
    activities: list[ActivityDeviation] = Field(default_factory=list)
    confinements: list[ConfinementDeviation] = Field(default_factory=list)

    def deviations(self) -> list[VisitDeviation]:
        """Only the visits that actually deviated."""
        return [
            item for item in self.visits
            if item.deviation_type not in {DeviationType.NONE, DeviationType.NOT_ASSESSABLE}
        ]


def _window_breach(value: date, earliest: date | None, latest: date | None) -> int:
    """Days outside the allowed window; 0 when inside or when no window is defined."""
    if earliest is not None and value < earliest:
        return (earliest - value).days
    if latest is not None and value > latest:
        return (value - latest).days
    return 0


def assess_visit(
    *,
    logical_key: str,
    event_code: str,
    display_name: str,
    status: str,
    planned_date: date | None,
    earliest_date: date | None,
    latest_date: date | None,
    actual_date: date | None,
    today: date,
) -> VisitDeviation:
    """Classify one occurrence against the window it is currently expected in.

    The planned date passed in must be the CURRENT protocol-expected date. For an
    ACTUAL_PREVIOUS_EVENT schedule that date legitimately moves when an earlier
    visit runs late, and doc 4 section 25 is explicit that the later visit must not
    be reported as deviated merely because its original provisional date differed.
    """
    base = {
        "logical_key": logical_key, "event_code": event_code, "display_name": display_name,
        "planned_date": planned_date, "earliest_date": earliest_date,
        "latest_date": latest_date, "actual_date": actual_date,
    }
    if actual_date is None and status in NOT_A_REQUIREMENT:
        return VisitDeviation(
            deviation_type=DeviationType.NOT_ASSESSABLE,
            reason=NOT_A_REQUIREMENT[status], **base,
        )
    if planned_date is None:
        return VisitDeviation(
            deviation_type=DeviationType.NOT_ASSESSABLE,
            reason="no protocol-expected date to compare against", **base,
        )
    if actual_date is None:
        # Only a window that has fully elapsed can be called missed; a visit still
        # inside its window is simply not done yet.
        deadline = latest_date or planned_date
        if today > deadline:
            return VisitDeviation(
                deviation_type=DeviationType.MISSED, within_window=False,
                outside_window_days=(today - deadline).days,
                reason=f"no actual date recorded and the window closed on {deadline.isoformat()}",
                **base,
            )
        return VisitDeviation(
            deviation_type=DeviationType.NONE, within_window=None,
            reason="not yet due", **base,
        )

    delta = (actual_date - planned_date).days
    breach = _window_breach(actual_date, earliest_date, latest_date)
    within = breach == 0
    if within:
        detail = "on the protocol-expected date" if delta == 0 else (
            f"{abs(delta)} day(s) {'early' if delta < 0 else 'late'} but inside the allowed window")
        return VisitDeviation(
            deviation_type=DeviationType.NONE, delta_from_planned_days=delta,
            outside_window_days=0, within_window=True,
            reason=f"completed {detail}", **base,
        )
    early = actual_date < (earliest_date or planned_date)
    return VisitDeviation(
        deviation_type=DeviationType.EARLY if early else DeviationType.LATE,
        delta_from_planned_days=delta, outside_window_days=breach, within_window=False,
        reason=(
            f"outside the protocol window: {breach} day(s) "
            f"{'before' if early else 'after'} the allowed window"
        ),
        **base,
    )


def assess_activity(
    *,
    logical_key: str,
    activity_code: str | None,
    display_name: str,
    status: str,
    planned_time: datetime | None,
    earliest_time: datetime | None,
    latest_time: datetime | None,
    actual_time: datetime | None,
) -> ActivityDeviation:
    """Classify one intra-day activity against its timing window (doc 5 section 17)."""
    base = {
        "logical_key": logical_key, "activity_code": activity_code,
        "display_name": display_name, "planned_time": planned_time,
        "earliest_time": earliest_time, "latest_time": latest_time,
        "actual_time": actual_time,
    }
    if status == "NOT_APPLICABLE":
        return ActivityDeviation(
            deviation_type=DeviationType.NOT_ASSESSABLE,
            reason="not applicable to this patient", **base,
        )
    if status == "NOT_DONE":
        return ActivityDeviation(
            deviation_type=DeviationType.MISSED,
            reason="recorded as not done", **base,
        )
    if planned_time is None:
        return ActivityDeviation(
            deviation_type=DeviationType.NOT_ASSESSABLE,
            reason="no protocol-expected time to compare against", **base,
        )
    if actual_time is None:
        return ActivityDeviation(
            deviation_type=DeviationType.NONE,
            reason="no actual time recorded", **base,
        )

    delta = round((actual_time - planned_time).total_seconds() / 60)
    breach = 0
    if earliest_time is not None and actual_time < earliest_time:
        breach = round((earliest_time - actual_time).total_seconds() / 60)
    elif latest_time is not None and actual_time > latest_time:
        breach = round((actual_time - latest_time).total_seconds() / 60)
    if breach == 0:
        return ActivityDeviation(
            deviation_type=DeviationType.NONE, delta_from_planned_minutes=delta,
            outside_window_minutes=0, within_window=True,
            reason="performed inside the allowed timing window", **base,
        )
    early = earliest_time is not None and actual_time < earliest_time
    return ActivityDeviation(
        deviation_type=DeviationType.EARLY if early else DeviationType.LATE,
        delta_from_planned_minutes=delta, outside_window_minutes=breach,
        within_window=False,
        reason=(
            f"activity timing deviation: {breach} minute(s) "
            f"{'before' if early else 'after'} the allowed window"
        ),
        **base,
    )


def assess_confinement(
    *,
    episode_code: str,
    display_name: str,
    planned_admission: date | None,
    actual_admission: date | None,
    planned_discharge: date | None,
    actual_discharge: date | None,
    extended: bool,
) -> ConfinementDeviation:
    """Compare planned against actual admission and discharge (doc 6 section 38).

    An extension that the protocol allows and a PI confirmed is reported separately
    from an unexplained late discharge, because they are not the same finding.
    """
    admission_delta = (
        (actual_admission - planned_admission).days
        if actual_admission and planned_admission else None
    )
    discharge_delta = (
        (actual_discharge - planned_discharge).days
        if actual_discharge and planned_discharge else None
    )
    notes: list[str] = []
    if admission_delta:
        notes.append(
            f"admission {abs(admission_delta)} day(s) "
            f"{'late' if admission_delta > 0 else 'early'}")
    if discharge_delta:
        notes.append(
            f"discharge {abs(discharge_delta)} day(s) "
            f"{'late' if discharge_delta > 0 else 'early'}")
    if extended:
        notes.append("stay was extended under a confirmed protocol condition")
    return ConfinementDeviation(
        episode_code=episode_code, display_name=display_name,
        admission_deviation_days=admission_delta,
        discharge_deviation_days=discharge_delta, extended=extended,
        reason="; ".join(notes) if notes else "admission and discharge matched the plan",
    )
