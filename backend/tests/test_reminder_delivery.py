"""The schedule projection now has an actual consumer (doc s15).

``schedule_projection.project()`` could always calculate reminders; nothing
ever turned that into a notification. These tests pin the two things that
actually matter for a system that reminds real patients:

  1. everything the requirement forbids reminding about (inactive
     conditionals, unresolved dates, cancelled/completed events) produces NO
     notification - proven by going through the real ``project()`` output,
     not by asserting on a hand-built candidate list;
  2. a moved date replaces the old reminder rather than leaving two, and an
     unchanged one is not re-sent every worker tick (no notification spam).
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db  # noqa: E402

from app.services.reminder_delivery import sync_patient_reminders  # noqa: E402
from app.services.schedule_projection import ProjectedEvent, project  # noqa: E402

TODAY = date(2026, 9, 6)
NOW = datetime(2026, 9, 6, 8, 0)


def projected(**overrides) -> ProjectedEvent:
    values = {
        "logical_key": "C1D1:0", "event_code": "C1D1", "event_type": "SITE_VISIT",
        "display_name": "Cycle 1 Day 1", "status": "RESOLVED",
        "nominal_date": date(2026, 9, 10),
    }
    values.update(overrides)
    return ProjectedEvent(**values)


def sync(events, existing_keys=()):
    result = project(events, today=TODAY)
    return sync_patient_reminders(
        user_id="user-1", patient_id="patient-1", trial_id="trial-1",
        projection=result, existing_keys=list(existing_keys), now=NOW,
    )


# --- a genuinely new, actionable reminder gets created ------------------------

def test_a_resolved_dated_visit_produces_one_notification():
    outcome = sync([projected()])

    assert len(outcome.to_create) == 1
    doc = outcome.to_create[0]
    assert doc["user_id"] == "user-1"
    assert doc["patient_id"] == "patient-1"
    assert doc["type"] == "visit_reminder"
    assert doc["reminder_key"].startswith("reminder:C1D1:0:")
    assert doc["read"] is False
    assert not outcome.to_withdraw_keys


def test_attendance_wording_differs_from_a_contact_only_event():
    site_visit = sync([projected(event_type="SITE_VISIT")]).to_create[0]
    phone_contact = sync([projected(
        logical_key="P:0", event_code="P", event_type="TELEPHONE_CONTACT",
        display_name="Safety call")]).to_create[0]

    assert site_visit["requires_attendance"] is True
    assert "visit" in site_visit["title"].lower()
    assert phone_contact["requires_attendance"] is False
    assert "study contact" in phone_contact["title"].lower()
    assert "travel" not in phone_contact["body"].lower()


# --- doc s15: never a reminder for these, proven through the real pipeline ---

def test_an_unscheduled_visit_produces_no_reminder():
    outcome = sync([projected(is_unscheduled=True)])
    assert not outcome.to_create


def test_an_event_waiting_on_a_condition_produces_no_reminder():
    outcome = sync([projected(status="WAITING_FOR_CONDITION", nominal_date=None)])
    assert not outcome.to_create


def test_an_event_waiting_on_an_anchor_produces_no_reminder():
    outcome = sync([projected(status="WAITING_FOR_ANCHOR", nominal_date=None)])
    assert not outcome.to_create


def test_a_cancelled_event_produces_no_reminder():
    outcome = sync([projected(status="CANCELLED")])
    assert not outcome.to_create


def test_a_completed_event_produces_no_reminder():
    outcome = sync([projected(status="COMPLETED")])
    assert not outcome.to_create


def test_an_unresolved_event_produces_no_reminder():
    outcome = sync([projected(status="UNRESOLVED", nominal_date=None)])
    assert not outcome.to_create


def test_an_undated_event_produces_no_reminder_even_if_marked_resolved():
    """Belt-and-braces: RESOLVED with no date must never be reminded about."""
    outcome = sync([projected(status="RESOLVED", nominal_date=None)])
    assert not outcome.to_create


# --- a moved date replaces the old reminder; an unchanged one is not resent --

def test_an_unchanged_reminder_is_not_recreated_on_every_tick():
    first = sync([projected()])
    existing_keys = [doc["reminder_key"] for doc in first.to_create]

    second = sync([projected()], existing_keys=existing_keys)

    assert not second.to_create, "an unmoved, already-delivered reminder must not repeat"
    assert not second.to_withdraw_keys


def test_a_moved_date_withdraws_the_old_reminder_and_creates_one_new_one():
    first = sync([projected(nominal_date=date(2026, 9, 10))])
    existing_keys = [doc["reminder_key"] for doc in first.to_create]

    moved = sync([projected(nominal_date=date(2026, 9, 17))], existing_keys=existing_keys)

    assert len(moved.to_create) == 1
    assert moved.to_create[0]["reminder_key"] != existing_keys[0]
    assert moved.to_withdraw_keys == existing_keys
    # Never two live reminders for the same visit at once.
    assert moved.to_create[0]["logical_key"] == "C1D1:0"


def test_a_visit_that_becomes_cancelled_withdraws_its_reminder_without_replacement():
    first = sync([projected()])
    existing_keys = [doc["reminder_key"] for doc in first.to_create]

    cancelled = sync([projected(status="CANCELLED")], existing_keys=existing_keys)

    assert not cancelled.to_create
    assert cancelled.to_withdraw_keys == existing_keys


def test_compute_patient_projection_agrees_with_a_real_enrolled_patient():
    """End to end, not just against a hand-built ProjectedEvent list: enrol a
    real patient against a real evaluated schedule and confirm the worker's
    own computation produces an actionable reminder for it.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.db.repositories import ScheduleRepository
    from app.domain.schedule.models import (
        Activity, Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata,
        ScheduleStatus, UniversalSchedule,
    )
    from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
    from app.services.operational_bridge import OperationalBridgeService
    from app.services.reminder_delivery import compute_patient_projection

    import test_canonical_journey as journey

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        trial = db.Trial(organization_id=journey.ORG, external_trial_id="trial-reminder")
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="ABC-123")
        session.add(protocol)
        session.flush()
        protocol_version = db.ProtocolVersion(
            protocol_id=protocol.id, version_label="v1", document_name="p.pdf",
            document_uri="private://p", document_hash="hash-reminder",
            uploaded_by=journey.REVIEWER, extraction_status="COMPLETED",
        )
        session.add(protocol_version)
        session.flush()

        evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Day 1")
        activity = Activity(
            code="LABS", protocol_label="Labs", display_name="Labs",
            activity_type="PROTOCOL_ACTIVITY", evidence_refs=[evidence.id],
        )
        event = Event(
            code="C1D1", protocol_label="Cycle Day 1", display_name="Cycle Day 1",
            event_type="SITE_VISIT",
            timing=OffsetTiming(
                reference=AnchorReference(code="BASELINE"),
                offset=TemporalAmount(value=0, unit="DAY")),
            activities=[activity], evidence_refs=[evidence.id],
        )
        claims = [
            ClaimEvidence(evidence_id=evidence.id, claim_type=claim_type,
                          claim_entity_type="EVENT", claim_entity_id=event.id, claim_path=path)
            for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
        ] + [ClaimEvidence(evidence_id=evidence.id, claim_type="ACTIVITY",
                            claim_entity_type="ACTIVITY", claim_entity_id=activity.id)]
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(
                name="Primary", protocol_version_id=protocol_version.id,
                version_number=1, status=ScheduleStatus.APPROVED,
            ),
            anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
            events=[event], evidence=[evidence], claim_evidence=claims,
        )
        definition = db.ScheduleDefinition(protocol_version_id=protocol_version.id, name="Primary")
        session.add(definition)
        session.flush()
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()

        baseline = date(2026, 9, 1)
        OperationalBridgeService(session).enroll_patient(
            organization_id=journey.ORG, actor_id=journey.REVIEWER,
            canonical_trial_id=trial.id, schedule_definition_id=definition.id,
            external_patient_id="patient-reminder", patient_code="P-001",
            baseline_date=baseline, horizon=baseline + timedelta(days=30),
        )
        session.commit()

        patient = session.scalar(select(db.Patient))
        projection = compute_patient_projection(session, patient, today=baseline)

        assert len(projection.reminders) == 1
        outcome = sync_patient_reminders(
            user_id="patient-user-1", patient_id=str(patient.id), trial_id="trial-reminder",
            projection=projection, existing_keys=[], now=NOW,
        )
        assert len(outcome.to_create) == 1
        assert outcome.to_create[0]["body"]


def test_a_confinement_absorbed_event_produces_no_separate_reminder():
    """Doc s11: an internal study day inside an admission must not remind
    the patient to 'arrive' again."""
    from app.services.schedule_projection import ProjectedConfinement

    events = [projected(event_code="DAY2", logical_key="DAY2:0",
                         display_name="Study Day 2")]
    confinement = ProjectedConfinement(
        episode_code="EPISODE1", display_name="Inpatient admission", status="IN_CONFINEMENT",
        start_date=date(2026, 9, 8), end_date=date(2026, 9, 12),
        member_event_codes=["DAY2"],
    )
    result = project(events, today=TODAY, confinements=[confinement])
    outcome = sync_patient_reminders(
        user_id="user-1", patient_id="patient-1", trial_id="trial-1",
        projection=result, existing_keys=[], now=NOW,
    )
    assert not outcome.to_create
