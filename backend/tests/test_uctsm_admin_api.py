"""The platform-admin API surface for the PROTOCOL_DEFINED extension-handler
process: ranking, alert payloads, and bulk revalidation -- the backend for
the "what should engineering build next, and how do we roll it out" screen.

Deliberately SQL-only (see uctsm_admin.py's module docstring): no Mongo
access, gated by role only (not per-organization), matching the existing
`create_uctsm_router` factory-dependency-injection pattern used throughout
this app's tests.
"""
from pathlib import Path
import sys
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.uctsm_admin import create_uctsm_admin_router
from app.db.base import Base, get_session
from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.extension_handlers import register_timing_extension
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import ProtocolDefinedTiming, ProtocolExtension
from app.domain.schedule.validator import ScheduleValidator


def _blocked_schedule(*, protocol_version_id, handler_name):
    evidence = Evidence(evidence_type="TEXT", page_number=14, source_text="At least 21 days after Period I dosing")
    event = Event(
        code="PERIOD2_DOSE", protocol_label="Period II dosing", display_name="Period II dosing",
        event_type="VISIT", requires_review=True, evidence_refs=[evidence.id],
        timing=ProtocolDefinedTiming(
            handler=handler_name,
            extensions=[ProtocolExtension(type=handler_name, value={"note": "unbuilt pattern"})],
        ),
    )
    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id, status=ScheduleStatus.DRAFT,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], evidence=[evidence],
        claim_evidence=[
            ClaimEvidence(evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                          claim_entity_id=event.id, claim_path="display_name"),
            ClaimEvidence(evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                          claim_entity_id=event.id, claim_path="timing"),
        ],
    )
    schedule.validation_issues = ScheduleValidator().validate(schedule)
    return schedule


def make_app():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = {"role": "admin"}
    app = FastAPI()

    def session():
        with factory() as value:
            yield value

    app.dependency_overrides[get_session] = session
    app.include_router(create_uctsm_admin_router(lambda: identity))
    return app, identity, factory


def test_openapi_exposes_the_admin_handler_endpoints():
    app = FastAPI()
    app.include_router(create_uctsm_admin_router(lambda: {"role": "admin"}))
    paths = app.openapi()["paths"]
    assert "/api/uctsm/admin/unsupported-handlers" in paths
    assert "/api/uctsm/admin/unsupported-handlers/alerts" in paths
    assert "/api/uctsm/admin/unsupported-handlers/{handler}/revalidate" in paths


def test_non_admin_roles_are_rejected():
    app, identity, _ = make_app()
    with TestClient(app) as client:
        for role in ("pi", "sponsor", "patient", None):
            identity.clear()
            identity["role"] = role
            assert client.get("/api/uctsm/admin/unsupported-handlers").status_code == 403
            assert client.get("/api/uctsm/admin/unsupported-handlers/alerts").status_code == 403
            assert client.post("/api/uctsm/admin/unsupported-handlers/X/revalidate").status_code == 403


def test_admin_sees_ranking_and_alerts_then_applies_a_fix_to_every_blocked_protocol():
    app, _, factory = make_app()
    with factory.begin() as session:
        trial = db.Trial(organization_id=uuid4())
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="ADMIN-API-1")
        session.add(protocol)
        session.flush()
        version_ids = []
        for index in range(2):
            version = db.ProtocolVersion(
                protocol_id=protocol.id, version_label=str(index), document_name=f"p{index}.pdf",
                document_uri=f"private://p{index}.pdf", document_hash=str(index) * 64,
            )
            session.add(version)
            session.flush()
            definition = db.ScheduleDefinition(protocol_version_id=version.id, name="Primary")
            session.add(definition)
            session.flush()
            schedule = _blocked_schedule(protocol_version_id=version.id, handler_name="ADMIN_API_PATTERN")
            ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
            version_ids.append(str(schedule.schedule_version_id))

    with TestClient(app) as client:
        ranking = client.get("/api/uctsm/admin/unsupported-handlers").json()["handlers"]
        assert ranking[0]["handler"] == "ADMIN_API_PATTERN"
        assert ranking[0]["occurrence_count"] == 2

        alerts = client.get("/api/uctsm/admin/unsupported-handlers/alerts").json()["alerts"]
        assert alerts[0]["handler"] == "ADMIN_API_PATTERN"
        assert alerts[0]["severity"] in {"low", "medium", "high"}

        # Real protocol text, deduplicated even though both schedules cite
        # the exact same quote -- not just the generic validator message.
        evidence = client.get("/api/uctsm/admin/unsupported-handlers/ADMIN_API_PATTERN/evidence").json()["evidence"]
        assert len(evidence) == 1
        assert evidence[0]["source_text"] == "At least 21 days after Period I dosing"
        assert evidence[0]["page_number"] == 14
        assert evidence[0]["schedule_version_id"] in version_ids

        # Engineering ships the handler.
        register_timing_extension("ADMIN_API_PATTERN", lambda expression, context: None)

        result = client.post("/api/uctsm/admin/unsupported-handlers/ADMIN_API_PATTERN/revalidate")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["processed"] == 2
        assert body["now_fully_clear"] == 2
        assert {item["schedule_version_id"] for item in body["results"]} == set(version_ids)

        # And the admin screen reflects reality: nothing left to alert on.
        assert client.get("/api/uctsm/admin/unsupported-handlers").json()["handlers"] == []
