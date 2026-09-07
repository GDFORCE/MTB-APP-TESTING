from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.operational_bridge import BridgeEvent
from app.services.operational_projection import bridge_visit_documents


def _event(status: str, nominal: date | None) -> BridgeEvent:
    return BridgeEvent(
        patient_event_id=uuid4(), logical_occurrence_id=uuid4(),
        logical_key=f"V1:{status}", event_definition_id=uuid4(),
        event_code="V1", name="Visit 1", event_type="SITE_VISIT",
        status=status, nominal_date=nominal,
        earliest_date=nominal, latest_date=nominal, activities=("ECG",),
    )


def test_projection_preserves_canonical_identity_and_does_not_invent_dates():
    generated = datetime(2026, 8, 31, tzinfo=timezone.utc)
    resolved = _event("RESOLVED", date(2026, 9, 2))
    waiting = _event("WAITING_FOR_ANCHOR", None)
    rows = bridge_visit_documents(
        patient_id="mongo-patient", trial_id="mongo-trial",
        schedule_version_id="version", evaluation_id="evaluation",
        events=[resolved, waiting], generated_at=generated,
    )

    assert rows[0]["uctsm_patient_event_id"] == str(resolved.patient_event_id)
    assert rows[0]["scheduled_date"].date() == date(2026, 9, 2)
    assert rows[0]["operational_status"] == "planned"
    assert rows[1]["scheduled_date"] is None
    assert rows[1]["window_start"] is None
    assert rows[1]["operational_status"] == "manual_review"
    assert rows[1]["canonical_status"] == "WAITING_FOR_ANCHOR"


def test_inactive_event_is_not_projected_as_due_or_overdue():
    row = bridge_visit_documents(
        patient_id="p", trial_id="t", schedule_version_id="v",
        evaluation_id="e", events=[_event("NOT_APPLICABLE", None)],
        generated_at=datetime.now(timezone.utc),
    )[0]
    assert row["scheduled_date"] is None
    assert row["operational_status"] == "not_applicable"
