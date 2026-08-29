"""Per-patient visit instances, tasks queue, schedule approve/flag — Task 1.2.

Covers: enrollment materializes one visit_instance per trial visit template;
PATCH /visit-instances/{id} mutates only that patient's copy (never the shared
template); GET /patients/{id} + /patients/{id}/visits; GET /visits/mine reads
instances (with template fallback); schedule approve/flag notifies sponsors and
audits; GET /tasks computes the pi/crc action queue.

Same harness as test_foundation.py: in-process ASGITransport against the real
Atlas DB, RUN_ID-marked data, module teardown cleanup, single module-level
event loop (Motor pins its io_loop on first use — never asyncio.run here).
"""
import asyncio
import sys
import uuid
from datetime import timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG_SITE = f'TESTORG-{RUN_ID} Hospital'
ORG_SPONSOR = f'TESTORG-{RUN_ID} Pharma'

LOOP = asyncio.new_event_loop()

_trial_ids = []          # every trial we create, for teardown
_conversation_ids = []   # chat fixtures for the tasks test


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


async def _make_trial(sponsor_headers, templates=((0, 'Screening'), (7, 'Baseline'), (14, 'Week 2'))):
    """Create a trial + visit templates via the public API; returns (trial, template_list)."""
    async with make_client() as cli:
        r = await cli.post('/api/trials', headers=sponsor_headers, json={
            'title': f'Test Trial {RUN_ID}', 'protocol_id': f'TEST-{RUN_ID}-{uuid.uuid4().hex[:4]}',
            'phase': 'Phase II', 'condition': 'Testing', 'sponsor_name': ORG_SPONSOR,
        })
        assert r.status_code == 200, r.text
        trial = r.json()
        _trial_ids.append(trial['id'])
        tpls = []
        for i, (off, name) in enumerate(templates, start=1):
            rv = await cli.post('/api/visits', headers=sponsor_headers, json={
                'trial_id': trial['id'], 'visit_number': i, 'name': name,
                'day_offset': off, 'window_days': 3, 'activities': ['Vitals'],
            })
            assert rv.status_code == 200, rv.text
            tpls.append(rv.json())
    return trial, tpls


async def _grant_trial_access(staff_headers, trial_id):
    """Give the calling staff member a REAL accepted trial invitation — the
    same relationship the fail-closed enrollment/access rule requires in
    production (`_has_accepted_trial_invitation` matches on email/phone)."""
    async with make_client() as cli:
        me = await cli.get('/api/auth/me', headers=staff_headers)
    if me.status_code != 200:
        return
    u = me.json()
    email = (u.get('email') or '').strip().lower()
    existing = await server.db.invitations.find_one({
        'trial_id': trial_id, 'status': 'accepted', 'email': email})
    if existing:
        return
    await server.db.invitations.insert_one({
        'id': str(uuid.uuid4()), 'token': uuid.uuid4().hex,
        'email': email, 'phone': u.get('phone', ''),
        'full_name': u.get('full_name', ''),
        'org': u.get('organization', ''), 'site': '',
        'trial_id': trial_id, 'role': u.get('role'),
        'status': 'accepted', 'created_at': server.now(),
        'accepted_at': server.now(),
    })


async def _enroll(staff_headers, trial_id, pi_id=None, crc_id=None, days_ago=5):
    """Enroll a fresh patient via POST /api/patients; returns the patient doc.
    Grants the caller the accepted-invitation trial relationship first (the
    backend enrollment rule is fail-closed)."""
    await _grant_trial_access(staff_headers, trial_id)
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


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.invitations.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.visits.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.patients.delete_many({'email': {'$regex': f'test-{RUN_ID}-'}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids + ['nonexistent-' + RUN_ID]}})
        await db.notifications.delete_many({'$or': [
            {'trial_id': {'$in': _trial_ids}}, {'title': {'$regex': RUN_ID}},
        ]})
        await db.conversations.delete_many({'id': {'$in': _conversation_ids}})
        await db.messages.delete_many({'conversation_id': {'$in': _conversation_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def sponsor():
    return run(_register('sponsor', org=ORG_SPONSOR))


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_SITE))


@pytest.fixture(scope='module')
def crc():
    return run(_register('crc', org=ORG_SITE))


@pytest.fixture(scope='module')
def trial(sponsor):
    return run(_make_trial(sponsor[1]))


# ── Enrollment materializes instances ────────────────────────────────────────
class TestEnrollmentMaterialization:
    def test_enroll_creates_one_instance_per_template(self, sponsor, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'], pi_id=pi[0]['id'], days_ago=5)
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}/visits", headers=pi_headers)
            assert r.status_code == 200, r.text
            insts = r.json()
            assert len(insts) == len(tpls)
            by_seq = sorted(insts, key=lambda i: i['seq'])
            for inst, tpl in zip(by_seq, tpls):
                uuid.UUID(inst['id'])                       # uuid4 string id
                assert inst['id'] != tpl['id']              # instance, not the template
                assert inst['patient_id'] == patient['id']
                assert inst['trial_id'] == trial_doc['id']
                assert inst['visit_template_id'] == tpl['id']
                assert inst['name'] == tpl['name']
                assert inst['seq'] == tpl['visit_number']
                for key in ('scheduled_date', 'window_start', 'window_end',
                            'status', 'note', 'updated_at'):
                    assert key in inst, f'missing field {key}'
                assert inst['window_start'] < inst['scheduled_date'] < inst['window_end']
            # enrolled 5 days ago, window_days=3: day 0's window (day -3..3)
            # already closed -> overdue; day 7's window (day 4..10) is open ->
            # due; day 14's window (day 11..17) hasn't opened yet -> planned.
            assert [i['status'] for i in by_seq] == ['overdue', 'due', 'planned']
            # the shared templates were NOT touched
            for tpl in tpls:
                raw = await server.db.visits.find_one({'id': tpl['id']}, {'_id': 0})
                assert 'status' not in raw and 'patient_id' not in raw
            # enrollment mutation is audited
            row = await server.db.audit_logs.find_one(
                {'action': 'patient.enroll', 'target_id': patient['id']})
            assert row, 'enrollment not audited'
        run(flow())

    def test_patient_detail_returns_patient_trial_and_instances(self, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'])
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}", headers=pi_headers)
            assert r.status_code == 200, r.text
            j = r.json()
            assert j['id'] == patient['id']
            assert j['trial']['id'] == trial_doc['id']
            assert j['trial']['protocol_id'] == trial_doc['protocol_id']
            assert len(j['instances']) == len(tpls)
        run(flow())

    def test_unknown_patient_404(self, pi):
        _, pi_headers = pi
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/patients/{uuid.uuid4()}', headers=pi_headers)
                r2 = await cli.get(f'/api/patients/{uuid.uuid4()}/visits', headers=pi_headers)
            assert r.status_code == 404 and r2.status_code == 404
        run(flow())

    def test_trial_without_templates_yields_zero_instances(self, sponsor, pi):
        _, pi_headers = pi
        async def flow():
            bare_trial, _ = await _make_trial(sponsor[1], templates=())
            patient = await _enroll(pi_headers, bare_trial['id'])
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}/visits", headers=pi_headers)
            assert r.status_code == 200 and r.json() == []
        run(flow())

    def test_unknown_trial_enrollment_is_rejected(self, pi):
        """Fail-closed: enrolling into a trial that does not exist is refused
        outright (it used to silently create a patient with zero instances)."""
        _, pi_headers = pi
        async def flow():
            trial_id = 'nonexistent-' + RUN_ID
            await _grant_trial_access(pi_headers, trial_id)
            async with make_client() as cli:
                r = await cli.post('/api/patients', headers=pi_headers, json={
                    'full_name': f'Test PATIENT {RUN_ID}',
                    'email': f'test-{RUN_ID}-ghost@example.com',
                    'trial_id': trial_id,
                    'enrolled_date': server.now().date().isoformat(),
                })
            assert r.status_code == 404, r.text
            ghost = await server.db.patients.find_one(
                {'email': f'test-{RUN_ID}-ghost@example.com'})
            assert ghost is None, 'no patient record may be created for an unknown trial'
        run(flow())

    def test_materialization_is_idempotent_per_patient(self, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'])
            doc = await server.db.patients.find_one({'id': patient['id']}, {'_id': 0})
            created_again = await server.materialize_visit_instances(doc)
            assert created_again == 0
            count = await server.db.visit_instances.count_documents({'patient_id': patient['id']})
            assert count == len(tpls)
        run(flow())


# ── Arm-scoped materialization (arm-leak regression) ─────────────────────────
class TestArmScopedMaterialization:
    """materialize_visit_instances / _materialize_new_template_for_enrolled
    filter templates by substudy_label but never by arm — a patient enrolled
    under one arm currently receives every arm's visit instances. Both tests
    FAIL today; they must pass unmodified once the arm filter lands."""

    async def _two_arm_trial(self, sp_headers):
        trial_doc, _ = await _make_trial(sp_headers, templates=())
        specs = [(1, 'Screening A', 0, 'Arm A'), (2, 'Dosing A', 7, 'Arm A'),
                 (3, 'Screening B', 0, 'Arm B'), (4, 'Dosing B', 7, 'Arm B')]
        async with make_client() as cli:
            for num, name, off, arm in specs:
                r = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': trial_doc['id'], 'visit_number': num, 'name': name,
                    'day_offset': off, 'window_days': 3, 'arm_label': arm,
                })
                assert r.status_code == 200, r.text
        return trial_doc

    def test_enrollment_only_materializes_the_patients_own_arm(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            trial_doc = await self._two_arm_trial(sp_headers)
            patient_doc = {
                'id': str(uuid.uuid4()), 'trial_id': trial_doc['id'],
                'full_name': f'Arm Test {RUN_ID}',
                'email': f'test-{RUN_ID}-arm-a@example.com',
                'arm_label': 'Arm A',
                'enrolled_date': server.now().date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            }
            await server.db.patients.insert_one(patient_doc)
            created = await server.materialize_visit_instances(patient_doc)
            insts = await server.db.visit_instances.find(
                {'patient_id': patient_doc['id']}, {'_id': 0}).to_list(10)
            names = {i['name'] for i in insts}
            assert created == 2 and names == {'Screening A', 'Dosing A'}, (
                f'arm leak: expected only Arm A visits, got {names}')
        run(flow())

    def test_late_added_template_only_reaches_the_matching_arm(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            trial_doc = await self._two_arm_trial(sp_headers)
            patient_a = {
                'id': str(uuid.uuid4()), 'trial_id': trial_doc['id'],
                'full_name': f'Arm A Patient {RUN_ID}',
                'email': f'test-{RUN_ID}-arm-a-late@example.com',
                'arm_label': 'Arm A',
                'enrolled_date': server.now().date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            }
            await server.db.patients.insert_one(patient_a)
            await server.materialize_visit_instances(patient_a)
            async with make_client() as cli:
                r = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': trial_doc['id'], 'visit_number': 5,
                    'name': 'Late Visit B', 'day_offset': 14, 'window_days': 3,
                    'arm_label': 'Arm B',
                })
            assert r.status_code == 200, r.text
            leaked = await server.db.visit_instances.count_documents(
                {'patient_id': patient_a['id'], 'name': 'Late Visit B'})
            assert leaked == 0, 'arm leak: Arm-B-only template reached an Arm-A patient'
        run(flow())

    def test_blank_arm_template_still_matches_every_patient(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            trial_doc = await self._two_arm_trial(sp_headers)
            async with make_client() as cli:
                r = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': trial_doc['id'], 'visit_number': 5,
                    'name': 'Shared Baseline', 'day_offset': -1, 'window_days': 3,
                })
                assert r.status_code == 200, r.text
            patient_a = {
                'id': str(uuid.uuid4()), 'trial_id': trial_doc['id'],
                'full_name': f'Shared Test A {RUN_ID}',
                'email': f'test-{RUN_ID}-shared-a@example.com',
                'arm_label': 'Arm A',
                'enrolled_date': server.now().date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            }
            patient_b = {
                'id': str(uuid.uuid4()), 'trial_id': trial_doc['id'],
                'full_name': f'Shared Test B {RUN_ID}',
                'email': f'test-{RUN_ID}-shared-b@example.com',
                'arm_label': 'Arm B',
                'enrolled_date': server.now().date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            }
            await server.db.patients.insert_many([patient_a, patient_b])
            await server.materialize_visit_instances(patient_a)
            await server.materialize_visit_instances(patient_b)
            for patient in (patient_a, patient_b):
                shared = await server.db.visit_instances.count_documents(
                    {'patient_id': patient['id'], 'name': 'Shared Baseline'})
                assert shared == 1, (
                    f"untagged template must reach every arm, missing for {patient['arm_label']}")
        run(flow())

    def test_omitted_arm_on_fully_arm_tagged_trial_yields_no_visits(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            trial_doc = await self._two_arm_trial(sp_headers)
            patient_doc = {
                'id': str(uuid.uuid4()), 'trial_id': trial_doc['id'],
                'full_name': f'No Arm Test {RUN_ID}',
                'email': f'test-{RUN_ID}-no-arm@example.com',
                'enrolled_date': server.now().date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            }
            await server.db.patients.insert_one(patient_doc)
            created = await server.materialize_visit_instances(patient_doc)
            assert created == 0, (
                'omitting arm_label on a fully arm-tagged trial must fail closed, not leak every arm')
        run(flow())

    def test_list_trial_arms_endpoint(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            bare_trial, _ = await _make_trial(sp_headers, templates=())
            async with make_client() as cli:
                r = await cli.get(f"/api/trials/{bare_trial['id']}/arms", headers=sp_headers)
            assert r.status_code == 200, r.text
            assert r.json() == []

            trial_doc = await self._two_arm_trial(sp_headers)
            async with make_client() as cli:
                r = await cli.get(f"/api/trials/{trial_doc['id']}/arms", headers=sp_headers)
            assert r.status_code == 200, r.text
            assert r.json() == ['Arm A', 'Arm B']
        run(flow())


# ── PATCH /visit-instances/{id} ──────────────────────────────────────────────
class TestVisitInstancePatch:
    def test_patch_touches_only_that_patients_copy(self, pi, crc, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        crc_user, crc_headers = crc
        async def flow():
            pa = await _enroll(pi_headers, trial_doc['id'])
            pb = await _enroll(pi_headers, trial_doc['id'])
            inst_a = await server.db.visit_instances.find(
                {'patient_id': pa['id']}, {'_id': 0}).sort('seq', 1).to_list(10)
            async with make_client() as cli:
                r = await cli.patch(f"/api/visit-instances/{inst_a[0]['id']}", headers=crc_headers,
                                    json={'status': 'completed', 'note': f'done {RUN_ID}'})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j['status'] == 'completed' and j['note'] == f'done {RUN_ID}'
            assert j['updated_by'] == crc_user['id']
            # patient B is untouched
            b_completed = await server.db.visit_instances.count_documents(
                {'patient_id': pb['id'], 'status': 'completed'})
            assert b_completed == 0
            # shared template untouched
            raw = await server.db.visits.find_one({'id': tpls[0]['id']}, {'_id': 0})
            assert 'status' not in raw and 'note' not in raw
            # audited
            row = await server.db.audit_logs.find_one(
                {'action': 'visit_instance.patch', 'target_id': inst_a[0]['id']})
            assert row and row['user_id'] == crc_user['id']
        run(flow())

    def test_reschedule_moves_window_with_the_date(self, pi, trial):
        trial_doc, _ = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'])
            inst = await server.db.visit_instances.find_one({'patient_id': patient['id']}, {'_id': 0})
            new_date = (server.now() + timedelta(days=30)).isoformat()
            async with make_client() as cli:
                r = await cli.patch(f"/api/visit-instances/{inst['id']}", headers=pi_headers,
                                    json={'scheduled_date': new_date})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j['scheduled_date'][:10] == new_date[:10]
            assert j['window_start'] < j['scheduled_date'] < j['window_end']
            assert j['window_start'][:10] != inst['window_start'].strftime('%Y-%m-%d')
        run(flow())

    def test_patch_guards(self, pi, trial):
        trial_doc, _ = trial
        _, pi_headers = pi
        async def flow():
            _, patient_headers = await _register('patient')
            patient = await _enroll(pi_headers, trial_doc['id'])
            inst = await server.db.visit_instances.find_one({'patient_id': patient['id']}, {'_id': 0})
            async with make_client() as cli:
                r = await cli.patch(f"/api/visit-instances/{inst['id']}",
                                    headers=patient_headers, json={'status': 'completed'})
                assert r.status_code == 403
                r2 = await cli.patch(f'/api/visit-instances/{uuid.uuid4()}',
                                     headers=pi_headers, json={'status': 'completed'})
                assert r2.status_code == 404
                r3 = await cli.patch(f"/api/visit-instances/{inst['id']}",
                                     headers=pi_headers, json={})
                assert r3.status_code == 400
        run(flow())


# ── Per-instance task snapshots and attributed comments ──────────────────────
class TestVisitInstanceWorkflow:
    def test_task_snapshot_completion_reopen_and_patient_isolation(self, pi, crc):
        async def flow():
            trial_doc, templates = await _make_trial(
                pi[1], templates=((7, f'Workflow {RUN_ID}'),))
            template = templates[0]
            await server.db.visits.update_one(
                {'id': template['id']},
                {'$set': {
                    'clinical_tasks': ['Record vitals', 'Review adverse events'],
                    'admin_tasks': ['Confirm source documents'],
                }},
            )
            patient_a = await _enroll(pi[1], trial_doc['id'], pi_id=pi[0]['id'], crc_id=crc[0]['id'])
            patient_b = await _enroll(pi[1], trial_doc['id'], pi_id=pi[0]['id'], crc_id=crc[0]['id'])
            instance_a = await server.db.visit_instances.find_one(
                {'patient_id': patient_a['id']}, {'_id': 0})
            instance_b = await server.db.visit_instances.find_one(
                {'patient_id': patient_b['id']}, {'_id': 0})
            task = instance_a['clinical_tasks'][0]
            assert task['id'] == instance_b['clinical_tasks'][0]['id']
            assert task['completed'] is False

            async with make_client() as cli:
                completed = await cli.patch(
                    f"/api/visit-instances/{instance_a['id']}/tasks/{task['id']}",
                    headers=crc[1], json={'completed': True})
                reopened = await cli.patch(
                    f"/api/visit-instances/{instance_a['id']}/tasks/{task['id']}",
                    headers=crc[1], json={'completed': False})
            assert completed.status_code == 200, completed.text
            done_task = completed.json()['clinical_tasks'][0]
            assert done_task['completed'] is True
            assert done_task['completed_by'] == crc[0]['id']
            assert done_task['completed_by_name'] == crc[0]['full_name']
            assert done_task['completed_at']
            assert reopened.status_code == 200, reopened.text
            reopened_task = reopened.json()['clinical_tasks'][0]
            assert reopened_task['completed'] is False
            assert reopened_task['completed_by'] is None
            untouched = await server.db.visit_instances.find_one(
                {'id': instance_b['id']}, {'_id': 0})
            assert untouched['clinical_tasks'][0]['completed'] is False
            actions = await server.db.audit_logs.distinct(
                'action', {'target_id': instance_a['id'], 'task_id': task['id']})
            assert 'visit_instance.task_complete' in actions
            assert 'visit_instance.task_reopen' in actions
        run(flow())

    def test_comment_persists_with_attribution_and_audit(self, pi):
        async def flow():
            trial_doc, _ = await _make_trial(pi[1], templates=((7, 'Comment visit'),))
            patient = await _enroll(pi[1], trial_doc['id'], pi_id=pi[0]['id'])
            instance = await server.db.visit_instances.find_one(
                {'patient_id': patient['id']}, {'_id': 0})
            async with make_client() as cli:
                response = await cli.post(
                    f"/api/visit-instances/{instance['id']}/comments",
                    headers=pi[1], json={'text': '  Patient tolerated procedures well.  '})
            assert response.status_code == 200, response.text
            comments = response.json()['comments']
            assert len(comments) == 1
            assert comments[0]['text'] == 'Patient tolerated procedures well.'
            assert comments[0]['created_by'] == pi[0]['id']
            assert comments[0]['created_by_name'] == pi[0]['full_name']
            stored = await server.db.visit_instances.find_one(
                {'id': instance['id']}, {'_id': 0})
            assert stored['comments'][0]['id'] == comments[0]['id']
            audit = await server.db.audit_logs.find_one({
                'action': 'visit_instance.comment_add',
                'target_id': instance['id'],
                'comment_id': comments[0]['id'],
            })
            assert audit and audit['user_id'] == pi[0]['id']
        run(flow())

    def test_task_and_comment_endpoints_are_patient_scoped(self, pi):
        async def flow():
            trial_doc, templates = await _make_trial(pi[1], templates=((7, 'Scoped workflow'),))
            await server.db.visits.update_one(
                {'id': templates[0]['id']},
                {'$set': {'clinical_tasks': ['Local-only task']}},
            )
            patient = await _enroll(pi[1], trial_doc['id'], pi_id=pi[0]['id'])
            instance = await server.db.visit_instances.find_one(
                {'patient_id': patient['id']}, {'_id': 0})
            foreign_pi, foreign_headers = await _register('pi', org=f'FOREIGN-{RUN_ID}')
            task_id = instance['clinical_tasks'][0]['id']
            async with make_client() as cli:
                task_response = await cli.patch(
                    f"/api/visit-instances/{instance['id']}/tasks/{task_id}",
                    headers=foreign_headers, json={'completed': True})
                comment_response = await cli.post(
                    f"/api/visit-instances/{instance['id']}/comments",
                    headers=foreign_headers, json={'text': 'Foreign write'})
            assert foreign_pi['id']
            assert task_response.status_code == 403
            assert comment_response.status_code == 403
            stored = await server.db.visit_instances.find_one(
                {'id': instance['id']}, {'_id': 0})
            assert stored['clinical_tasks'][0]['completed'] is False
            assert stored['comments'] == []
        run(flow())

    def test_pending_past_window_is_returned_as_overdue(self, pi):
        async def flow():
            trial_doc, _ = await _make_trial(pi[1], templates=((7, 'Overdue policy'),))
            patient = await _enroll(pi[1], trial_doc['id'], pi_id=pi[0]['id'])
            instance = await server.db.visit_instances.find_one(
                {'patient_id': patient['id']}, {'_id': 0})
            past = (server.now() - timedelta(days=10)).date().isoformat()
            async with make_client() as cli:
                response = await cli.patch(
                    f"/api/visit-instances/{instance['id']}",
                    headers=pi[1],
                    json={'status': 'scheduled', 'scheduled_date': past},
                )
                screening = await cli.patch(
                    f"/api/visit-instances/{instance['id']}",
                    headers=pi[1],
                    json={'status': 'screen_fail'},
                )
            assert response.status_code == 200, response.text
            assert response.json()['status'] == 'overdue'
            assert screening.status_code == 200, screening.text
            assert screening.json()['status'] == 'screen_fail'
        run(flow())


# ── GET /visits/mine reads instances (with template fallback) ────────────────
class TestVisitsMine:
    def test_reads_instances_for_logged_in_patient(self, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            pt_user, pt_headers = await _register('patient')
            patient = await _enroll(pi_headers, trial_doc['id'], days_ago=5)
            await server.db.patients.update_one({'id': patient['id']},
                                                {'$set': {'user_id': pt_user['id']}})
            async with make_client() as cli:
                r = await cli.get('/api/visits/mine', headers=pt_headers)
            assert r.status_code == 200, r.text
            visits = r.json()
            assert len(visits) == len(tpls)
            inst_ids = {i['id'] for i in await server.db.visit_instances.find(
                {'patient_id': patient['id']}, {'_id': 0, 'id': 1}).to_list(10)}
            for v in visits:
                assert v['id'] in inst_ids                  # served from instances
                # every field the RN app consumes today is still present
                for key in ('name', 'scheduled_date', 'status', 'window_days',
                            'activities', 'visit_number', 'patient_id'):
                    assert key in v, f'missing field {key}'
            # same day 0/7/14, days_ago=5, window_days=3 derivation as
            # TestEnrollmentMaterialization.test_enroll_creates_one_instance_per_template
            assert [v['status'] for v in visits] == ['overdue', 'due', 'planned']
        run(flow())

    def test_enriches_with_site_pi_and_checklist(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            # fresh trial with a template that carries a checklist
            t, _ = await _make_trial(sp_headers, templates=())
            prep = ['Fast 8 hours', 'Bring your ID card']
            async with make_client() as cli:
                rv = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': t['id'], 'visit_number': 1,
                    'name': f'Checklist Visit {RUN_ID}', 'day_offset': 3,
                    'window_days': 3, 'activities': ['Vitals'], 'checklist': prep,
                })
            assert rv.status_code == 200, rv.text
            assert rv.json().get('checklist') == prep     # template stores checklist
            pt_user, pt_headers = await _register('patient')
            patient = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'], days_ago=5)
            await server.db.patients.update_one({'id': patient['id']},
                                                {'$set': {'user_id': pt_user['id']}})
            async with make_client() as cli:
                r = await cli.get('/api/visits/mine', headers=pt_headers)
            assert r.status_code == 200, r.text
            visits = r.json()
            assert visits
            for v in visits:
                for key in ('site', 'pi_name', 'pi_phone', 'pi_email', 'checklist'):
                    assert key in v, f'missing enrichment field {key}'
                assert v['pi_name'] == pi_user['full_name']   # joined from PI user
                assert v['site'] == pi_user['organization']
                assert v['pi_email'] == pi_user['email']
                assert v['checklist'] == prep                 # joined from template
        run(flow())

    def test_falls_back_to_templates_when_no_instances(self, trial):
        trial_doc, tpls = trial
        async def flow():
            pt_user, pt_headers = await _register('patient')
            # legacy patient record: exists in db but was never materialized
            await server.db.patients.insert_one({
                'id': str(uuid.uuid4()), 'user_id': pt_user['id'],
                'full_name': f'Legacy {RUN_ID}',
                'email': f'test-{RUN_ID}-legacy@example.com',
                'trial_id': trial_doc['id'],
                'enrolled_date': (server.now() - timedelta(days=5)).date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            })
            async with make_client() as cli:
                r = await cli.get('/api/visits/mine', headers=pt_headers)
            assert r.status_code == 200, r.text
            visits = r.json()
            assert len(visits) == len(tpls)
            for v in visits:
                assert v['scheduled_date']
            # same day 0/7/14, days_ago=5, window_days=3 derivation as the
            # materialized-instance tests above — the fallback path must
            # compute the identical live overdue/due/planned status.
            assert [v['status'] for v in visits] == ['overdue', 'due', 'planned']
        run(flow())

    def test_exact_detail_is_scoped_and_returns_approved_fields(self, pi, crc):
        pi_user, pi_headers = pi
        crc_user, _ = crc

        async def flow():
            trial_doc, templates = await _make_trial(
                pi_headers, templates=((2, 'Detailed visit'),))
            template = templates[0]
            await server.db.visits.update_one(
                {'id': template['id']},
                {'$set': {
                    'visit_type': 'On-site',
                    'location': ORG_SITE,
                    'activities': [
                        {'id': 'vitals', 'label': 'Vital signs',
                         'description': 'Blood pressure and pulse'},
                    ],
                    'checklist': ['Bring your patient ID card'],
                }},
            )
            patient_user, patient_headers = await _register('patient')
            patient = await _enroll(
                pi_headers, trial_doc['id'], pi_id=pi_user['id'],
                crc_id=crc_user['id'])
            await server.db.patients.update_one(
                {'id': patient['id']}, {'$set': {'user_id': patient_user['id']}})
            instance = await server.db.visit_instances.find_one(
                {'patient_id': patient['id']}, {'_id': 0})
            completed_at = server.now()
            await server.db.visit_instances.update_one(
                {'id': instance['id']},
                {'$set': {
                    'status': 'completed',
                    'completed_at': completed_at,
                    'completed_by': pi_user['id'],
                    'completed_by_name': pi_user['full_name'],
                }},
            )

            foreign_user, foreign_headers = await _register('patient')
            foreign_patient = await _enroll(pi_headers, trial_doc['id'])
            await server.db.patients.update_one(
                {'id': foreign_patient['id']},
                {'$set': {'user_id': foreign_user['id']}})

            async with make_client() as cli:
                own = await cli.get(
                    f"/api/visits/mine/{instance['id']}", headers=patient_headers)
                foreign = await cli.get(
                    f"/api/visits/mine/{instance['id']}", headers=foreign_headers)
                missing = await cli.get(
                    f"/api/visits/mine/missing-{RUN_ID}", headers=patient_headers)
                staff = await cli.get(
                    f"/api/visits/mine/{instance['id']}", headers=pi_headers)

            assert own.status_code == 200, own.text
            detail = own.json()
            for key in (
                'window_start', 'window_end', 'window_days', 'visit_type',
                'location', 'completion_timestamp', 'clinician_id',
                'clinician_name', 'procedures', 'preparation', 'pi_id',
                'crc_id', 'assigned_contact_id', 'protocol_id', 'phase',
                'indication',
            ):
                assert key in detail, f'missing detail field {key}'
            assert detail['visit_type'] == 'On-site'
            assert detail['location'] == ORG_SITE
            assert detail['completion_timestamp']
            assert detail['clinician_id'] == pi_user['id']
            assert detail['assigned_contact_id'] == crc_user['id']
            assert detail['procedures'] == [{
                'id': 'vitals', 'label': 'Vital signs',
                'description': 'Blood pressure and pulse',
            }]
            assert detail['preparation'] == ['Bring your patient ID card']
            assert foreign.status_code == 403
            assert missing.status_code == 404
            assert staff.status_code == 403

        run(flow())


# ── Schedule approve / flag ──────────────────────────────────────────────────
class TestScheduleReview:
    def test_approve_sets_status_notifies_sponsor_and_audits(self, sponsor, pi):
        sp_user, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            # Fail-closed schedule authz (Task 3.75 C2): the PI must belong to the
            # trial to review it. Establish that tie by enrolling a PI-assigned
            # patient before approving (the removed 'unclaimed -> any PI' path is
            # a security bug, not a valid flow).
            await _enroll(pi_headers, t['id'], pi_id=pi_user['id'])
            async with make_client() as cli:
                r = await cli.post(f"/api/schedules/{t['id']}/approve", headers=pi_headers)
            assert r.status_code == 200, r.text
            fresh = await server.db.trials.find_one({'id': t['id']}, {'_id': 0})
            assert fresh['schedule_status'] == 'approved'
            notif = await server.db.notifications.find_one(
                {'user_id': sp_user['id'], 'trial_id': t['id']}, {'_id': 0})
            assert notif and notif.get('read') is False
            row = await server.db.audit_logs.find_one(
                {'action': 'schedule.approve', 'target_id': t['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_flag_records_reason_and_notifies(self, sponsor, pi):
        sp_user, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            # Fail-closed schedule authz (Task 3.75 C2): give the PI a legitimate
            # tie to the trial before flagging (see approve test above).
            await _enroll(pi_headers, t['id'], pi_id=pi_user['id'])
            reason = f'Window too tight {RUN_ID}'
            async with make_client() as cli:
                r_missing = await cli.post(f"/api/schedules/{t['id']}/flag",
                                           headers=pi_headers, json={})
                assert r_missing.status_code == 422
                r = await cli.post(f"/api/schedules/{t['id']}/flag",
                                   headers=pi_headers, json={'reason': reason})
            assert r.status_code == 200, r.text
            fresh = await server.db.trials.find_one({'id': t['id']}, {'_id': 0})
            assert fresh['schedule_status'] == 'flagged'
            notif = await server.db.notifications.find_one(
                {'user_id': sp_user['id'], 'trial_id': t['id']}, {'_id': 0})
            assert notif and reason in notif['body']
            row = await server.db.audit_logs.find_one(
                {'action': 'schedule.flag', 'target_id': t['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_pi_only_and_unknown_trial(self, sponsor, pi, crc):
        _, sp_headers = sponsor
        _, pi_headers = pi
        _, crc_headers = crc
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            async with make_client() as cli:
                for headers in (crc_headers, sp_headers):
                    r = await cli.post(f"/api/schedules/{t['id']}/approve", headers=headers)
                    assert r.status_code == 403
                r404 = await cli.post(f'/api/schedules/{uuid.uuid4()}/approve', headers=pi_headers)
                assert r404.status_code == 404
        run(flow())


# ── Tasks queue ──────────────────────────────────────────────────────────────
class TestTasks:
    def test_queue_has_overdue_today_schedule_and_messages(self, sponsor, pi, crc):
        sp_user, sp_headers = sponsor
        pi_user, pi_headers = pi
        crc_user, crc_headers = crc
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=((0, 'Only Visit'),))
            # day_offset 0, enrolled 10 days ago -> overdue; enrolled today -> due today
            overdue_pt = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'],
                                       crc_id=crc_user['id'], days_ago=10)
            today_pt = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'],
                                     crc_id=crc_user['id'], days_ago=0)
            # materialization always stores a static 'planned'; flip both to
            # 'scheduled' (still one of the recompute-triggering statuses) so
            # the queue derives their real overdue/due state from the dates
            await server.db.visit_instances.update_many(
                {'patient_id': {'$in': [overdue_pt['id'], today_pt['id']]}},
                {'$set': {'status': 'scheduled'}})
            # one unread chat message for the pi
            cid = str(uuid.uuid4())
            _conversation_ids.append(cid)
            await server.db.conversations.insert_one({
                'id': cid, 'participant_ids': sorted([pi_user['id'], crc_user['id']]),
                'title': '', 'is_group': False, 'last_message': 'hi',
                'created_at': server.now(), 'updated_at': server.now(),
            })
            await server.db.messages.insert_one({
                'id': str(uuid.uuid4()), 'conversation_id': cid,
                'sender_id': crc_user['id'], 'content': f'hello {RUN_ID}',
                'created_at': server.now(), 'read_by': {crc_user['id']: server.now()},
            })
            async with make_client() as cli:
                r = await cli.get('/api/tasks', headers=pi_headers)
            assert r.status_code == 200, r.text
            tasks = r.json()
            for task in tasks:
                for key in ('id', 'type', 'title', 'subtitle', 'due', 'priority'):
                    assert key in task, f'missing field {key}'
            by_type = {}
            for task in tasks:
                by_type.setdefault(task['type'], []).append(task)
            overdue = [x for x in by_type.get('overdue_visit', [])
                       if x.get('patient_id') == overdue_pt['id']]
            assert overdue, 'overdue visit instance not surfaced as a task'
            assert overdue[0]['priority'] == 'high'
            assert overdue[0]['trial_id'] == t['id']
            today = [x for x in by_type.get('visit_today', [])
                     if x.get('patient_id') == today_pt['id']]
            assert today, "today's visit not surfaced as a task"
            reviews = [x for x in by_type.get('schedule_review', [])
                       if x.get('trial_id') == t['id']]
            assert reviews, 'pending schedule review not surfaced'
            assert by_type.get('unread_messages'), 'unread messages count not surfaced'
            # crc sees the queue for their assigned patients too
            async with make_client() as cli:
                rc = await cli.get('/api/tasks', headers=crc_headers)
            assert rc.status_code == 200
            assert any(x['type'] == 'overdue_visit' and x.get('patient_id') == overdue_pt['id']
                       for x in rc.json())
        run(flow())

    def test_crc_queue_surfaces_admin_tasks_but_not_clinical_tasks(self, sponsor, pi, crc):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        crc_user, crc_headers = crc

        async def flow():
            trial, _ = await _make_trial(
                sp_headers, templates=((0, 'Administrative workflow'),))
            patient = await _enroll(
                pi_headers,
                trial['id'],
                pi_id=pi_user['id'],
                crc_id=crc_user['id'],
                days_ago=0,
            )
            instance = await server.db.visit_instances.find_one(
                {'patient_id': patient['id']}, {'_id': 0})
            assert instance
            clinical_id = str(uuid.uuid4())
            admin_id = str(uuid.uuid4())
            await server.db.visit_instances.update_one(
                {'id': instance['id']},
                {'$set': {
                    'status': 'scheduled',
                    'clinical_tasks': [{
                        'id': clinical_id,
                        'label': 'Record patient vitals',
                        'completed': False,
                    }],
                    'admin_tasks': [{
                        'id': admin_id,
                        'label': 'Submit visit documentation',
                        'completed': False,
                    }],
                }},
            )

            async with make_client() as cli:
                response = await cli.get('/api/tasks', headers=crc_headers)
            assert response.status_code == 200, response.text
            patient_tasks = [
                task for task in response.json()
                if task.get('patient_id') == patient['id']
            ]
            assert any(
                task['type'] == 'admin_task'
                and task['title'] == 'Submit visit documentation'
                and task['workflow_task_id'] == admin_id
                and task['workflow_task_kind'] == 'admin_tasks'
                for task in patient_tasks
            )
            assert not any(
                task.get('workflow_task_id') == clinical_id
                or task.get('title') == 'Record patient vitals'
                for task in patient_tasks
            )

            async with make_client() as cli:
                completed = await cli.patch(
                    f"/api/visit-instances/{instance['id']}/tasks/{admin_id}",
                    headers=crc_headers,
                    json={'completed': True},
                )
                refreshed = await cli.get('/api/tasks', headers=crc_headers)
            assert completed.status_code == 200, completed.text
            stored = completed.json()
            assert stored['admin_tasks'][0]['completed'] is True
            assert stored['clinical_tasks'][0]['completed'] is False
            assert not any(
                task.get('workflow_task_id') == admin_id
                for task in refreshed.json()
            )

            async with make_client() as cli:
                withdrawn = await cli.patch(
                    f"/api/visit-instances/{instance['id']}",
                    headers=crc_headers,
                    json={'status': 'withdrawn'},
                )
                after_withdrawal = await cli.get('/api/tasks', headers=crc_headers)
            assert withdrawn.status_code == 200, withdrawn.text
            assert not any(
                task.get('patient_id') == patient['id']
                for task in after_withdrawal.json()
            )

        run(flow())

    def test_missed_instance_leaves_the_overdue_queue(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=((0, 'Only Visit'),))
            # enrolled 10 days ago with day_offset 0 -> genuinely past its
            # window, so it would otherwise surface as 'overdue' (see
            # test_pending_past_window_is_returned_as_overdue). A visit only
            # ever becomes the terminal 'missed' via an explicit staff mark
            # (PATCH /visit-instances/{id}) — materialization itself never
            # writes it, so that's the action under test here.
            missed_pt = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'], days_ago=10)
            inst = await server.db.visit_instances.find_one(
                {'patient_id': missed_pt['id']}, {'_id': 0})
            assert inst is not None
            async with make_client() as cli:
                patch = await cli.patch(
                    f"/api/visit-instances/{inst['id']}",
                    headers=pi_headers, json={'status': 'missed'},
                )
                assert patch.status_code == 200, patch.text
                r = await cli.get('/api/tasks', headers=pi_headers)
            assert r.status_code == 200, r.text
            assert not any(x['type'] in ('overdue_visit', 'visit_today')
                           and x.get('patient_id') == missed_pt['id']
                           for x in r.json()), \
                'missed visit instance must not surface in the tasks queue'
        run(flow())

    def test_approved_schedule_leaves_the_queue(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            await _enroll(pi_headers, t['id'], pi_id=pi_user['id'])
            async with make_client() as cli:
                r1 = await cli.get('/api/tasks', headers=pi_headers)
                assert any(x['type'] == 'schedule_review' and x.get('trial_id') == t['id']
                           for x in r1.json())
                ra = await cli.post(f"/api/schedules/{t['id']}/approve", headers=pi_headers)
                assert ra.status_code == 200
                r2 = await cli.get('/api/tasks', headers=pi_headers)
                assert not any(x['type'] == 'schedule_review' and x.get('trial_id') == t['id']
                               for x in r2.json())
        run(flow())

    def test_tasks_role_guard(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            _, patient_headers = await _register('patient')
            async with make_client() as cli:
                r = await cli.get('/api/tasks', headers=patient_headers)
                assert r.status_code == 403
                r2 = await cli.get('/api/tasks', headers=sp_headers)
                assert r2.status_code == 403
        run(flow())


# ── GET /patients enrichment: derived status + next_visit ────────────────────
class TestPatientsListEnrichment:
    def _row(self, rows, patient_id):
        return next((p for p in rows if p['id'] == patient_id), None)

    def test_active_with_next_upcoming_visit(self, sponsor, pi):
        # All-future templates so nothing is due/overdue yet — next_visit is
        # simply the soonest planned instance. (The overdue case is covered
        # separately by test_overdue_when_pending_visit_is_past_due below.)
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, tpls = await _make_trial(
                sp_headers, templates=((7, 'V1'), (14, 'V2'), (21, 'V3')))
            patient = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'], days_ago=0)
            async with make_client() as cli:
                r = await cli.get('/api/patients', headers=pi_headers)
            assert r.status_code == 200, r.text
            row = self._row(r.json(), patient['id'])
            assert row is not None, 'enrolled patient missing from own-scoped list'
            assert row['status'] == 'active'
            nv = row['next_visit']
            assert nv is not None, 'active patient should surface a next visit'
            assert nv['seq'] == 1                       # day-7 is the soonest planned
            assert nv['name'] == tpls[0]['name']
            assert nv['status'] == 'planned'
            assert nv['scheduled_date']                 # ISO string present
            assert 'id' in nv
        run(flow())

    def test_overdue_when_pending_visit_is_past_due(self, pi, trial):
        trial_doc, _ = trial
        pi_user, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'], pi_id=pi_user['id'], days_ago=5)
            # day-0 (seq 1) materialized 'missed'; flip to 'scheduled' → genuinely
            # pending yet past-due, i.e. overdue.
            await server.db.visit_instances.update_one(
                {'patient_id': patient['id'], 'seq': 1}, {'$set': {'status': 'scheduled'}})
            async with make_client() as cli:
                r = await cli.get('/api/patients', headers=pi_headers)
            row = self._row(r.json(), patient['id'])
            assert row['status'] == 'overdue'
            assert row['next_visit']['seq'] == 1        # the past-due visit is next actionable
        run(flow())

    def test_completed_when_all_instances_done(self, pi, trial):
        trial_doc, _ = trial
        pi_user, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'], pi_id=pi_user['id'], days_ago=5)
            # Both fields: _effective_visit_status reads operational_status
            # first (same precedence PATCH /visit-instances/{id} writes with),
            # so status alone wouldn't override the stored 'planned'.
            await server.db.visit_instances.update_many(
                {'patient_id': patient['id']},
                {'$set': {'status': 'completed', 'operational_status': 'completed'}})
            async with make_client() as cli:
                r = await cli.get('/api/patients', headers=pi_headers)
            row = self._row(r.json(), patient['id'])
            assert row['status'] == 'completed'
            assert row['next_visit'] is None
        run(flow())

    def test_no_visits_status_when_trial_has_no_templates(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            bare, _ = await _make_trial(sp_headers, templates=())
            patient = await _enroll(pi_headers, bare['id'], pi_id=pi_user['id'])
            async with make_client() as cli:
                r = await cli.get('/api/patients', headers=pi_headers)
            row = self._row(r.json(), patient['id'])
            assert row['status'] == 'no_visits'
            assert row['next_visit'] is None
        run(flow())
