"""PI/CRC dashboard behaviour.

Sources: doc 2 s13, doc 3 s28, doc 5 s25, doc 10 s24.

The negative requirement matters most: the board must show ACTIONS, not the
schedule. An open-ended protocol must not put hundreds of future cycles on it, and
an untriggered conditional must not appear at all.
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
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, ConditionalAction, ConditionalDefinition, Event, Evidence,
    RecurrenceRule, RecurrenceTermination, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount, OffsetTiming,
    PositiveTemporalAmount, TemporalAmount, TimeUnit, Window,
)
from app.services.dashboard_service import ActionCategory, DashboardService
from app.services.schedule_service import PatientScheduleService

TODAY = date(2026, 9, 15)
BASELINE = date(2026, 9, 1)
HORIZON = date(2028, 12, 31)


def windowed(days: int, tolerance: int) -> NominalWindowTiming:
    return NominalWindowTiming(
        nominal=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=days, unit=TimeUnit.DAY),
        ),
        window=Window(
            before=NonNegativeTemporalAmount(value=tolerance, unit=TimeUnit.DAY),
            after=NonNegativeTemporalAmount(value=tolerance, unit=TimeUnit.DAY),
        ),
    )


def _schedule(protocol_version_id):
    """An open-ended q21d treatment plus a conditional follow-up."""
    evidence = Evidence(evidence_type="SECTION", page_number=12, source_text="Schedule")
    progressed = ComparisonCondition(
        operator="EQUALS", left=FieldOperand(field="disease_status"),
        right=LiteralOperand(value="PROGRESSION"),
    )
    treatment = Event(
        code="TREATMENT", protocol_label="Treatment", display_name="Treatment visit",
        event_type="SITE_VISIT", timing=windowed(0, 2), evidence_refs=[evidence.id],
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=21, unit=TimeUnit.DAY),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="HORIZON"),
        ),
    )
    phone = Event(
        code="DAY7_CALL", protocol_label="Day 7 follow-up",
        display_name="Day 7 safety follow-up", event_type="PHONE_CONTACT",
        timing=windowed(6, 1), evidence_refs=[evidence.id],
    )
    followup = Event(
        code="SURVIVAL_FOLLOWUP", protocol_label="Survival follow-up",
        display_name="Survival follow-up", event_type="SITE_VISIT",
        timing=windowed(3, 1), evidence_refs=[evidence.id],
    )
    events = [treatment, phone, followup]
    claims = []
    for event in events:
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path,
            ))
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="RECURRENCE", claim_entity_type="EVENT",
        claim_entity_id=treatment.id, claim_path="recurrence",
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
        PatientScheduleService(session).evaluate(
            patient.id, organization_id=ids["organization"], horizon=HORIZON)
        session.commit()
        yield session, ids, schedule, patient


def board(session, ids, today=TODAY):
    return DashboardService(session).actions(
        organization_id=ids["organization"], trial_id=ids["trial"], today=today)


def test_open_ended_schedule_does_not_flood_the_board_with_future_cycles(fixture):
    session, ids, _, patient = fixture
    # The evaluation generated cycles out to 2028; only the near ones may appear.
    total_events = session.query(db.PatientEvent).count()
    assert total_events > 30

    result = board(session, ids)
    visit_items = (result.by_category(ActionCategory.VISIT_DUE)
                   + result.by_category(ActionCategory.VISIT_OVERDUE))
    assert len(visit_items) <= 4, [item.message for item in visit_items]
    for item in visit_items:
        assert item.due_date <= TODAY + timedelta(days=7)


def test_untriggered_conditional_never_appears_on_the_board(fixture):
    session, ids, _, _ = fixture
    result = board(session, ids)
    assert not any("Survival" in item.message for item in result.actions)
    assert result.by_category(ActionCategory.CONDITION_ACTIVE) == []


def test_a_pending_anchor_confirmation_is_the_top_action(fixture):
    session, ids, schedule, patient = fixture
    session.add(db.PatientAnchor(
        patient_id=patient.id, anchor_definition_id=schedule.anchors[0].id,
        value_date=date(2026, 12, 15), status="PENDING_CONFIRMATION",
    ))
    session.commit()

    result = board(session, ids)
    top = result.actions[0]
    assert top.category in {
        ActionCategory.IMPACT_CONFIRMATION, ActionCategory.ANCHOR_CONFIRMATION}
    anchor_action = result.by_category(ActionCategory.ANCHOR_CONFIRMATION)[0]
    assert "confirm to update dependent visits" in anchor_action.message
    assert anchor_action.target_type == "PATIENT_ANCHOR"


def test_an_active_condition_asks_to_be_resolved(fixture):
    session, ids, _, patient = fixture
    session.add(db.PatientCondition(
        patient_id=patient.id, condition_code="ANC_LOW", state="ACTIVE",
        occurrence_index=0, occurrence_date=date(2026, 9, 10),
    ))
    session.commit()

    actions = board(session, ids).by_category(ActionCategory.CONDITION_ACTIVE)
    assert len(actions) == 1
    assert actions[0].message.startswith("Anc Low active")
    assert actions[0].target_type == "PATIENT_CONDITION"


def test_a_condition_awaiting_confirmation_outranks_an_active_one(fixture):
    session, ids, _, patient = fixture
    session.add_all([
        db.PatientCondition(
            patient_id=patient.id, condition_code="ANC_LOW", state="ACTIVE",
            occurrence_index=0, occurrence_date=date(2026, 9, 10)),
        db.PatientCondition(
            patient_id=patient.id, condition_code="DISEASE_PROGRESSION",
            state="PENDING_CONFIRMATION", occurrence_index=0,
            occurrence_date=date(2026, 9, 12)),
    ])
    session.commit()

    categories = [item.category for item in board(session, ids).actions]
    assert (categories.index(ActionCategory.CONDITION_CONFIRMATION)
            < categories.index(ActionCategory.CONDITION_ACTIVE))


def test_a_pending_impact_proposal_is_surfaced_until_it_expires(fixture):
    session, ids, schedule, patient = fixture
    live = db.ScheduleImpactProposal(
        patient_id=patient.id,
        from_schedule_version_id=schedule.schedule_version_id,
        to_schedule_version_id=schedule.schedule_version_id,
        horizon=HORIZON, input_hash="a" * 64, impact={}, status="PENDING",
        reason="Anchor recorded", created_by=ids["actor"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add(live)
    session.commit()
    assert board(session, ids).by_category(ActionCategory.IMPACT_CONFIRMATION)

    live.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()
    assert board(session, ids).by_category(ActionCategory.IMPACT_CONFIRMATION) == []


def test_an_overdue_visit_is_reported_with_its_deadline(fixture):
    session, ids, _, _ = fixture
    # The Day 7 call was due 7-Sep (+/-1); assess on 15-Sep.
    overdue = board(session, ids).by_category(ActionCategory.VISIT_OVERDUE)
    assert any("Day 7 safety follow-up was due" in item.message for item in overdue)


def test_a_completed_visit_drops_off_the_board(fixture):
    session, ids, _, patient = fixture
    call = session.query(db.PatientEvent).join(
        db.Event, db.Event.id == db.PatientEvent.event_definition_id,
    ).filter(db.Event.code == "DAY7_CALL").one()
    session.add(db.PatientEventOccurrence(
        patient_event_id=call.id, occurrence_type="VISIT",
        scheduled_date=call.nominal_start_date, actual_date=date(2026, 9, 7),
        status="COMPLETED",
    ))
    session.commit()

    overdue = board(session, ids).by_category(ActionCategory.VISIT_OVERDUE)
    assert not any("Day 7" in item.message for item in overdue)


def test_visit_mode_is_stated_in_clinical_language(fixture):
    session, ids, _, _ = fixture
    messages = " ".join(item.message for item in board(session, ids).actions)
    # A telephone follow-up must never read like a site visit on the board.
    assert "telephone" in messages or "site visit" in messages
    assert "PHONE_CONTACT" not in messages
    assert "SITE_VISIT" not in messages


def test_the_board_is_scoped_to_the_organization(fixture):
    session, ids, _, _ = fixture
    other = DashboardService(session).actions(
        organization_id=uuid4(), today=TODAY)
    assert other.actions == []
