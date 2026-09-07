from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.services.operational_bridge import OperationalBridgeService


def _schedule(protocol_version_id, version):
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Baseline and Day 8")
    event = Event(
        code="DAY_8", protocol_label="Day 8", display_name="Day 8", event_type="SITE_VISIT",
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=7 if version == 1 else 9, unit="DAY"),
        ), evidence_refs=[evidence.id],
    )
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=version, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], evidence=[evidence], claim_evidence=[
            ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path,
            ) for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
        ],
    )


def test_operational_enrolment_is_linked_pinned_and_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization_id, trial_id, protocol_id = uuid4(), uuid4(), uuid4()
        protocol_version_id, definition_id, actor_id = uuid4(), uuid4(), uuid4()
        session.add_all([
            db.Trial(id=trial_id, organization_id=organization_id),
            db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="P-1"),
            db.ProtocolVersion(
                id=protocol_version_id, protocol_id=protocol_id, version_label="1",
                document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=definition_id, protocol_version_id=protocol_version_id, name="Primary",
            ),
        ])
        session.flush()
        v1 = _schedule(protocol_version_id, 1)
        ScheduleRepository(session).persist_draft(v1, schedule_definition_id=definition_id)
        session.commit()
        bridge = OperationalBridgeService(session)
        linked = bridge.validate_trial_link(
            organization_id=organization_id, external_trial_id="mongo-trial-1",
            external_schedule_definition_id="mongo-schedule-1",
            trial_id=trial_id, schedule_definition_id=definition_id,
        )
        first = bridge.enroll_patient(
            organization_id=organization_id, actor_id=actor_id,
            canonical_trial_id=trial_id, schedule_definition_id=definition_id,
            external_patient_id="mongo-patient-1", patient_code="P001",
            baseline_date=date(2026, 1, 1), horizon=date(2026, 12, 31),
        )
        session.commit()

        assert linked["schedule_version_id"] == str(v1.schedule_version_id)
        assert first.schedule_version_id == v1.schedule_version_id
        assert first.events[0].nominal_date == date(2026, 1, 8)
        same = bridge.enroll_patient(
            organization_id=organization_id, actor_id=actor_id,
            canonical_trial_id=trial_id, schedule_definition_id=definition_id,
            external_patient_id="mongo-patient-1", patient_code="P001",
            baseline_date=date(2026, 1, 1), horizon=date(2026, 12, 31),
        )
        assert same.patient_id == first.patient_id
        assert same.evaluation_id == first.evaluation_id
        assert session.scalar(select(db.Patient).where(
            db.Patient.external_patient_id == "mongo-patient-1",
        )).current_schedule_version_id == v1.schedule_version_id


def test_new_approved_version_does_not_silently_move_existing_linked_patient():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization_id, trial_id, protocol_id = uuid4(), uuid4(), uuid4()
        protocol_version_id, definition_id, actor_id = uuid4(), uuid4(), uuid4()
        session.add_all([
            db.Trial(id=trial_id, organization_id=organization_id),
            db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="P-1"),
            db.ProtocolVersion(
                id=protocol_version_id, protocol_id=protocol_id, version_label="1",
                document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=definition_id, protocol_version_id=protocol_version_id, name="Primary",
            ),
        ])
        session.flush()
        repository = ScheduleRepository(session)
        v1 = _schedule(protocol_version_id, 1)
        repository.persist_draft(v1, schedule_definition_id=definition_id)
        bridge = OperationalBridgeService(session)
        enrolled = bridge.enroll_patient(
            organization_id=organization_id, actor_id=actor_id,
            canonical_trial_id=trial_id, schedule_definition_id=definition_id,
            external_patient_id="mongo-patient-1", patient_code="P001",
            baseline_date=date(2026, 1, 1), horizon=date(2026, 12, 31),
        )
        v2 = _schedule(protocol_version_id, 2)
        repository.persist_draft(v2, schedule_definition_id=definition_id)
        session.commit()

        with pytest.raises(ValueError, match="impact preview/confirm"):
            bridge.enroll_patient(
                organization_id=organization_id, actor_id=actor_id,
                canonical_trial_id=trial_id, schedule_definition_id=definition_id,
                external_patient_id="mongo-patient-1", patient_code="P001",
                baseline_date=date(2026, 1, 1), horizon=date(2026, 12, 31),
            )
        patient = session.get(db.Patient, enrolled.patient_id)
        assert patient.current_schedule_version_id == v1.schedule_version_id
