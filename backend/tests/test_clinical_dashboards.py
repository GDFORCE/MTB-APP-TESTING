"""Normalized PI/CRC dashboards and clinical trial-detail authorization."""
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
LOOP = asyncio.new_event_loop()
IDS = {
    "users": [], "trials": [], "patients": [], "instances": [],
    "reviews": [], "versions": [],
}


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


def headers(user):
    return {
        "Authorization": (
            f"Bearer {server.make_token(user['id'], user['role'], 'access')}"
        )
    }


async def add_user(role, org):
    user = {
        "id": str(uuid.uuid4()),
        "email": f"{role}-{RUN_ID}-{uuid.uuid4().hex[:5]}@example.com",
        "full_name": f"{role.upper()} {RUN_ID}",
        "role": role,
        "organization": org,
        "avatar_initials": role.upper()[:2],
        "created_at": server.now(),
    }
    await server.db.users.insert_one(user)
    IDS["users"].append(user["id"])
    return user


@pytest.fixture(scope="module")
def world():
    async def build():
        org_a = f"CLIN-DASH-{RUN_ID}-A"
        org_b = f"CLIN-DASH-{RUN_ID}-B"
        sponsor = await add_user("sponsor", f"SPONSOR-{RUN_ID}")
        pi = await add_user("pi", org_a)
        crc = await add_user("crc", org_a)
        foreign_pi = await add_user("pi", org_b)
        foreign_crc = await add_user("crc", org_b)

        trial_a = {
            "id": str(uuid.uuid4()),
            "title": "Assigned Trial",
            "protocol_id": f"DASH-{RUN_ID}-A",
            "phase": "Phase II",
            "condition": "Testing",
            "sponsor_name": sponsor["organization"],
            "created_by": sponsor["id"],
            "status": "active",
            "created_at": server.now(),
        }
        trial_b = {
            **trial_a,
            "id": str(uuid.uuid4()),
            "title": "Foreign Trial",
            "protocol_id": f"DASH-{RUN_ID}-B",
        }
        await server.db.trials.insert_many([trial_a, trial_b])
        IDS["trials"].extend([trial_a["id"], trial_b["id"]])

        patient_a = {
            "id": str(uuid.uuid4()),
            "subject_id": f"SUBJ-{RUN_ID}",
            "full_name": "Scoped Subject",
            "trial_id": trial_a["id"],
            "pi_id": pi["id"],
            "crc_id": crc["id"],
            "created_at": server.now(),
        }
        patient_b = {
            **patient_a,
            "id": str(uuid.uuid4()),
            "subject_id": f"FOREIGN-{RUN_ID}",
            "full_name": "Foreign Subject",
            "trial_id": trial_b["id"],
            "pi_id": foreign_pi["id"],
            "crc_id": foreign_crc["id"],
        }
        await server.db.patients.insert_many([patient_a, patient_b])
        IDS["patients"].extend([patient_a["id"], patient_b["id"]])

        start = server.now().replace(hour=0, minute=0, second=0, microsecond=0)
        instances = [
            {
                "id": str(uuid.uuid4()), "patient_id": patient_a["id"],
                "trial_id": trial_a["id"], "name": "Completed Today",
                "seq": 1, "visit_number": 1, "scheduled_date": start + timedelta(hours=8),
                "status": "completed",
            },
            {
                "id": str(uuid.uuid4()), "patient_id": patient_a["id"],
                "trial_id": trial_a["id"], "name": "Pending Today",
                "seq": 2, "visit_number": 2, "scheduled_date": start + timedelta(hours=11),
                "status": "upcoming",
            },
            {
                "id": str(uuid.uuid4()), "patient_id": patient_a["id"],
                "trial_id": trial_a["id"], "name": "Overdue",
                "seq": 3, "visit_number": 3, "scheduled_date": start - timedelta(days=1),
                "status": "upcoming",
            },
        ]
        await server.db.visit_instances.insert_many(instances)
        IDS["instances"].extend(row["id"] for row in instances)

        review = {
            "id": str(uuid.uuid4()), "trial_id": trial_a["id"],
            "reviewer_id": pi["id"], "site_name": org_a,
            "status": "pending", "created_at": server.now(),
        }
        await server.db.schedule_reviews.insert_one(review)
        IDS["reviews"].append(review["id"])

        version = {
            "id": str(uuid.uuid4()), "trial_id": trial_a["id"],
            "version": 1, "visits": [], "changed_visits": [],
            "created_at": server.now(),
        }
        await server.db.schedule_versions.insert_one(version)
        IDS["versions"].append(version["id"])
        return {
            "pi": pi, "crc": crc, "foreign_pi": foreign_pi,
            "foreign_crc": foreign_crc, "sponsor": sponsor,
            "trial": trial_a, "foreign_trial": trial_b,
            "patient": patient_a, "review": review,
        }

    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.schedule_versions.delete_many({"id": {"$in": IDS["versions"]}})
        await server.db.schedule_reviews.delete_many({"id": {"$in": IDS["reviews"]}})
        await server.db.visit_instances.delete_many({"id": {"$in": IDS["instances"]}})
        await server.db.patients.delete_many({"id": {"$in": IDS["patients"]}})
        await server.db.trials.delete_many({"id": {"$in": IDS["trials"]}})
        await server.db.users.delete_many({"id": {"$in": IDS["users"]}})

    run(clean())
    LOOP.close()


@pytest.mark.parametrize("role", ["pi", "crc"])
def test_dashboard_is_scoped_and_normalized(world, role):
    async def flow():
        actor = world[role]
        async with client() as cli:
            response = await cli.get(
                f"/api/{role}/dashboard", headers=headers(actor))
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["role"] == role
        assert payload["totals"] == {
            "trials": 1, "patients": 1, "sites": 1, "sponsors": 1,
            "team": 2, "pis": 1, "crcs": 1,
        }
        assert payload["today"] == {
            "date": server.now().date().isoformat(),
            "total": 2, "completed": 1, "pending": 1, "overdue": 1,
        }
        assert [trial["id"] for trial in payload["trials"]] == [
            world["trial"]["id"]
        ]
        assert [patient["id"] for patient in payload["patients"]] == [
            world["patient"]["id"]
        ]
        assert payload["capabilities"]["can_add_patient"] is True
        assert payload["capabilities"]["can_create_trial"] is (role == "pi")
        if role == "pi":
            review_tasks = [
                task for task in payload["tasks"]
                if task["type"] == "schedule_review"
            ]
            assert review_tasks[0]["schedule_review_id"] == world["review"]["id"]

    run(flow())


def test_dashboard_role_gates(world):
    async def flow():
        async with client() as cli:
            crc_to_pi = await cli.get(
                "/api/pi/dashboard", headers=headers(world["crc"]))
            pi_to_crc = await cli.get(
                "/api/crc/dashboard", headers=headers(world["pi"]))
            sponsor_to_pi = await cli.get(
                "/api/pi/dashboard", headers=headers(world["sponsor"]))
        assert crc_to_pi.status_code == 403
        assert pi_to_crc.status_code == 403
        assert sponsor_to_pi.status_code == 403

    run(flow())


def test_patient_care_context_includes_exact_pi_id(world):
    async def flow():
        care = await server._patient_care_context(world["patient"])
        assert care["pi_id"] == world["pi"]["id"]
        assert care["pi_name"] == world["pi"]["full_name"]

    run(flow())


@pytest.mark.parametrize("role", ["pi", "crc"])
def test_assigned_clinical_user_can_read_exact_trial_contracts(world, role):
    async def flow():
        trial_id = world["trial"]["id"]
        subject_id = world["patient"]["id"]
        paths = [
            f"/api/trials/{trial_id}/recruitment",
            f"/api/trials/{trial_id}/subjects",
            f"/api/trials/{trial_id}/subjects/{subject_id}/visits",
            f"/api/trials/{trial_id}/team",
            f"/api/trials/{trial_id}/documents",
            f"/api/trials/{trial_id}/versions",
        ]
        async with client() as cli:
            responses = [
                await cli.get(path, headers=headers(world[role]))
                for path in paths
            ]
        assert all(response.status_code == 200 for response in responses), [
            response.text for response in responses
        ]
        recruitment_sites = responses[0].json()["sites"]
        assert recruitment_sites
        assert {
            "department", "pi_name", "pi_email", "pi_phone"
        }.issubset(recruitment_sites[0])

    run(flow())


@pytest.mark.parametrize("role", ["foreign_pi", "foreign_crc"])
def test_foreign_clinical_user_cannot_read_exact_trial_contracts(world, role):
    async def flow():
        trial_id = world["trial"]["id"]
        subject_id = world["patient"]["id"]
        paths = [
            f"/api/trials/{trial_id}/recruitment",
            f"/api/trials/{trial_id}/subjects",
            f"/api/trials/{trial_id}/subjects/{subject_id}/visits",
            f"/api/trials/{trial_id}/team",
            f"/api/trials/{trial_id}/documents",
            f"/api/trials/{trial_id}/versions",
        ]
        async with client() as cli:
            responses = [
                await cli.get(path, headers=headers(world[role]))
                for path in paths
            ]
        assert all(response.status_code == 403 for response in responses), [
            response.text for response in responses
        ]

    run(flow())
