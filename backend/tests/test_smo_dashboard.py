"""SMO operational dashboard and organization isolation."""
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
ORG_A = f"SMO-DASH-{RUN_ID}-A"
ORG_B = f"SMO-DASH-{RUN_ID}-B"
LOOP = asyncio.new_event_loop()
USER_IDS = []
TRIAL_IDS = []
PATIENT_IDS = []


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


async def register(role, org):
    email = f"{role}-{RUN_ID}-{uuid.uuid4().hex[:5]}@example.com"
    async with client() as cli:
        response = await cli.post("/api/auth/register", json={
            "email": email, "password": PASSWORD,
            "full_name": f"Test {role.upper()} {RUN_ID}",
            "role": role, "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    USER_IDS.append(payload["user"]["id"])
    return payload["user"], {"Authorization": f"Bearer {payload['access_token']}"}


@pytest.fixture(scope="module")
def world():
    async def build():
        smo_a, smo_a_h = await register("smo", ORG_A)
        smo_b, smo_b_h = await register("smo", ORG_B)
        pi_a, pi_a_h = await register("pi", ORG_A)
        pi_b, pi_b_h = await register("pi", ORG_B)
        async with client() as cli:
            trial_a_r = await cli.post("/api/trials", headers=pi_a_h, json={
                "title": "SMO A Trial", "protocol_id": f"SMO-{RUN_ID}-A",
                "phase": "Phase II", "condition": "Testing",
                "sponsor_name": "External Sponsor A", "target_enrollment": 20,
            })
            trial_b_r = await cli.post("/api/trials", headers=pi_b_h, json={
                "title": "SMO B Trial", "protocol_id": f"SMO-{RUN_ID}-B",
                "phase": "Phase III", "condition": "Testing",
                "sponsor_name": "External Sponsor B", "target_enrollment": 30,
            })
        assert trial_a_r.status_code == 200, trial_a_r.text
        assert trial_b_r.status_code == 200, trial_b_r.text
        trial_a, trial_b = trial_a_r.json(), trial_b_r.json()
        TRIAL_IDS.extend([trial_a["id"], trial_b["id"]])

        patient_id = str(uuid.uuid4())
        PATIENT_IDS.append(patient_id)
        await server.db.patients.insert_one({
            "id": patient_id, "full_name": "Must Not Leak",
            "email": f"private-{RUN_ID}@example.com",
            "trial_id": trial_a["id"], "pi_id": pi_a["id"],
            "subject_id": f"SUBJ-{RUN_ID}", "avatar_initials": "MN",
            "status": "active", "created_at": server.now(),
        })
        return {
            "smo_a": smo_a, "smo_a_h": smo_a_h,
            "smo_b": smo_b, "smo_b_h": smo_b_h,
            "trial_a": trial_a, "trial_b": trial_b,
        }
    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    async def clean():
        await server.db.visit_instances.delete_many({"patient_id": {"$in": PATIENT_IDS}})
        await server.db.patients.delete_many({"id": {"$in": PATIENT_IDS}})
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({"name": {"$in": [ORG_A, ORG_B]}})
    run(clean())
    LOOP.close()


def test_smo_dashboard_is_scoped_and_deidentified(world):
    async def flow():
        async with client() as cli:
            response = await cli.get("/api/smo/dashboard", headers=world["smo_a_h"])
        assert response.status_code == 200, response.text
        payload = response.json()
        trial_ids = {trial["id"] for trial in payload["trials"]}
        assert world["trial_a"]["id"] in trial_ids
        assert world["trial_b"]["id"] not in trial_ids
        assert payload["totals"]["subjects"] == 1
        assert payload["subjects"][0]["subject_id"] == f"SUBJ-{RUN_ID}"
        lowered = response.text.lower()
        assert "must not leak" not in lowered
        assert f"private-{RUN_ID}".lower() not in lowered
    run(flow())


def test_non_smo_role_cannot_use_smo_dashboard(world):
    async def flow():
        _, pi_headers = await register("pi", ORG_A)
        async with client() as cli:
            response = await cli.get("/api/smo/dashboard", headers=pi_headers)
            assert response.status_code == 403, response.text
            response2 = await cli.get("/api/site/dashboard", headers=pi_headers)
            assert response2.status_code == 403, response2.text
    run(flow())


def test_site_role_gets_operational_dashboard_not_admin_console(world):
    """A `site` account uses the OPERATIONAL /site/dashboard contract; org
    governance stays in the org-admin console endpoints, which stay gated."""
    async def flow():
        site, site_h = await register("site", ORG_A)
        async with client() as cli:
            response = await cli.get("/api/site/dashboard", headers=site_h)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["organization"]["name"] == ORG_A
            assert payload["organization"]["org_admin"] is False
            for key in ("trials", "sites", "subjects", "sponsors"):
                assert key in payload["totals"]
            # operational access does NOT imply governance: without the
            # org_admin flag the org-admin console endpoints still 403.
            org = await server.db.organizations.find_one({"name": ORG_A}, {"_id": 0})
            assert org, "organization record missing"
            console = await cli.get(f"/api/org/{org['id']}/members", headers=site_h)
            assert console.status_code == 403, console.text
    run(flow())
