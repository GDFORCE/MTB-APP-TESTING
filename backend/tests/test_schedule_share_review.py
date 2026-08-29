"""Persistent sponsor share -> assigned PI review workflow."""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]
IDS = {}


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


def headers(user):
    return {"Authorization": f"Bearer {server.make_token(user['id'], user['role'])}"}


async def build_world():
    sponsor = {
        "id": str(uuid.uuid4()), "email": f"sponsor-{RUN_ID}@example.com",
        "full_name": "Schedule Sponsor", "role": "sponsor",
        "organization": f"Schedule Sponsor {RUN_ID}", "status": "Active",
    }
    pi_a = {
        "id": str(uuid.uuid4()), "email": f"pi-a-{RUN_ID}@example.com",
        "full_name": "Dr PI A", "role": "pi",
        "organization": f"Site A {RUN_ID}", "status": "Active",
    }
    pi_b = {
        "id": str(uuid.uuid4()), "email": f"pi-b-{RUN_ID}@example.com",
        "full_name": "Dr PI B", "role": "pi",
        "organization": f"Site B {RUN_ID}", "status": "Active",
    }
    outsider = {
        "id": str(uuid.uuid4()), "email": f"pi-x-{RUN_ID}@example.com",
        "full_name": "Dr Outside", "role": "pi",
        "organization": f"Other Site {RUN_ID}", "status": "Active",
    }
    sponsor_org = {
        "id": str(uuid.uuid4()),
        "name": sponsor["organization"],
        "type": "sponsor",
        "status": "active",
    }
    await server.db.organizations.insert_one(sponsor_org)
    await server.db.users.insert_many([sponsor, pi_a, pi_b, outsider])
    trial = {
        "id": str(uuid.uuid4()), "title": "Persistent Schedule Trial",
        "protocol_id": f"SCHED-{RUN_ID}", "phase": "Phase II",
        "condition": "Testing", "sponsor_name": sponsor["organization"],
        "created_by": sponsor["id"], "created_at": server.now(), "status": "active",
    }
    await server.db.trials.insert_one(trial)
    visits = [
        {
            "id": str(uuid.uuid4()), "trial_id": trial["id"], "visit_number": number,
            "name": name, "day_offset": offset, "window_days": 3, "activities": [],
        }
        for number, name, offset in [(1, "Screening", -7), (2, "Baseline", 0)]
    ]
    await server.db.visits.insert_many(visits)
    shared_document = {
        "id": str(uuid.uuid4()), "key": str(uuid.uuid4()),
        "owner_id": sponsor["id"],
        "scope": {"type": "trial", "id": trial["id"]},
        "name": "Protocol schedule v2.pdf",
        "content_type": "application/pdf", "size": 1024,
        "created_at": server.now(),
    }
    await server.db.files.insert_one(shared_document)
    patients = []
    for pi in (pi_a, pi_b):
        patients.append({
            "id": str(uuid.uuid4()), "full_name": f"Patient {pi['full_name']}",
            "email": f"patient-{uuid.uuid4().hex[:5]}@example.com",
            "trial_id": trial["id"], "pi_id": pi["id"], "created_by": pi["id"],
            "created_at": server.now(), "status": "active",
        })
    await server.db.patients.insert_many(patients)
    IDS.update({
        "users": [u["id"] for u in (sponsor, pi_a, pi_b, outsider)],
        "trial": trial["id"], "visits": [v["id"] for v in visits],
        "patients": [p["id"] for p in patients],
        "files": [shared_document["id"]],
        "organizations": [sponsor_org["id"]],
    })
    return sponsor, pi_a, pi_b, outsider, trial, shared_document


def test_share_creates_site_reviews_and_decisions_are_isolated():
    async def flow():
        sponsor, pi_a, pi_b, outsider, trial, shared_document = await build_world()
        async with client() as cli:
            shared = await cli.post("/api/shares", headers=headers(sponsor), json={
                "trial_id": trial["id"],
                "via": "in_app",
                "recipients": [],
                "sites": [
                    {"id": f"pi-{pi_a['id']}", "name": pi_a["organization"], "reviewer_id": pi_a["id"]},
                    {"id": f"pi-{pi_b['id']}", "name": pi_b["organization"], "reviewer_id": pi_b["id"]},
                ],
                "message": "Please review the updated windows.",
                "document_id": shared_document["id"],
                "version_note": "Visit 2 window updated",
            })
            assert shared.status_code == 200, shared.text
            assert shared.json()["via"] == "in_app"
            assert shared.json()["recipients"] == []
            assert len(shared.json()["review_ids"]) == 2
            assert shared.json()["document_id"] == shared_document["id"]

            inbox_a = await cli.get("/api/schedule-reviews", headers=headers(pi_a))
            assert inbox_a.status_code == 200, inbox_a.text
            assert len(inbox_a.json()) == 1
            row_a = inbox_a.json()[0]
            assert row_a["reviewer_id"] == pi_a["id"]
            assert row_a["message"] == "Please review the updated windows."
            assert row_a["document_id"] == shared_document["id"]
            assert row_a["document_name"] == "Protocol schedule v2.pdf"
            assert len(row_a["visits"]) == 2

            forbidden = await cli.post(
                f"/api/schedule-reviews/{row_a['id']}/approve",
                headers=headers(outsider), json={"notes": ""},
            )
            assert forbidden.status_code == 403

            approved = await cli.post(
                f"/api/schedule-reviews/{row_a['id']}/approve",
                headers=headers(pi_a), json={"notes": "Site A can support this schedule."},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "approved"
            assert approved.json()["trial_schedule_status"] == "pending_review"

            duplicate = await cli.post(
                f"/api/schedule-reviews/{row_a['id']}/approve",
                headers=headers(pi_a), json={"notes": ""},
            )
            assert duplicate.status_code == 409

            inbox_b = await cli.get("/api/schedule-reviews", headers=headers(pi_b))
            row_b = inbox_b.json()[0]
            rejected = await cli.post(
                f"/api/schedule-reviews/{row_b['id']}/reject",
                headers=headers(pi_b),
                json={"reason": "The baseline window conflicts with site capacity.", "notes": "Revise Visit 2."},
            )
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["status"] == "rejected"
            assert rejected.json()["trial_schedule_status"] == "flagged"

        fresh = await server.db.trials.find_one({"id": trial["id"]}, {"_id": 0})
        assert fresh["schedule_status"] == "flagged"
        assert await server.db.notifications.count_documents({
            "user_id": {"$in": [pi_a["id"], pi_b["id"]]},
            "schedule_review_id": {"$exists": True},
        }) == 2
        assert await server.db.audit_logs.count_documents({
            "trial_id": trial["id"],
            "action": {"$in": ["schedule_review.approved", "schedule_review.rejected"]},
        }) == 2

    try:
        run(flow())
    finally:
        async def cleanup():
            trial_id = IDS.get("trial")
            if trial_id:
                await server.db.schedule_reviews.delete_many({"trial_id": trial_id})
                await server.db.shares.delete_many({"trial_id": trial_id})
                await server.db.notifications.delete_many({"trial_id": trial_id})
                await server.db.audit_logs.delete_many({"trial_id": trial_id})
                await server.db.visit_instances.delete_many({"trial_id": trial_id})
                await server.db.visits.delete_many({"trial_id": trial_id})
                await server.db.patients.delete_many({"trial_id": trial_id})
                await server.db.trials.delete_many({"id": trial_id})
            if IDS.get("users"):
                await server.db.users.delete_many({"id": {"$in": IDS["users"]}})
            if IDS.get("files"):
                await server.db.files.delete_many({"id": {"$in": IDS["files"]}})
            if IDS.get("organizations"):
                await server.db.organizations.delete_many(
                    {"id": {"$in": IDS["organizations"]}})
        run(cleanup())


def teardown_module():
    LOOP.close()
