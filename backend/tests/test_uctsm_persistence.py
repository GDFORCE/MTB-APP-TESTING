from uuid import uuid4
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base
from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount


def test_relational_schedule_round_trip_uses_typed_json_contracts():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    organization_id, trial_id, protocol_id = uuid4(), uuid4(), uuid4()
    protocol_version_id, definition_id = uuid4(), uuid4()
    with Session(engine) as session:
        trial = db.Trial(id=trial_id, organization_id=organization_id)
        protocol = db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="ABC-123")
        protocol_version = db.ProtocolVersion(
            id=protocol_version_id, protocol_id=protocol_id, version_label="1",
            document_name="protocol.pdf", document_uri="private://protocol.pdf", document_hash="a" * 64,
        )
        definition = db.ScheduleDefinition(
            id=definition_id, protocol_version_id=protocol_version_id, name="Primary",
        )
        session.add_all([trial, protocol, protocol_version, definition])
        session.flush()
        evidence = Evidence(evidence_type="TABLE_CELL", page_number=2, source_text="Day 30")
        event = Event(
            code="DAY_30", protocol_label="Day 30", display_name="Day 30", event_type="VISIT",
            timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=30, unit="DAY")),
            evidence_refs=[evidence.id],
        )
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(name="Primary", protocol_version_id=protocol_version_id),
            anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
            events=[event], evidence=[evidence],
            claim_evidence=[
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="display_name",
                ),
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="timing",
                ),
            ],
        )
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition_id)
        session.commit()

        loaded = ScheduleRepository(session).get(schedule.schedule_version_id)
        assert loaded.model_dump(exclude={"created_at"}) == schedule.model_dump(exclude={"created_at"})
        assert loaded.events[0].timing.type == "OFFSET"
