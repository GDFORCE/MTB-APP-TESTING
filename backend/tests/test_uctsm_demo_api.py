from collections.abc import Generator
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.uctsm import create_uctsm_router
from app.db import models as _models  # noqa: F401
from app.db.base import Base, get_session


USER = {
    "id": "00000000-0000-0000-0000-000000000001",
    "organization_id": "00000000-0000-0000-0000-000000000002",
}


async def authenticated_user():
    return USER


def test_demo_screen_workflow_from_seed_through_patient_evaluation(monkeypatch):
    monkeypatch.setenv("UCTSM_DEMO_MODE", "true")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(create_uctsm_router(authenticated_user))
    app.dependency_overrides[get_session] = session_override
    client = TestClient(app)

    seeded = client.post("/api/uctsm/demo/seed")
    assert seeded.status_code == 201, seeded.text
    workspace = seeded.json()
    version_id = workspace["schedule_version_id"]
    patient_id = workspace["patient_id"]

    schedule_response = client.get(f"/api/uctsm/schedule-versions/{version_id}")
    assert schedule_response.status_code == 200, schedule_response.text
    schedule = schedule_response.json()
    assert schedule["schedule_metadata"]["status"] == "VALIDATION_REQUIRED"
    assert {event["code"] for event in schedule["events"]} == {
        "SAFETY_FOLLOW_UP", "PROGRESSION_ASSESSMENT",
    }

    validation = client.post(f"/api/uctsm/schedule-versions/{version_id}/validate")
    assert validation.status_code == 200, validation.text
    assert validation.json()["blocking_issues"] == 0
    assert client.post(f"/api/uctsm/schedule-versions/{version_id}/submit-review").status_code == 200

    for event in schedule["events"]:
        fields = ["display_name", "timing"]
        if event["conditions"]:
            fields.append("conditions")
        for field_path in fields:
            decision = client.post(
                f"/api/uctsm/schedule-versions/{version_id}/review-decisions",
                json={
                    "decision": "CONFIRM",
                    "entity_type": "EVENT",
                    "entity_id": event["id"],
                    "field_path": field_path,
                    "comment": "Confirmed by the interactive test.",
                },
            )
            assert decision.status_code == 201, decision.text

    approval = client.post(
        f"/api/uctsm/schedule-versions/{version_id}/review",
        json={"decision": "APPROVE", "comment": "Interactive test approval."},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["status"] == "APPROVED"

    last_dose = next(anchor for anchor in schedule["anchors"] if anchor["code"] == "LAST_DOSE")
    anchor_response = client.post(
        f"/api/uctsm/patients/{patient_id}/anchors",
        json={
            "anchor_definition_id": last_dose["id"],
            "value_date": "2026-12-15",
            "status": "CONFIRMED",
            "source_type": "DEMO_UI",
        },
    )
    assert anchor_response.status_code == 201, anchor_response.text

    evaluated = client.post(
        f"/api/uctsm/patients/{patient_id}/schedule/evaluate",
        json={"horizon": "2028-12-31"},
        headers={"Idempotency-Key": "demo-api-test-1"},
    )
    assert evaluated.status_code == 200, evaluated.text
    events = evaluated.json()["events"]
    assert any(event["status"] == "RESOLVED" for event in events)
    assert any(event["status"] in {"WAITING_FOR_ANCHOR", "WAITING_FOR_CONDITION"} for event in events)
