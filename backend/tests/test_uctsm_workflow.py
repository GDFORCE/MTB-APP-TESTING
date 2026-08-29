from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.services.schedule_service import PatientScheduleService, ScheduleReviewService
from app.domain.schedule.exceptions import ImmutableScheduleError
import pytest


def test_review_approval_and_patient_evaluation_are_reproducible_and_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    ids = {name: uuid4() for name in ("organization", "trial", "protocol", "protocol_version", "definition", "patient", "reviewer")}
    with Session(engine) as session:
        session.add_all([
            db.Trial(id=ids["trial"], organization_id=ids["organization"]),
            db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="ABC-123"),
            db.ProtocolVersion(
                id=ids["protocol_version"], protocol_id=ids["protocol"], version_label="1",
                document_name="protocol.pdf", document_uri="private://protocol.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=ids["definition"], protocol_version_id=ids["protocol_version"], name="Primary",
            ),
        ])
        session.flush()
        evidence = Evidence(evidence_type="TABLE_CELL", page_number=84, source_text="30 days after last dose")
        anchor = Anchor(code="LAST_DOSE", display_name="Last Dose", anchor_type="LAST_DOSE")
        event = Event(
            code="SAFETY_FOLLOW_UP", protocol_label="Safety Follow-up", display_name="Safety Follow-up",
            event_type="SAFETY_ASSESSMENT",
            timing=OffsetTiming(reference=AnchorReference(code="LAST_DOSE"), offset=TemporalAmount(value=30, unit="DAY")),
            evidence_refs=[evidence.id],
        )
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(
                name="Primary", protocol_version_id=ids["protocol_version"],
                status=ScheduleStatus.VALIDATION_REQUIRED,
            ),
            anchors=[anchor], events=[event], evidence=[evidence],
            claim_evidence=[
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="display_name",
                ),
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="timing",
                ),
            ],
        )
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=ids["definition"])
        session.flush()
        review = ScheduleReviewService(session)
        assert not review.validate(schedule.schedule_version_id)
        review.submit_for_review(schedule.schedule_version_id, actor_id=ids["reviewer"])
        for path in ("display_name", "timing"):
            review.record_decision(
                schedule.schedule_version_id, reviewer_id=ids["reviewer"], decision="CONFIRM",
                entity_type="EVENT", entity_id=event.id, field_path=path,
            )
        review.approve(schedule.schedule_version_id, reviewer_id=ids["reviewer"])
        with pytest.raises(ImmutableScheduleError):
            review.correct_event(
                schedule.schedule_version_id, event,
                reviewer_id=ids["reviewer"], reason="Attempted post-approval edit",
            )
        session.add(db.Patient(
            id=ids["patient"], organization_id=ids["organization"], trial_id=ids["trial"],
            patient_code="P001", current_schedule_version_id=schedule.schedule_version_id,
        ))
        session.add(db.PatientAnchor(
            patient_id=ids["patient"], anchor_definition_id=anchor.id,
            value_date=date(2026, 12, 15), status="CONFIRMED",
        ))
        session.commit()

        service = PatientScheduleService(session)
        evaluation, events = service.evaluate(
            ids["patient"], organization_id=ids["organization"],
            horizon=date(2027, 12, 31), idempotency_key="patient-p001-v1",
        )
        session.commit()
        assert events[0].nominal_start_date == date(2027, 1, 14)
        assert events[0].generation_reason["evidence_refs"] == [str(evidence.id)]
        same_evaluation, same_events = service.evaluate(
            ids["patient"], organization_id=ids["organization"],
            horizon=date(2027, 12, 31), idempotency_key="patient-p001-v1",
        )
        assert same_evaluation.id == evaluation.id
        assert [item.id for item in same_events] == [item.id for item in events]
