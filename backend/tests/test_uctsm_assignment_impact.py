"""Reassigning a patient between protocol groups (MTB requirement doc 8 s22).

Moving a patient from one arm or cohort to another changes WHICH visits they
have. Applying that directly is the failure mode the whole impact mechanism
exists to prevent: it can remove a visit the patient still needs, or add one
nobody has been told about. So it goes through preview and confirmation, and a
preview that is never confirmed must leave the patient exactly as it found them.
"""

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
    Anchor, ApplicabilityRule, ClaimEvidence, Event, Evidence, ScheduleMetadata,
    ScheduleStatus, StudyDimension, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.services.impact_service import PatientScheduleImpactService
from app.services.schedule_service import PatientScheduleService

BASELINE = date(2026, 9, 1)
HORIZON = date(2027, 6, 30)


def arm_event(code: str, days: int, arm: str | None) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.replace("_", " ").title(),
        event_type="SITE_VISIT",
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=days, unit="DAY"),
        ),
        applicability=(
            [ApplicabilityRule(dimension="ARM", operator="IN", values=[arm])]
            if arm else []
        ),
    )


def _schedule(protocol_version_id) -> UniversalSchedule:
    evidence = Evidence(
        evidence_type="SECTION", page_number=6, source_text="Arm-specific visits")
    events = [
        arm_event("BASELINE_VISIT", 0, None),
        arm_event("ARM_A_ONLY", 14, "ARM_A"),
        arm_event("ARM_B_ONLY", 21, "ARM_B"),
    ]
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type,
                claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path=path, confidence=0.9,
            ))
        if event.applicability:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="APPLICABILITY",
                claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path="applicability", confidence=0.9,
            ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=1, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        arms=[
            StudyDimension(code="ARM_A", protocol_label="Arm A", display_name="Arm A"),
            StudyDimension(code="ARM_B", protocol_label="Arm B", display_name="Arm B"),
        ],
        events=events, evidence=[evidence], claim_evidence=claims,
    )


@pytest.fixture()
def fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids = {name: uuid4() for name in (
            "organization", "trial", "protocol", "protocol_version", "definition",
            "actor",
        )}
        session.add_all([
            db.Trial(id=ids["trial"], organization_id=ids["organization"]),
            db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="P-1"),
            db.ProtocolVersion(
                id=ids["protocol_version"], protocol_id=ids["protocol"],
                version_label="1", document_name="protocol.pdf",
                document_uri="private://protocol.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=ids["definition"], protocol_version_id=ids["protocol_version"],
                name="Primary",
            ),
        ])
        session.flush()
        schedule = _schedule(ids["protocol_version"])
        ScheduleRepository(session).persist_draft(
            schedule, schedule_definition_id=ids["definition"])
        arm_a = session.scalar(select(db.Arm).where(db.Arm.code == "ARM_A"))
        patient = db.Patient(
            organization_id=ids["organization"], trial_id=ids["trial"],
            patient_code="P001", arm_id=arm_a.id,
            current_schedule_version_id=schedule.schedule_version_id,
        )
        session.add(patient)
        session.flush()
        session.add(db.PatientAnchor(
            patient_id=patient.id, anchor_definition_id=schedule.anchors[0].id,
            value_date=BASELINE, status="CONFIRMED",
        ))
        session.commit()
        PatientScheduleService(session).evaluate(
            patient.id, organization_id=ids["organization"], horizon=HORIZON)
        session.commit()
        yield session, ids, patient, schedule
    engine.dispose()


def preview(session, ids, patient, assignment, **changes):
    values = {
        "organization_id": ids["organization"],
        "target_schedule_version_id": patient.current_schedule_version_id,
        "horizon": HORIZON, "actor_id": ids["actor"], "reason": "arm correction",
        "assignment_change": assignment,
    }
    values.update(changes)
    return PatientScheduleImpactService(session).preview(patient.id, **values)


def event_codes(session, patient) -> set[str]:
    """Visits the patient's CURRENT evaluation still expects them to attend."""
    schedule_row = session.scalar(
        select(db.PatientSchedule)
        .where(db.PatientSchedule.patient_id == patient.id,
               db.PatientSchedule.status == "ACTIVE")
        .order_by(db.PatientSchedule.generated_at.desc())
    )
    rows = session.execute(
        select(db.Event.code, db.PatientEvent.status)
        .join(db.PatientEvent, db.PatientEvent.event_definition_id == db.Event.id)
        .where(
            db.PatientEvent.schedule_evaluation_id == schedule_row.current_evaluation_id)
    )
    return {
        code for code, status in rows
        if status not in {"NOT_APPLICABLE", "CANCELLED"}
    }


def test_a_preview_shows_the_change_without_applying_it(fixture):
    session, ids, patient, _ = fixture
    before_arm = patient.arm_id

    proposal = preview(session, ids, patient, {"ARM": "ARM_B"})
    session.commit()
    session.refresh(patient)

    assert proposal.status == "PENDING"
    assert proposal.impact["assignment_change"] == {"ARM": "ARM_B"}
    # Nothing moved: a question is not an instruction.
    assert patient.arm_id == before_arm


def test_confirming_applies_the_assignment_and_the_visits_that_follow(fixture):
    session, ids, patient, _ = fixture
    assert "ARM_A_ONLY" in event_codes(session, patient)

    proposal = preview(session, ids, patient, {"ARM": "ARM_B"})
    session.commit()
    PatientScheduleImpactService(session).confirm(
        proposal.id, organization_id=ids["organization"], actor_id=ids["actor"],
        reason="randomisation corrected",
    )
    session.commit()
    session.refresh(patient)

    arm_b = session.scalar(select(db.Arm).where(db.Arm.code == "ARM_B"))
    assert patient.arm_id == arm_b.id
    codes = event_codes(session, patient)
    assert "ARM_B_ONLY" in codes
    assert "ARM_A_ONLY" not in codes
    assert "BASELINE_VISIT" in codes


def test_the_assignment_change_is_audited_with_what_it_replaced(fixture):
    session, ids, patient, _ = fixture
    proposal = preview(session, ids, patient, {"ARM": "ARM_B"})
    session.commit()
    PatientScheduleImpactService(session).confirm(
        proposal.id, organization_id=ids["organization"], actor_id=ids["actor"],
        reason="randomisation corrected",
    )
    session.commit()

    audit = session.scalar(select(db.AuditEvent).where(
        db.AuditEvent.action == "PATIENT_ASSIGNMENT_CHANGED"))
    assert audit is not None
    assert audit.before["arm_id"] != audit.after["arm_id"]
    assert audit.after["reason"] == "randomisation corrected"


def test_an_arm_the_protocol_does_not_define_is_refused(fixture):
    """Doc 8 s30: an unknown group must not quietly become 'no group'."""
    session, ids, patient, _ = fixture

    with pytest.raises(ValueError, match="not a defined ARM"):
        preview(session, ids, patient, {"ARM": "ARM_Z"})


def test_an_undefined_dimension_is_refused(fixture):
    session, ids, patient, _ = fixture

    with pytest.raises(ValueError, match="not a dimension this protocol defines"):
        preview(session, ids, patient, {"PLANET": "MARS"})


def test_a_reassignment_confirmed_after_the_patient_moved_is_stale(fixture):
    """Someone else reassigned them in between; the numbers on screen are wrong."""
    session, ids, patient, _ = fixture
    proposal = preview(session, ids, patient, {"ARM": "ARM_B"})
    session.commit()

    arm_b = session.scalar(select(db.Arm).where(db.Arm.code == "ARM_B"))
    patient.arm_id = arm_b.id
    session.commit()

    with pytest.raises(ValueError, match="generate a new impact preview"):
        PatientScheduleImpactService(session).confirm(
            proposal.id, organization_id=ids["organization"], actor_id=ids["actor"])
