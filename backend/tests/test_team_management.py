"""Scoped, capability-aware clinical team editing and removal."""
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
USER_IDS = []


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


async def add_user(role, organization, *, org_admin=False):
    user = {
        "id": str(uuid.uuid4()),
        "email": f"{role}-{RUN_ID}-{uuid.uuid4().hex[:5]}@example.com",
        "full_name": f"{role.upper()} {RUN_ID}",
        "role": role,
        "organization": organization,
        "org_admin": org_admin,
        "status": "Active",
        "created_at": server.now(),
    }
    await server.db.users.insert_one(user)
    USER_IDS.append(user["id"])
    return user


def test_team_capabilities_patch_remove_and_scope():
    async def flow():
        org = f"TEAM-{RUN_ID}"
        admin = await add_user("sponsor", org, org_admin=True)
        pi = await add_user("pi", org)
        crc = await add_user("crc", org)
        foreign = await add_user("crc", f"FOREIGN-{RUN_ID}")

        async with client() as cli:
            listing = await cli.get("/api/team", headers=headers(admin))
            assert listing.status_code == 200, listing.text
            rows = {row["id"]: row for row in listing.json()}
            assert rows[pi["id"]]["capabilities"] == {
                "can_edit": True, "can_remove": True,
            }
            assert rows[crc["id"]]["capabilities"]["can_edit"] is True

            read_only = await cli.get("/api/team", headers=headers(pi))
            read_only_rows = {row["id"]: row for row in read_only.json()}
            assert read_only_rows[crc["id"]]["capabilities"] == {
                "can_edit": False, "can_remove": False,
            }

            invite_denied = await cli.post(
                "/api/invitations",
                headers=headers(pi),
                json={
                    "email": f"invite-{RUN_ID}@example.com",
                    "full_name": "Not Allowed",
                    "role": "crc",
                },
            )
            assert invite_denied.status_code == 403
            assert "Organization Admin" in invite_denied.json()["detail"]

            patient_invite_reaches_validation = await cli.post(
                "/api/invitations",
                headers=headers(pi),
                json={"role": "patient"},
            )
            assert patient_invite_reaches_validation.status_code == 400
            assert patient_invite_reaches_validation.json()["detail"] == (
                "Email or phone required")

            denied = await cli.patch(
                f"/api/team/{crc['id']}",
                headers=headers(pi),
                json={"designation": "Not allowed"},
            )
            assert denied.status_code == 403

            foreign_denied = await cli.patch(
                f"/api/team/{foreign['id']}",
                headers=headers(admin),
                json={"designation": "Not allowed"},
            )
            assert foreign_denied.status_code == 403

            updated = await cli.patch(
                f"/api/team/{crc['id']}",
                headers=headers(admin),
                json={
                    "full_name": "Updated Coordinator",
                    "designation": "Senior CRC",
                    "phone": "+91 9000000000",
                    "role": "crc",
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["designation"] == "Senior CRC"
            assert updated.json()["full_name"] == "Updated Coordinator"

            last_pi = await cli.delete(
                f"/api/team/{pi['id']}", headers=headers(admin))
            assert last_pi.status_code == 409

            removed = await cli.delete(
                f"/api/team/{crc['id']}", headers=headers(admin))
            assert removed.status_code == 200, removed.text
            assert removed.json()["removed"] is True

        stored = await server.db.users.find_one({"id": crc["id"]}, {"_id": 0})
        assert stored["status"] == "Suspended"
        assert stored["force_logout_at"]
        assert await server.db.audit_logs.count_documents({
            "target_id": crc["id"],
            "action": {"$in": ["team.member_update", "team.member_remove"]},
        }) == 2
        assert await server.db.notifications.count_documents({
            "user_id": crc["id"], "type": "team",
        }) == 2

    try:
        run(flow())
    finally:
        async def cleanup():
            await server.db.audit_logs.delete_many(
                {"target_id": {"$in": USER_IDS}})
            await server.db.notifications.delete_many(
                {"user_id": {"$in": USER_IDS}})
            await server.db.users.delete_many({"id": {"$in": USER_IDS}})

        run(cleanup())


def teardown_module():
    LOOP.close()
