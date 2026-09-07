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
    Activity, Anchor, ClaimEvidence, ConditionalAction, ConfinementDefinition,
    DependencyMode, Event, Evidence, Qualifier, ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
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
            dependency_mode=DependencyMode.NOMINAL,
            qualifiers=[Qualifier(
                marker="a", text="Perform only when clinically indicated", scope="VISIT",
                category="CONDITION", target_codes=["DAY_30"], resolved=True,
                evidence_refs=[evidence.id],
            )],
            conditional_actions=[ConditionalAction(
                action_type="MANUAL_REVIEW", target_code="DAY_30",
                condition=ComparisonCondition(
                    operator="EQUALS", left=FieldOperand(field="patient.symptomatic"),
                    right=LiteralOperand(value=True),
                ),
                evidence_refs=[evidence.id],
            )],
            confinement=ConfinementDefinition(
                admission_event_code="ADMIT", dose_event_codes=["DOSE"],
                discharge_event_code="DISCHARGE", evidence_refs=[evidence.id],
            ),
            activities=[Activity(
                protocol_label="ECG", display_name="ECG", activity_type="ASSESSMENT",
                qualifiers=[Qualifier(
                    marker="b", text="Arm A only", scope="ACTIVITY",
                    category="APPLICABILITY", target_codes=["ECG"], resolved=True,
                    evidence_refs=[evidence.id],
                )],
            )],
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
