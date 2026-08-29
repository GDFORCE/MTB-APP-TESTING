"""Protocol lookup/extraction and complete Add Trial persistence."""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import protocol_extraction as pe  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
ORG = f"PROTOCOL-CREATE-{RUN_ID}"
SITE_ORG = f"PROTOCOL-SITE-{RUN_ID}"
PROTOCOL = f"PROTO-{RUN_ID}"
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()
USER_IDS = []
TRIAL_IDS = []


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


@pytest.fixture(scope="module")
def headers():
    async def build():
        async with client() as cli:
            response = await cli.post("/api/auth/register", json={
                "email": f"protocol-{RUN_ID}@example.com",
                "password": PASSWORD,
                "full_name": "Protocol Sponsor",
                "role": "sponsor",
                "organization": ORG,
            })
        assert response.status_code == 200, response.text
        USER_IDS.append(response.json()["user"]["id"])
        return {"Authorization": f"Bearer {response.json()['access_token']}"}
    return run(build())


@pytest.fixture(scope="module")
def site_headers():
    async def build():
        async with client() as cli:
            response = await cli.post("/api/auth/register", json={
                "email": f"protocol-site-{RUN_ID}@example.com",
                "password": PASSWORD,
                "full_name": "Delegated Site Admin",
                "role": "site",
                "organization": SITE_ORG,
            })
        assert response.status_code == 200, response.text
        user = response.json()["user"]
        USER_IDS.append(user["id"])
        await server.db.users.update_one(
            {"id": user["id"]}, {"$set": {"org_admin": True}})
        token = server.make_token(user["id"], "site", "access")
        return {"Authorization": f"Bearer {token}"}
    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.protocol_registry.delete_many({"protocol_id": PROTOCOL})
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({"name": {"$in": [ORG, SITE_ORG]}})
        await server.db.audit_logs.delete_many({"actor_id": {"$in": USER_IDS}})
        await server.db.protocol_extractions.delete_many({"user_id": {"$in": USER_IDS}})
    run(clean())
    LOOP.close()


def test_lookup_found_and_not_found(headers):
    async def exercise():
        await server.db.protocol_registry.insert_one({
            "protocol_id": PROTOCOL,
            "ctri_number": "CTRI/2026/07/123456",
            "title": "Registry-backed protocol",
            "phase": "Phase III",
            "indications": ["Diabetes", "Hypertension"],
            "drug": "Drug X",
            "duration": "18 months",
            "target_enrollment": 100,
            "total_visits": 18,
            "status": "active",
        })
        async with client() as cli:
            found = await cli.get(
                f"/api/protocols/lookup/{PROTOCOL.lower()}", headers=headers)
            found_query = await cli.get(
                "/api/protocols/lookup", headers=headers,
                params={"protocol_id": PROTOCOL.lower()})
            missing = await cli.get(
                f"/api/protocols/lookup/MISSING-{RUN_ID}", headers=headers)
        assert found.status_code == 200, found.text
        assert found_query.status_code == 200, found_query.text
        assert found_query.json() == found.json()
        assert found.json()["found"] is True
        assert found.json()["source"] == "registry"
        assert found.json()["details"]["indications"] == [
            "Diabetes", "Hypertension"]
        assert found.json()["details"]["total_visits"] == 18
        assert missing.status_code == 200, missing.text
        assert missing.json() == {
            "found": False,
            "protocol_id": f"MISSING-{RUN_ID}",
            "source": None,
            "details": None,
        }
    run(exercise())


def test_precreation_pdf_extraction(headers, monkeypatch):
    calls = []

    async def fake_bundle_all(data):
        calls.append(data)
        return (
            pe.ExtractedTrialDetails(
                ctri_number="CTRI/2026/07/654321",
                title="Extracted study",
                phase="Phase II",
                indications=["Oncology"],
                drug="Compound Y",
                duration="12 months",
                target_enrollment=80,
                total_visits=12,
                status="active",
            ),
            [(None, pe.ExtractedSchedule.model_validate({
                "schedule_kind": "linear",
                "visits": [{"name": "Baseline", "day_offset": 0}],
                "verification_status": "verified",
                "verification_confidence": 0.98,
                "verification_scores": {"overall_schedule": 0.97},
            }))],
        )

    monkeypatch.setattr(pe, "extract_protocol_bundle_all", fake_bundle_all)

    async def exercise():
        async with client() as cli:
            response = await cli.post(
                "/api/protocols/extract",
                headers=headers,
                files={"file": ("protocol.pdf", b"%PDF-test", "application/pdf")},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        details = body["details"]
        assert details["title"] == "Extracted study"
        assert details["target_enrollment"] == 80
        assert details["total_visits"] == 12
        assert body["schedule_visit_count"] == 1
        assert body["extraction_id"]

        async with client() as cli:
            created = await cli.post("/api/trials", headers=headers, json={
                "title": details["title"],
                "protocol_id": f"COMBINED-{RUN_ID}",
                "phase": details["phase"],
                "condition": "Oncology",
            })
            assert created.status_code == 200, created.text
            trial_id = created.json()["id"]
            TRIAL_IDS.append(trial_id)
            consumed = await cli.post(
                f"/api/trials/{trial_id}/protocol-extractions/"
                f"{body['extraction_id']}/consume",
                headers=headers,
            )
        assert consumed.status_code == 200, consumed.text
        assert consumed.json()["visits"][0]["name"] == "Baseline"
        assert consumed.json()["verification"]["status"] == "verified"
        assert calls == [b"%PDF-test"], "consuming must not call AI again"
    run(exercise())


def test_create_trial_persists_all_add_trial_fields(headers):
    async def exercise():
        async with client() as cli:
            response = await cli.post("/api/trials", headers=headers, json={
                "title": "Complete Add Trial",
                "protocol_id": f"NEW-{RUN_ID}",
                "phase": "Phase IV",
                "condition": "Cardiology, Hypertension",
                "indications": ["Cardiology", "Hypertension"],
                "drug": "Study Drug Z",
                "duration": "24 months",
                "target_enrollment": 140,
                "total_visits": 20,
                "ctri_number": "CTRI/2026/07/999999",
                "status": "completed",
                "recruitment_status": "closed",
            })
        assert response.status_code == 200, response.text
        trial = response.json()
        TRIAL_IDS.append(trial["id"])
        assert trial["sponsor_name"] == ORG
        assert trial["indications"] == ["Cardiology", "Hypertension"]
        assert trial["drug"] == "Study Drug Z"
        assert trial["duration"] == "24 months"
        assert trial["target_enrollment"] == 140
        assert trial["total_visits"] == 20
        assert trial["ctri_number"] == "CTRI/2026/07/999999"
        assert trial["status"] == "completed"
    run(exercise())


def test_site_trial_creation_requires_active_delegation(site_headers):
    async def exercise():
        payload = {
            "title": "Delegated Site Trial",
            "protocol_id": f"SITE-{RUN_ID}",
            "phase": "Phase II",
            "condition": "Cardiology",
            "sponsor_name": "Investigator Initiated",
        }
        async with client() as cli:
            denied = await cli.post(
                "/api/trials", headers=site_headers, json=payload)
            assert denied.status_code == 403, denied.text
            assert "delegation" in denied.json()["detail"].lower()

            organization = await server.db.organizations.find_one(
                {"name": SITE_ORG}, {"_id": 0})
            request_id = str(uuid.uuid4())
            await server.db.organizations.update_one(
                {"id": organization["id"]},
                {"$set": {
                    "trial_creation_delegated": True,
                    "trial_creation_delegation_request_id": request_id,
                }})
            site_user = await server.db.users.find_one(
                {"email": f"protocol-site-{RUN_ID}@example.com"}, {"_id": 0})
            await server.db.users.update_one(
                {"id": site_user["id"]}, {"$set": {"org_admin": False}})
            non_admin = await cli.post(
                "/api/trials", headers=site_headers, json=payload)
            assert non_admin.status_code == 403, non_admin.text
            assert "administrator" in non_admin.json()["detail"].lower()
            await server.db.users.update_one(
                {"id": site_user["id"]}, {"$set": {"org_admin": True}})
            allowed = await cli.post(
                "/api/trials", headers=site_headers, json=payload)
        assert allowed.status_code == 200, allowed.text
        trial = allowed.json()
        TRIAL_IDS.append(trial["id"])
        assert trial["owning_organization_id"] == organization["id"]
        assert trial["owning_organization_name"] == SITE_ORG
        assert trial["created_under_delegation_request_id"] == request_id
        audit = await server.db.audit_logs.find_one({
            "action": "trial.create", "target_id": trial["id"]})
        assert audit, "delegated trial creation must be audited"
    run(exercise())
