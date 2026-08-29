"""Pre-login, email-verified login support tickets."""
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
CREATED_USER_IDS = []
CREATED_REQUEST_IDS = []


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

    async def capture(channel, target, code, **metadata):
        sent.append((channel, target, code, metadata))

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', capture)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)
    yield sent


def teardown_module():
    async def clean():
        await server.db.support_tickets.delete_many(
            {'prelogin_request_id': {'$in': CREATED_REQUEST_IDS}})
        await server.db.prelogin_support_requests.delete_many(
            {'id': {'$in': CREATED_REQUEST_IDS}})
        await server.db.refresh_tokens.delete_many(
            {'user_id': {'$in': CREATED_USER_IDS}})
        await server.db.users.delete_many({'id': {'$in': CREATED_USER_IDS}})
    run(clean())
    LOOP.close()


async def register_user():
    async with make_client() as cli:
        response = await cli.post('/api/auth/register', json={
            'email': f'login-support-{RUN_ID}@example.com',
            'phone': f'8{int(RUN_ID, 16) % 1_000_000_000:09d}',
            'password': 'Password1!',
            'full_name': 'Login Support User',
            'role': 'patient',
        })
    assert response.status_code == 200, response.text
    user = response.json()['user']
    CREATED_USER_IDS.append(user['id'])
    return user


def test_registered_email_verification_creates_one_admin_visible_ticket(delivery):
    async def flow():
        user = await register_user()
        payload = {
            'email': user['email'].upper(),
            'subject': 'Unable to log in',
            'description': 'The application says login failed with my password.',
        }
        async with make_client() as cli:
            started = await cli.post('/api/auth/support/start', json=payload)
            assert started.status_code == 200, started.text
            data = started.json()
            CREATED_REQUEST_IDS.append(data['request_id'])
            assert data['message'] == (
                'If the email is registered, a verification code has been sent.')
            assert len(delivery) == 1
            channel, target, code, metadata = delivery[0]
            assert (channel, target) == ('email', user['email'])
            assert metadata['purpose'] == 'login_support'

            wrong_code = '111111' if code != '111111' else '222222'
            rejected = await cli.post('/api/auth/support/verify', json={
                'request_id': data['request_id'], 'otp': wrong_code,
            })
            assert rejected.status_code == 400
            assert rejected.json()['detail'] == 'Invalid OTP. Please enter the correct OTP.'

            verified = await cli.post('/api/auth/support/verify', json={
                'request_id': data['request_id'], 'otp': code,
            })
            assert verified.status_code == 200, verified.text
            ticket_id = verified.json()['ticket_id']

            ticket = await server.db.support_tickets.find_one(
                {'prelogin_request_id': data['request_id']}, {'_id': 0})
            assert ticket
            assert ticket['ticket_id'] == ticket_id
            assert ticket['user_id'] == user['id']
            assert ticket['registered_email'] == user['email']
            assert ticket['category'] == 'Login Issue'
            assert ticket['source'] == 'Pre-login'
            assert ticket['status'] == 'Open'

            repeated = await cli.post('/api/auth/support/verify', json={
                'request_id': data['request_id'], 'otp': code,
            })
            assert repeated.status_code == 200
            assert repeated.json()['ticket_id'] == ticket_id
            assert await server.db.support_tickets.count_documents(
                {'prelogin_request_id': data['request_id']}) == 1

    run(flow())


def test_unknown_email_uses_same_start_response_without_creating_ticket(delivery):
    async def flow():
        async with make_client() as cli:
            started = await cli.post('/api/auth/support/start', json={
                'email': f'unknown-{RUN_ID}@example.com',
                'subject': 'Unable to log in',
                'description': 'I cannot access my account from the login page.',
            })
            assert started.status_code == 200, started.text
            data = started.json()
            CREATED_REQUEST_IDS.append(data['request_id'])
            assert data['message'] == (
                'If the email is registered, a verification code has been sent.')
            assert delivery == []

            verify = await cli.post('/api/auth/support/verify', json={
                'request_id': data['request_id'], 'otp': '123456',
            })
            assert verify.status_code == 400
            assert verify.json()['detail'] == 'Invalid OTP. Please enter the correct OTP.'
            assert await server.db.support_tickets.count_documents(
                {'prelogin_request_id': data['request_id']}) == 0

    run(flow())


def test_expired_support_code_is_rejected(delivery):
    async def flow():
        user = await server.db.users.find_one({'id': CREATED_USER_IDS[0]}, {'_id': 0})
        async with make_client() as cli:
            started = await cli.post('/api/auth/support/start', json={
                'email': user['email'],
                'subject': 'Sign in error',
                'description': 'The sign in screen will not let me continue.',
            })
            data = started.json()
            CREATED_REQUEST_IDS.append(data['request_id'])
            await server.db.prelogin_support_requests.update_one(
                {'id': data['request_id']},
                {'$set': {'otp_sent_at': server.now() - timedelta(
                    minutes=server.otp_service.OTP_TTL_MIN, seconds=1)}},
            )
            expired = await cli.post('/api/auth/support/verify', json={
                'request_id': data['request_id'], 'otp': delivery[-1][2],
            })
            assert expired.status_code == 400
            assert 'expired' in expired.json()['detail'].lower()

    run(flow())
