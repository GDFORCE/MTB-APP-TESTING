"""Sponsor/CRO dashboard contract and cross-organization isolation."""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
ORG_A = f"SPONSOR-DASH-{RUN_ID}-A"
ORG_B = f"SPONSOR-DASH-{RUN_ID}-B"
LOOP = asyncio.new_event_loop()
TRIAL_IDS = []
USER_IDS = []
PATIENT_IDS = []
SITE_IDS = []
VISIT_INSTANCE_IDS = []
DOSE_LOG_IDS = []


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


async def register(org):
    email = f"sponsor-{RUN_ID}-{uuid.uuid4().hex[:5]}@example.com"
    async with client() as cli:
        response = await cli.post("/api/auth/register", json={
            "email": email,
            "password": PASSWORD,
            "full_name": f"Sponsor {org}",
            "role": "sponsor",
            "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    USER_IDS.append(payload["user"]["id"])
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


async def create_trial(headers, protocol, spoofed_org):
    async with client() as cli:
        response = await cli.post("/api/trials", headers=headers, json={
            "title": f"Dashboard Trial {protocol}",
            "protocol_id": protocol,
            "phase": "Phase III",
            "condition": "Testing",
            "drug": "Study Drug",
            "target_enrollment": 40,
            "sponsor_name": spoofed_org,
        })
    assert response.status_code == 200, response.text
    TRIAL_IDS.append(response.json()["id"])
    return response.json()


@pytest.fixture(scope="module")
def world():
    async def build():
        user_a, headers_a = await register(ORG_A)
        user_b, headers_b = await register(ORG_B)
        await server.db.users.update_one(
            {"id": user_a["id"]}, {"$set": {"org_admin": True}})
        user_a["org_admin"] = True
        trial_a = await create_trial(headers_a, f"DASH-{RUN_ID}-A", ORG_B)
        trial_b = await create_trial(headers_b, f"DASH-{RUN_ID}-B", ORG_A)
        return {
            "user_a": user_a, "headers_a": headers_a, "trial_a": trial_a,
            "user_b": user_b, "headers_b": headers_b, "trial_b": trial_b,
        }
    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    async def clean():
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({"name": {"$in": [ORG_A, ORG_B]}})
        await server.db.shares.delete_many({"trial_id": {"$in": TRIAL_IDS}})
        await server.db.audit_logs.delete_many({"trial_id": {"$in": TRIAL_IDS}})
        await server.db.patients.delete_many({"id": {"$in": PATIENT_IDS}})
        await server.db.org_sites.delete_many({"id": {"$in": SITE_IDS}})
        await server.db.visit_instances.delete_many(
            {"id": {"$in": VISIT_INSTANCE_IDS}})
        await server.db.dose_logs.delete_many({"id": {"$in": DOSE_LOG_IDS}})
        await server.db.invitations.delete_many({"trial_id": {"$in": TRIAL_IDS}})
    run(clean())
    LOOP.close()


def test_server_forces_authenticated_organization(world):
    assert world["trial_a"]["sponsor_name"] == ORG_A
    assert world["trial_b"]["sponsor_name"] == ORG_B


def test_trial_list_and_detail_are_cross_org_scoped(world):
    async def flow():
        async with client() as cli:
            listing = await cli.get("/api/trials", headers=world["headers_a"])
            assert listing.status_code == 200, listing.text
            ids = {trial["id"] for trial in listing.json()}
            assert world["trial_a"]["id"] in ids
            assert world["trial_b"]["id"] not in ids

            own = await cli.get(
                f"/api/trials/{world['trial_a']['id']}", headers=world["headers_a"])
            foreign = await cli.get(
                f"/api/trials/{world['trial_b']['id']}", headers=world["headers_a"])
            assert own.status_code == 200, own.text
            assert foreign.status_code == 403, foreign.text
    run(flow())

def test_trial_patch_is_owner_scoped_and_audited(world):
    async def flow():
        async with client() as cli:
            updated = await cli.patch(
                f"/api/trials/{world['trial_a']['id']}",
                headers=world["headers_a"],
                json={
                    "duration": "18 months",
                    "target_enrollment": 72,
                    "recruitment_status": "recruiting",
                },
            )
            assert updated.status_code == 200, updated.text
            payload = updated.json()
            assert payload["duration"] == "18 months"
            assert payload["target_enrollment"] == 72
            assert payload["updated_by"] == world["user_a"]["id"]
            assert payload["updated_by_name"] == world["user_a"]["full_name"]

            foreign = await cli.patch(
                f"/api/trials/{world['trial_b']['id']}",
                headers=world["headers_a"],
                json={"duration": "Not allowed"},
            )
            assert foreign.status_code == 403, foreign.text

        audit = await server.db.audit_logs.find_one({
            "action": "trial.update",
            "trial_id": world["trial_a"]["id"],
        })
        assert audit
        assert audit["changes"]["target_enrollment"] == 72
    run(flow())


def test_dashboard_contract_is_scoped_and_deidentified(world):
    async def flow():
        async with client() as cli:
            response = await cli.get(
                "/api/sponsor/dashboard", headers=world["headers_a"])
            assert response.status_code == 200, response.text
            payload = response.json()
            assert set(payload) >= {
                "portfolio", "totals", "trials", "sites",
                "recent_notifications", "capabilities",
            }
            assert set(payload["portfolio"]) >= {
                "health_score", "active_trials", "alerts", "enrolled",
                "target", "enrollment_pct", "compliance_pct",
                "adherence_pct", "recruitment",
            }
            ids = {trial["id"] for trial in payload["trials"]}
            assert world["trial_a"]["id"] in ids
            assert world["trial_b"]["id"] not in ids
            assert payload["totals"]["trials"] == 1
            serialized = response.text.lower()
            assert "full_name" not in serialized
            assert "dob" not in serialized
    run(flow())


def test_foreign_trial_cannot_be_shared(world):
    async def flow():
        async with client() as cli:
            response = await cli.post("/api/shares", headers=world["headers_a"], json={
                "trial_id": world["trial_b"]["id"],
                "via": "link",
                "recipients": [],
            })
            assert response.status_code == 403, response.text
    run(flow())


def test_trial_site_is_persistent_and_scoped(world):
    async def flow():
        body = {
            "name": f"Apollo Sponsor Site {RUN_ID}",
            "address": "12 Trial Road",
            "city": "Mumbai",
            "state": "Maharashtra",
            "hospital_type": "Private",
            "department": "Oncology",
            "pi_name": "Dr Site Investigator",
            "pi_email": f"site-pi-{RUN_ID}@example.com",
            "pi_phone": "+91 9000000000",
            "target_enrollment": 25,
        }
        async with client() as cli:
            added = await cli.post(
                f"/api/sponsor/trials/{world['trial_a']['id']}/sites",
                headers=world["headers_a"], json=body)
            assert added.status_code == 200, added.text
            payload = added.json()
            SITE_IDS.append(payload["site"]["id"])
            assert payload["site"]["trial_ids"] == [world["trial_a"]["id"]]
            assert payload["site"]["department"] == "Oncology"
            assert payload["invitation"]["status"] == "pending"

            # Re-saving the same site updates it and does not duplicate it.
            updated = await cli.post(
                f"/api/sponsor/trials/{world['trial_a']['id']}/sites",
                headers=world["headers_a"],
                json={**body, "department": "Pulmonology"})
            assert updated.status_code == 200, updated.text
            assert updated.json()["site"]["id"] == payload["site"]["id"]
            assert updated.json()["site"]["department"] == "Pulmonology"

            detail = await cli.get(
                f"/api/sponsor/trials/{world['trial_a']['id']}",
                headers=world["headers_a"])
            assert detail.status_code == 200, detail.text
            site = next(
                row for row in detail.json()["sites"]
                if row["id"] == payload["site"]["id"])
            assert site["target_enrollment"] == 25
            assert site["pi_name"] == "Dr Site Investigator"
            assert any(member["email"] == body["pi_email"]
                       for member in detail.json()["team"])

            foreign = await cli.post(
                f"/api/sponsor/trials/{world['trial_b']['id']}/sites",
                headers=world["headers_a"], json=body)
            assert foreign.status_code == 403, foreign.text

            # A non-admin sponsor cannot mutate even its own trial-site network.
            non_admin = await cli.post(
                f"/api/sponsor/trials/{world['trial_b']['id']}/sites",
                headers=world["headers_b"], json=body)
            assert non_admin.status_code == 403, non_admin.text
    run(flow())


def test_trial_detail_subjects_are_deidentified(world):
    async def flow():
        patient_id = str(uuid.uuid4())
        PATIENT_IDS.append(patient_id)
        await server.db.patients.insert_one({
            "id": patient_id,
            "trial_id": world["trial_a"]["id"],
            "full_name": "Private Patient Name",
            "email": f"private-{RUN_ID}@example.com",
            "phone": "+91 9888888888",
            "dob": "1970-01-01",
            "subject_id": f"SUBJ-{RUN_ID}",
            "avatar_initials": "PP",
            "status": "randomized",
            "created_at": server.now(),
        })
        visit_instance_id = str(uuid.uuid4())
        VISIT_INSTANCE_IDS.append(visit_instance_id)
        await server.db.visit_instances.insert_one({
            "id": visit_instance_id,
            "patient_id": patient_id,
            "trial_id": world["trial_a"]["id"],
            "status": "completed",
            "scheduled_date": server.now(),
        })
        for status in ("taken", "missed"):
            dose_id = str(uuid.uuid4())
            DOSE_LOG_IDS.append(dose_id)
            await server.db.dose_logs.insert_one({
                "id": dose_id,
                "patient_id": patient_id,
                "status": status,
                "date": server.now().date().isoformat(),
                "time": "09:00" if status == "taken" else "21:00",
            })
        async with client() as cli:
            response = await cli.get(
                f"/api/sponsor/trials/{world['trial_a']['id']}",
                headers=world["headers_a"])
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["recruitment"]["screened"] >= 1
            assert payload["recruitment"]["randomized"] >= 1
            subject = next(
                row for row in payload["subjects"]
                if row["subject_id"] == f"SUBJ-{RUN_ID}")
            assert subject["deidentified"] is True
            assert set(subject).isdisjoint(
                {"full_name", "email", "phone", "dob"})
            assert "Private Patient Name" not in response.text
            assert f"private-{RUN_ID}@example.com" not in response.text

            dashboard = await cli.get(
                "/api/sponsor/dashboard", headers=world["headers_a"])
            assert dashboard.status_code == 200, dashboard.text
            portfolio = dashboard.json()["portfolio"]
            assert portfolio["enrolled"] >= 1
            assert portfolio["target"] == 72
            assert portfolio["enrollment_pct"] == round(
                portfolio["enrolled"] / portfolio["target"] * 100)
            assert portfolio["compliance_pct"] == 100
            assert portfolio["adherence_pct"] == 50
            assert portfolio["health_score"] == round((
                min(100, portfolio["enrollment_pct"])
                + portfolio["compliance_pct"]
                + portfolio["adherence_pct"]
            ) / 3)
            assert portfolio["recruitment"]["randomized"] >= 1
            unassigned = next(
                row for row in dashboard.json()["sites"]
                if row["name"] == "Unassigned site")
            assert set(unassigned) >= {
                "enrollment_pct", "visit_compliance", "adherence_pct",
                "performance_score",
            }
            assert unassigned["performance_score"] == round((
                min(100, unassigned["enrollment_pct"])
                + unassigned["visit_compliance"]
                + unassigned["adherence_pct"]
            ) / 3)

            filtered = await cli.get(
                f"/api/sponsor/trials/{world['trial_a']['id']}/subjects",
                headers=world["headers_a"],
                params={"status": "randomized"})
            assert filtered.status_code == 200, filtered.text
            assert any(row["subject_id"] == f"SUBJ-{RUN_ID}"
                       for row in filtered.json())
    run(flow())
