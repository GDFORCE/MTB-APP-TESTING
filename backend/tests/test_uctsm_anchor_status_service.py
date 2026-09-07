"""Anchor status derived from stored records (MTB requirement doc 1 section 22).

The pure vocabulary rules live in test_uctsm_anchor_status.py. This proves the
service reads the patient's own schedule and records: that an unconfirmed
candidate is not treated as the anchor's value, and that an anchor no visit this
patient will have is reported NOT_REQUIRED rather than sitting on a site's to-do
list forever.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.anchor_status import AnchorStatus, AnchorStatusView
from app.domain.schedule.models import (
    Anchor, ApplicabilityRule, ClaimEvidence, Event, Evidence, ScheduleMetadata,
    ScheduleStatus, StudyDimension, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.services.anchor_status_service import AnchorStatusService

BASELINE = date(2026, 9, 1)


def offset_event(code: str, anchor_code: str, days: int, **changes) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.replace("_", " ").title(),
        event_type="SITE_VISIT",
        timing=OffsetTiming(
            reference=AnchorReference(code=anchor_code),
            offset=TemporalAmount(value=days, unit="DAY"),
        ),
        **changes,
    )


def _schedule(protocol_version_id) -> UniversalSchedule:
    """Two arms: only the surgical arm depends on the SURGERY anchor."""
    evidence = Evidence(
        evidence_type="SECTION", page_number=11, source_text="Protocol rule")
    surgical = StudyDimension(
        code="SURGICAL", protocol_label="Surgical", display_name="Surgical arm")
    medical = StudyDimension(
        code="MEDICAL", protocol_label="Medical", display_name="Medical arm")
    events = [
        offset_event("BASELINE_VISIT", "BASELINE", 0),
        offset_event(
            "POST_OP", "SURGERY", 30,
            applicability=[ApplicabilityRule(
                dimension="ARM", operator="IN", values=["SURGICAL"])],
        ),
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
        anchors=[
            Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE"),
            Anchor(code="SURGERY", display_name="Surgery Date", anchor_type="SURGERY",
                   source_event_code="SURGERY_PROCEDURE"),
        ],
        arms=[surgical, medical], events=events,
        evidence=[evidence], claim_evidence=claims,
    )


@pytest.fixture()
def fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids = {name: uuid4() for name in (
            "organization", "trial", "protocol", "protocol_version", "definition",
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
        session.commit()
        yield session, ids, schedule
    engine.dispose()


def make_patient(session, ids, schedule, *, arm_code: str | None = None) -> db.Patient:
    arm_id = None
    if arm_code is not None:
        arm_id = session.scalar(
            db.Arm.__table__.select().where(db.Arm.code == arm_code).with_only_columns(
                db.Arm.id)
        )
    patient = db.Patient(
        organization_id=ids["organization"], trial_id=ids["trial"],
        patient_code=f"P{uuid4().hex[:6]}", arm_id=arm_id,
        current_schedule_version_id=schedule.schedule_version_id,
    )
    session.add(patient)
    session.commit()
    return patient


def add_anchor(session, schedule, patient, code, **changes):
    anchor = next(item for item in schedule.anchors if item.code == code)
    values = {
        "patient_id": patient.id, "anchor_definition_id": anchor.id,
        "status": "PROVISIONAL",
        "recorded_at": datetime.now(timezone.utc),
    }
    values.update(changes)
    row = db.PatientAnchor(**values)
    session.add(row)
    session.commit()
    return row


def statuses(session, ids, patient) -> dict[str, AnchorStatusView]:
    return {
        item.anchor_code: item
        for item in AnchorStatusService(session).statuses(
            patient.id, organization_id=ids["organization"])
    }


def test_an_unset_anchor_names_the_event_it_waits_on(fixture):
    session, ids, schedule = fixture
    patient = make_patient(session, ids, schedule, arm_code="SURGICAL")

    view = statuses(session, ids, patient)["SURGERY"]
    assert view.status == AnchorStatus.AWAITING_EVENT
    assert view.awaiting_event_code == "SURGERY_PROCEDURE"


def test_an_anchor_only_another_arm_needs_is_not_required(fixture):
    """The medical arm has no post-operative visit, so no surgery date is wanted."""
    session, ids, schedule = fixture
    patient = make_patient(session, ids, schedule, arm_code="MEDICAL")

    assert statuses(session, ids, patient)["SURGERY"].status == AnchorStatus.NOT_REQUIRED
    # The anchor the patient's own visits use is still required.
    assert statuses(session, ids, patient)["BASELINE"].status != AnchorStatus.NOT_REQUIRED


def test_a_planned_date_is_planned_even_though_it_is_stored(fixture):
    session, ids, schedule = fixture
    patient = make_patient(session, ids, schedule, arm_code="SURGICAL")
    add_anchor(session, schedule, patient, "SURGERY",
               value_date=date(2026, 10, 1), source_type="PLANNED_PROCEDURE")

    view = statuses(session, ids, patient)["SURGERY"]
    assert view.status == AnchorStatus.PLANNED
    assert view.value == date(2026, 10, 1)


def test_an_unconfirmed_candidate_is_not_treated_as_the_anchor_value(fixture):
    """Doc 1 s10 and s16: a candidate needs confirmation before it takes effect."""
    session, ids, schedule = fixture
    patient = make_patient(session, ids, schedule, arm_code="SURGICAL")
    add_anchor(session, schedule, patient, "SURGERY",
               value_date=date(2026, 10, 1), status="PENDING_CONFIRMATION",
               source_type="SOURCE_DOCUMENT")

    view = statuses(session, ids, patient)["SURGERY"]
    assert view.status == AnchorStatus.AWAITING_EVENT
    assert view.value is None


def test_a_confirmed_anchor_reads_as_confirmed(fixture):
    session, ids, schedule = fixture
    patient = make_patient(session, ids, schedule, arm_code="SURGICAL")
    add_anchor(session, schedule, patient, "SURGERY",
               value_date=date(2026, 10, 3), status="CONFIRMED",
               source_type="SOURCE_DOCUMENT")

    view = statuses(session, ids, patient)["SURGERY"]
    assert view.status == AnchorStatus.CONFIRMED
    assert view.confirmed is True


def test_changing_a_confirmed_anchor_reads_as_corrected(fixture):
    session, ids, schedule = fixture
    patient = make_patient(session, ids, schedule, arm_code="SURGICAL")
    now = datetime.now(timezone.utc)
    add_anchor(session, schedule, patient, "SURGERY",
               value_date=date(2026, 10, 3), status="CONFIRMED",
               source_type="SOURCE_DOCUMENT", recorded_at=now)
    add_anchor(session, schedule, patient, "SURGERY",
               value_date=date(2026, 10, 5), status="CONFIRMED",
               source_type="SOURCE_DOCUMENT", recorded_at=now + timedelta(days=1))

    view = statuses(session, ids, patient)["SURGERY"]
    assert view.status == AnchorStatus.CORRECTED
    assert view.value == date(2026, 10, 5)


def test_one_patients_anchor_does_not_change_another_patients_status(fixture):
    session, ids, schedule = fixture
    first = make_patient(session, ids, schedule, arm_code="SURGICAL")
    second = make_patient(session, ids, schedule, arm_code="SURGICAL")
    add_anchor(session, schedule, first, "SURGERY",
               value_date=date(2026, 10, 3), status="CONFIRMED",
               source_type="SOURCE_DOCUMENT")

    assert statuses(session, ids, first)["SURGERY"].status == AnchorStatus.CONFIRMED
    assert statuses(session, ids, second)["SURGERY"].status == AnchorStatus.AWAITING_EVENT
