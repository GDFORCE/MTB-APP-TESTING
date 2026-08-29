"""Focused authorization tests for REST and WebSocket clinical chat."""
import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import WebSocketDisconnect

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = "Password1!"
ORG_A = f"CHAT-{RUN_ID}-A"
ORG_B = f"CHAT-{RUN_ID}-B"
LOOP = asyncio.new_event_loop()
USER_IDS = []
PATIENT_IDS = []
CONVERSATION_IDS = []


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
            "full_name": f"Chat {role.upper()} {RUN_ID}",
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
        pi_a, pi_a_h = await register("pi", ORG_A)
        crc_a, crc_a_h = await register("crc", ORG_A)
        pi_b, pi_b_h = await register("pi", ORG_B)
        patient, patient_h = await register("patient", "")

        patient_id = str(uuid.uuid4())
        PATIENT_IDS.append(patient_id)
        await server.db.patients.insert_one({
            "id": patient_id,
            "full_name": patient["full_name"],
            "email": patient["email"],
            "user_id": patient["id"],
            "trial_id": f"chat-trial-{RUN_ID}",
            "pi_id": pi_a["id"],
            "crc_id": crc_a["id"],
            "created_at": server.now(),
        })
        return {
            "pi_a": pi_a, "pi_a_h": pi_a_h,
            "crc_a": crc_a, "crc_a_h": crc_a_h,
            "pi_b": pi_b, "pi_b_h": pi_b_h,
            "patient": patient, "patient_h": patient_h,
        }

    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.messages.delete_many({
            "conversation_id": {"$in": CONVERSATION_IDS}
        })
        await server.db.conversations.delete_many({
            "id": {"$in": CONVERSATION_IDS}
        })
        await server.db.patients.delete_many({"id": {"$in": PATIENT_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({
            "name": {"$in": [ORG_A, ORG_B]}
        })

    run(clean())
    LOOP.close()


def test_conversation_creation_allows_only_valid_relationships(world):
    async def flow():
        async with client() as cli:
            same_org = await cli.post(
                "/api/conversations", headers=world["pi_a_h"],
                json={"participant_ids": [world["crc_a"]["id"]]},
            )
            assigned_patient = await cli.post(
                "/api/conversations", headers=world["patient_h"],
                json={"participant_ids": [world["pi_a"]["id"]]},
            )
            unrelated = await cli.post(
                "/api/conversations", headers=world["patient_h"],
                json={"participant_ids": [world["pi_b"]["id"]]},
            )
            unknown = await cli.post(
                "/api/conversations", headers=world["pi_a_h"],
                json={"participant_ids": [f"missing-{RUN_ID}"]},
            )

        assert same_org.status_code == 200, same_org.text
        assert assigned_patient.status_code == 200, assigned_patient.text
        assert unrelated.status_code == 403, unrelated.text
        assert unknown.status_code == 404, unknown.text
        CONVERSATION_IDS.extend([
            same_org.json()["id"], assigned_patient.json()["id"]
        ])

    run(flow())


def test_history_message_and_read_are_membership_scoped(world):
    async def flow():
        async with client() as cli:
            created = await cli.post(
                "/api/conversations", headers=world["pi_a_h"],
                json={"participant_ids": [world["crc_a"]["id"]]},
            )
            assert created.status_code == 200, created.text
            cid = created.json()["id"]
            if cid not in CONVERSATION_IDS:
                CONVERSATION_IDS.append(cid)

            sent = await cli.post(
                f"/api/conversations/{cid}/messages",
                headers=world["pi_a_h"], json={"content": "  scoped hello  "},
            )
            history = await cli.get(
                f"/api/conversations/{cid}/messages",
                headers=world["crc_a_h"],
            )
            read = await cli.post(
                f"/api/conversations/{cid}/read",
                headers=world["crc_a_h"],
            )
            outsider_history = await cli.get(
                f"/api/conversations/{cid}/messages",
                headers=world["pi_b_h"],
            )
            outsider_send = await cli.post(
                f"/api/conversations/{cid}/messages",
                headers=world["pi_b_h"], json={"content": "must fail"},
            )
            outsider_read = await cli.post(
                f"/api/conversations/{cid}/read",
                headers=world["pi_b_h"],
            )
            mine = await cli.get("/api/conversations", headers=world["pi_a_h"])

        assert sent.status_code == 200, sent.text
        assert sent.json()["content"] == "scoped hello"
        # last-message attribution surfaces in the list contract: PI-A sent
        # the last message and CRC-A has read it, so the sender sees ✓✓-read.
        row = next(r for r in mine.json() if r["id"] == cid)
        assert row["last_sender_id"] == world["pi_a"]["id"]
        assert row["last_read"] is True
        assert history.status_code == 200, history.text
        assert any(row["id"] == sent.json()["id"] for row in history.json())
        assert read.status_code == 200, read.text
        assert outsider_history.status_code == 403, outsider_history.text
        assert outsider_send.status_code == 403, outsider_send.text
        assert outsider_read.status_code == 403, outsider_read.text

        stored = await server.db.messages.find_one(
            {"id": sent.json()["id"]}, {"_id": 0})
        assert world["crc_a"]["id"] in stored["read_by"]
        assert world["pi_b"]["id"] not in stored["read_by"]

    run(flow())


class FakeWebSocket:
    def __init__(self, events):
        self.events = list(events)

    async def accept(self):
        return None

    async def receive_text(self):
        if not self.events:
            raise WebSocketDisconnect()
        return json.dumps(self.events.pop(0))

    async def send_text(self, payload):
        return None

    async def close(self, code=1000):
        return None


def test_websocket_typing_and_read_ignore_non_member(world, monkeypatch):
    async def flow():
        cid = str(uuid.uuid4())
        CONVERSATION_IDS.append(cid)
        await server.db.conversations.insert_one({
            "id": cid,
            "participant_ids": [world["pi_a"]["id"], world["crc_a"]["id"]],
            "title": "",
            "is_group": False,
            "last_message": "private",
            "created_at": server.now(),
            "updated_at": server.now(),
        })
        message_id = str(uuid.uuid4())
        await server.db.messages.insert_one({
            "id": message_id,
            "conversation_id": cid,
            "sender_id": world["pi_a"]["id"],
            "content": "private",
            "created_at": server.now(),
            "read_by": {world["pi_a"]["id"]: server.now()},
        })

        delivered = []

        async def capture(user_id, payload):
            delivered.append((user_id, payload))

        monkeypatch.setattr(server.manager, "send", capture)
        ws = FakeWebSocket([
            {"type": "typing", "conversation_id": cid},
            {"type": "read", "conversation_id": cid},
        ])
        token = server.make_token(world["pi_b"]["id"], "pi")
        await server.ws_endpoint(ws, token)

        stored = await server.db.messages.find_one(
            {"id": message_id}, {"_id": 0})
        assert delivered == []
        assert world["pi_b"]["id"] not in stored["read_by"]

    run(flow())


def test_conversation_flags_pin_mute_are_member_scoped(world):
    """Per-user pin/mute flags: members can set/clear them, they surface in
    the list contract, and non-members are rejected."""
    async def flow():
        async with client() as cli:
            created = await cli.post(
                "/api/conversations", headers=world["pi_a_h"],
                json={"participant_ids": [world["crc_a"]["id"]]})
            assert created.status_code == 200, created.text
            cid = created.json()["id"]
            CONVERSATION_IDS.append(cid)
            # empty body → 400
            empty = await cli.post(f"/api/conversations/{cid}/flags",
                                   headers=world["pi_a_h"], json={})
            assert empty.status_code == 400, empty.text
            # pin + mute for PI-A only
            flagged = await cli.post(f"/api/conversations/{cid}/flags",
                                     headers=world["pi_a_h"],
                                     json={"pinned": True, "muted": True})
            assert flagged.status_code == 200, flagged.text
            assert flagged.json() == {"ok": True, "pinned": True, "muted": True, "archived": False}
            mine = await cli.get("/api/conversations", headers=world["pi_a_h"])
            row = next(r for r in mine.json() if r["id"] == cid)
            assert row["pinned"] is True and row["muted"] is True
            # the flags are per-user: CRC-A sees them unset
            theirs = await cli.get("/api/conversations", headers=world["crc_a_h"])
            other_row = next(r for r in theirs.json() if r["id"] == cid)
            assert other_row["pinned"] is False and other_row["muted"] is False
            # clearing works
            cleared = await cli.post(f"/api/conversations/{cid}/flags",
                                     headers=world["pi_a_h"],
                                     json={"pinned": False})
            assert cleared.json()["pinned"] is False
            assert cleared.json()["muted"] is True
            # non-member is rejected
            outsider = await cli.post(f"/api/conversations/{cid}/flags",
                                      headers=world["pi_b_h"],
                                      json={"pinned": True})
            assert outsider.status_code in (403, 404), outsider.text
    run(flow())


def test_group_settings_rename_is_admin_gated(world):
    """Only the creator (admin) of a group conversation may rename it or
    edit its description via PATCH /conversations/{cid}/settings."""
    async def flow():
        async with client() as cli:
            created = await cli.post(
                "/api/conversations", headers=world["pi_a_h"],
                json={
                    "participant_ids": [world["crc_a"]["id"]],
                    "is_group": True,
                    "title": "Original Title",
                },
            )
            assert created.status_code == 200, created.text
            cid = created.json()["id"]
            CONVERSATION_IDS.append(cid)

            renamed = await cli.patch(
                f"/api/conversations/{cid}/settings",
                headers=world["pi_a_h"],
                json={"title": "Apollo Mumbai — Site Team", "description": "Coordination channel."},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["title"] == "Apollo Mumbai — Site Team"
            assert renamed.json()["description"] == "Coordination channel."

            fetched = await cli.get(f"/api/conversations/{cid}", headers=world["pi_a_h"])
            assert fetched.json()["title"] == "Apollo Mumbai — Site Team"
            assert fetched.json()["description"] == "Coordination channel."

            forbidden = await cli.patch(
                f"/api/conversations/{cid}/settings",
                headers=world["crc_a_h"],
                json={"title": "Hijacked"},
            )
            assert forbidden.status_code == 403, forbidden.text

    run(flow())
