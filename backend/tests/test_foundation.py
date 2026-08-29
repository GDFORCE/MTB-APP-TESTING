"""Foundation tests — Task 1.1.

Covers: write_audit helper, organizations (auto-upsert + public list/search),
notification unread-count / read-all, and the invitation lifecycle
(create -> list -> resolve -> accept / resend / cancel), with audit rows.

These tests run in-process against the FastAPI app via httpx.ASGITransport,
hitting the real (Atlas) database configured in backend/.env. All test data
carries a unique per-run marker (RUN_ID) and is deleted in module teardown.

NOTE: Motor pins its io_loop on first use, so every coroutine here runs on the
single module-level LOOP (never asyncio.run, which would create/close loops).
"""
import asyncio
import re
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402  (loads .env, builds app + db handle)

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG_HOSPITAL = f'TESTORG-{RUN_ID} General Hospital'
ORG_PHARMA = f'TESTORG-{RUN_ID} Pharma'

LOOP = asyncio.new_event_loop()

# ids of invitations we create, so teardown can purge their audit rows too
_created_invitation_ids = []
# ids of patients we create via POST /patients, purged (+ their visit instances)
_created_patient_ids = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None):
    """Create a fresh user via the public register endpoint; return (user, headers)."""
    email = f'test-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            'full_name': f'Test {role.upper()} {RUN_ID}',
            'role': role, 'organization': org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        _uids = [u['id'] async for u in db.users.find(
            {'email': {'$regex': f'^test-{RUN_ID}-'}}, {'id': 1})]
        await db.preferences.delete_many({'user_id': {'$in': _uids}})
        await db.refresh_tokens.delete_many({'user_id': {'$in': _uids}})
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.invitations.delete_many({'email': {'$regex': RUN_ID}})
        await db.notifications.delete_many({'title': {'$regex': RUN_ID}})
        if _created_patient_ids:
            await db.visit_instances.delete_many({'patient_id': {'$in': _created_patient_ids}})
            await db.patients.delete_many({'id': {'$in': _created_patient_ids}})
        await db.audit_logs.delete_many({'$or': [
            {'user_name': {'$regex': RUN_ID}},
            {'target_id': {'$in': _created_invitation_ids}},
        ]})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_HOSPITAL))


# ── write_audit helper ───────────────────────────────────────────────────────
class TestWriteAudit:
    def test_helper_writes_standard_row(self):
        async def flow():
            user = {'id': f'test-{RUN_ID}-uid', 'full_name': f'Audit Actor {RUN_ID}',
                    'role': 'pi', 'organization': ORG_HOSPITAL}
            aid = await server.write_audit(user, 'visit.patch', 'Updated visit v-1',
                                           target_id='v-1', changes={'status': 'completed'})
            row = await server.db.audit_logs.find_one({'id': aid}, {'_id': 0})
            assert row, 'audit row not written'
            for key in ('id', 'user_id', 'user_name', 'role', 'org', 'action',
                        'category', 'detail', 'ip', 'device', 'status', 'created_at'):
                assert key in row, f'missing audit field {key}'
            assert row['user_id'] == user['id']
            assert row['user_name'] == user['full_name']
            assert row['org'] == ORG_HOSPITAL
            assert row['action'] == 'visit.patch'
            assert row['category'] == 'visit'
            assert row['status'] == 'success'
            assert row['target_id'] == 'v-1'          # extra ctx preserved
            assert row['changes'] == {'status': 'completed'}
        run(flow())

    def test_helper_tolerates_anonymous_actor(self):
        async def flow():
            aid = await server.write_audit(None, 'invitation.accept',
                                           f'anon accept {RUN_ID}', target_id=f'test-{RUN_ID}-anon')
            _created_invitation_ids.append(f'test-{RUN_ID}-anon')
            row = await server.db.audit_logs.find_one({'id': aid}, {'_id': 0})
            assert row and row['user_id'] is None and row['status'] == 'success'
        run(flow())


# ── Organizations ────────────────────────────────────────────────────────────
class TestOrganizations:
    def test_register_autoupserts_org_and_public_search_finds_it(self, pi):
        async def flow():
            async with make_client() as cli:  # NO auth header — endpoint is public
                r = await cli.get('/api/organizations', params={'search': f'TESTORG-{RUN_ID}'})
            assert r.status_code == 200, r.text
            orgs = r.json()
            names = [o['name'] for o in orgs]
            assert ORG_HOSPITAL in names
            org = next(o for o in orgs if o['name'] == ORG_HOSPITAL)
            assert org['type'] == 'site'         # pi role maps to a site org
            assert org['status'] == 'active'
            assert '_id' not in org
            uuid.UUID(org['id'])                 # uuid4 string id
        run(flow())

    def test_type_filter(self, pi):
        async def flow():
            await _register('sponsor', org=ORG_PHARMA)
            async with make_client() as cli:
                r_all = await cli.get('/api/organizations', params={'search': f'TESTORG-{RUN_ID}'})
                r_sponsor = await cli.get('/api/organizations',
                                          params={'search': f'TESTORG-{RUN_ID}', 'type': 'sponsor'})
            assert {o['name'] for o in r_all.json()} == {ORG_HOSPITAL, ORG_PHARMA}
            sponsor_orgs = r_sponsor.json()
            assert [o['name'] for o in sponsor_orgs] == [ORG_PHARMA]
            assert sponsor_orgs[0]['type'] == 'sponsor'
        run(flow())

    def test_same_org_name_not_duplicated(self, pi):
        async def flow():
            await _register('crc', org=ORG_HOSPITAL)   # second user, same org string
            async with make_client() as cli:
                r = await cli.get('/api/organizations', params={'search': f'TESTORG-{RUN_ID}'})
            matches = [o for o in r.json() if o['name'] == ORG_HOSPITAL]
            assert len(matches) == 1
        run(flow())

    def test_platform_contact_requires_exact_organization_endpoint(self, pi):
        async def flow():
            async with make_client() as cli:
                plain = await cli.get('/api/organizations', params={'search': ORG_HOSPITAL})
                detailed = await cli.get('/api/organizations', params={
                    'search': ORG_HOSPITAL,
                    'include_platform_contact': 'true',
                })
                org_id = plain.json()[0]['id']
                exact = await cli.get(f'/api/organizations/{org_id}/platform-contact')
            assert plain.status_code == 200, plain.text
            assert detailed.status_code == 200, detailed.text
            assert 'platform_contact' not in plain.json()[0]
            assert 'platform_contact' not in detailed.json()[0]
            assert exact.status_code == 200, exact.text
            # Registration help is routed to the public platform-support
            # contract, never to an arbitrary organization employee.
            contact = exact.json()['platform_contact']
            assert contact['name'] == 'MTB Platform Support'
            assert contact['designation'] == 'Platform Administrator'
            assert contact['email']
        run(flow())


# ── Admin self-registration is blocked ───────────────────────────────────────
class TestAdminSelfRegistrationBlocked:
    def test_public_register_rejects_admin(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/auth/register', json={
                    'email': f'test-{RUN_ID}-admin-{uuid.uuid4().hex[:6]}@example.com',
                    'password': PASSWORD, 'full_name': f'Sneaky Admin {RUN_ID}',
                    'role': 'admin', 'organization': ORG_HOSPITAL,
                })
            assert r.status_code == 403, r.text
            assert 'self-register' in r.json()['detail']
        run(flow())

    def test_register_start_rejects_admin(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/auth/register/start', json={
                    'full_name': f'Sneaky Admin {RUN_ID}', 'role': 'admin',
                    'email': f'test-{RUN_ID}-admin-{uuid.uuid4().hex[:6]}@example.com',
                    'password': PASSWORD, 'organization': ORG_HOSPITAL,
                })
            assert r.status_code == 403, r.text
            assert 'self-register' in r.json()['detail']
        run(flow())

    def test_non_admin_roles_still_register(self):
        async def flow():
            user, _ = await _register('pi', org=ORG_HOSPITAL)
            assert user['role'] == 'pi'
        run(flow())


# ── Notification counts ──────────────────────────────────────────────────────
class TestNotificationCounts:
    def test_unread_count_and_read_all(self):
        async def flow():
            user, headers = await _register('patient')
            # seed two unread notifications directly (no create-notification endpoint)
            for i in range(2):
                await server.db.notifications.insert_one({
                    'id': str(uuid.uuid4()), 'user_id': user['id'],
                    'title': f'TESTNOTIF-{RUN_ID} #{i}', 'body': 'x',
                    'kind': 'reminder', 'read': False, 'created_at': server.now(),
                })
            async with make_client() as cli:
                r = await cli.get('/api/notifications/unread-count', headers=headers)
                assert r.status_code == 200 and r.json() == {'count': 2}, r.text

                r2 = await cli.post('/api/notifications/read-all', headers=headers)
                assert r2.status_code == 200, r2.text

                r3 = await cli.get('/api/notifications/unread-count', headers=headers)
                assert r3.json() == {'count': 0}
            # mutation audited
            row = await server.db.audit_logs.find_one(
                {'user_id': user['id'], 'action': 'notifications.read_all'})
            assert row, 'read-all not audited'
        run(flow())

    def test_unread_count_requires_auth(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/notifications/unread-count')
            assert r.status_code == 401
        run(flow())


# ── Invitation lifecycle ─────────────────────────────────────────────────────
async def _invite(headers, role='crc'):
    async with make_client() as cli:
        r = await cli.post('/api/invitations', headers=headers, json={
            'email': f'test-{RUN_ID}-invitee-{uuid.uuid4().hex[:6]}@example.com',
            'full_name': f'Invitee {RUN_ID}', 'role': role,
        })
    assert r.status_code == 200, r.text
    inv = r.json()
    _created_invitation_ids.append(inv['id'])
    return inv


class TestInvitationLifecycle:
    def test_create_keeps_existing_shape_and_lists_for_own_org(self, pi):
        pi_user, headers = pi
        async def flow():
            inv = await _invite(headers)
            # existing response contract intact
            assert inv['status'] == 'pending' and inv['token'] and inv['invite_link']
            assert re.fullmatch(r'MTB-[A-F0-9]{4}-[A-F0-9]{4}', inv['token'])
            assert inv['invite_link'].endswith(f"/invite/{inv['token']}")
            assert inv['invited_by'] == pi_user['id']
            assert inv.get('expires_at'), 'lifecycle needs an expiry'
            # own-org list
            async with make_client() as cli:
                r = await cli.get('/api/invitations', headers=headers)
            assert r.status_code == 200
            assert any(i['id'] == inv['id'] for i in r.json())
            # create is audited
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.create', 'target_id': inv['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_public_resolve(self, pi):
        pi_user, headers = pi
        async def flow():
            inv = await _invite(headers)
            pasted_variant = inv['token'].replace('-', '').lower()
            async with make_client() as cli:   # public: no auth
                r = await cli.get(f"/api/invitations/{pasted_variant}")
            assert r.status_code == 200, r.text
            j = r.json()
            assert set(j) >= {
                'org', 'site', 'role', 'inviter', 'email', 'status', 'expires_at',
                'full_name', 'designation', 'phone', 'org_name', 'admin_name',
            }
            assert j['org'] == ORG_HOSPITAL
            assert j['role'] == 'crc'
            assert j['inviter'] == pi_user['full_name']
            assert j['email'] == inv['email']
            assert j['status'] == 'pending'
            assert j['expires_at']
        run(flow())

    def test_resolve_unknown_token_404(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/invitations/{uuid.uuid4().hex}')
            assert r.status_code == 404
        run(flow())

    def test_accept_reserves_until_registration_then_consumes_atomically(self, pi):
        _, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:
                r = await cli.post(f"/api/invitations/{inv['token']}/accept")
                assert r.status_code == 200, r.text
                assert r.json()['status'] == 'pending'
                r2 = await cli.get(f"/api/invitations/{inv['token']}")
                assert r2.json()['status'] == 'pending'

            pending = {
                'id': str(uuid.uuid4()),
                'full_name': f'Atomic Invitee {RUN_ID}',
                'role': inv['role'],
                'email': inv['email'],
                'phone': '',
                'organization': ORG_HOSPITAL,
                'hashed_password': server.pwd_ctx.hash(PASSWORD),
                'profile': {'designation': 'Coordinator'},
                'channels': [],
                'invitation_id': inv['id'],
                'invite_token': inv['token'],
            }
            await server.db.pending_registrations.insert_one(pending)
            session = await server._complete_registration(pending)
            assert session['user']['role'] == inv['role']
            accepted = await server.db.invitations.find_one(
                {'id': inv['id']}, {'_id': 0})
            assert accepted['status'] == 'accepted'
            assert accepted['accepted_user_id'] == session['user']['id']

            async with make_client() as cli:
                r3 = await cli.post(f"/api/invitations/{inv['token']}/accept")
            assert r3.status_code == 400
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.accept', 'target_id': inv['id']})
            assert row, 'accept not audited'
        run(flow())

    def test_resend_extends_expiry_and_is_audited(self, pi):
        pi_user, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:
                r = await cli.post(f"/api/invitations/{inv['id']}/resend", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()['expires_at'] >= inv['expires_at']
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.resend', 'target_id': inv['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_cancel_blocks_accept_and_resend(self, pi):
        _, headers = pi
        async def flow():
            inv = await _invite(headers)
            async with make_client() as cli:
                r = await cli.post(f"/api/invitations/{inv['id']}/cancel", headers=headers)
                assert r.status_code == 200, r.text
                r2 = await cli.get(f"/api/invitations/{inv['token']}")
                assert r2.json()['status'] == 'cancelled'
                r3 = await cli.post(f"/api/invitations/{inv['token']}/accept")
                assert r3.status_code == 400
                r4 = await cli.post(f"/api/invitations/{inv['id']}/resend", headers=headers)
                assert r4.status_code == 400
            row = await server.db.audit_logs.find_one(
                {'action': 'invitation.cancel', 'target_id': inv['id']})
            assert row, 'cancel not audited'
        run(flow())

    def test_patient_role_cannot_list_or_cancel(self, pi):
        _, pi_headers = pi
        async def flow():
            inv = await _invite(pi_headers)
            _, patient_headers = await _register('patient')
            async with make_client() as cli:
                r = await cli.get('/api/invitations', headers=patient_headers)
                assert r.status_code == 403
                r2 = await cli.post(f"/api/invitations/{inv['id']}/cancel", headers=patient_headers)
                assert r2.status_code == 403
        run(flow())


# ── User preferences (calendar settings persistence) ─────────────────────────
class TestPreferences:
    def test_calendar_settings_keys_persist(self):
        async def flow():
            _, headers = await _register('patient')
            payload = {
                'calendar_default_view': 'week',
                'week_start': 'monday',
                'reminders_visits': False,
                'reminders_meds': True,
                'reminder_hours_before': 72,
            }
            async with make_client() as cli:
                r = await cli.patch('/api/preferences', headers=headers, json=payload)
                assert r.status_code == 200, r.text
                r2 = await cli.get('/api/preferences', headers=headers)
                assert r2.status_code == 200, r2.text
                got = r2.json()
            for k, v in payload.items():
                assert got.get(k) == v, f'{k}: expected {v!r}, got {got.get(k)!r}'
        run(flow())


# ── Seed expansion (Task 1.4) ────────────────────────────────────────────────
# The seed writes the durable demo dataset (natural-key upserts), so nothing
# here is cleaned up in teardown — reruns must simply never duplicate rows.
SEED_EMAILS = ['admin@mtb.app', 'sponsor@mtb.app', 'cro@mtb.app', 'smo@mtb.app',
               'site@mtb.app', 'pi@mtb.app', 'crc@mtb.app', 'patient@mtb.app']
SEED_PROTOCOLS = ['Protocol-001', 'Protocol-002', 'Protocol-003']


async def _seed_once():
    async with make_client() as cli:
        r = await cli.post('/api/seed')
    assert r.status_code == 200, r.text
    return r.json()


async def _snapshot():
    """Row counts across every collection the seed touches, keyed so a rerun
    that duplicates anything shows up as a diff."""
    db = server.db
    trial_ids = [t['id'] async for t in db.trials.find(
        {'protocol_id': {'$in': SEED_PROTOCOLS}}, {'id': 1})]
    pids = [p['id'] async for p in db.patients.find(
        {'email': {'$regex': r'@mtb\.app$'}}, {'id': 1})]
    return {
        'users': await db.users.count_documents({'email': {'$in': SEED_EMAILS}}),
        'orgs': await db.organizations.count_documents({'seed': True}),
        'trials': len(trial_ids),
        'visits': await db.visits.count_documents({'trial_id': {'$in': trial_ids}}),
        'patients': len(pids),
        'instances': await db.visit_instances.count_documents({'patient_id': {'$in': pids}}),
        'medications': await db.medications.count_documents({'patient_id': {'$in': pids}}),
        'dose_logs': await db.dose_logs.count_documents({'patient_id': {'$in': pids}}),
        'notifications': await db.notifications.count_documents({'seed': True}),
        'tickets': await db.support_tickets.count_documents({'seed': True}),
        'invitations': await db.invitations.count_documents({'seed': True}),
        'audits': await db.audit_logs.count_documents({'seed': True}),
        'master_data': await db.master_data_submissions.count_documents({'seed': True}),
        'terms': await db.terms_versions.count_documents({'seed': True}),
        'alerts': await db.system_alerts.count_documents({'seed': True}),
        'broadcasts': await db.broadcast_messages.count_documents({'seed': True}),
    }


async def _login(email):
    async with make_client() as cli:
        r = await cli.post('/api/auth/login', json={'email': email, 'password': PASSWORD})
    assert r.status_code == 200, f'{email}: {r.text}'
    return r.json()


@pytest.fixture(scope='module')
def seeded():
    return run(_seed_once())


class TestSeedExpansion:
    def test_seed_twice_no_duplicates(self, seeded):
        async def flow():
            await _seed_once()
            snap1 = await _snapshot()
            await _seed_once()
            snap2 = await _snapshot()
            assert snap1 == snap2, f'seed rerun changed row counts:\n{snap1}\nvs\n{snap2}'
            assert snap1['users'] == 8       # one per role incl. admin
            assert snap1['orgs'] == 4
            assert snap1['trials'] == 3
            assert snap1['patients'] == 8
            for key, val in snap1.items():
                assert val > 0, f'no seed rows for {key}'
        run(flow())

    def test_admin_login_works(self, seeded):
        async def flow():
            j = await _login('admin@mtb.app')
            assert j['user']['role'] == 'admin'
            async with make_client() as cli:
                r = await cli.get('/api/auth/me',
                                  headers={'Authorization': f"Bearer {j['access_token']}"})
            assert r.status_code == 200, r.text
            assert r.json()['email'] == 'admin@mtb.app'
        run(flow())

    def test_existing_demo_accounts_still_login(self, seeded):
        async def flow():
            for email, role in [('patient@mtb.app', 'patient'), ('pi@mtb.app', 'pi'),
                                ('crc@mtb.app', 'crc'), ('sponsor@mtb.app', 'sponsor'),
                                ('cro@mtb.app', 'cro'), ('smo@mtb.app', 'smo'),
                                ('site@mtb.app', 'site')]:
                j = await _login(email)
                assert j['user']['role'] == role, f'{email} has role {j["user"]["role"]}'
        run(flow())

    def test_org_admin_flags_on_sponsor_and_site(self, seeded):
        async def flow():
            for email in ('sponsor@mtb.app', 'site@mtb.app'):
                u = await server.db.users.find_one({'email': email}, {'_id': 0})
                assert u and u.get('org_admin') is True, f'{email} missing org_admin flag'
        run(flow())

    def test_patient_adherence_about_93(self, seeded):
        async def flow():
            j = await _login('patient@mtb.app')
            async with make_client() as cli:
                r = await cli.get('/api/adherence',
                                  headers={'Authorization': f"Bearer {j['access_token']}"})
            assert r.status_code == 200, r.text
            a = r.json()
            assert 90 <= a['rate'] <= 95, f'expected ~93% adherence, got {a}'
            assert a['total'] > 0
            assert a['streak_days'] >= 1
            assert len(a['last7']) == 7
        run(flow())

    def test_reseed_prunes_stale_seed_dose_logs(self, seeded):
        async def flow():
            db = server.db
            priya = (await db.patients.find_one({'email': 'patient@mtb.app'}, {'id': 1}))['id']
            stale_date = (server.now().date() - server.timedelta(days=90)).isoformat()
            stale_id = f'stale-{RUN_ID}-{uuid.uuid4().hex[:6]}'
            # An old seed-marked row (out of window) must be pruned on reseed…
            await db.dose_logs.insert_one({
                'id': stale_id, 'patient_id': priya, 'medication_id': 'x',
                'date': stale_date, 'time': '08:00', 'status': 'taken', 'seed': True})
            # …but a non-seed old row for the same patient must be left untouched.
            keep_id = f'keep-{RUN_ID}-{uuid.uuid4().hex[:6]}'
            await db.dose_logs.insert_one({
                'id': keep_id, 'patient_id': priya, 'medication_id': 'x',
                'date': stale_date, 'time': '09:00', 'status': 'taken'})
            await _seed_once()
            assert await db.dose_logs.find_one({'id': stale_id}) is None, 'stale seed row not pruned'
            assert await db.dose_logs.find_one({'id': keep_id}), 'non-seed row wrongly deleted'
            await db.dose_logs.delete_one({'id': keep_id})   # teardown our own crumb
        run(flow())

    def test_seed_invitations_cover_all_statuses(self, seeded):
        async def flow():
            invs = await server.db.invitations.find({'seed': True}, {'_id': 0}).to_list(50)
            statuses = {server._invitation_status(i) for i in invs}
            assert statuses >= {'pending', 'accepted', 'expired', 'cancelled'}, statuses
        run(flow())

    def test_pi_tasks_include_overdue_and_today(self, seeded):
        async def flow():
            j = await _login('pi@mtb.app')
            async with make_client() as cli:
                r = await cli.get('/api/tasks',
                                  headers={'Authorization': f"Bearer {j['access_token']}"})
            assert r.status_code == 200, r.text
            types = {t['type'] for t in r.json()}
            assert 'overdue_visit' in types, types
            assert 'visit_today' in types, types
        run(flow())


# ── Add-patient: extra fields + baseline scheduling + duplicate subject-ID ────
class TestAddPatientFields:
    async def _pi_and_trial(self):
        j = await _login('pi@mtb.app')
        headers = {'Authorization': f"Bearer {j['access_token']}"}
        async with make_client() as cli:
            r = await cli.get('/api/trials', headers=headers)
        assert r.status_code == 200, r.text
        return headers, r.json()[0]['id']

    def test_stores_all_fields_and_baseline_drives_first_visit(self, seeded):
        async def flow():
            headers, trial_id = await self._pi_and_trial()
            subj = f'SUBJ-{RUN_ID}-A'
            baseline = '2025-05-05'
            async with make_client() as cli:
                r = await cli.post('/api/patients', headers=headers, json={
                    'full_name': f'Fields Patient {RUN_ID}',
                    'email': f'test-{RUN_ID}-fields@example.com',
                    'phone': '+910000000000', 'trial_id': trial_id,
                    'subject_id': subj, 'dob': '1990-01-01', 'gender': 'Female',
                    'language': 'Hindi', 'avatar_initials': 'FPT',
                    'baseline_date': baseline,
                })
            assert r.status_code == 200, r.text
            created = r.json()
            _created_patient_ids.append(created['id'])
            # all new fields round-tripped
            for k, v in [('subject_id', subj), ('dob', '1990-01-01'),
                         ('gender', 'Female'), ('language', 'Hindi'),
                         ('baseline_date', baseline)]:
                assert created.get(k) == v, f'{k}: {created.get(k)!r} != {v!r}'
            assert created['trial_id'] == trial_id      # the SELECTED trial is used
            assert created['avatar_initials'] == 'FPT'
            # baseline_date (not enrolled_date) anchors visit-instance scheduling:
            # the first template has day_offset 0, so it lands on the baseline day.
            async with make_client() as cli:
                r2 = await cli.get(f"/api/patients/{created['id']}", headers=headers)
            assert r2.status_code == 200, r2.text
            insts = sorted(r2.json()['instances'], key=lambda i: i['seq'])
            assert insts, 'no visit instances materialized'
            assert insts[0]['scheduled_date'][:10] == baseline, insts[0]['scheduled_date']
        run(flow())

    def test_duplicate_subject_id_in_trial_409(self, seeded):
        async def flow():
            headers, trial_id = await self._pi_and_trial()
            subj = f'SUBJ-{RUN_ID}-DUP'
            body = {
                'full_name': f'Dup One {RUN_ID}',
                'email': f'test-{RUN_ID}-dup1@example.com',
                'trial_id': trial_id, 'subject_id': subj,
            }
            async with make_client() as cli:
                r1 = await cli.post('/api/patients', headers=headers, json=body)
                assert r1.status_code == 200, r1.text
                _created_patient_ids.append(r1.json()['id'])
                # same subject-id, same trial, different email → rejected
                r2 = await cli.post('/api/patients', headers=headers, json={
                    **body, 'email': f'test-{RUN_ID}-dup2@example.com',
                    'full_name': f'Dup Two {RUN_ID}'})
            assert r2.status_code == 409, r2.text
            assert subj in r2.json()['detail']
        run(flow())


# ── Team directory: org- + trial-scoped, NOT the whole user list ─────────────
class TestTeam:
    def test_team_is_org_and_trial_scoped(self, seeded):
        async def flow():
            j = await _login('pi@mtb.app')          # AIIMS Delhi, staffs Protocol-001
            headers = {'Authorization': f"Bearer {j['access_token']}"}
            async with make_client() as cli:
                r = await cli.get('/api/team', headers=headers)
            assert r.status_code == 200, r.text
            emails = {m['email'] for m in r.json()}
            # same-org staff
            assert 'crc@mtb.app' in emails
            assert 'site@mtb.app' in emails
            # trial collaborator (Protocol-001 created_by = sponsor)
            assert 'sponsor@mtb.app' in emails
            # NOT the whole directory: patients, admin, unrelated orgs excluded
            assert 'patient@mtb.app' not in emails
            assert 'admin@mtb.app' not in emails
            assert 'cro@mtb.app' not in emails
            assert 'smo@mtb.app' not in emails
            # caller never lists themselves
            assert 'pi@mtb.app' not in emails
        run(flow())

    def test_team_requires_staff_role(self, seeded):
        async def flow():
            j = await _login('patient@mtb.app')
            headers = {'Authorization': f"Bearer {j['access_token']}"}
            async with make_client() as cli:
                r = await cli.get('/api/team', headers=headers)
            assert r.status_code == 403, r.text
        run(flow())
