from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.notification_projection import project_notification_candidate


def test_notifications_consume_resolved_patient_windows_not_protocol_or_pending_rules():
    assert project_notification_candidate(
        patient_event_id="event-1", event_status="WAITING_FOR_ANCHOR",
        nominal_date=None, earliest_date=None, latest_date=None, today=date(2027, 1, 1),
    ) is None
    candidate = project_notification_candidate(
        patient_event_id="event-1", event_status="RESOLVED",
        nominal_date=date(2027, 1, 14), earliest_date=date(2027, 1, 9),
        latest_date=date(2027, 1, 19), today=date(2027, 1, 18),
    )
    assert candidate.policy_state == "DUE_WINDOW"

