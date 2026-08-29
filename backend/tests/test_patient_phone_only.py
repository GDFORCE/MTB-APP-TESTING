"""Regression coverage for patients whose only required contact is a phone.

The staff Add Patient flow may omit email, but it must never omit phone.  A
patient invited this way must also be able to start registration and sign in
using the same normalized phone number.
"""
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
PASSWORD = 'Password1!'


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url='http://testserver',
    )


def unique_phone():
    local = f"9{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}"
    return f'+91 {local[:5]} {local[5:]}', f'+91{local}'


def test_patient_invite_accepts_no_email_but_rejects_no_phone(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    pi_id = str(uuid.uuid4())
    trial_id = str(uuid.uuid4())
    organization = f'Phone-only invite org {suffix}'
    raw_phone, normalized_phone = unique_phone()
    invitation_id = None
    email_deliveries = []

    def capture_invitation_email(*args, **kwargs):
        email_deliveries.append((args, kwargs))

    monkeypatch.setattr(
        server.otp_service, 'send_invitation_email', capture_invitation_email)

    async def flow():
        nonlocal invitation_id
        await server.db.users.insert_one({
            'id': pi_id,
            'email': f'phone-only-pi-{suffix}@example.com',
            'phone': '',
            'full_name': 'Phone-only Invite PI',
            'role': 'pi',
            'organization': organization,
            'hashed_password': server.pwd_ctx.hash(PASSWORD),
            'created_at': server.now(),
        })
        await server.db.trials.insert_one({
            'id': trial_id,
            'protocol_id': f'PHONE-{suffix}',
            'title': 'Phone-only Patient Trial',
            'created_by': pi_id,
            'sponsor_name': organization,
            'created_at': server.now(),
        })
        headers = {
            'Authorization': f"Bearer {server.make_token(pi_id, 'pi', 'access')}"
        }
        try:
            async with make_client() as cli:
                missing_phone = await cli.post(
                    '/api/patients/invite',
                    headers=headers,
                    json={
                        'full_name': 'Patient Without Contact',
                        'trial_id': trial_id,
                        'subject_id': f'SUBJ-{suffix}-MISSING',
                    },
                )
                created = await cli.post(
                    '/api/patients/invite',
                    headers=headers,
                    json={
                        'full_name': 'Phone-only Patient',
                        'phone': raw_phone,
                        'trial_id': trial_id,
                        'subject_id': f'SUBJ-{suffix}',
                        'dob': '1990-01-01',
                        'gender': 'Female',
                    },
                )

            assert missing_phone.status_code == 422, missing_phone.text
            assert missing_phone.json()['detail'][0]['loc'][-1] == 'phone'
            assert created.status_code == 200, created.text
            payload = created.json()
            invitation_id = payload['id']
            assert payload['email'] == ''
            assert payload['phone'] == normalized_phone
            assert payload['patient_data']['email'] == ''
            assert payload['patient_data']['phone'] == normalized_phone
            assert payload['token']
            assert payload['token'] in payload['invite_link']
            assert email_deliveries == []

            stored = await server.db.invitations.find_one(
                {'id': invitation_id}, {'_id': 0})
            assert stored['email'] == ''
            assert stored['phone'] == normalized_phone
        finally:
            if invitation_id:
                await server.db.invitations.delete_one({'id': invitation_id})
                await server.db.audit_logs.delete_many({'target_id': invitation_id})
            await server.db.trials.delete_one({'id': trial_id})
            await server.db.users.delete_one({'id': pi_id})

    run(flow())


def test_phone_only_patient_invitation_starts_registration_without_email(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    organization_name = f'Phone-only registration org {suffix}'
    invitation_id = str(uuid.uuid4())
    invitation_token = server.new_invite_code()
    raw_phone, normalized_phone = unique_phone()
    registration_id = None
    delivered = []

    async def capture_delivery(channel, target, code, **_kwargs):
        delivered.append((channel, target, code))

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', capture_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        nonlocal registration_id
        organization = None
        try:
            organization, created = await server.ensure_organization(
                organization_name, 'site')
            assert created is True
            await server.db.invitations.insert_one({
                'id': invitation_id,
                'token': invitation_token,
                'email': '',
                'phone': normalized_phone,
                'full_name': 'Phone-only Patient',
                'designation': '',
                'role': 'patient',
                'org': organization_name,
                'status': 'pending',
                'created_at': server.now(),
                'expires_at': server.now() + timedelta(days=1),
            })

            async with make_client() as cli:
                response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Phone-only Patient',
                    'role': 'patient',
                    'phone': raw_phone,
                    'organization': organization_name,
                    'profile': {'dob': '1990-01-01', 'gender': 'Female'},
                    'invite_token': invitation_token,
                })

            assert response.status_code == 200, response.text
            payload = response.json()
            registration_id = payload['registration_id']
            assert payload['email'] is None
            assert payload['phone'] == normalized_phone
            assert payload['channels'] == ['phone']
            assert [(channel, target) for channel, target, _ in delivered] == [
                ('phone', normalized_phone),
            ]

            pending = await server.db.pending_registrations.find_one(
                {'id': registration_id}, {'_id': 0})
            assert pending['email'] is None
            assert pending['phone'] == normalized_phone
            assert pending['channels'] == ['phone']
            assert pending['email_verified'] is False
        finally:
            if registration_id:
                await server.db.pending_registrations.delete_one(
                    {'id': registration_id})
            await server.db.invitations.delete_one({'id': invitation_id})
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.audit_logs.delete_many(
                    {'target_id': organization['id']})

    run(flow())


def test_phone_only_patient_can_log_in_with_phone_number():
    user_id = str(uuid.uuid4())
    raw_phone, normalized_phone = unique_phone()

    async def flow():
        await server.db.users.insert_one({
            'id': user_id,
            'email': '',
            'phone': normalized_phone,
            'full_name': 'Phone-only Login Patient',
            'role': 'patient',
            'organization': '',
            'hashed_password': server.pwd_ctx.hash(PASSWORD),
            'created_at': server.now(),
        })
        try:
            async with make_client() as cli:
                login = await cli.post('/api/auth/login', json={
                    # Keep the existing request key for API compatibility; its
                    # value is now an email-or-phone identifier.
                    'email': raw_phone,
                    'password': PASSWORD,
                })
                wrong_password = await cli.post('/api/auth/login', json={
                    'email': raw_phone,
                    'password': 'WrongPassword1!',
                })

            assert login.status_code == 200, login.text
            assert login.json()['user']['id'] == user_id
            assert login.json()['user']['email'] == ''
            assert login.json()['user']['phone'] == normalized_phone
            assert wrong_password.status_code == 401, wrong_password.text
            assert wrong_password.json()['detail'] == 'Invalid credentials'
        finally:
            await server.db.refresh_tokens.delete_many({'user_id': user_id})
            await server.db.audit_logs.delete_many({'user_id': user_id})
            await server.db.users.delete_one({'id': user_id})

    run(flow())
