from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class NotificationCandidate(BaseModel):
    patient_event_id: str
    status: str
    nominal_date: date | None
    earliest_date: date | None
    latest_date: date | None
    policy_state: str


def project_notification_candidate(
    *,
    patient_event_id: str,
    event_status: str,
    nominal_date: date | None,
    earliest_date: date | None,
    latest_date: date | None,
    today: date,
) -> NotificationCandidate | None:
    """Project resolved events only; delivery policy remains a separate service."""
    if event_status != "RESOLVED":
        return None
    due_start = earliest_date or nominal_date
    due_end = latest_date or nominal_date
    if due_start is None or due_end is None:
        return None
    if today < due_start:
        state = "UPCOMING"
    elif today <= due_end:
        state = "DUE_WINDOW"
    else:
        state = "OVERDUE"
    return NotificationCandidate(
        patient_event_id=patient_event_id, status=event_status,
        nominal_date=nominal_date, earliest_date=earliest_date,
        latest_date=latest_date, policy_state=state,
    )

