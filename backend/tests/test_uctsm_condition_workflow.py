"""Condition activation as a confirmed, auditable workflow (requirement doc 2).

Doc 2 section 10 is explicit: selecting a condition must NOT immediately restructure
the patient schedule. It must show the effect first, and only a confirmation may
change the patient's dated plan. These tests exercise that end to end against the
database, including the intra-day activity recording path.
"""

from datetime import date, datetime, timezone
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
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    Activity, Anchor, ClaimEvidence, ConditionalAction, ConditionalDefinition,
    ConditionState, Event, Evidence, PatientConditionStatus, ScheduleMetadata,
    ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    ActivityReference, AnchorReference, OffsetTiming, TemporalAmount,
)
from app.services.impact_service import PatientScheduleImpactService
from app.services.schedule_service import PatientScheduleService

HORIZON = date(2027, 6, 30)


def progressed() -> ComparisonCondition:
    return ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="disease_status"),
        right=LiteralOperand(value="PROGRESSION"),
    )


def _schedule(protocol_version_id):
    """Routine visit, an intra-day dosing visit, and a progression-gated follow-up."""
    evidence = Evidence(evidence_type="SECTION", page_number=64, source_text="Protocol rule")
    dose = Activity(
        code="DOSE", protocol_label="Dose", display_name="Study drug administration",
        activity_type="TREATMENT", sequence_number=1, evidence_refs=[evidence.id],
    )
    pk = Activity(
        code="PK_2H", protocol_label="PK +2h", display_name="PK sample 2 hours post-dose",
        activity_type="SAMPLE", sequence_number=2, evidence_refs=[evidence.id],
        timing=OffsetTiming(
            reference=ActivityReference(activity_code="DOSE"),
            offset=TemporalAmount(value=2, unit="HOUR"),
        ),
    )
    events = [
        Event(
            code="C1D1", protocol_label="C1D1", display_name="Cycle 1 Day 1",
            event_type="SITE_VISIT", evidence_refs=[evidence.id],
            timing=OffsetTiming(
                reference=AnchorReference(code="BASELINE"),
                offset=TemporalAmount(value=0, unit="DAY"),
            ),
            activities=[dose, pk],
        ),
        Event(
            code="SURVIVAL_FOLLOWUP", protocol_label="Survival follow-up",
            display_name="Survival follow-up", event_type="SITE_VISIT",
            evidence_refs=[evidence.id],
            timing=OffsetTiming(
                reference=AnchorReference(code="PROGRESSION_DATE"),
                offset=TemporalAmount(value=84, unit="DAY"),
            ),
        ),
    ]
    claims = []
    for event in events:
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path,
            ))
    for activity in (dose, pk):
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type="ACTIVITY", claim_entity_type="ACTIVITY",
            claim_entity_id=activity.id, claim_path="display_name",
        ))
        if activity.timing is not None:
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="ACTIVITY_TIMING",
                claim_entity_type="ACTIVITY", claim_entity_id=activity.id,
                claim_path="timing",
            ))
    conditional = ConditionalDefinition(
        code="DISEASE_PROGRESSION", protocol_label="Disease progression confirmed",
        display_name="Disease progression confirmed", condition=progressed(),
        evidence_refs=[evidence.id],
        actions=[ConditionalAction(
            action_type="ADD_EVENT", target_code="SURVIVAL_FOLLOWUP",
            condition=progressed(),
        )],
    )
    for claim_type, path in (
        ("CONDITION", "condition"), ("CONDITIONAL_ACTION", "actions"),
    ):
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type,
            claim_entity_type="CONDITION", claim_entity_id=conditional.id,
            claim_path=path,
        ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=1, status=ScheduleStatus.APPROVED,
        ),
        anchors=[
            Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE"),
            Anchor(
                code="PROGRESSION_DATE", display_name="Disease progression",
                anchor_type="CONDITION", source_condition_code="DISEASE_PROGRESSION",
            ),
        ],
        events=events,
        conditional_definitions=[conditional],
        evidence=[evidence], claim_evidence=claims,
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
            document_name="protocol.pdf", document_uri="private://protocol.pdf",
            document_hash="a" * 64,
        ),
        db.ScheduleDefinition(
            id=ids["definition"], protocol_version_id=ids["protocol_version"], name="Primary",
        ),
    ])
    session.flush()
    schedule = _schedule(ids["protocol_version"])
    ScheduleRepository(session).persist_draft(
        schedule, schedule_definition_id=ids["definition"])
    patient = db.Patient(
        id=ids["patient"], organization_id=ids["organization"], trial_id=ids["trial"],
        patient_code="P001", current_schedule_version_id=schedule.schedule_version_id,
    )
    session.add(patient)
    session.add(db.PatientAnchor(
        patient_id=patient.id, anchor_definition_id=schedule.anchors[0].id,
        value_date=date(2026, 9, 1), status="CONFIRMED",
    ))
    session.commit()
    return ids, schedule, patient


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        yield active


def by_key(events) -> dict:
    return {item.logical_key: item for item in events}


def test_condition_preview_does_not_change_the_patient_schedule(session):
    ids, schedule, patient = _fixture(session)
    service = PatientScheduleService(session)
    _, before = service.evaluate(
        patient.id, organization_id=ids["organization"], horizon=HORIZON)
    session.commit()
    assert by_key(before)["SURVIVAL_FOLLOWUP:0"].status == "WAITING_FOR_CONDITION"
    assert by_key(before)["SURVIVAL_FOLLOWUP:0"].nominal_start_date is None

    proposal = PatientScheduleImpactService(session).preview(
        patient.id, organization_id=ids["organization"],
        target_schedule_version_id=schedule.schedule_version_id,
        horizon=HORIZON, actor_id=ids["actor"], reason="Progression confirmed by PI",
        condition_change=PatientConditionStatus(
            condition_code="DISEASE_PROGRESSION", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 11, 14),
        ),
    )
    session.commit()

    # The preview shows the consequence...
    followup = next(item for item in proposal.impact["events"]
                    if item["logical_key"] == "SURVIVAL_FOLLOWUP:0")
    assert followup["change"] in {"MOVED", "STATUS_CHANGED"}
    assert followup["after"]["date"] == "2027-02-06"
    assert proposal.impact["condition_change"]["state"] == "ACTIVE"

    # ...but nothing has been applied and no condition has been recorded yet.
    current = by_key(service.current_events(patient.id, schedule.schedule_version_id))
    assert current["SURVIVAL_FOLLOWUP:0"].nominal_start_date is None
    assert session.scalar(select(db.PatientCondition)) is None


def test_confirmation_activates_the_pathway_and_records_an_audit_trail(session):
    ids, schedule, patient = _fixture(session)
    service = PatientScheduleService(session)
    service.evaluate(patient.id, organization_id=ids["organization"], horizon=HORIZON)
    session.commit()

    impact = PatientScheduleImpactService(session)
    proposal = impact.preview(
        patient.id, organization_id=ids["organization"],
        target_schedule_version_id=schedule.schedule_version_id,
        horizon=HORIZON, actor_id=ids["actor"], reason="Progression confirmed by PI",
        condition_change=PatientConditionStatus(
            condition_code="DISEASE_PROGRESSION", state=ConditionState.ACTIVE,
            occurrence_date=date(2026, 11, 14),
        ),
    )
    session.commit()
    _, _, events = impact.confirm(
        proposal.id, organization_id=ids["organization"], actor_id=ids["actor"])
    session.commit()

    assert by_key(events)["SURVIVAL_FOLLOWUP:0"].status == "RESOLVED"
    assert by_key(events)["SURVIVAL_FOLLOWUP:0"].nominal_start_date == date(2027, 2, 6)

    recorded = session.scalar(select(db.PatientCondition))
    assert recorded.state == "ACTIVE"
    assert recorded.occurrence_date == date(2026, 11, 14)
    assert recorded.impact_proposal_id == proposal.id
    audit = session.scalars(select(db.AuditEvent).where(
        db.AuditEvent.action == "PATIENT_CONDITION_STATE_CHANGED")).all()
    assert len(audit) == 1
    assert audit[0].after["condition_code"] == "DISEASE_PROGRESSION"


def test_reversing_a_condition_supersedes_rather_than_deletes_history(session):
    ids, schedule, patient = _fixture(session)
    PatientScheduleService(session).evaluate(
        patient.id, organization_id=ids["organization"], horizon=HORIZON)
    session.commit()
    impact = PatientScheduleImpactService(session)

    def apply(state: ConditionState, occurrence: date | None, reason: str):
        proposal = impact.preview(
            patient.id, organization_id=ids["organization"],
            target_schedule_version_id=schedule.schedule_version_id,
            horizon=HORIZON, actor_id=ids["actor"], reason=reason,
            condition_change=PatientConditionStatus(
                condition_code="DISEASE_PROGRESSION", state=state,
                occurrence_date=occurrence,
            ),
        )
        session.commit()
        result = impact.confirm(
            proposal.id, organization_id=ids["organization"], actor_id=ids["actor"])
        session.commit()
        return result[2]

    apply(ConditionState.ACTIVE, date(2026, 11, 14), "Progression confirmed")
    events = apply(ConditionState.CANCELLED, None, "Entered in error")

    rows = session.scalars(select(db.PatientCondition).order_by(
        db.PatientCondition.recorded_at)).all()
    assert [row.state for row in rows] == ["ACTIVE", "CANCELLED"]
    # The activation row survives, superseded rather than deleted.
    assert rows[0].superseded_by_id == rows[1].id
    assert rows[1].superseded_by_id is None
    # The wrongly activated follow-up returns to inactive with no date.
    followup = by_key(events)["SURVIVAL_FOLLOWUP:0"]
    assert followup.status == "WAITING_FOR_CONDITION"
    assert followup.nominal_start_date is None


def test_recorded_intra_day_actual_drives_the_day_wise_schedule(session):
    ids, schedule, patient = _fixture(session)
    service = PatientScheduleService(session)
    _, events = service.evaluate(
        patient.id, organization_id=ids["organization"], horizon=HORIZON)
    session.commit()

    visit = by_key(events)["C1D1:0"]
    activities = {item.activity_code: item for item in session.scalars(
        select(db.PatientActivity).where(db.PatientActivity.patient_event_id == visit.id))}
    assert activities["PK_2H"].status == "WAITING_FOR_ANCHOR"
    assert activities["PK_2H"].planned_time is None

    service.record_activity(
        patient.id, organization_id=ids["organization"], event_code="C1D1",
        occurrence_index=0, activity_code="DOSE", status="COMPLETED",
        actual_time=datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc),
        actor_id=ids["actor"], reason="Dose administered",
    )
    session.commit()
    _, events = service.evaluate(
        patient.id, organization_id=ids["organization"], horizon=HORIZON)
    session.commit()

    visit = by_key(events)["C1D1:0"]
    activities = {item.activity_code: item for item in session.scalars(
        select(db.PatientActivity).where(db.PatientActivity.patient_event_id == visit.id))}
    assert activities["DOSE"].status == "COMPLETED"
    assert activities["PK_2H"].status == "RESOLVED"
    assert activities["PK_2H"].planned_time.hour == 11
    assert session.scalars(select(db.AuditEvent).where(
        db.AuditEvent.action == "PATIENT_ACTIVITY_RECORDED")).all()


def test_correcting_an_intra_day_actual_supersedes_the_previous_record(session):
    ids, _, patient = _fixture(session)
    service = PatientScheduleService(session)
    for hour in (9, 10):
        service.record_activity(
            patient.id, organization_id=ids["organization"], event_code="C1D1",
            occurrence_index=0, activity_code="DOSE", status="COMPLETED",
            actual_time=datetime(2026, 9, 1, hour, 0, tzinfo=timezone.utc),
            actor_id=ids["actor"], reason="Correction",
        )
    session.commit()
    rows = session.scalars(select(db.PatientActivityRecord).order_by(
        db.PatientActivityRecord.recorded_at)).all()
    assert len(rows) == 2
    assert rows[0].superseded_by_id == rows[1].id
    assert rows[1].superseded_by_id is None
    assert service.patient_conditions(patient.id) == {}


def test_completed_activity_requires_an_actual_time(session):
    ids, _, patient = _fixture(session)
    with pytest.raises(ValueError, match="actual time"):
        PatientScheduleService(session).record_activity(
            patient.id, organization_id=ids["organization"], event_code="C1D1",
            occurrence_index=0, activity_code="DOSE", status="COMPLETED",
            actual_time=None, actor_id=ids["actor"],
        )
