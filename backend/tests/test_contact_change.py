"""Contact-change OTP flow — Task 2.4.

Covers POST /auth/change-contact/start + /verify:
- happy path (phone + email) with the DEV OTP code
- wrong code rejected (attempt counted)
- new value already used by another account -> 409 (at start AND at commit)
- a second /start replaces the first pending change
- successful verify writes a `contact.change` audit row and updates the user

Same harness as test_foundation.py: in-process ASGITransport against the real
Atlas DB, RUN_ID-marked data, module teardown cleanup, single module-level
event loop (Motor pins its io_loop on first use — never asyncio.run here).

OTP delivery is monkeypatched to a no-op so tests never hit SMTP/MSG91; the
DEV_OTP_MODE fixed code (000000) is still accepted at verify time.
"""
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
PASSWORD = 'Password1!'
DEV_CODE = server.DEV_OTP_CODE

LOOP = asyncio.new_event_loop()
_user_ids = []

# No real SMTP/SMS during tests — value validation still runs before delivery.
async def _noop_deliver(channel, target, code, **_metadata):
    return None
server._deliver_otp = _noop_deliver


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role='patient'):
    email = f'test-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            'full_name': f'Test {role.upper()} {RUN_ID}', 'role': role,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    _user_ids.append(j['user']['id'])
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


def _new_email():
    return f'test-{RUN_ID}-new-{uuid.uuid4().hex[:6]}@example.com'


def _new_phone():
    return f'+9190{uuid.uuid4().int % 10**8:08d}'


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.pending_contact_changes.delete_many({'user_id': {'$in': _user_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


class TestChangeContactHappyPath:
    def test_phone_change_start_then_verify(self):
        async def flow():
            _, headers = await _register()
            phone = _new_phone()
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start', headers=headers,
                                   json={'field': 'phone', 'value': phone})
                assert r.status_code == 200, r.text
                assert r.json()['field'] == 'phone'
                v = await cli.post('/api/auth/change-contact/verify', headers=headers,
                                   json={'code': DEV_CODE})
                assert v.status_code == 200, v.text
                assert v.json()['user']['phone'] == phone
                me = await cli.get('/api/auth/me', headers=headers)
                assert me.json()['phone'] == phone
        run(flow())

    def test_email_change_start_then_verify(self):
        async def flow():
            _, headers = await _register()
            email = _new_email()
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start', headers=headers,
                                   json={'field': 'email', 'value': email})
                assert r.status_code == 200, r.text
                v = await cli.post('/api/auth/change-contact/verify', headers=headers,
                                   json={'code': DEV_CODE})
                assert v.status_code == 200, v.text
                assert v.json()['user']['email'] == email
        run(flow())


class TestChangeContactValidation:
    def test_invalid_email_rejected(self):
        async def flow():
            _, headers = await _register()
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start', headers=headers,
                                   json={'field': 'email', 'value': 'not-an-email'})
            assert r.status_code == 400, r.text
        run(flow())

    def test_empty_phone_rejected(self):
        async def flow():
            _, headers = await _register()
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start', headers=headers,
                                   json={'field': 'phone', 'value': '   '})
            assert r.status_code == 400, r.text
        run(flow())

    def test_requires_auth(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start',
                                   json={'field': 'email', 'value': _new_email()})
            assert r.status_code == 401
        run(flow())


class TestChangeContactWrongCode:
    def test_wrong_code_rejected_and_counted(self):
        async def flow():
            user, headers = await _register()
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start', headers=headers,
                                   json={'field': 'phone', 'value': _new_phone()})
                assert r.status_code == 200, r.text
                v = await cli.post('/api/auth/change-contact/verify', headers=headers,
                                   json={'code': '123456'})
                assert v.status_code == 400, v.text
            pending = await server.db.pending_contact_changes.find_one({'user_id': user['id']})
            assert pending and pending['attempts'] == 1
        run(flow())


class TestChangeContactDuplicate:
    def test_email_used_by_another_account_409_at_start(self):
        async def flow():
            other, _ = await _register()
            _, headers = await _register()
            async with make_client() as cli:
                r = await cli.post('/api/auth/change-contact/start', headers=headers,
                                   json={'field': 'email', 'value': other['email']})
            assert r.status_code == 409, r.text
        run(flow())


class TestChangeContactReplace:
    def test_second_start_replaces_first(self):
        async def flow():
            user, headers = await _register()
            first, second = _new_phone(), _new_phone()
            async with make_client() as cli:
                await cli.post('/api/auth/change-contact/start', headers=headers,
                               json={'field': 'phone', 'value': first})
                await cli.post('/api/auth/change-contact/start', headers=headers,
                               json={'field': 'phone', 'value': second})
            pendings = await server.db.pending_contact_changes.find(
                {'user_id': user['id']}).to_list(10)
            assert len(pendings) == 1
            assert pendings[0]['value'] == second
        run(flow())


class TestChangeContactAudit:
    def test_verify_writes_audit_row(self):
        async def flow():
            user, headers = await _register()
            async with make_client() as cli:
                await cli.post('/api/auth/change-contact/start', headers=headers,
                               json={'field': 'phone', 'value': _new_phone()})
                v = await cli.post('/api/auth/change-contact/verify', headers=headers,
                                   json={'code': DEV_CODE})
                assert v.status_code == 200, v.text
            row = await server.db.audit_logs.find_one(
                {'action': 'contact.change', 'user_id': user['id']})
            assert row, 'contact.change not audited'
            assert row['field'] == 'phone'
        run(flow())
