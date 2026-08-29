"""Authorization ownership scoping — Task 3.75 (SECURITY).

Cross-tenant / cross-site patient-data isolation for the single-resource staff
endpoints that were previously under-scoped:

  - GET   /api/patients/{id}
  - GET   /api/patients/{id}/visits
  - PATCH /api/visit-instances/{id}
  - POST  /api/schedules/{trial_id}/approve|flag

Rule mirrored from the GET /patients list: a pi/crc reaches only patients at
their own site (assigned pi_id/crc_id, enrolled by them, or same site org); a
sponsor reaches only patients enrolled in a trial belonging to their org; a PI
reviews a schedule only for a trial they belong to. Foreign access → 403.

Same harness as test_visit_instances.py: in-process ASGITransport against the
real Atlas DB, RUN_ID-marked data, single module-level event loop (Motor pins
its io_loop on first use — never asyncio.run here), module teardown cleanup.
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
ORG_SITE_A = f'TESTORG-{RUN_ID} Site A Hospital'
ORG_SITE_B = f'TESTORG-{RUN_ID} Site B Hospital'
ORG_SPONSOR_A = f'TESTORG-{RUN_ID} Pharma A'
ORG_SPONSOR_B = f'TESTORG-{RUN_ID} Pharma B'

LOOP = asyncio.new_event_loop()
_trial_ids = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None):
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


async def _make_trial(sponsor_headers, sponsor_name,
                      templates=((0, 'Screening'), (7, 'Baseline'))):
    async with make_client() as cli:
        r = await cli.post('/api/trials', headers=sponsor_headers, json={
            'title': f'Test Trial {RUN_ID}', 'protocol_id': f'TEST-{RUN_ID}-{uuid.uuid4().hex[:4]}',
            'phase': 'Phase II', 'condition': 'Testing', 'sponsor_name': sponsor_name,
        })
        assert r.status_code == 200, r.text
        trial = r.json()
        _trial_ids.append(trial['id'])
        for i, (off, name) in enumerate(templates, start=1):
            rv = await cli.post('/api/visits', headers=sponsor_headers, json={
                'trial_id': trial['id'], 'visit_number': i, 'name': name,
                'day_offset': off, 'window_days': 3, 'activities': ['Vitals'],
            })
            assert rv.status_code == 200, rv.text
    return trial


async def _enroll(staff_headers, trial_id, pi_id=None, crc_id=None, days_ago=5):
    enrolled = (server.now() - timedelta(days=days_ago)).date().isoformat()
    async with make_client() as cli:
        r = await cli.post('/api/patients', headers=staff_headers, json={
            'full_name': f'Test PATIENT {RUN_ID}',
            'email': f'test-{RUN_ID}-enrollee-{uuid.uuid4().hex[:6]}@example.com',
            'trial_id': trial_id, 'pi_id': pi_id, 'crc_id': crc_id,
            'enrolled_date': enrolled,
        })
    assert r.status_code == 200, r.text
    return r.json()


async def _accept_trial_invite(user, trial_id):
    """Model the real sponsor/site invitation relationship before enrollment."""
    await server.db.invitations.insert_one({
        'id': str(uuid.uuid4()),
        'email': user['email'],
        'phone': user.get('phone', ''),
        'trial_id': trial_id,
        'role': user['role'],
        'status': 'accepted',
        'created_at': server.now(),
        'accepted_at': server.now(),
    })


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.visits.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.patients.delete_many({'email': {'$regex': f'test-{RUN_ID}-'}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.notifications.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.invitations.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.org_trial_access.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def world():
    """Two isolated sites + sponsors, each with its own trial + enrolled patient.

    Site A: pi_A / crc_A (ORG_SITE_A), sponsor_A (ORG_SPONSOR_A), trial_A,
            patient_A (pi_id=pi_A, crc_id=crc_A) — a legitimately-shared patient.
    Site B: pi_B / crc_B (ORG_SITE_B), sponsor_B (ORG_SPONSOR_B), trial_B,
            patient_B (pi_id=pi_B, crc_id=crc_B).
    """
    async def build():
        pi_a, pi_a_h = await _register('pi', org=ORG_SITE_A)
        crc_a, crc_a_h = await _register('crc', org=ORG_SITE_A)
        pi_b, pi_b_h = await _register('pi', org=ORG_SITE_B)
        crc_b, crc_b_h = await _register('crc', org=ORG_SITE_B)
        sp_a, sp_a_h = await _register('sponsor', org=ORG_SPONSOR_A)
        sp_b, sp_b_h = await _register('sponsor', org=ORG_SPONSOR_B)
        # A second, unassigned PI at Site A (same site org as pi_a, but not the
        # pi_id/crc_id/creator of patient_a) — exercises the org-match branch.
        pi_a2, pi_a2_h = await _register('pi', org=ORG_SITE_A)
        # A PI whose org == the sponsor's org (ORG_SPONSOR_A) — the legitimate
        # same-org pre-enrollment approver for an unclaimed trial.
        pi_sp_a, pi_sp_a_h = await _register('pi', org=ORG_SPONSOR_A)

        trial_a = await _make_trial(sp_a_h, ORG_SPONSOR_A)
        trial_b = await _make_trial(sp_b_h, ORG_SPONSOR_B)
        # An UNCLAIMED trial in sponsor_A's org: has a schedule but zero enrolled
        # patients, so no patient carries a pi_id yet.
        trial_a_unclaimed = await _make_trial(sp_a_h, ORG_SPONSOR_A)

        await _accept_trial_invite(pi_a, trial_a['id'])
        await _accept_trial_invite(pi_b, trial_b['id'])
        patient_a = await _enroll(pi_a_h, trial_a['id'], pi_id=pi_a['id'], crc_id=crc_a['id'])
        patient_b = await _enroll(pi_b_h, trial_b['id'], pi_id=pi_b['id'], crc_id=crc_b['id'])

        inst_a = await server.db.visit_instances.find_one(
            {'patient_id': patient_a['id']}, {'_id': 0})
        assert inst_a, 'patient_a should have materialized visit instances'
        return {
            'pi_a': (pi_a, pi_a_h), 'crc_a': (crc_a, crc_a_h),
            'pi_b': (pi_b, pi_b_h), 'crc_b': (crc_b, crc_b_h),
            'pi_a2': (pi_a2, pi_a2_h), 'pi_sp_a': (pi_sp_a, pi_sp_a_h),
            'sp_a': (sp_a, sp_a_h), 'sp_b': (sp_b, sp_b_h),
            'trial_a': trial_a, 'trial_b': trial_b,
            'trial_a_unclaimed': trial_a_unclaimed,
            'patient_a': patient_a, 'patient_b': patient_b, 'inst_a': inst_a,
        }
    return run(build())


# ── GET /patients/{id} ───────────────────────────────────────────────────────
class TestPatientDetailScoping:
    def test_own_site_pi_and_crc_get_200(self, world):
        """A legitimately-shared patient (both pi_id and crc_id set) resolves for
        both its PI and its CRC."""
        pid = world['patient_a']['id']
        async def flow():
            async with make_client() as cli:
                for _, headers in (world['pi_a'], world['crc_a']):
                    r = await cli.get(f'/api/patients/{pid}', headers=headers)
                    assert r.status_code == 200, r.text
                    assert r.json()['id'] == pid
        run(flow())

    def test_cross_site_pi_and_crc_get_403(self, world):
        pid = world['patient_a']['id']
        async def flow():
            async with make_client() as cli:
                for _, headers in (world['pi_b'], world['crc_b']):
                    r = await cli.get(f'/api/patients/{pid}', headers=headers)
                    assert r.status_code == 403, r.text
        run(flow())

    def test_same_org_unassigned_pi_get_200(self, world):
        """M2: a PI at the same site as the patient, but not its pi_id/crc_id/
        creator, still resolves 200 via the org-match branch."""
        pid = world['patient_a']['id']
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/patients/{pid}', headers=world['pi_a2'][1])
                assert r.status_code == 200, r.text
                assert r.json()['id'] == pid
        run(flow())


# ── GET /patients/{id}/visits ────────────────────────────────────────────────
class TestPatientVisitsScoping:
    def test_own_site_pi_200_cross_site_pi_403(self, world):
        pid = world['patient_a']['id']
        async def flow():
            async with make_client() as cli:
                r_own = await cli.get(f'/api/patients/{pid}/visits', headers=world['pi_a'][1])
                assert r_own.status_code == 200, r_own.text
                assert isinstance(r_own.json(), list) and r_own.json()
                r_foreign = await cli.get(f'/api/patients/{pid}/visits', headers=world['pi_b'][1])
                assert r_foreign.status_code == 403, r_foreign.text
        run(flow())


# ── PATCH /visit-instances/{id} ──────────────────────────────────────────────
class TestVisitInstancePatchScoping:
    def test_own_site_pi_200_cross_site_pi_403(self, world):
        iid = world['inst_a']['id']
        async def flow():
            async with make_client() as cli:
                r_foreign = await cli.patch(f'/api/visit-instances/{iid}',
                                            headers=world['pi_b'][1],
                                            json={'status': 'completed'})
                assert r_foreign.status_code == 403, r_foreign.text
                # unchanged in the DB after the blocked write
                still = await server.db.visit_instances.find_one({'id': iid}, {'_id': 0})
                assert still['status'] != 'completed'
                r_own = await cli.patch(f'/api/visit-instances/{iid}',
                                        headers=world['pi_a'][1],
                                        json={'status': 'completed', 'note': f'ok {RUN_ID}'})
                assert r_own.status_code == 200, r_own.text
                assert r_own.json()['status'] == 'completed'
        run(flow())

    def test_cross_site_crc_403(self, world):
        iid = world['inst_a']['id']
        async def flow():
            async with make_client() as cli:
                r = await cli.patch(f'/api/visit-instances/{iid}',
                                    headers=world['crc_b'][1], json={'status': 'missed'})
                assert r.status_code == 403, r.text
        run(flow())


# ── POST /schedules/{trial_id}/approve|flag ──────────────────────────────────
class TestScheduleReviewScoping:
    def test_own_trial_pi_can_approve(self, world):
        tid = world['trial_a']['id']
        async def flow():
            async with make_client() as cli:
                r = await cli.post(f'/api/schedules/{tid}/approve', headers=world['pi_a'][1])
                assert r.status_code == 200, r.text
                assert r.json()['schedule_status'] == 'approved'
        run(flow())

    def test_foreign_trial_pi_cannot_approve_or_flag(self, world):
        tid = world['trial_a']['id']       # claimed by pi_a (patient_a enrolled)
        async def flow():
            async with make_client() as cli:
                r_app = await cli.post(f'/api/schedules/{tid}/approve', headers=world['pi_b'][1])
                assert r_app.status_code == 403, r_app.text
                r_flag = await cli.post(f'/api/schedules/{tid}/flag', headers=world['pi_b'][1],
                                        json={'reason': f'nope {RUN_ID}'})
                assert r_flag.status_code == 403, r_flag.text
        run(flow())

    def test_foreign_org_pi_cannot_approve_or_flag_unclaimed_trial(self, world):
        """C2 fail-closed: a trial with ZERO pi-assigned patients ('unclaimed')
        must NOT be approvable/flaggable by a PI from a different org. The old
        'unclaimed -> any PI' fallback made this return 200; it must be 403."""
        tid = world['trial_a_unclaimed']['id']   # sponsor_A org, no patients
        async def flow():
            async with make_client() as cli:
                r_app = await cli.post(f'/api/schedules/{tid}/approve', headers=world['pi_b'][1])
                assert r_app.status_code == 403, r_app.text
                r_flag = await cli.post(f'/api/schedules/{tid}/flag', headers=world['pi_b'][1],
                                        json={'reason': f'nope {RUN_ID}'})
                assert r_flag.status_code == 403, r_flag.text
        run(flow())

    def test_same_org_pi_can_approve_unclaimed_trial(self, world):
        """The legitimate pre-enrollment flow still works: a PI whose org matches
        the trial's org (sponsor_name) may approve an unclaimed trial -> 200."""
        tid = world['trial_a_unclaimed']['id']
        async def flow():
            async with make_client() as cli:
                r = await cli.post(f'/api/schedules/{tid}/approve', headers=world['pi_sp_a'][1])
                assert r.status_code == 200, r.text
                assert r.json()['schedule_status'] == 'approved'
        run(flow())


# ── Sponsor org-trial scoping ────────────────────────────────────────────────
class TestSponsorScoping:
    def test_sponsor_sees_own_org_trial_patient(self, world):
        pid = world['patient_a']['id']
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/patients/{pid}', headers=world['sp_a'][1])
                assert r.status_code == 200, r.text
                assert r.json()['id'] == pid
        run(flow())

    def test_sponsor_blocked_from_foreign_org_trial_patient(self, world):
        pid_b = world['patient_b']['id']
        pid_a = world['patient_a']['id']
        async def flow():
            async with make_client() as cli:
                # sponsor_A must not reach a patient in sponsor_B's trial
                r1 = await cli.get(f'/api/patients/{pid_b}', headers=world['sp_a'][1])
                assert r1.status_code == 403, r1.text
                # and vice-versa
                r2 = await cli.get(f'/api/patients/{pid_a}', headers=world['sp_b'][1])
                assert r2.status_code == 403, r2.text
        run(flow())


class TestPatientEnrollmentScoping:
    def test_pi_cannot_enroll_into_foreign_trial(self, world):
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/patients', headers=world['pi_a'][1], json={
                    'full_name': f'Test Foreign Enrollment {RUN_ID}',
                    'email': f'test-{RUN_ID}-foreign-enrollment@example.com',
                    'trial_id': world['trial_b']['id'],
                    'pi_id': world['pi_a'][0]['id'],
                })
                assert r.status_code == 403, r.text
        run(flow())

    def test_pi_id_is_derived_from_authenticated_caller(self, world):
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/patients', headers=world['pi_a'][1], json={
                    'full_name': f'Test Forced PI {RUN_ID}',
                    'email': f'test-{RUN_ID}-forced-pi@example.com',
                    'trial_id': world['trial_a']['id'],
                    'pi_id': world['pi_b'][0]['id'],
                })
                assert r.status_code == 200, r.text
                assert r.json()['pi_id'] == world['pi_a'][0]['id']
        run(flow())

    def test_smo_org_admin_must_select_same_org_pi(self, world):
        async def flow():
            manager, manager_h = await _register('smo', org=ORG_SITE_A)
            await server.db.users.update_one(
                {'id': manager['id']}, {'$set': {'org_admin': True}})
            org = await server.db.organizations.find_one(
                {'name': ORG_SITE_A}, {'_id': 0, 'id': 1})
            await server.db.org_trial_access.insert_one({
                'id': str(uuid.uuid4()),
                'org_id': org['id'],
                'trial_id': world['trial_a']['id'],
                'granted': True,
                'created_at': server.now(),
            })
            async with make_client() as cli:
                missing = await cli.post('/api/patients', headers=manager_h, json={
                    'full_name': f'Test Managed Missing PI {RUN_ID}',
                    'email': f'test-{RUN_ID}-managed-missing-pi@example.com',
                    'trial_id': world['trial_a']['id'],
                })
                assert missing.status_code == 400, missing.text
                created = await cli.post('/api/patients', headers=manager_h, json={
                    'full_name': f'Test Managed Enrollment {RUN_ID}',
                    'email': f'test-{RUN_ID}-managed-enrollment@example.com',
                    'trial_id': world['trial_a']['id'],
                    'pi_id': world['pi_a'][0]['id'],
                })
                assert created.status_code == 200, created.text
                assert created.json()['pi_id'] == world['pi_a'][0]['id']
        run(flow())

    def test_sponsor_list_excludes_foreign_org_patients(self, world):
        """C1 fail-closed: GET /patients for a sponsor returns ONLY patients in
        that sponsor's own-org trials — never a foreign org's patient (whose
        full_name/email/phone/dob would otherwise leak)."""
        pid_a = world['patient_a']['id']   # sponsor_A's org trial
        pid_b = world['patient_b']['id']   # sponsor_B's org trial
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/patients', headers=world['sp_a'][1])
                assert r.status_code == 200, r.text
                ids = {p['id'] for p in r.json()}
                assert pid_b not in ids, 'sponsor_A leaked a foreign-org patient'
                assert pid_a in ids, 'sponsor_A should still see its own-org patient'
        run(flow())

    def test_sponsor_without_org_trials_gets_empty_list(self, world):
        """A sponsor whose org owns no trials sees an empty list, not everyone."""
        async def flow():
            _, headers = await _register('sponsor', org=f'TESTORG-{RUN_ID} Empty Pharma')
            async with make_client() as cli:
                r = await cli.get('/api/patients', headers=headers)
                assert r.status_code == 200, r.text
                assert r.json() == [], r.text
        run(flow())
