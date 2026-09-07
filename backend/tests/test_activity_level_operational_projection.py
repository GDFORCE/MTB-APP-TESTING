"""Activity-level timing reaches the patient/CRC operational view (doc s12).

Before this, ``bridge_visit_documents`` projected an event's activities as
names only, pulled from the event's STATIC definition - never which activities
actually applied to THIS occurrence, and never any timing, planned time, or
actual time. A cycle-gated activity ("MRI every second cycle") therefore
appeared on every cycle in the legacy Mongo view even where the canonical
engine had correctly marked it NOT_APPLICABLE.

These tests enrol a real patient against a real evaluated schedule and check
what actually reaches the operational projection: the plain name list is
occurrence-correct, and a new ``activity_details`` list carries status,
planned time and actual time per activity.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.domain.schedule.condition import FieldOperand, MembershipCondition  # noqa: E402
from app.domain.schedule.models import (  # noqa: E402
    Activity, Anchor, ClaimEvidence, Event, Evidence, RecurrenceRule,
    RecurrenceTermination, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (  # noqa: E402
    AnchorReference, OffsetTiming, PositiveTemporalAmount, TemporalAmount,
)
from app.services.operational_bridge import OperationalBridgeService  # noqa: E402
from app.services.operational_projection import bridge_visit_documents  # noqa: E402

BASELINE = date(2026, 1, 1)


def _schedule(protocol_version_id):
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Cycle 1 Day 1")
    mri = Activity(
        code="MRI", protocol_label="MRI", display_name="MRI", activity_type="PROTOCOL_ACTIVITY",
        conditions=[MembershipCondition(
            operator="IN", value=FieldOperand(field="occurrence_number"), values=[1, 3])],
        # A stated intra-day timing ("2 hours after arrival") must produce a
        # real planned_time; an activity with none (Labs, below) must not.
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=0, unit="DAY"),
        ),
        evidence_refs=[evidence.id],
    )
    labs = Activity(
        code="LABS", protocol_label="Labs", display_name="Labs", activity_type="PROTOCOL_ACTIVITY",
        evidence_refs=[evidence.id],
    )
    event = Event(
        code="C1D1", protocol_label="Cycle Day 1", display_name="Cycle Day 1",
        event_type="SITE_VISIT",
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=0, unit="DAY"),
        ),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=21, unit="DAY"),
            start_reference=AnchorReference(code="BASELINE"),
            termination=RecurrenceTermination(type="COUNT", count=3),
        ),
        activities=[mri, labs], evidence_refs=[evidence.id],
    )
    claims = [
        ClaimEvidence(evidence_id=evidence.id, claim_type=claim_type,
                      claim_entity_type="EVENT", claim_entity_id=event.id, claim_path=path)
        for claim_type, path in (
            ("EVENT_NAME", "display_name"), ("TIMING", "timing"), ("RECURRENCE", "recurrence"),
        )
    ] + [
        ClaimEvidence(evidence_id=evidence.id, claim_type="ACTIVITY",
                      claim_entity_type="ACTIVITY", claim_entity_id=activity.id)
        for activity in (mri, labs)
    ] + [
        ClaimEvidence(evidence_id=evidence.id, claim_type="ACTIVITY_TIMING",
                      claim_entity_type="ACTIVITY", claim_entity_id=mri.id, claim_path="timing"),
    ]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=1, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], evidence=[evidence], claim_evidence=claims,
    )


@pytest.fixture()
def enrolled():
    """A patient enrolled against the schedule above, evaluated for real."""
    from test_canonical_journey import ORG, REVIEWER

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    trial = db.Trial(organization_id=ORG, external_trial_id="trial-activity")
    session.add(trial)
    session.flush()
    protocol = db.Protocol(trial_id=trial.id, protocol_number="ABC-123")
    session.add(protocol)
    session.flush()
    protocol_version = db.ProtocolVersion(
        protocol_id=protocol.id, version_label="v1", document_name="p.pdf",
        document_uri="private://p", document_hash="hash1", uploaded_by=REVIEWER,
        extraction_status="COMPLETED",
    )
    session.add(protocol_version)
    session.flush()

    schedule = _schedule(protocol_version.id)
    from app.db.repositories import ScheduleRepository
    definition = db.ScheduleDefinition(protocol_version_id=protocol_version.id, name="Primary")
    session.add(definition)
    session.flush()
    ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
    session.commit()

    horizon = BASELINE + timedelta(days=90)
    enrollment = OperationalBridgeService(session).enroll_patient(
        organization_id=ORG, actor_id=REVIEWER, canonical_trial_id=trial.id,
        schedule_definition_id=definition.id, external_patient_id="patient-1",
        patient_code="P-001", baseline_date=BASELINE, horizon=horizon,
    )
    session.commit()
    yield enrollment
    session.close()


def by_occurrence(enrollment) -> dict[int, object]:
    return {i: event for i, event in enumerate(
        sorted(enrollment.events, key=lambda e: e.nominal_date or BASELINE))}


# --- the plain name list is occurrence-correct --------------------------------

def test_the_plain_activity_list_reflects_this_occurrence_not_the_static_definition(enrolled):
    events = by_occurrence(enrolled)

    # Cycle 1 (occurrence 0 -> occurrence_number 1): MRI applies.
    assert "MRI" in events[0].activities
    assert "Labs" in events[0].activities
    # Cycle 2 (occurrence 1 -> occurrence_number 2): MRI does NOT apply.
    assert "MRI" not in events[1].activities
    assert "Labs" in events[1].activities
    # Cycle 3 (occurrence 2 -> occurrence_number 3): MRI applies again.
    assert "MRI" in events[2].activities


# --- the rich per-activity data now exists ------------------------------------

def test_activity_details_carry_status_and_planned_time(enrolled):
    events = by_occurrence(enrolled)
    mri_cycle1 = next(item for item in events[0].activity_details if item.display_name == "MRI")

    assert mri_cycle1.status == "RESOLVED"
    assert mri_cycle1.planned_time is not None, (
        "MRI states its own intra-day timing; planned_time must be populated")
    assert mri_cycle1.actual_time is None

    # Labs has no protocol-stated timing of its own (it happens at the visit's
    # own date) - doc s12: never invent an activity time that was not stated.
    labs_cycle1 = next(item for item in events[0].activity_details if item.display_name == "Labs")
    assert labs_cycle1.planned_time is None

    mri_cycle2 = next(item for item in events[1].activity_details if item.display_name == "MRI")
    assert mri_cycle2.status == "NOT_APPLICABLE"


def test_activity_details_reach_the_projected_operational_document(enrolled):
    """The actual data path a patient/CRC screen would read from Mongo."""
    documents = bridge_visit_documents(
        patient_id="p1", trial_id="t1", schedule_version_id=str(enrolled.schedule_version_id),
        evaluation_id=str(enrolled.evaluation_id), events=enrolled.events,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cycle1_doc = min(documents, key=lambda d: d["scheduled_date"] or BASELINE)

    names = {item["name"] for item in cycle1_doc["activity_details"]}
    assert names == {"MRI", "Labs"}
    mri_detail = next(item for item in cycle1_doc["activity_details"] if item["name"] == "MRI")
    assert mri_detail["status"] == "RESOLVED"
    assert "planned_time" in mri_detail and "actual_time" in mri_detail

    # The existing plain-string field is untouched in shape - every current
    # consumer that does activities.join(", ") keeps working unmodified.
    assert all(isinstance(item, str) for item in cycle1_doc["activities"])
