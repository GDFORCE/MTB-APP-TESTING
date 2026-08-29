"""Audit-trail role scoping — Task 3.7 (SECURITY).

GET /api/audit-logs is opened to ALL authenticated roles but the returned rows
are scoped fail-closed:

  - patient  → only their OWN rows (actor==self, or the row's subject is their
               own patient record).
  - pi / crc → only rows within their own site (mirrors _can_access_patient /
               org-trial scoping); NEVER another site's rows.
  - sponsor  → rows for their org's trials, DE-IDENTIFIED (no patient
               full_name / email / phone / dob anywhere in the payload).
  - admin    → unrestricted.

Plus ?category=&from=&to= filters (category exact-match; from/to inclusive
YYYY-MM-DD bounds reusing the calendar date-parse + range guard).

Same in-process ASGITransport harness as test_authz_scoping.py: real Atlas DB,
RUN_ID-marked data, single module-level event loop, module teardown cleanup.
"""
import asyncio
import json
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
ORG_SITE_A = f'AUDITORG-{RUN_ID} Site A Hospital'
ORG_SITE_B = f'AUDITORG-{RUN_ID} Site B Hospital'
ORG_SPONSOR_A = f'AUDITORG-{RUN_ID} Pharma A'
ORG_SPONSOR_B = f'AUDITORG-{RUN_ID} Pharma B'

# Distinct, RUN_ID-tagged patient names so the de-identification assertion can
# look for an exact string that must NEVER reach a sponsor.
NAME_A = f'Alice Aardvark {RUN_ID}'
NAME_B = f'Bob Baboon {RUN_ID}'

LOOP = asyncio.new_event_loop()
_trial_ids = []
_audit_ids = []


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None, full_name=None):
    email = f'audit-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            'full_name': full_name or f'Test {role.upper()} {RUN_ID}',
            'role': role, 'organization': org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


async def _make_trial(sponsor_headers, sponsor_name):
    async with make_client() as cli:
        r = await cli.post('/api/trials', headers=sponsor_headers, json={
            'title': f'Audit Trial {RUN_ID}',
            'protocol_id': f'AUD-{RUN_ID}-{uuid.uuid4().hex[:4]}',
            'phase': 'Phase II', 'condition': 'Testing', 'sponsor_name': sponsor_name,
        })
        assert r.status_code == 200, r.text
        trial = r.json()
        _trial_ids.append(trial['id'])
        rv = await cli.post('/api/visits', headers=sponsor_headers, json={
            'trial_id': trial['id'], 'visit_number': 1, 'name': 'Screening',
            'day_offset': 0, 'window_days': 3, 'activities': ['Vitals'],
        })
        assert rv.status_code == 200, rv.text
    return trial


async def _enroll(staff_headers, trial_id, full_name, pi_id=None, crc_id=None):
    enrolled = (server.now() - timedelta(days=5)).date().isoformat()
    async with make_client() as cli:
        r = await cli.post('/api/patients', headers=staff_headers, json={
            'full_name': full_name,
            'email': f'audit-{RUN_ID}-enrollee-{uuid.uuid4().hex[:6]}@example.com',
            'trial_id': trial_id, 'pi_id': pi_id, 'crc_id': crc_id,
            'enrolled_date': enrolled,
        })
    assert r.status_code == 200, r.text
    return r.json()


def _audit_doc(actor, action, detail, created_at, **ctx):
    actor = actor or {}
    return {
        'id': str(uuid.uuid4()),
        'user_id': actor.get('id'),
        'user_name': actor.get('full_name', ''),
        'role': actor.get('role', ''),
        'org': actor.get('organization', ''),
        'action': action,
        'category': ctx.pop('category', action.split('.', 1)[0]),
        'detail': detail,
        'ip': '', 'device': '',
        'status': 'success',
        'created_at': created_at,
        'test_run': RUN_ID,
        **ctx,
    }


# Old-row date (40 days back) used by the from/to filter tests.
OLD_DATE = (server.now() - timedelta(days=40)).date().isoformat()


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^audit-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.visits.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.patients.delete_many({'email': {'$regex': f'audit-{RUN_ID}-'}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.notifications.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.audit_logs.delete_many({'test_run': RUN_ID})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def world():
    """Two sites + sponsors, each with a trial + enrolled patient, a patient USER
    account linked to patient_a, and a set of hand-seeded audit rows spanning the
    scoping branches (own / same-site / foreign / trial-level / de-id / old)."""
    async def build():
        pi_a, pi_a_h = await _register('pi', org=ORG_SITE_A)
        crc_a, crc_a_h = await _register('crc', org=ORG_SITE_A)
        pi_b, pi_b_h = await _register('pi', org=ORG_SITE_B)
        sp_a, sp_a_h = await _register('sponsor', org=ORG_SPONSOR_A)
        sp_b, sp_b_h = await _register('sponsor', org=ORG_SPONSOR_B)
        # Patient user account (full_name == NAME_A so a patient-actor row carries
        # a name that a sponsor must never see).
        pt, pt_h = await _register('patient', org=None, full_name=NAME_A)

        trial_a = await _make_trial(sp_a_h, ORG_SPONSOR_A)
        trial_b = await _make_trial(sp_b_h, ORG_SPONSOR_B)

        patient_a = await _enroll(pi_a_h, trial_a['id'], NAME_A,
                                  pi_id=pi_a['id'], crc_id=crc_a['id'])
        patient_b = await _enroll(pi_b_h, trial_b['id'], NAME_B, pi_id=pi_b['id'])
        # Link patient_a's record to the patient user account.
        await server.db.patients.update_one(
            {'id': patient_a['id']}, {'$set': {'user_id': pt['id']}})

        n = server.now()
        rows = {
            # patient's own action (actor == self)
            'pt_own': _audit_doc(pt, 'login.success', 'Signed in from mobile',
                                 n - timedelta(hours=1),
                                 patient_id=patient_a['id'], trial_id=trial_a['id']),
            # patient-actor dose row: user_name == NAME_A, subject == own record
            'dose': _audit_doc(pt, 'dose.log', 'Logged morning dose',
                               n - timedelta(hours=2),
                               patient_id=patient_a['id'], trial_id=trial_a['id']),
            # site-A staff row about patient_a (name in the free text)
            'pi_a': _audit_doc(pi_a, 'patient.enroll', f'Enrolled {NAME_A} in trial',
                               n - timedelta(hours=3),
                               patient_id=patient_a['id'], trial_id=trial_a['id']),
            # site-A enroll row shaped EXACTLY like the real patient.enroll writer:
            # the subject is linked via target_id (NOT patient_id) with the name in
            # free text. This forces the sponsor de-id target_id FALLBACK that
            # production actually relies on — the patient_id path above would mask a
            # regression in that fallback.
            'enroll_tid': _audit_doc(pi_a, 'patient.enroll',
                               f'Enrolled {NAME_A} in trial',
                               n - timedelta(hours=3),
                               target_id=patient_a['id'], trial_id=trial_a['id']),
            # site-B staff row about patient_b (FOREIGN to site A + sponsor A)
            'pi_b': _audit_doc(pi_b, 'patient.enroll', f'Enrolled {NAME_B} in trial',
                               n - timedelta(hours=3),
                               patient_id=patient_b['id'], trial_id=trial_b['id']),
            # trial-level row for trial_a, no patient reference
            'trial_a': _audit_doc(sp_a, 'trial.create', 'Created trial protocol',
                                  n - timedelta(hours=4), trial_id=trial_a['id']),
            # an OLD site-A row (40 days back) for the date-range filter tests
            'old': _audit_doc(pi_a, 'visit.patch', 'Updated a visit long ago',
                              server.now() - timedelta(days=40),
                              patient_id=patient_a['id'], trial_id=trial_a['id']),
        }
        docs = list(rows.values())
        _audit_ids.extend(d['id'] for d in docs)
        await server.db.audit_logs.insert_many([dict(d) for d in docs])

        return {
            'pi_a': (pi_a, pi_a_h), 'crc_a': (crc_a, crc_a_h),
            'pi_b': (pi_b, pi_b_h),
            'sp_a': (sp_a, sp_a_h), 'sp_b': (sp_b, sp_b_h),
            'pt': (pt, pt_h),
            'trial_a': trial_a, 'trial_b': trial_b,
            'patient_a': patient_a, 'patient_b': patient_b,
            'rows': {k: v['id'] for k, v in rows.items()},
        }
    return run(build())


async def _get(headers, **params):
    async with make_client() as cli:
        return await cli.get('/api/audit-logs', headers=headers, params=params)


def _ids(resp):
    return {r['id'] for r in resp.json()}


# ── Patient scope ────────────────────────────────────────────────────────────
class TestPatientScope:
    def test_patient_sees_own_and_own_record_rows_only(self, world):
        rows, pt_h = world['rows'], world['pt'][1]
        async def flow():
            r = await _get(pt_h)
            assert r.status_code == 200, r.text
            ids = _ids(r)
            # own actions + rows about their own patient record
            assert rows['pt_own'] in ids
            assert rows['dose'] in ids
            assert rows['pi_a'] in ids       # subject is their own record
            # never other people's rows / other patients
            assert rows['pi_b'] not in ids
            assert rows['trial_a'] not in ids
        run(flow())


# ── pi / crc site scope ──────────────────────────────────────────────────────
class TestSiteScope:
    def test_pi_sees_own_site_not_foreign(self, world):
        rows, pi_a_h = world['rows'], world['pi_a'][1]
        async def flow():
            r = await _get(pi_a_h)
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['pi_a'] in ids
            assert rows['pi_b'] not in ids       # foreign site — must not leak
        run(flow())

    def test_crc_sees_own_site_not_foreign(self, world):
        rows, crc_a_h = world['rows'], world['crc_a'][1]
        async def flow():
            r = await _get(crc_a_h)
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['pi_a'] in ids           # patient_a is theirs (crc_id)
            assert rows['pi_b'] not in ids
        run(flow())

    def test_foreign_pi_cannot_see_other_site_patient_row(self, world):
        rows, pi_b_h = world['rows'], world['pi_b'][1]
        async def flow():
            r = await _get(pi_b_h)
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['pi_b'] in ids
            assert rows['pi_a'] not in ids
        run(flow())


# ── sponsor scope + de-identification (SECURITY-CRITICAL) ────────────────────
class TestSponsorScope:
    def test_sponsor_sees_own_trial_rows_not_foreign(self, world):
        rows, sp_a_h = world['rows'], world['sp_a'][1]
        async def flow():
            r = await _get(sp_a_h)
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['pi_a'] in ids           # patient in sponsor A's trial
            assert rows['trial_a'] in ids        # trial-level, sponsor A's org
            assert rows['pi_b'] not in ids       # foreign org trial
        run(flow())

    def test_sponsor_response_is_deidentified(self, world):
        """No patient full_name / email / phone appears ANYWHERE in a sponsor's
        audit payload — neither in free-text detail nor a patient-actor user_name."""
        sp_a_h, rows = world['sp_a'][1], world['rows']
        pt_email = world['patient_a']['email']
        async def flow():
            r = await _get(sp_a_h)
            assert r.status_code == 200, r.text
            blob = json.dumps(r.json())
            assert NAME_A not in blob, 'patient full_name leaked to sponsor'
            assert pt_email not in blob, 'patient email leaked to sponsor'
            # The real-writer-shaped row (subject via target_id, no patient_id) must
            # REACH the sponsor and be scrubbed — otherwise the NAME_A assertion above
            # never exercises the target_id de-id fallback production depends on.
            enroll_row = next(
                (row for row in r.json() if row['id'] == rows['enroll_tid']), None)
            assert enroll_row is not None, \
                'target_id-linked enroll row must reach the sponsor (else fallback untested)'
            assert NAME_A not in json.dumps(enroll_row), \
                'target_id de-id fallback failed — enrollee name leaked to sponsor'
            # de-identified rows carry a subject label instead
            deid = [row for row in r.json() if row.get('patient_id')]
            assert deid, 'expected at least one patient-referencing row'
            assert all(row.get('subject_label') for row in deid)
        run(flow())


# ── ?category= & ?from=&to= filters ──────────────────────────────────────────
class TestFilters:
    def test_category_filter_narrows(self, world):
        rows, sp_a_h = world['rows'], world['sp_a'][1]
        async def flow():
            r = await _get(sp_a_h, category='trial')
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['trial_a'] in ids
            assert rows['pi_a'] not in ids       # category 'patient', filtered out
            assert all(row['category'] == 'trial' for row in r.json())
        run(flow())

    def test_date_range_excludes_old_row(self, world):
        rows, pi_a_h = world['rows'], world['pi_a'][1]
        today = server.now().date()
        async def flow():
            r = await _get(pi_a_h,
                           **{'from': (today - timedelta(days=1)).isoformat(),
                              'to': today.isoformat()})
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['pi_a'] in ids           # recent
            assert rows['old'] not in ids        # 40 days back — out of range
        run(flow())

    def test_date_range_includes_old_row_when_bounded(self, world):
        rows, pi_a_h = world['rows'], world['pi_a'][1]
        async def flow():
            r = await _get(pi_a_h, **{'from': OLD_DATE, 'to': OLD_DATE})
            assert r.status_code == 200, r.text
            ids = _ids(r)
            assert rows['old'] in ids
            assert rows['pi_a'] not in ids       # recent — out of the old-day range
        run(flow())


# ── range guard + auth ───────────────────────────────────────────────────────
class TestGuards:
    def test_reversed_range_400(self, world):
        pi_a_h = world['pi_a'][1]
        async def flow():
            r = await _get(pi_a_h, **{'from': '2026-05-10', 'to': '2026-05-01'})
            assert r.status_code == 400, r.text
        run(flow())

    def test_malformed_date_400(self, world):
        pi_a_h = world['pi_a'][1]
        async def flow():
            r = await _get(pi_a_h, **{'from': 'not-a-date'})
            assert r.status_code == 400, r.text
        run(flow())

    def test_unauthenticated_401(self, world):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/audit-logs')
            assert r.status_code == 401, r.text
        run(flow())
