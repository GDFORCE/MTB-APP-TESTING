from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Iterable

from app.services.operational_bridge import BridgeActivity, BridgeEvent


def _activity_detail(item: BridgeActivity) -> dict:
    """One row of the Level-3 expanded view: Activity | Timing | Planned | Actual | Status."""
    return {
        "code": item.code,
        "name": item.display_name,
        "status": item.status,
        "requiredness": item.requiredness,
        "planned_time": item.planned_time,
        "earliest_time": item.earliest_time,
        "latest_time": item.latest_time,
        "actual_time": item.actual_time,
    }


def _at_midnight(value: date | None) -> datetime | None:
    return datetime.combine(value, time.min, tzinfo=timezone.utc) if value else None


def _operational_status(status: str) -> tuple[str, str]:
    """Map canonical states without manufacturing due/overdue/completion state."""
    if status == "RESOLVED":
        return "planned", ""
    if status in {"WAITING_FOR_ANCHOR", "WAITING_FOR_CONDITION", "UNRESOLVED", "BLOCKED"}:
        return "manual_review", status.replace("_", " ").title()
    if status == "NOT_APPLICABLE":
        return "not_applicable", "Protocol condition does not apply"
    if status == "CANCELLED":
        return "cancelled", "Cancelled by the canonical schedule"
    if status == "PAUSED":
        return "paused", "Paused by the canonical schedule"
    return status.lower(), ""


def bridge_visit_documents(
    *, patient_id: str, trial_id: str, schedule_version_id: str,
    evaluation_id: str, events: Iterable[BridgeEvent], generated_at: datetime,
) -> list[dict]:
    """Compatibility projection for legacy consumers; canonical SQL remains source of truth."""
    rows: list[dict] = []
    for sequence, event in enumerate(events, start=1):
        operational_status, reason = _operational_status(event.status)
        nominal = _at_midnight(event.nominal_date)
        rows.append({
            "patient_id": patient_id,
            "trial_id": trial_id,
            "visit_template_id": None,
            "uctsm_patient_event_id": str(event.patient_event_id),
            "uctsm_logical_occurrence_id": str(event.logical_occurrence_id),
            "uctsm_logical_key": event.logical_key,
            "uctsm_event_definition_id": str(event.event_definition_id),
            "uctsm_schedule_version_id": schedule_version_id,
            "uctsm_evaluation_id": evaluation_id,
            "uctsm_source_of_truth": True,
            "name": event.name,
            "seq": sequence,
            "visit_number": sequence,
            "event_code": event.event_code,
            "event_type": event.event_type,
            "visit_type": event.event_type,
            "activities": list(event.activities),
            # Doc s12: the day-wise activity schedule the patient/CRC "Level 3"
            # view needs (Activity | Timing Rule | Planned Time | Actual Time |
            # Status), sourced from the per-occurrence evaluation - not the
            # plain name list above, which stays for the existing UI that
            # already reads it as strings.
            "activity_details": [_activity_detail(item) for item in event.activity_details],
            "procedures": [],
            "scheduled_date": nominal,
            "scheduled_end": nominal,
            "window_start": _at_midnight(event.earliest_date),
            "window_end": _at_midnight(event.latest_date),
            "status": operational_status,
            "operational_status": operational_status,
            "canonical_status": event.status,
            "manual_review_reason": reason,
            "note": "",
            "clinical_tasks": [],
            "admin_tasks": [],
            "comments": [],
            "updated_by": None,
            "updated_at": generated_at,
            "created_at": generated_at,
        })
    return rows
