from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount,
    OffsetTiming, PositiveTemporalAmount, TemporalAmount, TriggeredTiming,
    TriggerWithin, Window,
)


class DemoService:
    """Creates isolated, tenant-scoped test data for the in-app UCTSM workbench."""

    def __init__(self, session: Session):
        self.session = session

    def seed(self, *, organization_id: UUID, actor_id: UUID) -> dict[str, str]:
        existing = self.session.scalar(select(db.Trial).where(
            db.Trial.organization_id == organization_id,
            db.Trial.protocol_id == "UCTSM-DEMO-001",
        ))
        if existing:
            patient = self.session.scalar(select(db.Patient).where(
                db.Patient.trial_id == existing.id, db.Patient.patient_code == "DEMO-P001",
            ))
            if patient and patient.current_schedule_version_id:
                return {
                    "trial_id": str(existing.id), "patient_id": str(patient.id),
                    "schedule_version_id": str(patient.current_schedule_version_id),
                }

        trial = db.Trial(
            organization_id=organization_id, protocol_id="UCTSM-DEMO-001",
            study_title="Universal Schedule Interactive Test",
            indication="Demonstration only", sponsor_name="Local UCTSM Demo",
            metadata_json={"demo": True},
        )
        self.session.add(trial)
        self.session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="UCTSM-DEMO-001")
        self.session.add(protocol)
        self.session.flush()
        protocol_version = db.ProtocolVersion(
            protocol_id=protocol.id, version_label="Demo 1.0",
            document_name="uctsm-demo-protocol.txt",
            document_uri="private://uctsm-demo/uctsm-demo-protocol.txt",
            document_hash="d" * 64, uploaded_by=actor_id,
            extraction_status="READY_FOR_REVIEW", metadata_json={"demo": True},
        )
        self.session.add(protocol_version)
        self.session.flush()
        protocol.current_version_id = protocol_version.id
        definition = db.ScheduleDefinition(
            protocol_version_id=protocol_version.id, name="Primary Demo Schedule",
            description="Interactive UCTSM review and patient scheduling test",
            schedule_type="PRIMARY",
        )
        self.session.add(definition)
        self.session.flush()

        evidence = Evidence(
            evidence_type="DEMO_PROTOCOL_TEXT", page_number=84,
            section_title="Safety and Progression Follow-up",
            source_text=(
                "Safety follow-up occurs 30 days after last dose, ±5 days. "
                "Following disease progression, perform assessment within 7 days."
            ),
        )
        last_dose = Anchor(
            code="LAST_DOSE", display_name="Last Dose", anchor_type="LAST_DOSE",
            evidence_refs=[evidence.id],
        )
        progression_anchor = Anchor(
            code="PROGRESSION", display_name="Disease Progression",
            anchor_type="PROGRESSION", evidence_refs=[evidence.id],
        )
        safety = Event(
            code="SAFETY_FOLLOW_UP", protocol_label="Safety Follow-up",
            display_name="Safety Follow-up", event_type="SAFETY_ASSESSMENT",
            timing=NominalWindowTiming(
                nominal=OffsetTiming(
                    reference=AnchorReference(code="LAST_DOSE"),
                    offset=TemporalAmount(value=30, unit="DAY"),
                ),
                window=Window(
                    before=NonNegativeTemporalAmount(value=5, unit="DAY"),
                    after=NonNegativeTemporalAmount(value=5, unit="DAY"),
                ),
            ),
            evidence_refs=[evidence.id],
        )
        progression = Event(
            code="PROGRESSION_ASSESSMENT", protocol_label="Progression Assessment",
            display_name="Progression Assessment", event_type="ASSESSMENT",
            timing=TriggeredTiming(
                trigger=AnchorReference(code="PROGRESSION"),
                timing_after_trigger=TriggerWithin(
                    duration=PositiveTemporalAmount(value=7, unit="DAY"),
                ),
            ),
            conditions=[ComparisonCondition(
                operator="EQUALS", left=FieldOperand(field="patient.progression"),
                right=LiteralOperand(value=True),
            )],
            evidence_refs=[evidence.id],
        )
        claims = []
        for event in (safety, progression):
            claims.extend([
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="EVENT_NAME",
                    claim_entity_type="EVENT", claim_entity_id=event.id,
                    claim_path="display_name", confidence=1,
                ),
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="TIMING",
                    claim_entity_type="EVENT", claim_entity_id=event.id,
                    claim_path="timing", confidence=1,
                ),
            ])
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type="CONDITION",
            claim_entity_type="EVENT", claim_entity_id=progression.id,
            claim_path="conditions", confidence=1,
        ))
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(
                name=definition.name, description=definition.description,
                protocol_version_id=protocol_version.id,
                status=ScheduleStatus.VALIDATION_REQUIRED,
            ),
            anchors=[last_dose, progression_anchor], events=[safety, progression],
            evidence=[evidence], claim_evidence=claims,
        )
        ScheduleRepository(self.session).persist_draft(
            schedule, schedule_definition_id=definition.id,
        )
        patient = db.Patient(
            organization_id=organization_id, trial_id=trial.id,
            patient_code="DEMO-P001",
            current_schedule_version_id=schedule.schedule_version_id,
        )
        self.session.add(patient)
        self.session.flush()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="UCTSM_DEMO_SEEDED", entity_type="TRIAL", entity_id=trial.id,
            before=None, after={
                "schedule_version_id": str(schedule.schedule_version_id),
                "patient_id": str(patient.id),
            },
        ))
        return {
            "trial_id": str(trial.id), "patient_id": str(patient.id),
            "schedule_version_id": str(schedule.schedule_version_id),
        }
