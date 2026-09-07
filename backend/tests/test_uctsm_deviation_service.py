"""Deviation reporting end to end against the database.

Proves the derived report reads the patient's OWN pinned schedule version, uses the
recalculated protocol-expected date, and refuses to assess anything that was never
an active requirement for that patient.
"""

from datetime import date, datetime, timezone
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
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.deviation import DeviationType
from app.domain.schedule.models import (
    Activity, Anchor, ClaimEvidence, ConditionalAction, ConditionalDefinition, Event,
    Evidence, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    ActivityReference, AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount,
    OffsetTiming, TemporalAmount, Window,
)
from app.services.deviation_service import DeviationService
from app.services.schedule_service import PatientScheduleService

HORIZON = date(2027, 6, 30)
BASELINE = date(2026, 9, 1)


def windowed(days: int, tolerance: int) -> NominalWindowTiming:
    return NominalWindowTiming(
        nominal=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=days, unit="DAY"),
        ),
        window=Window(
            before=NonNegativeTemporalAmount(value=tolerance, unit="DAY"),
            after=NonNegativeTemporalAmount(value=tolerance, unit="DAY"),
        ),
    )


def _schedule(protocol_version_id):
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=8, source_text="Week 4 +/-2 days")
    dose = Activity(
        code="DOSE", protocol_label="Dose", display_name="Dose",
        activity_type="TREATMENT", sequence_number=1, evidence_refs=[evidence.id],
    )
    pk = Activity(
        code="PK_2H", protocol_label="PK +2h", display_name="PK +2h",
        activity_type="SAMPLE", sequence_number=2, evidence_refs=[evidence.id],
        timing=NominalWindowTiming(
            nominal=OffsetTiming(
                reference=ActivityReference(activity_code="DOSE"),
                offset=TemporalAmount(value=2, unit="HOUR"),
            ),
            window=Window(
                before=NonNegativeTemporalAmount(value=10, unit="MINUTE"),
                after=NonNegativeTemporalAmount(value=10, unit="MINUTE"),
            ),
        ),
    )
    progressed = ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="disease_status"),
        right=LiteralOperand(value="PROGRESSION"),
    )
    events = [
        Event(
            code="C1D1", protocol_label="C1D1", display_name="Cycle 1 Day 1",
            event_type="SITE_VISIT", timing=windowed(0, 1),
            evidence_refs=[evidence.id], activities=[dose, pk],
        ),
        Event(
            code="WEEK_4", protocol_label="Week 4", display_name="Week 4",
            event_type="SITE_VISIT", timing=windowed(28, 2), evidence_refs=[evidence.id],
        ),
        Event(
            code="SURVIVAL_FOLLOWUP", protocol_label="Survival follow-up",
            display_name="Survival follow-up", event_type="SITE_VISIT",
            timing=windowed(84, 7), evidence_refs=[evidence.id],
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
    definition = ConditionalDefinition(
        code="DISEASE_PROGRESSION", protocol_label="Disease progression",
        display_name="Disease progression confirmed", condition=progressed,
        evidence_refs=[evidence.id],
        actions=[ConditionalAction(
            action_type="ADD_EVENT", target_code="SURVIVAL_FOLLOWUP", condition=progressed)],
    )
    claims.extend(
        ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type,
            claim_entity_type="CONDITION", claim_entity_id=definition.id,
        )
        for claim_type in ("CONDITION", "CONDITIONAL_ACTION")
    )
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=1, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, conditional_definitions=[definition],
        evidence=[evidence], claim_evidence=claims,
    )


@pytest.fixture()
def fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids = {name: uuid4() for name in (
            "organization", "trial", "protocol", "protocol_version", "definition",
            "patient", "actor",
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
                id=ids["definition"], protocol_version_id=ids["protocol_version"],
                name="Primary",
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
            value_date=BASELINE, status="CONFIRMED",
        ))
        session.commit()
        yield session, ids, patient


def by_key(items) -> dict:
    return {item.logical_key: item for item in items}


def evaluate(session, ids, patient):
    events = PatientScheduleService(session).evaluate(
        patient.id, organization_id=ids["organization"], horizon=HORIZON)[1]
    session.commit()
    return by_key(events)


def record_actual(session, event_row, actual: date):
    session.add(db.PatientEventOccurrence(
        patient_event_id=event_row.id, occurrence_type="VISIT",
        scheduled_date=event_row.nominal_start_date, actual_date=actual,
        status="COMPLETED",
    ))
    event_row.status = "COMPLETED"
    session.commit()


def test_visit_outside_its_window_is_reported_with_the_version_window(fixture):
    session, ids, patient = fixture
    events = evaluate(session, ids, patient)
    # Week 4 is Day 29 (1-Sep + 28) with a +/-2 day window: 27-Sep to 1-Oct.
    week_4 = events["WEEK_4:0"]
    assert week_4.nominal_start_date == date(2026, 9, 29)
    record_actual(session, week_4, date(2026, 10, 3))

    report = DeviationService(session).report(
        patient.id, organization_id=ids["organization"], today=date(2026, 10, 10))
    finding = by_key(report.visits)["WEEK_4:0"]

    assert finding.deviation_type == DeviationType.LATE
    assert finding.outside_window_days == 2
    assert finding.delta_from_planned_days == 4
    assert report.schedule_version_id == str(patient.current_schedule_version_id)


def test_a_visit_inside_its_window_is_not_a_deviation(fixture):
    session, ids, patient = fixture
    events = evaluate(session, ids, patient)
    record_actual(session, events["WEEK_4:0"], date(2026, 10, 1))

    report = DeviationService(session).report(
        patient.id, organization_id=ids["organization"], today=date(2026, 10, 10))
    finding = by_key(report.visits)["WEEK_4:0"]
    assert finding.deviation_type == DeviationType.NONE
    assert finding.within_window is True
    # Week 4 is clean, so it is absent from the deviation list. (C1D1 legitimately
    # appears there: its window closed with no actual recorded.)
    assert "WEEK_4:0" not in {item.logical_key for item in report.deviations()}


def test_an_unactivated_conditional_visit_is_never_reported_as_deviated(fixture):
    session, ids, patient = fixture
    evaluate(session, ids, patient)

    # Assess long after the follow-up would nominally have been due.
    report = DeviationService(session).report(
        patient.id, organization_id=ids["organization"], today=date(2027, 3, 1))
    finding = by_key(report.visits)["SURVIVAL_FOLLOWUP:0"]

    assert finding.deviation_type == DeviationType.NOT_ASSESSABLE
    assert "never activated" in finding.reason
    assert finding.logical_key not in {item.logical_key for item in report.deviations()}


def test_a_visit_whose_window_closed_without_an_actual_is_missed(fixture):
    session, ids, patient = fixture
    evaluate(session, ids, patient)

    report = DeviationService(session).report(
        patient.id, organization_id=ids["organization"], today=date(2026, 10, 20))
    assert by_key(report.visits)["WEEK_4:0"].deviation_type == DeviationType.MISSED


def test_activity_timing_deviation_is_reported_from_the_recorded_actual(fixture):
    session, ids, patient = fixture
    service = PatientScheduleService(session)
    evaluate(session, ids, patient)

    service.record_activity(
        patient.id, organization_id=ids["organization"], event_code="C1D1",
        occurrence_index=0, activity_code="DOSE", status="COMPLETED",
        actual_time=datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc), actor_id=ids["actor"])
    service.record_activity(
        patient.id, organization_id=ids["organization"], event_code="C1D1",
        occurrence_index=0, activity_code="PK_2H", status="COMPLETED",
        actual_time=datetime(2026, 9, 1, 11, 48, tzinfo=timezone.utc), actor_id=ids["actor"])
    session.commit()
    evaluate(session, ids, patient)

    report = DeviationService(session).report(
        patient.id, organization_id=ids["organization"], today=date(2026, 9, 2))
    pk = next(item for item in report.activities if item.activity_code == "PK_2H")

    # Dose 09:30 -> PK expected 11:30, window 11:20-11:40, actual 11:48.
    assert pk.deviation_type == DeviationType.LATE
    assert pk.outside_window_minutes == 8
    assert pk.delta_from_planned_minutes == 18
