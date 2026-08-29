"""Registration normalization and fail-closed backend validation."""
import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url='http://testserver',
    )


def test_phone_normalization_accepts_any_country_calling_code():
    """Registration is open to every country, not only +91."""
    valid = {
        # Bare numbers keep the historical Indian interpretation.
        '9876543210': '+919876543210',
        '+91 98765-43210': '+919876543210',
        '0091 9876543210': '+919876543210',
        '+1 415 555 2671': '+14155552671',
        '+44 7911 123456': '+447911123456',
        # A national trunk "0" is dropped before the calling code is applied.
        '+44 07911 123456': '+447911123456',
        '+971 50 123 4567': '+971501234567',
        '+65 8123 4567': '+6581234567',
        '+81 90 1234 5678': '+819012345678',
        '+27 82 123 4567': '+27821234567',
        # NANP territories carry a four-digit code and a seven-digit local part.
        '+1684 622 1234': '+16846221234',
    }
    for raw, expected in valid.items():
        assert server.normalize_phone(raw) == expected, raw

    for raw in ['12345', '+999 1', '+1', '+1 555 1234', '+44 1']:
        with pytest.raises(HTTPException) as excinfo:
            server.normalize_phone(raw)
        assert excinfo.value.status_code == 400, raw

    assert server.normalize_phone('') is None
    assert server.normalize_phone(None) is None


def test_registration_contact_availability_reports_field_duplicates():
    user_id = str(uuid.uuid4())
    email = f'availability-{RUN_ID}@example.com'
    phone = f'+9198{int(RUN_ID, 16) % 100_000_000:08d}'

    async def flow():
        await server.db.users.insert_one({
            'id': user_id,
            'email': email,
            'phone': phone,
            'full_name': 'Availability Test',
            'role': 'patient',
        })
        try:
            async with make_client() as cli:
                duplicate = await cli.post('/api/auth/register/check-availability', json={
                    'email': email.upper(),
                    'phone': phone,
                })
                available = await cli.post('/api/auth/register/check-availability', json={
                    'email': f'new-{RUN_ID}@example.com',
                    'phone': '+919700000001',
                })
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()['email']['available'] is False
            assert duplicate.json()['phone']['available'] is False
            assert available.status_code == 200, available.text
            assert available.json()['email']['available'] is True
            assert available.json()['phone']['available'] is True
        finally:
            await server.db.users.delete_one({'id': user_id})

    run(flow())


def test_registration_start_accepts_a_foreign_phone_number(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        async with make_client() as cli:
            response = await cli.post('/api/auth/register/start', json={
                'full_name': 'Overseas Patient',
                'role': 'patient',
                'email': f'overseas-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '+44 7911 123456',
                'profile': {'dob': '1990-01-01', 'gender': 'Female'},
                'security_questions': [],
            })
        assert response.status_code == 200, response.text
        registration_id = response.json()['registration_id']
        try:
            pending = await server.db.pending_registrations.find_one(
                {'id': registration_id}, {'_id': 0, 'phone': 1})
            assert pending['phone'] == '+447911123456'
        finally:
            await server.db.pending_registrations.delete_one({'id': registration_id})

    run(flow())


def test_registration_normalizes_email_phone_dob_and_computed_age(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)
    email = f'TEST-{RUN_ID}@Example.COM'
    local_phone = f"9{int(RUN_ID, 16) % 1_000_000_000:09d}"
    phone = f'+91 {local_phone[:5]}-{local_phone[5:]}'
    server_today = server.now().date()
    dob = server_today.replace(year=server_today.year - 30)

    async def flow():
        async with make_client() as cli:
            response = await cli.post('/api/auth/register/start', json={
                'full_name': '  Patient Example  ',
                'role': 'patient',
                'email': email,
                'phone': phone,
                'profile': {
                    'dob': dob.isoformat(),
                    'age': 999,
                    'gender': 'Female',
                },
                'security_questions': [],
            })
        assert response.status_code == 200, response.text
        registration_id = response.json()['registration_id']
        pending = await server.db.pending_registrations.find_one(
            {'id': registration_id}, {'_id': 0})
        try:
            assert pending['email'] == email.lower()
            assert pending['phone'] == f'+91{local_phone}'
            assert pending['full_name'] == 'Patient Example'
            assert pending['profile']['dob'] == dob.isoformat()
            assert pending['profile']['age'] == 30
        finally:
            await server.db.pending_registrations.delete_one({'id': registration_id})

    run(flow())


def test_registration_rejects_invalid_phone_dob_future_and_age(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)
    base = {
        'full_name': 'Patient Example',
        'role': 'patient',
        'email': f'test-{RUN_ID}@example.com',
        'phone': '+919876543210',
        'profile': {'dob': '1990-01-01', 'gender': 'Female'},
    }

    async def flow():
        cases = [
            ({**base, 'phone': '12345'}, 'mobile number'),
            ({**base, 'profile': {'dob': '2024-02-30'}}, 'real date'),
            ({
                **base,
                'profile': {'dob': (date.today() + timedelta(days=1)).isoformat()},
            }, 'future'),
            ({**base, 'profile': {'dob': '1800-01-01'}}, 'between 0 and 120'),
            ({**base, 'profile': {}}, 'required'),
        ]
        async with make_client() as cli:
            for payload, message in cases:
                response = await cli.post('/api/auth/register/start', json=payload)
                assert response.status_code == 400, response.text
                assert message in response.json()['detail'].lower()

    run(flow())


def test_site_registration_maps_selected_site_role(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        created = []
        try:
            async with make_client() as cli:
                for index, (selected, expected) in enumerate([
                    ('PI', 'pi'),
                    ('Research Team', 'crc'),
                    ('Administrative', 'site'),
                ]):
                    response = await cli.post('/api/auth/register/start', json={
                        'full_name': f'Site Member {index}',
                        'role': 'site',
                        'email': f'site-{RUN_ID}-{index}@example.com',
                        'phone': f'+91987654{index:04d}',
                        'organization': f'Site {RUN_ID}',
                        'profile': {'role': selected},
                    })
                    assert response.status_code == 200, response.text
                    registration_id = response.json()['registration_id']
                    created.append(registration_id)
                    pending = await server.db.pending_registrations.find_one(
                        {'id': registration_id}, {'_id': 0, 'role': 1})
                    assert pending['role'] == expected
        finally:
            await server.db.pending_registrations.delete_many(
                {'id': {'$in': created}})

    run(flow())


def test_smo_registration_maps_selected_role_and_keeps_smo_entity(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        created = []
        try:
            async with make_client() as cli:
                for index, (selected, expected) in enumerate([
                    ('PI', 'pi'),
                    ('Research Team', 'crc'),
                    ('Administrative', 'smo'),
                ]):
                    response = await cli.post('/api/auth/register/start', json={
                        'full_name': f'SMO Member {index}',
                        'role': 'smo',
                        'email': f'smo-role-{RUN_ID}-{index}@example.com',
                        'phone': f'+91976543{index:04d}',
                        'organization': f'SMO Role {RUN_ID} {index}',
                        'profile': {
                            'role': selected,
                            'hospitals': [{
                                'name': f'Hospital {index}',
                                'address': f'Address {index}',
                                'type': 'Private',
                                'role': selected,
                            }],
                        },
                    })
                    assert response.status_code == 200, response.text
                    registration_id = response.json()['registration_id']
                    created.append(registration_id)
                    pending = await server.db.pending_registrations.find_one(
                        {'id': registration_id},
                        {'_id': 0, 'role': 1, 'organization_type': 1, 'profile': 1},
                    )
                    assert pending['role'] == expected
                    assert pending['organization_type'] == 'smo'
                    assert pending['profile']['role'] == selected
                    assert pending['profile']['hospitals'][0]['role'] == selected
        finally:
            await server.db.pending_registrations.delete_many(
                {'id': {'$in': created}})

    run(flow())


def test_sponsor_and_cro_default_without_profile_role(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        created = []
        try:
            async with make_client() as cli:
                for index, selected_role in enumerate(('sponsor', 'cro')):
                    response = await cli.post('/api/auth/register/start', json={
                        'full_name': f'{selected_role.upper()} User',
                        'role': selected_role,
                        'email': f'{selected_role}-default-{RUN_ID}@example.com',
                        'phone': f'+91965432{index:04d}',
                        'organization': f'{selected_role.upper()} Default {RUN_ID}',
                        'profile': {'designation': 'Manager'},
                    })
                    assert response.status_code == 200, response.text
                    registration_id = response.json()['registration_id']
                    created.append(registration_id)
                    pending = await server.db.pending_registrations.find_one(
                        {'id': registration_id},
                        {'_id': 0, 'role': 1, 'organization_type': 1, 'profile': 1},
                    )
                    assert pending['role'] == selected_role
                    assert pending['organization_type'] == selected_role
                    assert not pending['profile'].get('role')
        finally:
            await server.db.pending_registrations.delete_many(
                {'id': {'$in': created}})

    run(flow())


def test_first_registrant_is_org_admin_and_invitee_is_regular_member():
    org_name = f'Ownership {RUN_ID} {uuid.uuid4().hex[:6]}'
    created_user_ids = []

    async def flow():
        organization = None
        try:
            owner = await server._finalize_registration({
                'full_name': 'Organization Owner',
                'role': 'pi',
                'email': f'owner-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '',
                'organization': org_name,
                'hashed_password': server.pwd_ctx.hash('Password1!'),
                'profile': {'designation': 'Principal Investigator'},
                'creates_organization': True,
                'organization_type': 'site',
                'email_verified': True,
                'phone_verified': False,
            })
            created_user_ids.append(owner['user']['id'])
            assert owner['user']['org_admin'] is True

            organization = await server.find_organization_by_name(org_name)
            assert organization and organization['type'] == 'site'

            member = await server._finalize_registration({
                'full_name': 'Invited CRC',
                'role': 'crc',
                'email': f'member-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '',
                'organization': org_name,
                'hashed_password': server.pwd_ctx.hash('Password1!'),
                'profile': {'designation': 'CRC'},
                'invitation_id': str(uuid.uuid4()),
                'email_verified': True,
                'phone_verified': False,
            })
            created_user_ids.append(member['user']['id'])
            assert member['user']['org_admin'] is False
        finally:
            await server.db.refresh_tokens.delete_many(
                {'user_id': {'$in': created_user_ids}})
            await server.db.users.delete_many({'id': {'$in': created_user_ids}})
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.audit_logs.delete_many(
                    {'target_id': organization['id']})

    run(flow())


def test_email_invitee_verifies_phone_only(monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    org_name = f'Invite Verification {RUN_ID} {suffix}'
    invite_email = f'invite-phone-only-{suffix}@example.com'
    invite_phone = f"+919{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}"
    invite_token = server.new_invite_code()
    delivered = []

    async def capture_delivery(channel, target, code, **_kwargs):
        delivered.append((channel, target, code))

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', capture_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        organization = None
        registration_id = None
        invitation_id = str(uuid.uuid4())
        try:
            organization, created = await server.ensure_organization(org_name, 'site')
            assert created is True
            await server.db.invitations.insert_one({
                'id': invitation_id,
                'token': invite_token,
                'email': invite_email,
                'phone': '',
                'full_name': 'Invited Researcher',
                'designation': 'Research Coordinator',
                'role': 'crc',
                'org': org_name,
                'status': 'pending',
                'created_at': server.now(),
                'expires_at': server.now() + timedelta(days=1),
            })
            async with make_client() as cli:
                response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Invited Researcher',
                    'role': 'crc',
                    'email': invite_email,
                    'phone': invite_phone,
                    'organization': org_name,
                    'profile': {'designation': 'Research Coordinator'},
                    'security_questions': [],
                    'invite_token': invite_token,
                })
            assert response.status_code == 200, response.text
            payload = response.json()
            registration_id = payload['registration_id']
            assert payload['channels'] == ['phone']
            assert [(channel, target) for channel, target, _code in delivered] == [
                ('phone', invite_phone),
            ]
            pending = await server.db.pending_registrations.find_one(
                {'id': registration_id},
                {'_id': 0, 'channels': 1, 'email_verified': 1, 'phone_verified': 1},
            )
            assert pending == {
                'channels': ['phone'],
                'email_verified': True,
                'phone_verified': False,
            }

            async with make_client() as cli:
                verified = await cli.post('/api/auth/register/verify', json={
                    'registration_id': registration_id,
                    'phone_otp': delivered[0][2],
                })
                password_too_early = await cli.post('/api/auth/register/complete', json={
                    'registration_id': registration_id,
                    'password': 'Password1!',
                })
                saved = await cli.post('/api/auth/register/security-questions', json={
                    'registration_id': registration_id,
                    'security_questions': [
                        {'question': 'Question one?', 'answer': 'First answer'},
                        {'question': 'Question two?', 'answer': 'Second answer'},
                        {'question': 'Question three?', 'answer': 'Third answer'},
                    ],
                })
            assert verified.status_code == 200, verified.text
            assert verified.json()['verified'] is True
            assert password_too_early.status_code == 400
            assert 'security questions' in password_too_early.json()['detail'].lower()
            assert saved.status_code == 200, saved.text
            pending_after_questions = await server.db.pending_registrations.find_one(
                {'id': registration_id},
                {'_id': 0, 'security_questions_completed': 1, 'security_questions': 1},
            )
            assert pending_after_questions['security_questions_completed'] is True
            assert [item['question'] for item in pending_after_questions['security_questions']] == [
                'Question one?', 'Question two?', 'Question three?',
            ]
            assert all('answer_hash' in item for item in pending_after_questions['security_questions'])
        finally:
            if registration_id:
                await server.db.pending_registrations.delete_one({'id': registration_id})
            await server.db.invitations.delete_one({'id': invitation_id})
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})

    run(flow())


def test_self_registration_also_requires_security_questions_before_password(monkeypatch):
    """Every registration path — not just invited ones — verifies contact
    details, then answers security questions, then sets a password.

    /auth/register/security-questions used to hard-require an invitation_id,
    and /auth/register/complete only enforced security_questions_completed
    for invited registrations. Both gates now apply uniformly."""
    org_name = f'SelfReg Order {RUN_ID} {uuid.uuid4().hex[:6]}'
    delivered = []

    async def capture_delivery(channel, target, code, **_kwargs):
        delivered.append((channel, target, code))

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', capture_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        registration_id = None
        organization = None
        try:
            phone = f"+919{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}"
            async with make_client() as cli:
                response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Self Registering Sponsor',
                    'role': 'sponsor',
                    'email': f'self-reg-{uuid.uuid4().hex[:8]}@example.com',
                    'phone': phone,
                    'organization': org_name,
                    'profile': {'designation': 'Manager'},
                    'security_questions': [],
                })
            assert response.status_code == 200, response.text
            payload = response.json()
            registration_id = payload['registration_id']
            phone_code = next(code for channel, _target, code in delivered if channel == 'phone')
            email_code = next(code for channel, _target, code in delivered if channel == 'email')

            async with make_client() as cli:
                verified = await cli.post('/api/auth/register/verify', json={
                    'registration_id': registration_id,
                    'phone_otp': phone_code,
                    'email_otp': email_code,
                })
                password_too_early = await cli.post('/api/auth/register/complete', json={
                    'registration_id': registration_id,
                    'password': 'Password1!',
                })
                saved = await cli.post('/api/auth/register/security-questions', json={
                    'registration_id': registration_id,
                    'security_questions': [
                        {'question': 'Question one?', 'answer': 'First answer'},
                        {'question': 'Question two?', 'answer': 'Second answer'},
                        {'question': 'Question three?', 'answer': 'Third answer'},
                    ],
                })
            assert verified.status_code == 200, verified.text
            assert verified.json()['verified'] is True
            # Before this fix, a self-registration was never blocked here (the
            # invitation_id-only check let it straight through); now it is.
            assert password_too_early.status_code == 400
            assert 'security questions' in password_too_early.json()['detail'].lower()
            # Before this fix, this call was rejected outright for any
            # non-invited registration ("must be submitted during registration").
            assert saved.status_code == 200, saved.text
            pending_after_questions = await server.db.pending_registrations.find_one(
                {'id': registration_id},
                {'_id': 0, 'security_questions_completed': 1, 'invitation_id': 1},
            )
            assert pending_after_questions['security_questions_completed'] is True
            assert 'invitation_id' not in pending_after_questions
        finally:
            if registration_id:
                await server.db.pending_registrations.delete_one({'id': registration_id})
            organization = await server.db.organizations.find_one({'name': org_name})
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.users.delete_many({'organization_id': organization['id']})

    run(flow())


def test_verify_accepts_one_channel_per_call(monkeypatch):
    """The phone-verify and email-verify screens each submit only their own
    channel's code in separate calls, not both codes together in one call.

    /auth/register/verify used to require every still-unverified required
    channel's code in the SAME request ("Email verification code is
    required" even when the caller only meant to verify phone right now).
    It now accepts one channel at a time and remembers what's already
    verified across calls."""
    org_name = f'IncrementalVerify {RUN_ID} {uuid.uuid4().hex[:6]}'
    delivered = []

    async def capture_delivery(channel, target, code, **_kwargs):
        delivered.append((channel, target, code))

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', capture_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        registration_id = None
        organization = None
        try:
            phone = f"+919{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}"
            async with make_client() as cli:
                response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Incremental Verify Sponsor',
                    'role': 'sponsor',
                    'email': f'incremental-verify-{uuid.uuid4().hex[:8]}@example.com',
                    'phone': phone,
                    'organization': org_name,
                    'profile': {'designation': 'Manager'},
                    'security_questions': [],
                })
            assert response.status_code == 200, response.text
            registration_id = response.json()['registration_id']
            phone_code = next(code for channel, _target, code in delivered if channel == 'phone')
            email_code = next(code for channel, _target, code in delivered if channel == 'email')

            async with make_client() as cli:
                neither_supplied = await cli.post('/api/auth/register/verify', json={
                    'registration_id': registration_id,
                })
                phone_only = await cli.post('/api/auth/register/verify', json={
                    'registration_id': registration_id,
                    'phone_otp': phone_code,
                })
                email_only = await cli.post('/api/auth/register/verify', json={
                    'registration_id': registration_id,
                    'email_otp': email_code,
                })
            assert neither_supplied.status_code == 400, neither_supplied.text
            assert 'verification code is required' in neither_supplied.json()['detail'].lower()
            # Before this fix, this call failed with "Email verification code
            # is required" instead of accepting the phone code on its own.
            assert phone_only.status_code == 200, phone_only.text
            phone_only_body = phone_only.json()
            assert phone_only_body['verified'] is False
            assert phone_only_body['phone_verified'] is True
            assert phone_only_body['email_verified'] is False
            assert email_only.status_code == 200, email_only.text
            assert email_only.json()['verified'] is True
        finally:
            if registration_id:
                await server.db.pending_registrations.delete_one({'id': registration_id})
            organization = await server.db.organizations.find_one({'name': org_name})
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.users.delete_many({'organization_id': organization['id']})

    run(flow())


def test_custom_department_is_queued_when_site_registration_completes():
    suffix = uuid.uuid4().hex[:8]
    org_name = f'Custom Department Site {RUN_ID} {suffix}'
    department = f'Translational Medicine {RUN_ID} {suffix}'
    created_user_id = None

    async def flow():
        nonlocal created_user_id
        try:
            session = await server._complete_registration({
                'id': str(uuid.uuid4()),
                'full_name': 'Custom Department PI',
                'role': 'pi',
                'email': f'custom-dept-{suffix}@example.com',
                'phone': '',
                'organization': org_name,
                'hashed_password': server.pwd_ctx.hash('Password1!'),
                'profile': {
                    'designation': 'Principal Investigator',
                    'department': department,
                    'department_is_custom': True,
                },
                'creates_organization': True,
                'organization_type': 'site',
                'email_verified': True,
                'phone_verified': False,
            })
            created_user_id = session['user']['id']
            submission = await server.db.master_data_submissions.find_one(
                {'submittedById': created_user_id}, {'_id': 0})
            assert submission['fieldType'] == 'department'
            assert submission['value'] == department
            assert submission['status'] == 'pending'
            assert session['user']['profile']['department_review_status'] == 'pending'
        finally:
            if created_user_id:
                await server.db.master_data_submissions.delete_many(
                    {'submittedById': created_user_id})
                await server.db.refresh_tokens.delete_many(
                    {'user_id': created_user_id})
                await server.db.users.delete_one({'id': created_user_id})
            await server.db.organizations.delete_many({'name': org_name})

    run(flow())


def test_smo_self_registration_requires_and_stores_hospitals(monkeypatch):
    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        registration_id = None
        async with make_client() as cli:
            missing = await cli.post('/api/auth/register/start', json={
                'full_name': 'SMO User',
                'role': 'smo',
                'email': f'smo-missing-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '+919700000011',
                'organization': f'SMO Missing {uuid.uuid4().hex[:8]}',
                'profile': {
                    'designation': 'SMO Manager',
                    'role': 'Administrative',
                },
            })
            valid = await cli.post('/api/auth/register/start', json={
                'full_name': 'SMO Administrative User',
                'role': 'smo',
                'email': f'smo-valid-{uuid.uuid4().hex[:8]}@example.com',
                'phone': '+919700000012',
                'organization': f'SMO Valid {uuid.uuid4().hex[:8]}',
                'profile': {
                    'designation': 'SMO Manager',
                    'role': 'Administrative',
                    'hospitals': [{
                        'name': 'Apollo Hospitals Mumbai',
                        'address': 'Bandra West, Mumbai',
                        'type': 'private',
                        'role': 'administrative',
                    }],
                },
            })
        assert missing.status_code == 400, missing.text
        assert 'at least one hospital' in missing.json()['detail']
        assert valid.status_code == 200, valid.text
        registration_id = valid.json()['registration_id']
        pending = await server.db.pending_registrations.find_one(
            {'id': registration_id}, {'_id': 0, 'profile': 1})
        assert pending['profile']['hospitals'] == [{
            'name': 'Apollo Hospitals Mumbai',
            'address': 'Bandra West, Mumbai',
            'type': 'Private',
            'role': 'Administrative',
        }]
        await server.db.pending_registrations.delete_one({'id': registration_id})

    run(flow())


def test_normal_registration_rejects_existing_organization(monkeypatch):
    org_name = f'Existing {RUN_ID} {uuid.uuid4().hex[:6]}'
    google_place_id = f'ChIJexisting{uuid.uuid4().hex[:16]}'

    async def no_delivery(*_args, **_kwargs):
        return None

    async def no_throttle(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, '_deliver_otp', no_delivery)
    monkeypatch.setattr(server, '_enforce_rate_limit', no_throttle)

    async def flow():
        organization = None
        try:
            organization, created = await server.ensure_organization(
                org_name, 'site', details={'googlePlaceId': google_place_id})
            assert created is True
            phone = f"+919{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}"
            async with make_client() as cli:
                response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Uninvited Member',
                    'role': 'pi',
                    'email': f'uninvited-{uuid.uuid4().hex[:8]}@example.com',
                    'phone': phone,
                    'organization': org_name,
                    'profile': {'designation': 'PI'},
                })
                place_id_response = await cli.post('/api/auth/register/start', json={
                    'full_name': 'Google Place Duplicate',
                    'role': 'pi',
                    'email': f'place-duplicate-{uuid.uuid4().hex[:8]}@example.com',
                    'phone': f"+919{int(uuid.uuid4().hex[:8], 16) % 1_000_000_000:09d}",
                    'organization': f'A different display name {uuid.uuid4().hex[:6]}',
                    'profile': {
                        'designation': 'PI',
                        'googlePlaceId': google_place_id,
                    },
                })
            assert response.status_code == 409, response.text
            assert 'invite' in response.json()['detail'].lower()
            assert place_id_response.status_code == 409, place_id_response.text
            assert 'invite' in place_id_response.json()['detail'].lower()
        finally:
            if organization:
                await server.db.organizations.delete_one({'id': organization['id']})
                await server.db.audit_logs.delete_many(
                    {'target_id': organization['id']})

    run(flow())
