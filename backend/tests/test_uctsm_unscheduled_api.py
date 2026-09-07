"""Unscheduled visit creation, end to end over the API (MTB requirement doc 10).

Doc 10 sections 13-15 and 31-32. The engine rules are covered in
test_uctsm_event_types.py; this checks the workflow a CRC actually performs, and
the refusals that protect the protocol from it: a scheduled visit cannot be
created ad hoc, a reason is mandatory, and a mode the protocol does not permit is
rejected rather than recorded.
"""

from collections.abc import Generator
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
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
API = "/api/uctsm"


async def authenticated_user():
    return USER


@pytest.fixture()
def client(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("UCTSM_DEMO_MODE", "true")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(create_uctsm_router(authenticated_user))
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


@pytest.fixture()
def approved_patient(client: TestClient) -> dict[str, str]:
    """Seed the demo trial and take its schedule all the way to APPROVED."""
    workspace = client.post(f"{API}/demo/seed").json()
    version_id = workspace["schedule_version_id"]
    schedule = client.get(f"{API}/schedule-versions/{version_id}").json()

    assert client.post(f"{API}/schedule-versions/{version_id}/validate").json()[
        "blocking_issues"] == 0
    client.post(f"{API}/schedule-versions/{version_id}/submit-review")
    for event in schedule["events"]:
        fields = ["display_name", "timing"]
        for name in ("conditions", "activities", "applicability", "recurrence"):
            if event.get(name):
                fields.append(name)
        for field_path in fields:
            decision = client.post(
                f"{API}/schedule-versions/{version_id}/review-decisions",
                json={
                    "decision": "CONFIRM", "entity_type": "EVENT",
                    "entity_id": event["id"], "field_path": field_path,
                    "comment": "Confirmed by the test.",
                },
            )
            assert decision.status_code == 201, decision.text
    approval = client.post(
        f"{API}/schedule-versions/{version_id}/review",
        json={"decision": "APPROVE", "comment": "reviewed"},
    )
    assert approval.status_code == 200, approval.text
    return workspace


def test_a_protocol_defined_unscheduled_visit_is_visible_but_not_due(
    client: TestClient, approved_patient: dict[str, str],
):
    version_id = approved_patient["schedule_version_id"]
    schedule = client.get(f"{API}/schedule-versions/{version_id}").json()
    definition = next(
        item for item in schedule["events"] if item["code"] == "UNSCHEDULED_VISIT")

    assert definition["activation"] == "ON_DEMAND"
    # Doc 10 s31-s32: both permitted modes survive extraction, neither is chosen.
    assert sorted(definition["allowed_visit_modes"]) == ["CLINIC", "TELEPHONE"]
    assert definition["visit_mode"] is None


def test_creating_one_records_it_against_this_patient_only(
    client: TestClient, approved_patient: dict[str, str],
):
    patient_id = approved_patient["patient_id"]
    created = client.post(
        f"{API}/patients/{patient_id}/unscheduled-visits",
        json={
            "event_code": "UNSCHEDULED_VISIT", "occurred_on": "2026-10-14",
            "reason": "Grade 3 rash reported by telephone", "visit_mode": "TELEPHONE",
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["occurred_on"] == "2026-10-14"
    assert body["visit_mode"] == "TELEPHONE"
    assert body["reason"] == "Grade 3 rash reported by telephone"

    # The protocol itself is untouched: this created an occurrence, not a rule.
    schedule = client.get(
        f"{API}/schedule-versions/{approved_patient['schedule_version_id']}").json()
    assert len(schedule["events"]) == 3


def test_a_scheduled_protocol_visit_cannot_be_created_ad_hoc(
    client: TestClient, approved_patient: dict[str, str],
):
    """Otherwise a CRC could invent a protocol visit that no protocol requires."""
    response = client.post(
        f"{API}/patients/{approved_patient['patient_id']}/unscheduled-visits",
        json={
            "event_code": "SAFETY_FOLLOW_UP", "occurred_on": "2026-10-14",
            "reason": "patient came in early",
        },
    )

    assert response.status_code == 422
    assert "scheduled protocol visit" in response.json()["detail"]


def test_a_reason_is_required(client: TestClient, approved_patient: dict[str, str]):
    """Doc 10 s14: an unscheduled visit is defined by its cause."""
    response = client.post(
        f"{API}/patients/{approved_patient['patient_id']}/unscheduled-visits",
        json={
            "event_code": "UNSCHEDULED_VISIT", "occurred_on": "2026-10-14",
            "reason": "   ",
        },
    )

    assert response.status_code == 422


def test_a_mode_the_protocol_does_not_permit_is_refused(
    client: TestClient, approved_patient: dict[str, str],
):
    """Doc 10 s32: the allowed modes are a constraint, not a suggestion."""
    response = client.post(
        f"{API}/patients/{approved_patient['patient_id']}/unscheduled-visits",
        json={
            "event_code": "UNSCHEDULED_VISIT", "occurred_on": "2026-10-14",
            "reason": "Grade 3 rash", "visit_mode": "HOME_NURSE",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "CLINIC" in detail and "TELEPHONE" in detail


def test_an_unknown_event_code_is_a_not_found(
    client: TestClient, approved_patient: dict[str, str],
):
    response = client.post(
        f"{API}/patients/{approved_patient['patient_id']}/unscheduled-visits",
        json={
            "event_code": "NO_SUCH_VISIT", "occurred_on": "2026-10-14",
            "reason": "Grade 3 rash",
        },
    )

    assert response.status_code == 404


def test_extraction_without_a_configured_model_fails_visibly(
    client: TestClient, monkeypatch,
):
    """A run that cannot start must say so, not sit QUEUED forever."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = client.post(f"{API}/demo/seed").json()
    trial_id = workspace["trial_id"]

    protocol = client.post(f"{API}/trials/{trial_id}/protocols", json={
        "protocol_number": "P-EXTRACT", "title": "Extraction test",
    })
    assert protocol.status_code == 201, protocol.text
    version = client.post(
        f"{API}/protocols/{protocol.json()['id']}/versions",
        json={
            "version_label": "1.0", "document_name": "protocol.pdf",
            "document_uri": "private://protocols/protocol.pdf",
            "document_hash": "b" * 64,
        },
    )
    assert version.status_code == 201, version.text

    queued = client.post(
        f"{API}/protocols/{protocol.json()['id']}"
        f"/versions/{version.json()['id']}/extract-schedule",
        headers={"Idempotency-Key": "extract-1"},
    )

    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["provider_configured"] is False
    assert body["status"] == "FAILED"

    run = client.get(f"{API}/extraction-runs/{body['extraction_run_id']}").json()
    assert run["status"] == "FAILED"
    assert "ANTHROPIC_API_KEY" in str(run.get("error_details"))


# --- the cutover gate, over the API -------------------------------------------------

def test_the_cutover_endpoint_refuses_before_parity_is_proven(
    client: TestClient, approved_patient: dict[str, str],
):
    """The API must not be an easier route to a cutover than the service is."""
    trial_id = approved_patient["trial_id"]

    status = client.get(f"{API}/trials/{trial_id}/read-mode")
    assert status.status_code == 200, status.text
    assert status.json()["read_mode"] == "LEGACY"
    assert status.json()["ready"] is False

    response = client.post(f"{API}/trials/{trial_id}/read-mode", json={
        "mode": "ENGINE", "reason": "looks fine to me",
    })

    assert response.status_code == 409
    assert "parity has not been proven" in response.json()["detail"]


def test_a_parity_check_records_its_result_whatever_it_is(
    client: TestClient, approved_patient: dict[str, str],
):
    patient_id = approved_patient["patient_id"]

    response = client.post(f"{API}/patients/{patient_id}/parity-check", json={
        "legacy_visits": [{"name": "Some other visit", "scheduled_date": "2026-01-01"}],
        "horizon": "2027-12-31",
    })

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["passed"] is False
    assert body["parity_run_id"]
    assert body["differences"]


def test_moving_back_to_the_operational_store_is_always_allowed(
    client: TestClient, approved_patient: dict[str, str],
):
    """Reversing a cutover must never be harder than making one."""
    response = client.post(
        f"{API}/trials/{approved_patient['trial_id']}/read-mode",
        json={"mode": "LEGACY", "reason": "staying on the operational store for now"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["read_mode"] == "LEGACY"


def test_a_read_mode_change_needs_a_reason(
    client: TestClient, approved_patient: dict[str, str],
):
    response = client.post(
        f"{API}/trials/{approved_patient['trial_id']}/read-mode",
        json={"mode": "LEGACY", "reason": ""},
    )

    assert response.status_code == 422
