"""Org-admin console API — Task 6.1 (SECURITY).

Covers /api/org/{orgId}/… gating and flows:
- fail-closed guard: plain members 403, org-admin of org A cannot touch org B,
  platform admin passes
- team roster: list, invite, make-admin, assign-site, deactivate
- ownership transfer: propose → successor accepts → flags + handover applied
- org-scoped audit trail
- sites CRUD (SMO hospital network)
- trials with accessLevel full/restricted, subjects ALWAYS masked (SUBJ-xxx)
- trial access-requests + grant (owning-org admin or platform admin)
- trial-creation delegation status/requests

Same harness as test_authz_scoping.py: in-process ASGITransport against the
real Atlas DB, RUN_ID-marked data, single module-level event loop, module
teardown cleanup.
"""
import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG_A = f'ORGADM-{RUN_ID} Site Alpha'
ORG_B = f'ORGADM-{RUN_ID} Site Beta'
ORG_SPONSOR = f'ORGADM-{RUN_ID} Pharma'

LOOP = asyncio.new_event_loop()
_trial_ids = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None, org_admin=False):
    suffix = uuid.uuid4().hex[:6]
    email = f'orgadm-{RUN_ID}-{role}-{suffix}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            # Unique per registration so two same-role users (e.g. org-A and org-B
            # 'site' admins) don't collide when a test checks names across orgs.
            'full_name': f'OrgTest {role.upper()} {RUN_ID} {suffix}',
            'role': role, 'organization': org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    if org_admin:
        await server.db.users.update_one({'id': j['user']['id']},
                                         {'$set': {'org_admin': True}})
        j['user']['org_admin'] = True
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


async def _make_admin():
    email = f'orgadm-{RUN_ID}-padmin-{uuid.uuid4().hex[:4]}@example.com'
    doc = {
        'id': str(uuid.uuid4()), 'email': email,
        'full_name': f'Platform Admin {RUN_ID}', 'role': 'admin',
        'organization': 'MTB Health Technologies', 'phone': '',
        'hashed_password': server.pwd_ctx.hash(PASSWORD),
        'security_question': '', 'security_answer_hash': '',
        'avatar_initials': 'PA', 'created_at': server.now(), 'is_online': False,
    }
    await server.db.users.insert_one(doc)
    async with make_client() as cli:
        r = await cli.post('/api/auth/login', json={'email': email, 'password': PASSWORD})
    assert r.status_code == 200, r.text
    return doc, {'Authorization': f"Bearer {r.json()['access_token']}"}


async def _org_id(name):
    org = await server.db.organizations.find_one({'name': name}, {'_id': 0})
    assert org, f'organization {name} missing'
    return org['id']


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': RUN_ID}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.invitations.delete_many({'org': {'$regex': RUN_ID}})
        await db.ownership_transfers.delete_many({'org_name': {'$regex': RUN_ID}})
        await db.org_sites.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.visits.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.patients.delete_many({'email': {'$regex': RUN_ID}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.trial_access_requests.delete_many({'org_name': {'$regex': RUN_ID}})
        await db.org_trial_access.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.org_delegation_requests.delete_many({'org_name': {'$regex': RUN_ID}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
        await db.notifications.delete_many({'title': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def world():
    """Org A (admin_a + members), org B (admin_b), a sponsor org with a trial,
    and a restricted-view trial worked by org A staff."""
    async def build():
        admin_a, admin_a_h = await _register('site', org=ORG_A, org_admin=True)
        member_a, member_a_h = await _register('crc', org=ORG_A)          # plain member
        # Stable org-A admin used by the ops/trials tests (order-independent).
        successor_a, successor_a_h = await _register('pi', org=ORG_A, org_admin=True)
        assigned_site = f'ORGADM-{RUN_ID} Research Hospital'
        await server.db.users.update_one(
            {'id': successor_a['id']}, {'$set': {'site': assigned_site}})
        # A fresh non-admin member is the successor for the ownership-transfer test,
        # so that flow doesn't depend on (or mutate) the shared admin above.
        xfer_target, xfer_target_h = await _register('crc', org=ORG_A)
        admin_b, admin_b_h = await _register('site', org=ORG_B, org_admin=True)
        sponsor_admin, sponsor_admin_h = await _register('sponsor', org=ORG_SPONSOR,
                                                         org_admin=True)
        padmin, padmin_h = await _make_admin()
        org_a_id = await _org_id(ORG_A)
        org_b_id = await _org_id(ORG_B)
        org_sponsor_id = await _org_id(ORG_SPONSOR)

        # sponsor-owned trial (FULL for sponsor org; org A only works on it)
        async with make_client() as cli:
            r = await cli.post('/api/trials', headers=sponsor_admin_h, json={
                'title': f'Org Trial {RUN_ID}', 'protocol_id': f'ORG-{RUN_ID}',
                'phase': 'Phase III', 'condition': 'Testing',
                'sponsor_name': ORG_SPONSOR})
            assert r.status_code == 200, r.text
            trial = r.json()
            _trial_ids.append(trial['id'])
            rv = await cli.post('/api/visits', headers=sponsor_admin_h, json={
                'trial_id': trial['id'], 'visit_number': 1, 'name': 'Screening',
                'day_offset': 0, 'window_days': 3, 'activities': ['Vitals']})
            assert rv.status_code == 200, rv.text
        await server.db.invitations.insert_one({
            'id': str(uuid.uuid4()),
            'email': successor_a['email'],
            'phone': successor_a.get('phone', ''),
            'org': ORG_A,
            'trial_id': trial['id'],
            'role': 'pi',
            'status': 'accepted',
            'created_at': server.now(),
            'accepted_at': server.now(),
        })
        # org A's PI enrolls a patient → org A gets a RESTRICTED view of it
        async with make_client() as cli:
            rp = await cli.post('/api/patients', headers=successor_a_h, json={
                'full_name': f'Org Secret Patient {RUN_ID}',
                'email': f'orgadm-{RUN_ID}-subject@example.com',
                'trial_id': trial['id'], 'pi_id': successor_a['id'],
                'enrolled_date': (server.now() - timedelta(days=2)).date().isoformat()})
            assert rp.status_code == 200, rp.text
            patient = rp.json()
        return {
            'admin_a': (admin_a, admin_a_h), 'member_a': (member_a, member_a_h),
            'successor_a': (successor_a, successor_a_h),
            'xfer_target': (xfer_target, xfer_target_h),
            'admin_b': (admin_b, admin_b_h),
            'sponsor_admin': (sponsor_admin, sponsor_admin_h),
            'padmin': (padmin, padmin_h),
            'org_a_id': org_a_id, 'org_b_id': org_b_id,
            'org_sponsor_id': org_sponsor_id,
            'trial': trial, 'patient': patient, 'assigned_site': assigned_site,
        }
    return run(world_coro := build())


# ── Guard: fail-closed org-admin gating ──────────────────────────────────────
class TestOrgGuard:
    def test_plain_member_403(self, world):
        oid = world['org_a_id']
        async def flow():
            async with make_client() as cli:
                for path in (f'/api/org/{oid}/members', f'/api/org/{oid}/audit-trail',
                             f'/api/org/{oid}/sites', f'/api/org/{oid}/trials',
                             f'/api/org/{oid}/delegation-status'):
                    r = await cli.get(path, headers=world['member_a'][1])
                    assert r.status_code == 403, f'{path}: {r.status_code} {r.text}'
        run(flow())

    def test_cross_org_admin_403(self, world):
        """An org-admin of org B must NEVER reach org A."""
        oid = world['org_a_id']
        async def flow():
            async with make_client() as cli:
                h = world['admin_b'][1]
                r = await cli.get(f'/api/org/{oid}/members', headers=h)
                assert r.status_code == 403, r.text
                r2 = await cli.post(f'/api/org/{oid}/members/invite', headers=h, json={
                    'email': f'orgadm-{RUN_ID}-evil@example.com', 'role': 'crc'})
                assert r2.status_code == 403, r2.text
                r3 = await cli.post(f'/api/org/{oid}/sites', headers=h,
                                    json={'name': f'ORGADM-{RUN_ID} Evil Site'})
                assert r3.status_code == 403, r3.text
        run(flow())

    def test_own_admin_and_platform_admin_pass(self, world):
        oid = world['org_a_id']
        async def flow():
            async with make_client() as cli:
                for key in ('admin_a', 'padmin'):
                    r = await cli.get(f'/api/org/{oid}/members', headers=world[key][1])
                    assert r.status_code == 200, f'{key}: {r.text}'
        run(flow())

    def test_unauthenticated_401(self, world):
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f"/api/org/{world['org_a_id']}/members")
                assert r.status_code == 401, r.text
        run(flow())

    def test_unknown_org_404(self, world):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/org/nope-does-not-exist/members',
                                  headers=world['padmin'][1])
                assert r.status_code == 404, r.text
        run(flow())


# ── Team roster ──────────────────────────────────────────────────────────────
class TestOrgRoster:
    def test_members_list_marks_you_and_admin(self, world):
        oid = world['org_a_id']
        admin_a = world['admin_a'][0]
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/org/{oid}/members', headers=world['admin_a'][1])
                rows = r.json()
                me = [m for m in rows if m['id'] == admin_a['id']]
                assert me and me[0]['you'] is True and me[0]['admin'] is True
                member = [m for m in rows if m['id'] == world['member_a'][0]['id']]
                assert member and member[0]['admin'] is False
        run(flow())

    def test_invite_shows_as_invited_roster_row(self, world):
        oid = world['org_a_id']
        h = world['admin_a'][1]
        async def flow():
            async with make_client() as cli:
                r = await cli.post(f'/api/org/{oid}/members/invite', headers=h, json={
                    'email': f'orgadm-{RUN_ID}-newhire@example.com',
                    'phone': '+91 98765 43210',
                    'full_name': f'New Hire {RUN_ID}', 'role': 'crc'})
                assert r.status_code == 200, r.text
                assert r.json()['invite_link']
                assert r.json()['phone'] == '+91 98765 43210'
                lst = await cli.get(f'/api/org/{oid}/members', headers=h)
                invited = [m for m in lst.json() if m['status'] == 'invited'
                           and m['email'] == f'orgadm-{RUN_ID}-newhire@example.com']
                assert invited, 'pending invite must appear in the roster'
                # inviting an existing member is refused
                dup = await cli.post(f'/api/org/{oid}/members/invite', headers=h, json={
                    'email': world['member_a'][0]['email'], 'role': 'crc'})
                assert dup.status_code == 400, dup.text
                audit = await server.db.audit_logs.find_one({'action': 'org.member_invite'})
                assert audit, 'invite must be audited'
        run(flow())

    def test_make_admin_and_assign_site(self, world):
        oid = world['org_a_id']
        h = world['admin_a'][1]
        async def flow():
            target, _ = await _register('crc', org=ORG_A)
            async with make_client() as cli:
                r = await cli.post(f"/api/org/{oid}/members/{target['id']}/make-admin",
                                   headers=h)
                assert r.status_code == 200, r.text
                fresh = await server.db.users.find_one({'id': target['id']}, {'_id': 0})
                assert fresh.get('org_admin') is True
                r2 = await cli.post(f"/api/org/{oid}/members/{target['id']}/assign-site",
                                    headers=h, json={'site': f'ORGADM-{RUN_ID} North Wing'})
                assert r2.status_code == 200, r2.text
                fresh2 = await server.db.users.find_one({'id': target['id']}, {'_id': 0})
                assert fresh2.get('site') == f'ORGADM-{RUN_ID} North Wing'
                # a member of ANOTHER org is 404 (no cross-org writes)
                foreign = world['admin_b'][0]
                r3 = await cli.post(f"/api/org/{oid}/members/{foreign['id']}/make-admin",
                                    headers=h)
                assert r3.status_code == 404, r3.text
        run(flow())

    def test_remove_member_deactivates(self, world):
        oid = world['org_a_id']
        h = world['admin_a'][1]
        admin_a = world['admin_a'][0]
        async def flow():
            victim, _ = await _register('crc', org=ORG_A)
            async with make_client() as cli:
                # cannot remove yourself
                r0 = await cli.delete(f"/api/org/{oid}/members/{admin_a['id']}", headers=h)
                assert r0.status_code == 400, r0.text
                r = await cli.delete(f"/api/org/{oid}/members/{victim['id']}", headers=h)
                assert r.status_code == 200, r.text
                fresh = await server.db.users.find_one({'id': victim['id']}, {'_id': 0})
                assert fresh['status'] == 'Deactivated', 'record kept, not hard-deleted'
                lst = await cli.get(f'/api/org/{oid}/members', headers=h)
                row = [m for m in lst.json() if m['id'] == victim['id']]
                assert row and row[0]['status'] == 'deactivated'
        run(flow())


# ── Ownership transfer ───────────────────────────────────────────────────────
class TestOwnershipTransfer:
    def test_propose_and_successor_accepts(self, world):
        oid = world['org_a_id']
        admin_a, admin_a_h = world['admin_a']
        successor, successor_h = world['xfer_target']
        member_h = world['member_a'][1]
        async def flow():
            async with make_client() as cli:
                r = await cli.post(f'/api/org/{oid}/ownership-transfer', headers=admin_a_h,
                                   json={'successor_id': successor['id'],
                                         'reason': 'Moving to another engagement',
                                         'handover': 'deactivate'})
                assert r.status_code == 200, r.text
                transfer = r.json()
                assert transfer['status'] == 'pending'
                pending = await cli.get(
                    f"/api/org/{oid}/ownership-transfer/pending",
                    headers=successor_h)
                assert pending.status_code == 200, pending.text
                assert pending.json()['id'] == transfer['id']
                not_mine = await cli.get(
                    f"/api/org/{oid}/ownership-transfer/pending",
                    headers=member_h)
                assert not_mine.status_code == 200 and not_mine.json() is None
                # only the designated successor may accept — another member 403
                r_wrong = await cli.post(
                    f"/api/org/{oid}/ownership-transfer/{transfer['id']}/accept",
                    headers=member_h)
                assert r_wrong.status_code == 403, r_wrong.text
                # the successor (NOT yet org admin) accepts
                r_ok = await cli.post(
                    f"/api/org/{oid}/ownership-transfer/{transfer['id']}/accept",
                    headers=successor_h)
                assert r_ok.status_code == 200, r_ok.text
                new_admin = await server.db.users.find_one({'id': successor['id']}, {'_id': 0})
                assert new_admin.get('org_admin') is True
                old_admin = await server.db.users.find_one({'id': admin_a['id']}, {'_id': 0})
                assert old_admin.get('org_admin') is False
                assert old_admin.get('status') == 'Deactivated'
                # accepting twice is refused
                again = await cli.post(
                    f"/api/org/{oid}/ownership-transfer/{transfer['id']}/accept",
                    headers=successor_h)
                assert again.status_code == 400, again.text
                for action in ('org.ownership_transfer_start',
                               'org.ownership_transfer_accept'):
                    audit = await server.db.audit_logs.find_one(
                        {'action': action, 'target_id': transfer['id']})
                    assert audit, f'{action} must be audited'
            # the successor now administers org A (used by later tests)
        run(flow())

    def test_successor_can_decline_without_privilege_changes(self, world):
        oid = world['org_b_id']
        admin_b, admin_b_h = world['admin_b']

        async def flow():
            successor, successor_h = await _register('crc', org=ORG_B)
            async with make_client() as cli:
                proposed = await cli.post(
                    f'/api/org/{oid}/ownership-transfer', headers=admin_b_h,
                    json={'successor_id': successor['id'],
                          'reason': 'Testing a declined ownership handover',
                          'handover': 'deactivate'})
                assert proposed.status_code == 200, proposed.text
                transfer = proposed.json()
                pending = await cli.get(
                    f"/api/org/{oid}/ownership-transfer/pending",
                    headers=successor_h)
                assert pending.status_code == 200
                assert pending.json()['id'] == transfer['id']
                declined = await cli.post(
                    f"/api/org/{oid}/ownership-transfer/{transfer['id']}/decline",
                    headers=successor_h,
                    json={'reason': 'I cannot take responsibility at this time'})
                assert declined.status_code == 200, declined.text
                assert declined.json()['status'] == 'declined'
                assert (await cli.get(
                    f"/api/org/{oid}/ownership-transfer/pending",
                    headers=successor_h)).json() is None
                successor_fresh = await server.db.users.find_one(
                    {'id': successor['id']}, {'_id': 0})
                admin_fresh = await server.db.users.find_one(
                    {'id': admin_b['id']}, {'_id': 0})
                assert not successor_fresh.get('org_admin')
                assert admin_fresh.get('org_admin') is True
                audit = await server.db.audit_logs.find_one({
                    'action': 'org.ownership_transfer_decline',
                    'target_id': transfer['id']})
                assert audit, 'decline must be audited'
        run(flow())


# ── Audit trail / sites / delegation ─────────────────────────────────────────
class TestOrgOps:
    def test_audit_trail_is_org_scoped(self, world):
        oid = world['org_a_id']
        h = world['successor_a'][1]   # org A admin after the transfer
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/org/{oid}/audit-trail', headers=h)
                assert r.status_code == 200, r.text
                rows = r.json()
                assert rows, 'org A has audited activity'
                assert all({'id', 'at', 'actor', 'action', 'kind'} <= set(e) for e in rows)
                # entries are from org A's world only (never org B's admin)
                actors = {e['actor'] for e in rows}
                assert world['admin_b'][0]['full_name'] not in actors
        run(flow())

    def test_sites_crud(self, world):
        oid = world['org_a_id']
        h = world['successor_a'][1]
        async def flow():
            async with make_client() as cli:
                r = await cli.post(f'/api/org/{oid}/sites', headers=h,
                                   json={'name': f'ORGADM-{RUN_ID} East Clinic',
                                         'address': '12 East Road'})
                assert r.status_code == 200, r.text
                site = r.json()
                dup = await cli.post(f'/api/org/{oid}/sites', headers=h,
                                     json={'name': f'ORGADM-{RUN_ID} East Clinic'})
                assert dup.status_code == 400, dup.text
                lst = await cli.get(f'/api/org/{oid}/sites', headers=h)
                assert any(s['id'] == site['id'] for s in lst.json())
                rd = await cli.delete(f"/api/org/{oid}/sites/{site['id']}", headers=h)
                assert rd.status_code == 200, rd.text
                lst2 = await cli.get(f'/api/org/{oid}/sites', headers=h)
                assert not any(s['id'] == site['id'] for s in lst2.json())
        run(flow())

    def test_delegation_status_and_request(self, world):
        oid = world['org_a_id']
        h = world['successor_a'][1]
        async def flow():
            async with make_client() as cli:
                r0 = await cli.get(f'/api/org/{oid}/delegation-status', headers=h)
                assert r0.status_code == 200 and r0.json()['delegated'] is False
                r1 = await cli.post(f'/api/org/{oid}/delegation-requests', headers=h,
                                    json={'reason': 'We run our own investigator-initiated trials'})
                assert r1.status_code == 200, r1.text
                # duplicate pending request refused
                r2 = await cli.post(f'/api/org/{oid}/delegation-requests', headers=h,
                                    json={'reason': 'Second request should be refused'})
                assert r2.status_code == 400, r2.text
                r3 = await cli.get(f'/api/org/{oid}/delegation-status', headers=h)
                assert r3.json()['request']['status'] == 'pending'
        run(flow())


# ── Trials: access levels + masking + access requests ────────────────────────
class TestOrgTrials:
    def test_sponsor_org_full_site_org_restricted_and_masked(self, world):
        trial = world['trial']
        patient = world['patient']
        async def flow():
            async with make_client() as cli:
                # sponsor org → FULL (it owns the trial), subjects masked
                r = await cli.get(f"/api/org/{world['org_sponsor_id']}/trials",
                                  headers=world['sponsor_admin'][1])
                assert r.status_code == 200, r.text
                mine = [t for t in r.json() if t['id'] == trial['id']]
                assert mine and mine[0]['accessLevel'] == 'full'
                owner_row = mine[0]
                assert owner_row['accessStatus'] == 'full'
                assert owner_row['sponsor'] == ORG_SPONSOR
                assert 'target' in owner_row
                assert isinstance(owner_row['documentCount'], int)
                assert owner_row['permissions']['canEdit'] is True
                assert owner_row['permissions']['canManageDocuments'] is True
                assert owner_row['updatedBy']['id'] == trial['created_by']
                assert owner_row['pis'] and owner_row['pis'][0]['id'] == world['successor_a'][0]['id']
                assert 'crcs' in owner_row
                assert mine[0]['subjects'][0]['subject'].startswith('SUBJ-')
                assert patient['full_name'] not in r.text
                assert patient['email'] not in r.text
                # org A (site working the trial) → RESTRICTED, schedule only
                r2 = await cli.get(f"/api/org/{world['org_a_id']}/trials",
                                   headers=world['successor_a'][1])
                assert r2.status_code == 200, r2.text
                mine2 = [t for t in r2.json() if t['id'] == trial['id']]
                assert mine2 and mine2[0]['accessLevel'] == 'restricted'
                assert mine2[0]['accessStatus'] == 'restricted'
                assert mine2[0]['permissions']['canEdit'] is False
                assert mine2[0]['permissions']['canRequestAccess'] is True
                assert 'documentCount' in mine2[0]
                assert 'updatedAt' in mine2[0]
                assert 'subjects' not in mine2[0], 'restricted view is schedule-only'
                assert mine2[0]['schedule'], 'restricted view still shows the schedule'
                assert world['assigned_site'] in mine2[0]['sites']
                assert patient['full_name'] not in r2.text
        run(flow())

    def test_access_request_and_grant_upgrades_to_full(self, world):
        trial = world['trial']
        org_a_id = world['org_a_id']
        h_a = world['successor_a'][1]      # org A admin (requester)
        async def flow():
            async with make_client() as cli:
                # a plain member cannot request
                bad = await cli.post(f"/api/trials/{trial['id']}/access-requests",
                                     headers=world['member_a'][1],
                                     json={'org_id': org_a_id})
                assert bad.status_code == 403, bad.text
                # org B's admin cannot request FOR org A
                badb = await cli.post(f"/api/trials/{trial['id']}/access-requests",
                                      headers=world['admin_b'][1],
                                      json={'org_id': org_a_id})
                assert badb.status_code == 403, badb.text
                r = await cli.post(f"/api/trials/{trial['id']}/access-requests",
                                   headers=h_a, json={'org_id': org_a_id,
                                                      'reason': 'Need full oversight'})
                assert r.status_code == 200, r.text
                req = r.json()
                owner_list = await cli.get(
                    f"/api/org/{world['org_sponsor_id']}/trial-access-requests",
                    headers=world['sponsor_admin'][1])
                assert owner_list.status_code == 200, owner_list.text
                listed = [row for row in owner_list.json() if row['id'] == req['id']]
                assert listed and listed[0]['protocol_id'] == trial['protocol_id']
                owner_note = await server.db.notifications.find_one({
                    'user_id': world['sponsor_admin'][0]['id'],
                    'kind': 'trial_access_request', 'request_id': req['id']})
                assert owner_note, 'trial owner must be notified of the request'
                # the REQUESTER cannot grant their own request (not the owner)
                selfg = await cli.post(
                    f"/api/trials/{trial['id']}/access-requests/{req['id']}/grant",
                    headers=h_a)
                assert selfg.status_code == 403, selfg.text
                # the trial-owning org's admin grants it
                g = await cli.post(
                    f"/api/trials/{trial['id']}/access-requests/{req['id']}/grant",
                    headers=world['sponsor_admin'][1])
                assert g.status_code == 200, g.text
                # org A now sees the trial as FULL (still masked subjects)
                r2 = await cli.get(f'/api/org/{org_a_id}/trials', headers=h_a)
                mine = [t for t in r2.json() if t['id'] == world['trial']['id']]
                assert mine and mine[0]['accessLevel'] == 'full'
                assert mine[0]['subjects'][0]['subject'].startswith('SUBJ-')
                assert world['patient']['full_name'] not in r2.text
                for action in ('org.trial_access_request', 'org.trial_access_grant'):
                    audit = await server.db.audit_logs.find_one({'action': action})
                    assert audit, f'{action} must be audited'
                requester_note = await server.db.notifications.find_one({
                    'user_id': world['successor_a'][0]['id'],
                    'kind': 'trial_access_decision', 'request_id': req['id']})
                assert requester_note, 'requester must be notified when access is granted'
        run(flow())

    def test_access_request_reject_is_listed_audited_and_notified(self, world):
        trial = world['trial']
        org_b_id = world['org_b_id']
        requester, requester_h = world['admin_b']
        owner_h = world['sponsor_admin'][1]

        async def flow():
            async with make_client() as cli:
                requested = await cli.post(
                    f"/api/trials/{trial['id']}/access-requests",
                    headers=requester_h,
                    json={'org_id': org_b_id, 'reason': 'Need monitoring access'})
                assert requested.status_code == 200, requested.text
                req = requested.json()
                rejected = await cli.post(
                    f"/api/trials/{trial['id']}/access-requests/{req['id']}/reject",
                    headers=owner_h,
                    json={'reason': 'No active operating relationship'})
                assert rejected.status_code == 200, rejected.text
                assert rejected.json()['status'] == 'rejected'
                history = await cli.get(
                    f"/api/org/{world['org_sponsor_id']}/trial-access-requests"
                    '?status=rejected',
                    headers=owner_h)
                assert history.status_code == 200, history.text
                assert any(row['id'] == req['id'] for row in history.json())
                grant_after_reject = await cli.post(
                    f"/api/trials/{trial['id']}/access-requests/{req['id']}/grant",
                    headers=owner_h)
                assert grant_after_reject.status_code == 400
                audit = await server.db.audit_logs.find_one({
                    'action': 'org.trial_access_reject',
                    'target_id': req['id']})
                assert audit, 'rejection must be audited'
                note = await server.db.notifications.find_one({
                    'user_id': requester['id'], 'kind': 'trial_access_decision',
                    'request_id': req['id']})
                assert note, 'requester must be notified when access is rejected'
        run(flow())


# ── Org-admin trial edit & archive (owner + full access only) ────────────────
class TestOrgTrialEditArchive:
    def test_owner_edits_restricted_org_cannot(self, world):
        trial = world['trial']
        owner_h = world['sponsor_admin'][1]
        restricted_h = world['admin_a'][1]
        async def flow():
            async with make_client() as cli:
                # restricted (non-owner) org-admin: 403 on edit and archive
                denied = await cli.patch(
                    f"/api/org/{world['org_a_id']}/trials/{trial['id']}",
                    headers=restricted_h, json={'duration': '12 months'})
                assert denied.status_code == 403, denied.text
                denied2 = await cli.post(
                    f"/api/org/{world['org_a_id']}/trials/{trial['id']}/archive",
                    headers=restricted_h, json={'archived': True})
                assert denied2.status_code == 403, denied2.text
                # owning org-admin edits successfully with provenance + audit
                edited = await cli.patch(
                    f"/api/org/{world['org_sponsor_id']}/trials/{trial['id']}",
                    headers=owner_h,
                    json={'duration': '24 months', 'target_enrollment': 55,
                          'recruitment_status': 'recruiting'})
                assert edited.status_code == 200, edited.text
                j = edited.json()
                assert j['duration'] == '24 months'
                assert j['target_enrollment'] == 55
                assert j['updated_by'] == world['sponsor_admin'][0]['id']
                audit = await server.db.audit_logs.find_one(
                    {'action': 'org.trial_edit', 'target_id': trial['id']})
                assert audit and 'duration' in (audit.get('changes') or {})
                # empty patch is refused
                empty = await cli.patch(
                    f"/api/org/{world['org_sponsor_id']}/trials/{trial['id']}",
                    headers=owner_h, json={})
                assert empty.status_code == 400, empty.text
        run(flow())

    def test_archive_lifecycle_blocks_edit_and_is_audited(self, world):
        trial = world['trial']
        owner_h = world['sponsor_admin'][1]
        async def flow():
            async with make_client() as cli:
                archived = await cli.post(
                    f"/api/org/{world['org_sponsor_id']}/trials/{trial['id']}/archive",
                    headers=owner_h, json={'archived': True})
                assert archived.status_code == 200, archived.text
                # the org trial list reflects the archived state
                rows = await cli.get(f"/api/org/{world['org_sponsor_id']}/trials",
                                     headers=owner_h)
                row = next(r for r in rows.json() if r['id'] == trial['id'])
                assert row['archived'] is True
                # double-archive refused; edit while archived refused
                dup = await cli.post(
                    f"/api/org/{world['org_sponsor_id']}/trials/{trial['id']}/archive",
                    headers=owner_h, json={'archived': True})
                assert dup.status_code == 409, dup.text
                locked = await cli.patch(
                    f"/api/org/{world['org_sponsor_id']}/trials/{trial['id']}",
                    headers=owner_h, json={'duration': '30 months'})
                assert locked.status_code == 409, locked.text
                audit = await server.db.audit_logs.find_one(
                    {'action': 'org.trial_archive', 'target_id': trial['id']})
                assert audit, 'archiving must be audited'
                # unarchive restores editability
                restored = await cli.post(
                    f"/api/org/{world['org_sponsor_id']}/trials/{trial['id']}/archive",
                    headers=owner_h, json={'archived': False})
                assert restored.status_code == 200, restored.text
                fresh = await server.db.trials.find_one({'id': trial['id']}, {'_id': 0})
                assert fresh.get('archived') is False
                assert 'archived_at' not in fresh
        run(flow())
