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
from app.services.impact_service import PatientScheduleImpactService
from app.services.schedule_service import PatientScheduleService


def _schedule(protocol_version_id, version_number: int, day: int, *, add_extra=False):
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=10, source_text=f"Day {day}")
    events = [Event(
        code="VISIT_1", protocol_label="Visit 1", display_name="Visit 1", event_type="VISIT",
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=day, unit="DAY"),
        ),
        evidence_refs=[evidence.id],
    )]
    if add_extra:
        events.append(Event(
            code="VISIT_2", protocol_label="Visit 2", display_name="Visit 2", event_type="VISIT",
            timing=OffsetTiming(
                reference=AnchorReference(code="BASELINE"),
                offset=TemporalAmount(value=day + 7, unit="DAY"),
            ),
            evidence_refs=[evidence.id],
        ))
    claims = []
    for event in events:
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path,
            ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=version_number, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, evidence=[evidence], claim_evidence=claims,
    )


def _fixture(session: Session):
    ids = {name: uuid4() for name in (
        "organization", "trial", "protocol", "protocol_version", "definition", "patient", "actor",
    )}
    session.add_all([
        db.Trial(id=ids["trial"], organization_id=ids["organization"]),
        db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="P-1"),
        db.ProtocolVersion(
            id=ids["protocol_version"], protocol_id=ids["protocol"], version_label="1",
            document_name="protocol.pdf", document_uri="private://protocol.pdf", document_hash="a" * 64,
        ),
        db.ScheduleDefinition(
            id=ids["definition"], protocol_version_id=ids["protocol_version"], name="Primary",
        ),
    ])
    session.flush()
    source = _schedule(ids["protocol_version"], 1, 7)
    target = _schedule(ids["protocol_version"], 2, 10, add_extra=True)
    repository = ScheduleRepository(session)
    repository.persist_draft(source, schedule_definition_id=ids["definition"])
    repository.persist_draft(target, schedule_definition_id=ids["definition"])
    patient = db.Patient(
        id=ids["patient"], organization_id=ids["organization"], trial_id=ids["trial"],
        patient_code="P001", current_schedule_version_id=source.schedule_version_id,
    )
    session.add(patient)
    session.add(db.PatientAnchor(
        patient_id=patient.id, anchor_definition_id=source.anchors[0].id,
        value_date=date(2026, 1, 1), status="CONFIRMED",
    ))
    session.commit()
    return ids, source, target, patient


def test_preview_and_confirm_preserve_actual_history_and_assign_version():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids, source, target, patient = _fixture(session)
        _, source_events = PatientScheduleService(session).evaluate(
            patient.id, organization_id=ids["organization"], horizon=date(2026, 12, 31),
        )
        source_event = source_events[0]
        source_event.status = "COMPLETED"
        session.add(db.PatientEventOccurrence(
            patient_event_id=source_event.id, occurrence_type="VISIT",
            scheduled_date=source_event.nominal_start_date, actual_date=date(2026, 1, 9),
            status="COMPLETED",
        ))
        session.commit()

        service = PatientScheduleImpactService(session)
        proposal = service.preview(
            patient.id, organization_id=ids["organization"],
            target_schedule_version_id=target.schedule_version_id,
            horizon=date(2026, 12, 31), actor_id=ids["actor"], reason="Protocol amendment",
        )
        session.commit()
        assert proposal.impact["summary"]["PROTECTED"] == 1
        assert proposal.impact["summary"]["ADDED"] == 1

        _, _, current = service.confirm(
            proposal.id, organization_id=ids["organization"], actor_id=ids["actor"],
        )
        session.commit()
        session.refresh(patient)
        assert patient.current_schedule_version_id == target.schedule_version_id
        protected = next(item for item in current if item.logical_key == "VISIT_1:0")
        assert protected.status == "COMPLETED"
        assert protected.nominal_start_date == source_event.nominal_start_date
        assert protected.logical_occurrence_id == source_event.logical_occurrence_id
        assert protected.protected_history is True
        assignment = session.scalar(select(db.PatientScheduleAssignment).where(
            db.PatientScheduleAssignment.impact_proposal_id == proposal.id,
        ))
        assert assignment is not None
        assert assignment.previous_schedule_version_id == source.schedule_version_id


def test_changed_patient_inputs_make_confirmation_stale():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids, source, target, patient = _fixture(session)
        PatientScheduleService(session).evaluate(
            patient.id, organization_id=ids["organization"], horizon=date(2026, 12, 31),
        )
        session.commit()
        service = PatientScheduleImpactService(session)
        proposal = service.preview(
            patient.id, organization_id=ids["organization"],
            target_schedule_version_id=target.schedule_version_id,
            horizon=date(2026, 12, 31), actor_id=ids["actor"], reason="Protocol amendment",
        )
        session.commit()
        session.add(db.PatientState(
            patient_id=patient.id, state_code="new_fact", state_value=True,
            effective_at=db.func.now(),
        ))
        session.commit()

        with pytest.raises(ValueError, match="new impact preview"):
            service.confirm(
                proposal.id, organization_id=ids["organization"], actor_id=ids["actor"],
            )
        assert proposal.status == "STALE"


def test_anchor_candidate_moves_future_events_only_after_confirmed_impact():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids, source, _, patient = _fixture(session)
        _, initial = PatientScheduleService(session).evaluate(
            patient.id, organization_id=ids["organization"],
            horizon=date(2026, 12, 31),
        )
        candidate = db.PatientAnchor(
            patient_id=patient.id, anchor_definition_id=source.anchors[0].id,
            value_date=date(2026, 1, 3), status="PENDING_CONFIRMATION",
            source_type="PATIENT_EVENT_OCCURRENCE",
        )
        session.add(candidate)
        session.commit()

        # Pending clinical data is not executable input.
        pending_result = PatientScheduleService(session).evaluate_result(
            patient, source.schedule_version_id, horizon=date(2026, 12, 31))
        assert pending_result.events[0].timing.nominal_start == date(2026, 1, 8)

        service = PatientScheduleImpactService(session)
        proposal = service.preview(
            patient.id, organization_id=ids["organization"],
            target_schedule_version_id=source.schedule_version_id,
            horizon=date(2026, 12, 31), actor_id=ids["actor"],
            reason="Confirm corrected baseline", anchor_candidate=candidate,
        )
        session.commit()
        assert proposal.impact["summary"]["MOVED"] == 1
        assert proposal.impact["anchor_change"]["candidate_status"] == "PENDING_CONFIRMATION"

        _, _, current = service.confirm(
            proposal.id, organization_id=ids["organization"], actor_id=ids["actor"],
        )
        session.commit()
        session.refresh(candidate)
        assert candidate.status == "CONFIRMED"
        assert current[0].nominal_start_date == date(2026, 1, 10)
        assert current[0].logical_occurrence_id == initial[0].logical_occurrence_id
