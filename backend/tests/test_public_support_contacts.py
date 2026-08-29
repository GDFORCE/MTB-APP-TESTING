"""Public support and exact organization-contact data contracts."""
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
ORG_NAME = f"CONTACT-{RUN_ID} Hospital"
ORG_ID = str(uuid.uuid4())
ADMIN_ID = str(uuid.uuid4())
STAFF_ID = str(uuid.uuid4())
GOOGLE_PLACE_ID = f"ChIJcontact{RUN_ID}"
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


@pytest.fixture(scope="module", autouse=True)
def world():
    async def build():
        await server.db.organizations.insert_one({
            "id": ORG_ID,
            "name": ORG_NAME,
            "type": "site",
            "google_place_id": GOOGLE_PLACE_ID,
            "status": "active",
            "email": "public-office@example.com",
            "contact": "+91-1800-000-000",
            "created_at": server.now(),
        })
        await server.db.users.insert_many([
            {
                "id": STAFF_ID,
                "full_name": "Private Staff Member",
                "email": f"private-{RUN_ID}@example.com",
                "phone": "+91-99999-11111",
                "role": "crc",
                "organization": ORG_NAME,
                "org_admin": False,
                "status": "Active",
                "created_at": server.now(),
            },
            {
                "id": ADMIN_ID,
                "full_name": "Registered Contact Admin",
                "email": f"admin-{RUN_ID}@example.com",
                "phone": "+91-99999-22222",
                "role": "pi",
                "organization": ORG_NAME,
                "org_admin": True,
                "status": "Active",
                "profile": {"designation": "Site Platform Administrator"},
                "created_at": server.now(),
            },
        ])

    run(build())
    yield

    async def clean():
        await server.db.users.delete_many({"id": {"$in": [ADMIN_ID, STAFF_ID]}})
        await server.db.organizations.delete_one({"id": ORG_ID})

    run(clean())
    LOOP.close()


def test_support_contact_is_public_and_uses_config_contract():
    async def flow():
        async with client() as cli:
            response = await cli.get("/api/support/contact")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["name"] == "MTB Platform Support"
        assert payload["email"]
        assert payload["phone"]
        assert payload["hours"]
        assert payload["channels"] == {"email": True, "phone": True}
        assert "key" not in payload

    run(flow())


def test_exact_organization_contact_returns_only_platform_admin():
    async def flow():
        async with client() as cli:
            response = await cli.get(
                f"/api/organizations/{ORG_ID}/platform-contact")
            support_response = await cli.get("/api/support/contact")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["organization"] == {
            "id": ORG_ID, "name": ORG_NAME, "type": "site"
        }
        contact = payload["platform_contact"]
        assert contact == {
            "name": "MTB Platform Support",
            "designation": "Platform Administrator",
            "email": support_response.json()["email"],
            "phone": support_response.json()["phone"],
        }
        assert f"private-{RUN_ID}" not in response.text
        assert f"admin-{RUN_ID}" not in response.text
        assert "Registered Contact Admin" not in response.text
        assert "hashed_password" not in response.text

    run(flow())


def test_registration_check_is_exact_normalized_and_returns_admin_contact():
    async def flow():
        async with client() as cli:
            existing = await cli.get(
                "/api/organizations/registration-check",
                params={"name": f"  {ORG_NAME.lower().replace(' ', '   ')}  "},
            )
            missing = await cli.get(
                "/api/organizations/registration-check",
                params={"name": f"Missing {RUN_ID}"},
            )
            place_match = await cli.get(
                "/api/organizations/registration-check",
                params={
                    "name": "A different Google display name",
                    "google_place_id": GOOGLE_PLACE_ID,
                },
            )
        assert existing.status_code == 200, existing.text
        assert existing.json()["exists"] is True
        assert existing.json()["organization"] == {
            "id": ORG_ID, "name": ORG_NAME, "type": "site"
        }
        async with client() as cli:
            support = await cli.get("/api/support/contact")
        assert existing.json()["platform_contact"]["email"] == support.json()["email"]
        assert place_match.status_code == 200, place_match.text
        assert place_match.json()["organization"] == {
            "id": ORG_ID, "name": ORG_NAME, "type": "site"
        }
        assert f"admin-{RUN_ID}" not in existing.text
        assert missing.status_code == 200, missing.text
        assert missing.json() == {
            "exists": False,
            "organization": None,
            "platform_contact": None,
        }

    run(flow())


def test_unknown_organization_contact_is_404():
    async def flow():
        async with client() as cli:
            response = await cli.get(
                f"/api/organizations/{uuid.uuid4()}/platform-contact")
        assert response.status_code == 404, response.text

    run(flow())
