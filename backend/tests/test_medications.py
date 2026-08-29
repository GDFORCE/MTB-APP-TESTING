"""Medications, dose logs, adherence — Task 1.3.

Covers: POST/GET /medications (pi/crc create, patient sees own, staff scoped by
?patient_id= to their own patients); POST /medications/{id}/doses (patient-only,
idempotent upsert per date+time slot); GET /medications/{id}/doses?from=&to=;
GET /adherence math ({rate, taken, total, streak_days, last7}) and role scoping.

Same harness as test_foundation.py: in-process ASGITransport against the real
Atlas DB, RUN_ID-marked data, module teardown cleanup, single module-level
event loop (Motor pins its io_loop on first use — never asyncio.run here).
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
ORG_SITE = f'TESTORG-{RUN_ID} Hospital'

LOOP = asyncio.new_event_loop()

_trial_ids = []
_patient_ids = []
_medication_ids = []


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


async def _make_trial(staff_headers):
    async with make_client() as cli:
        r = await cli.post('/api/trials', headers=staff_headers, json={
            'title': f'Test Trial {RUN_ID}', 'protocol_id': f'TEST-{RUN_ID}-{uuid.uuid4().hex[:4]}',
            'phase': 'Phase II', 'condition': 'Testing',
        })
    assert r.status_code == 200, r.text
    trial = r.json()
    _trial_ids.append(trial['id'])
    return trial


async def _enroll_linked(staff_headers, trial_id, pi_id=None, crc_id=None, link_user_id=None):
    """Enroll a patient via POST /patients; optionally link a patient login."""
    async with make_client() as cli:
        r = await cli.post('/api/patients', headers=staff_headers, json={
            'full_name': f'Test PATIENT {RUN_ID}',
            'email': f'test-{RUN_ID}-enrollee-{uuid.uuid4().hex[:6]}@example.com',
            'trial_id': trial_id, 'pi_id': pi_id, 'crc_id': crc_id,
        })
    assert r.status_code == 200, r.text
    p = r.json()
    _patient_ids.append(p['id'])
    if link_user_id:
        await server.db.patients.update_one({'id': p['id']}, {'$set': {'user_id': link_user_id}})
    return p


async def _create_med(staff_headers, patient_id, schedule=None, start_days_ago=6,
                      end_date=None, active=True, name='Metformin'):
    body = {
        'patient_id': patient_id, 'name': name, 'dosage': '500 mg', 'route': 'oral',
        'schedule': schedule if schedule is not None else [
            {'time': '08:00', 'label': 'Morning'}, {'time': '20:00', 'label': 'Evening'},
        ],
        'start_date': (server.now() - timedelta(days=start_days_ago)).date().isoformat(),
        'active': active,
    }
    if end_date:
        body['end_date'] = end_date
    async with make_client() as cli:
        r = await cli.post('/api/medications', headers=staff_headers, json=body)
    assert r.status_code == 200, r.text
    med = r.json()
    _medication_ids.append(med['id'])
    return med


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.visits.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.patients.delete_many({'id': {'$in': _patient_ids}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.medications.delete_many({'$or': [
            {'id': {'$in': _medication_ids}}, {'patient_id': {'$in': _patient_ids}},
        ]})
        await db.dose_logs.delete_many({'$or': [
            {'medication_id': {'$in': _medication_ids}}, {'patient_id': {'$in': _patient_ids}},
        ]})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_SITE))


@pytest.fixture(scope='module')
def crc():
    return run(_register('crc', org=ORG_SITE))


@pytest.fixture(scope='module')
def trial(pi):
    return run(_make_trial(pi[1]))


@pytest.fixture(scope='module')
def patient(pi, crc, trial):
    """(patient_record, patient_headers) — a patient login linked to an enrolled record."""
    async def flow():
        user, headers = await _register('patient')
        p = await _enroll_linked(pi[1], trial['id'], pi_id=pi[0]['id'],
                                 crc_id=crc[0]['id'], link_user_id=user['id'])
        return p, headers
    return run(flow())


# ── Medication CRUD + scoping ────────────────────────────────────────────────
class TestMedications:
    def test_pi_creates_medication_with_full_shape_and_audit(self, pi, patient, trial):
        p, _ = patient
        async def flow():
            med = await _create_med(pi[1], p['id'])
            uuid.UUID(med['id'])                       # uuid4 string id
            assert med['patient_id'] == p['id']
            assert med['trial_id'] == trial['id']      # derived from the patient record
            assert med['name'] == 'Metformin'
            assert med['dosage'] == '500 mg'
            assert med['route'] == 'oral'
            assert med['schedule'] == [{'time': '08:00', 'label': 'Morning'},
                                       {'time': '20:00', 'label': 'Evening'}]
            assert med['active'] is True
            assert med['start_date']
            assert '_id' not in med
            row = await server.db.audit_logs.find_one(
                {'action': 'medication.create', 'target_id': med['id']})
            assert row and row['user_id'] == pi[0]['id'], 'medication create not audited'
        run(flow())

    def test_patient_cannot_create_medication(self, patient):
        p, headers = patient
        async def flow():
            async with make_client() as cli:
                r = await cli.post('/api/medications', headers=headers, json={
                    'patient_id': p['id'], 'name': 'X', 'dosage': '1 mg',
                    'schedule': [{'time': '08:00', 'label': 'Morning'}],
                })
            assert r.status_code == 403
        run(flow())

    def test_staff_cannot_create_med_for_unrelated_patient(self, patient):
        p, _ = patient
        async def flow():
            _, other_pi_headers = await _register('pi', org=ORG_SITE)
            async with make_client() as cli:
                r = await cli.post('/api/medications', headers=other_pi_headers, json={
                    'patient_id': p['id'], 'name': 'X', 'dosage': '1 mg',
                    'schedule': [{'time': '08:00', 'label': 'Morning'}],
                })
            assert r.status_code == 403
        run(flow())

    def test_patient_lists_own_meds(self, pi, patient):
        p, headers = patient
        async def flow():
            med = await _create_med(pi[1], p['id'], name='Vitamin D')
            async with make_client() as cli:
                r = await cli.get('/api/medications', headers=headers)
            assert r.status_code == 200, r.text
            meds = r.json()
            assert any(m['id'] == med['id'] for m in meds)
            assert all(m['patient_id'] == p['id'] for m in meds)
        run(flow())

    def test_staff_list_requires_patient_id_and_scoping(self, pi, crc, patient):
        p, _ = patient
        async def flow():
            async with make_client() as cli:
                r_missing = await cli.get('/api/medications', headers=pi[1])
                assert r_missing.status_code == 400
                r_pi = await cli.get('/api/medications', headers=pi[1],
                                     params={'patient_id': p['id']})
                assert r_pi.status_code == 200, r_pi.text
                assert all(m['patient_id'] == p['id'] for m in r_pi.json())
                r_crc = await cli.get('/api/medications', headers=crc[1],
                                      params={'patient_id': p['id']})
                assert r_crc.status_code == 200
                # a different pi does not manage this patient
                _, other_headers = await _register('pi', org=ORG_SITE)
                r_other = await cli.get('/api/medications', headers=other_headers,
                                        params={'patient_id': p['id']})
                assert r_other.status_code == 403
        run(flow())


# ── Dose logging ─────────────────────────────────────────────────────────────
class TestDoseLogs:
    def test_dose_upsert_is_idempotent_per_slot(self, pi, patient):
        p, headers = patient
        async def flow():
            med = await _create_med(pi[1], p['id'], name='Upsertol')
            d = server.now().date().isoformat()
            async with make_client() as cli:
                r1 = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                    json={'date': d, 'time': '08:00', 'status': 'taken'})
                assert r1.status_code == 200, r1.text
                log1 = r1.json()
                assert log1['status'] == 'taken' and log1['medication_id'] == med['id']
                assert log1['patient_id'] == p['id'] and log1['logged_at']
                uuid.UUID(log1['id'])
                # re-log the SAME slot with a different status → replaced, not duplicated
                r2 = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                    json={'date': d, 'time': '08:00', 'status': 'skipped'})
                assert r2.status_code == 200, r2.text
                log2 = r2.json()
                assert log2['status'] == 'skipped'
                assert log2['id'] == log1['id'], 'upsert must keep the same row'
            count = await server.db.dose_logs.count_documents(
                {'medication_id': med['id'], 'date': d, 'time': '08:00'})
            assert count == 1, 'same (date,time) slot must never duplicate'
            row = await server.db.audit_logs.find_one(
                {'action': 'dose.log', 'target_id': log1['id']})
            assert row, 'dose log not audited'
        run(flow())

    def test_staff_cannot_log_doses(self, pi, patient):
        p, _ = patient
        async def flow():
            med = await _create_med(pi[1], p['id'], name='Staffblock')
            d = server.now().date().isoformat()
            async with make_client() as cli:
                r = await cli.post(f"/api/medications/{med['id']}/doses", headers=pi[1],
                                   json={'date': d, 'time': '08:00', 'status': 'taken'})
            assert r.status_code == 403
        run(flow())

    def test_patient_cannot_log_dose_on_someone_elses_med(self, pi, crc, trial, patient):
        p, _ = patient
        async def flow():
            med = await _create_med(pi[1], p['id'], name='Foreign')
            # a different patient login, enrolled separately
            other_user, other_headers = await _register('patient')
            await _enroll_linked(pi[1], trial['id'], pi_id=pi[0]['id'],
                                 link_user_id=other_user['id'])
            d = server.now().date().isoformat()
            async with make_client() as cli:
                r = await cli.post(f"/api/medications/{med['id']}/doses", headers=other_headers,
                                   json={'date': d, 'time': '08:00', 'status': 'taken'})
            assert r.status_code == 403
        run(flow())

    def test_invalid_date_time_status_rejected(self, pi, patient):
        p, headers = patient
        async def flow():
            med = await _create_med(pi[1], p['id'], name='Validol')
            d = server.now().date().isoformat()
            async with make_client() as cli:
                r_date = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                        json={'date': 'not-a-date', 'time': '08:00', 'status': 'taken'})
                assert r_date.status_code == 400
                r_time = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                        json={'date': d, 'time': '8am', 'status': 'taken'})
                assert r_time.status_code == 400
                r_status = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                          json={'date': d, 'time': '08:00', 'status': 'maybe'})
                assert r_status.status_code == 422   # pydantic Literal
        run(flow())

    def test_dose_history_with_range_filter(self, pi, patient):
        p, headers = patient
        async def flow():
            med = await _create_med(pi[1], p['id'], name='Rangerol')
            days = [(server.now() - timedelta(days=k)).date().isoformat() for k in range(4)]
            async with make_client() as cli:
                for d in days:
                    r = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                       json={'date': d, 'time': '08:00', 'status': 'taken'})
                    assert r.status_code == 200, r.text
                r_all = await cli.get(f"/api/medications/{med['id']}/doses", headers=headers)
                assert r_all.status_code == 200 and len(r_all.json()) == 4
                # from/to inclusive window: only the middle two days
                r_win = await cli.get(f"/api/medications/{med['id']}/doses", headers=headers,
                                      params={'from': days[2], 'to': days[1]})
                assert {x['date'] for x in r_win.json()} == {days[1], days[2]}
                # staff who manage the patient can read the history too
                r_staff = await cli.get(f"/api/medications/{med['id']}/doses", headers=pi[1])
                assert r_staff.status_code == 200 and len(r_staff.json()) == 4
        run(flow())


# ── Adherence ────────────────────────────────────────────────────────────────
class TestAdherence:
    def test_adherence_math_13_of_14_is_93(self, pi, crc, trial):
        """2 slots/day × 7 days (start 6 days ago .. today) = 14 expected.

        All 12 slots on the 6 past days taken + 1 of 2 today = 13 taken →
        rate 93, streak 6 (today incomplete → streak ends yesterday),
        last7 = six {taken:2,total:2} days then today {taken:1,total:2}.
        """
        async def flow():
            user, headers = await _register('patient')
            p = await _enroll_linked(pi[1], trial['id'], pi_id=pi[0]['id'],
                                     crc_id=crc[0]['id'], link_user_id=user['id'])
            med = await _create_med(pi[1], p['id'], start_days_ago=6, name='Adherol')
            async with make_client() as cli:
                for k in range(1, 7):                       # 6 fully-taken past days
                    d = (server.now() - timedelta(days=k)).date().isoformat()
                    for t in ('08:00', '20:00'):
                        r = await cli.post(f"/api/medications/{med['id']}/doses",
                                           headers=headers,
                                           json={'date': d, 'time': t, 'status': 'taken'})
                        assert r.status_code == 200, r.text
                today = server.now().date().isoformat()
                r = await cli.post(f"/api/medications/{med['id']}/doses", headers=headers,
                                   json={'date': today, 'time': '08:00', 'status': 'taken'})
                assert r.status_code == 200, r.text
                ra = await cli.get('/api/adherence', headers=headers)
            assert ra.status_code == 200, ra.text
            a = ra.json()
            assert a['total'] == 14, a
            assert a['taken'] == 13, a
            assert a['rate'] == 93, a
            assert a['streak_days'] == 6, a
            assert len(a['last7']) == 7
            assert a['last7'][-1] == {'date': today, 'taken': 1, 'total': 2}
            for day in a['last7'][:-1]:
                assert day['taken'] == 2 and day['total'] == 2
        run(flow())

    def test_adherence_zero_expected_and_future_start(self, pi, crc, trial):
        async def flow():
            user, headers = await _register('patient')
            p = await _enroll_linked(pi[1], trial['id'], pi_id=pi[0]['id'],
                                     crc_id=crc[0]['id'], link_user_id=user['id'])
            async with make_client() as cli:
                # no meds at all → zeros, no crash
                r0 = await cli.get('/api/adherence', headers=headers)
                assert r0.status_code == 200, r0.text
                a0 = r0.json()
                assert a0 == {'rate': 0, 'taken': 0, 'total': 0, 'streak_days': 0,
                              'last7': a0['last7']}
                assert len(a0['last7']) == 7 and all(d['total'] == 0 for d in a0['last7'])
                # future start_date → contributes nothing yet
                await _create_med(pi[1], p['id'], start_days_ago=-3, name='Futurol')
                # med with an empty schedule → contributes nothing either
                await _create_med(pi[1], p['id'], schedule=[], name='Nosched')
                # inactive med → excluded
                await _create_med(pi[1], p['id'], active=False, name='Inactivol')
                r1 = await cli.get('/api/adherence', headers=headers)
                a1 = r1.json()
                assert a1['total'] == 0 and a1['taken'] == 0 and a1['rate'] == 0
                assert a1['streak_days'] == 0
        run(flow())

    def test_adherence_respects_end_date(self, pi, crc, trial):
        async def flow():
            user, headers = await _register('patient')
            p = await _enroll_linked(pi[1], trial['id'], pi_id=pi[0]['id'],
                                     crc_id=crc[0]['id'], link_user_id=user['id'])
            # 1 slot/day, started 9 days ago, ended 5 days ago → 5 expected days
            end = (server.now() - timedelta(days=5)).date().isoformat()
            await _create_med(pi[1], p['id'], schedule=[{'time': '08:00', 'label': 'Morning'}],
                              start_days_ago=9, end_date=end, name='Endol')
            async with make_client() as cli:
                r = await cli.get('/api/adherence', headers=headers)
            a = r.json()
            assert a['total'] == 5, a
        run(flow())

    def test_adherence_role_scoping(self, pi, crc, trial, patient):
        p, patient_headers = patient
        async def flow():
            async with make_client() as cli:
                # staff must pass patient_id
                r_missing = await cli.get('/api/adherence', headers=pi[1])
                assert r_missing.status_code == 400
                r_pi = await cli.get('/api/adherence', headers=pi[1],
                                     params={'patient_id': p['id']})
                assert r_pi.status_code == 200, r_pi.text
                # unrelated staff blocked
                _, other_headers = await _register('crc', org=ORG_SITE)
                r_other = await cli.get('/api/adherence', headers=other_headers,
                                        params={'patient_id': p['id']})
                assert r_other.status_code == 403
                # unauthenticated blocked
                r_anon = await cli.get('/api/adherence')
                assert r_anon.status_code == 401
        run(flow())
