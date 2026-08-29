"""Fail-closed authorization for PI/CRC clinical visit operations."""
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
ORG_A = f"CLINICAL-{RUN_ID}-A"
ORG_B = f"CLINICAL-{RUN_ID}-B"
LOOP = asyncio.new_event_loop()
USER_IDS = []
TRIAL_IDS = []
VISIT_IDS = []
PATIENT_IDS = []
INSTANCE_IDS = []


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
            "email": email,
            "password": PASSWORD,
            "full_name": f"Clinical {role.upper()} {RUN_ID}",
            "role": role,
            "organization": org,
        })
    assert response.status_code == 200, response.text
    payload = response.json()
    USER_IDS.append(payload["user"]["id"])
    return payload["user"], {
        "Authorization": f"Bearer {payload['access_token']}"
    }


@pytest.fixture(scope="module")
def world():
    async def build():
        sponsor_a, sponsor_a_h = await register("sponsor", ORG_A)
        sponsor_b, sponsor_b_h = await register("sponsor", ORG_B)
        pi_a, pi_a_h = await register("pi", ORG_A)
        pi_b, pi_b_h = await register("pi", ORG_B)
        crc_a, crc_a_h = await register("crc", ORG_A)

        trial_id = str(uuid.uuid4())
        visit_id = str(uuid.uuid4())
        patient_id = str(uuid.uuid4())
        instance_id = str(uuid.uuid4())
        TRIAL_IDS.append(trial_id)
        VISIT_IDS.append(visit_id)
        PATIENT_IDS.append(patient_id)
        INSTANCE_IDS.append(instance_id)

        await server.db.trials.insert_one({
            "id": trial_id,
            "title": f"Clinical Authz {RUN_ID}",
            "protocol_id": f"AUTHZ-{RUN_ID}",
            "phase": "Phase II",
            "condition": "Testing",
            "sponsor_name": ORG_A,
            "created_by": sponsor_a["id"],
            "status": "active",
            "created_at": server.now(),
        })
        await server.db.visits.insert_one({
            "id": visit_id,
            "trial_id": trial_id,
            "visit_number": 1,
            "name": "Screening",
            "day_offset": 0,
            "window_days": 3,
            "activities": [],
            "created_at": server.now(),
        })
        await server.db.patients.insert_one({
            "id": patient_id,
            "full_name": f"Scoped Patient {RUN_ID}",
            "email": f"patient-{RUN_ID}@example.com",
            "trial_id": trial_id,
            "pi_id": pi_a["id"],
            "crc_id": crc_a["id"],
            "created_at": server.now(),
        })
        await server.db.visit_instances.insert_one({
            "id": instance_id,
            "patient_id": patient_id,
            "trial_id": trial_id,
            "visit_template_id": visit_id,
            "name": "Screening",
            "seq": 1,
            "status": "upcoming",
            "scheduled_date": server.now(),
            "window_days": 3,
        })
        return {
            "sponsor_a_h": sponsor_a_h,
            "sponsor_b_h": sponsor_b_h,
            "pi_a_h": pi_a_h,
            "pi_b_h": pi_b_h,
            "crc_a_h": crc_a_h,
            "trial_id": trial_id,
            "visit_id": visit_id,
            "instance_id": instance_id,
        }

    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.audit_logs.delete_many({
            "$or": [
                {"target_id": {"$in": VISIT_IDS}},
                {"target_id": {"$in": INSTANCE_IDS}},
            ]
        })
        await server.db.visit_instances.delete_many({"id": {"$in": INSTANCE_IDS}})
        await server.db.patients.delete_many({"id": {"$in": PATIENT_IDS}})
        await server.db.visits.delete_many({"id": {"$in": VISIT_IDS}})
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({
            "name": {"$in": [ORG_A, ORG_B]}
        })

    run(clean())
    LOOP.close()


def test_legacy_visit_patch_is_trial_scoped(world):
    async def flow():
        async with client() as cli:
            blocked = await cli.patch(
                f"/api/visits/{world['visit_id']}",
                headers=world["pi_b_h"],
                json={"note": "foreign write"},
            )
            allowed = await cli.patch(
                f"/api/visits/{world['visit_id']}",
                headers=world["pi_a_h"],
                json={"note": "reviewed by local PI"},
            )

        assert blocked.status_code == 403, blocked.text
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["note"] == "reviewed by local PI"

    run(flow())


def test_sponsor_cannot_mutate_subject_visit_instance(world):
    async def flow():
        async with client() as cli:
            response = await cli.patch(
                f"/api/visit-instances/{world['instance_id']}",
                headers=world["sponsor_a_h"],
                json={"status": "completed"},
            )
        assert response.status_code == 403, response.text
        stored = await server.db.visit_instances.find_one(
            {"id": world["instance_id"]}, {"_id": 0})
        assert stored["status"] == "upcoming"

    run(flow())


def test_assigned_crc_can_complete_subject_visit(world):
    async def flow():
        async with client() as cli:
            response = await cli.patch(
                f"/api/visit-instances/{world['instance_id']}",
                headers=world["crc_a_h"],
                json={"status": "completed"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"

    run(flow())
