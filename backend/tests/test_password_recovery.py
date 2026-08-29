"""Email/phone password recovery, expiry, cooldown and resend enforcement."""
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
NEW_PASSWORD = 'Changed2@'
CREATED_USER_IDS = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url='http://testserver',
    )


@pytest.fixture(autouse=True)
def delivery(monkeypatch):
    sent = []

    async def capture(channel, target, code, **_metadata):
        sent.append((channel, target, code))

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', capture)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)
    yield sent


def teardown_module():
    async def clean():
        await server.db.refresh_tokens.delete_many(
            {'user_id': {'$in': CREATED_USER_IDS}})
        await server.db.users.delete_many({'id': {'$in': CREATED_USER_IDS}})
    run(clean())
    LOOP.close()


async def register_user(suffix: str):
    phone_local = f"9{(int(RUN_ID, 16) + len(CREATED_USER_IDS)) % 1_000_000_000:09d}"
    async with make_client() as cli:
        response = await cli.post('/api/auth/register', json={
            'email': f'test-{RUN_ID}-{suffix}@example.com',
            'phone': phone_local,
            'password': PASSWORD,
            'full_name': f'Recovery {suffix}',
            'role': 'patient',
        })
    assert response.status_code == 200, response.text
    user = response.json()['user']
    CREATED_USER_IDS.append(user['id'])
    return user


def test_recovery_works_with_email_and_phone(delivery):
    async def flow():
        for channel in ('email', 'phone'):
            user = await register_user(channel)
            payload = {channel: user[channel]}
            async with make_client() as cli:
                forgot = await cli.post('/api/auth/forgot', json=payload)
                assert forgot.status_code == 200, forgot.text
                data = forgot.json()
                assert data['channel'] == channel
                assert data['expires_in'] == server.otp_service.OTP_TTL_MIN * 60
                assert data['resend_limit'] == 3
                sent_channel, sent_target, code = delivery[-1]
                assert (sent_channel, sent_target) == (channel, user[channel])
                reset = await cli.post('/api/auth/reset', json={
                    'recovery_id': data['recovery_id'],
                    'otp': code,
                    'new_password': NEW_PASSWORD,
                })
                assert reset.status_code == 200, reset.text
                login = await cli.post('/api/auth/login', json={
                    'email': user['email'], 'password': NEW_PASSWORD,
                })
                assert login.status_code == 200, login.text

    run(flow())


def test_incorrect_otp_is_rejected_on_verification_screen(delivery):
    async def flow():
        user = await register_user('verify-immediately')
        async with make_client() as cli:
            forgot = await cli.post('/api/auth/forgot', json={'email': user['email']})
            assert forgot.status_code == 200, forgot.text
            correct_code = delivery[-1][2]
            wrong_code = '111111' if correct_code != '111111' else '222222'

            invalid = await cli.post('/api/auth/forgot/verify', json={
                'recovery_id': forgot.json()['recovery_id'],
                'otp': wrong_code,
            })
            assert invalid.status_code == 400, invalid.text
            assert invalid.json()['detail'] == 'Invalid OTP. Please enter the correct OTP.'

            valid = await cli.post('/api/auth/forgot/verify', json={
                'recovery_id': forgot.json()['recovery_id'],
                'otp': correct_code,
            })
            assert valid.status_code == 200, valid.text
            assert valid.json() == {'verified': True}

    run(flow())


def test_recovery_enforces_cooldown_expiry_and_three_resends(delivery):
    async def flow():
        user = await register_user('limits')
        async with make_client() as cli:
            initial = await cli.post('/api/auth/forgot', json={'phone': user['phone']})
            assert initial.status_code == 200
            immediate = await cli.post('/api/auth/forgot', json={'phone': user['phone']})
            assert immediate.status_code == 429
            assert 'wait' in immediate.json()['detail'].lower()

            latest = initial
            for expected in range(1, 4):
                await server.db.users.update_one(
                    {'id': user['id']},
                    {'$set': {'reset_otp_at': server.now() - timedelta(seconds=31)}})
                latest = await cli.post('/api/auth/forgot', json={'phone': user['phone']})
                assert latest.status_code == 200, latest.text
                assert latest.json()['resend_count'] == expected

            await server.db.users.update_one(
                {'id': user['id']},
                {'$set': {'reset_otp_at': server.now() - timedelta(seconds=31)}})
            blocked = await cli.post('/api/auth/forgot', json={'phone': user['phone']})
            assert blocked.status_code == 429
            assert 'maximum resend' in blocked.json()['detail'].lower()

            # The server, not the UI timer, is authoritative for expiry.
            await server.db.users.update_one(
                {'id': user['id']},
                {'$set': {
                    'reset_otp_at': server.now() - timedelta(
                        minutes=server.otp_service.OTP_TTL_MIN, seconds=1),
                }})
            expired = await cli.post('/api/auth/reset', json={
                'recovery_id': latest.json()['recovery_id'],
                'otp': delivery[-1][2],
                'new_password': NEW_PASSWORD,
            })
            assert expired.status_code == 400
            assert 'expired' in expired.json()['detail'].lower()

    run(flow())

