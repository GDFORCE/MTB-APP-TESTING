"""Sponsor visit-schedule EDIT mode — Task 4.1.

Covers the dedicated template CRUD the edit screen drives:
  GET  /api/trials/{id}/visits  → the trial's visit TEMPLATES (sorted), scoped
  PUT  /api/visits/{id}          → update a template + re-materialize FUTURE
                                   pending instances (preserve completed/past)
  DELETE /api/visits/{id}        → remove a template + its future pending
                                   instances (preserve completed/past)

Key invariants:
  * Re-saving the SAME schedule is idempotent — no duplicate templates, no
    duplicate/new visit_instances.
  * A template edit propagates only to FUTURE, still-pending, un-touched
    instances; completed / missed / past / patient-touched instances are left
    exactly as they were.
  * Foreign sponsor (different org, not creator) → 403 on every endpoint.

Same harness as test_visit_instances.py: in-process ASGITransport against the
real Atlas DB, RUN_ID-marked data, one module-level event loop (Motor pins its
io_loop on first use — never asyncio.run here), module teardown cleanup.
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
ORG_OTHER = f'TESTORG-{RUN_ID} Rival Pharma'

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


async def _make_trial(sponsor_headers,
                      templates=((0, 'Screening'), (7, 'Baseline'), (14, 'Week 2'))):
    """Create a trial + visit templates via the public API; (trial, template_list)."""
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


async def _enroll(staff_headers, trial_id, pi_id=None, days_ago=5):
    if pi_id:
        pi_user = await server.db.users.find_one(
            {'id': pi_id}, {'_id': 0, 'email': 1, 'phone': 1})
        contacts = {
            'email': (pi_user or {}).get('email') or '',
            'phone': (pi_user or {}).get('phone') or '',
        }
        await server.db.invitations.update_one(
            {'trial_id': trial_id, 'role': 'pi', 'accepted_user_id': pi_id},
            {'$setOnInsert': {
                'id': str(uuid.uuid4()), 'token': uuid.uuid4().hex,
                'trial_id': trial_id, 'role': 'pi', 'status': 'accepted',
                'accepted_user_id': pi_id, 'accepted_at': server.now(),
                'created_at': server.now(), **contacts,
            }},
            upsert=True,
        )
    enrolled = (server.now() - timedelta(days=days_ago)).date().isoformat()
    async with make_client() as cli:
        r = await cli.post('/api/patients', headers=staff_headers, json={
            'full_name': f'Test PATIENT {RUN_ID}',
            'email': f'test-{RUN_ID}-enrollee-{uuid.uuid4().hex[:6]}@example.com',
            'trial_id': trial_id, 'pi_id': pi_id, 'enrolled_date': enrolled,
        })
    assert r.status_code == 200, r.text
    return r.json()


async def _insts(patient_id):
    return await server.db.visit_instances.find(
        {'patient_id': patient_id}, {'_id': 0}).sort('seq', 1).to_list(50)


def _as_dt(v):
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


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
        await db.invitations.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def sponsor():
    return run(_register('sponsor', org=ORG_SPONSOR))


@pytest.fixture(scope='module')
def other_sponsor():
    return run(_register('sponsor', org=ORG_OTHER))


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_SITE))


# ── GET /trials/{id}/visits ──────────────────────────────────────────────────
class TestListTemplates:
    def test_returns_templates_sorted_by_visit_number(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            async with make_client() as cli:
                r = await cli.get(f"/api/trials/{trial['id']}/visits", headers=sp_headers)
            assert r.status_code == 200, r.text
            got = r.json()
            assert [v['visit_number'] for v in got] == [1, 2, 3]
            assert [v['id'] for v in got] == [t['id'] for t in tpls]
            for v in got:
                for key in ('id', 'trial_id', 'name', 'day_offset', 'window_days', 'activities'):
                    assert key in v, f'missing field {key}'
        run(flow())

    def test_review_fields_persist_through_create_and_update(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            trial, _ = await _make_trial(sp_headers, templates=())
            async with make_client() as cli:
                created = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': trial['id'], 'visit_number': 1,
                    'name': 'Extraction review', 'day_offset': 0,
                    'window_days': 2, 'activities': ['Vitals'],
                    'clinical_tasks': ['Vitals', 'ECG'],
                    'admin_tasks': ['eCRF'],
                    'comments': 'Confirm timing with the site.',
                    'extraction_warning': True,
                    'review_status': 'pending',
                })
                assert created.status_code == 200, created.text
                row = created.json()
                assert row['clinical_tasks'] == ['Vitals', 'ECG']
                assert row['admin_tasks'] == ['eCRF']
                assert row['comments'] == 'Confirm timing with the site.'
                assert row['extraction_warning'] is True
                assert row['review_status'] == 'pending'

                acknowledged = await cli.put(
                    f"/api/visits/{row['id']}", headers=sp_headers,
                    json={
                        'comments': 'Timing confirmed.',
                        'extraction_warning': False,
                        'review_status': 'ok',
                    },
                )
                assert acknowledged.status_code == 200, acknowledged.text
                assert acknowledged.json()['comments'] == 'Timing confirmed.'
                assert acknowledged.json()['extraction_warning'] is False
                assert acknowledged.json()['review_status'] == 'ok'
        run(flow())

    def test_pi_owner_can_list(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, _ = await _make_trial(sp_headers)
            # tie the PI to the trial by enrolling a PI-assigned patient
            await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'])
            async with make_client() as cli:
                r = await cli.get(f"/api/trials/{trial['id']}/visits", headers=pi_headers)
            assert r.status_code == 200, r.text
            assert len(r.json()) == 3
        run(flow())

    def test_unknown_trial_404(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/trials/{uuid.uuid4()}/visits', headers=sp_headers)
            assert r.status_code == 404
        run(flow())

    def test_foreign_sponsor_403(self, sponsor, other_sponsor):
        _, sp_headers = sponsor
        _, other_headers = other_sponsor
        async def flow():
            trial, _ = await _make_trial(sp_headers)
            async with make_client() as cli:
                r = await cli.get(f"/api/trials/{trial['id']}/visits", headers=other_headers)
            assert r.status_code == 403
        run(flow())


# ── Idempotent re-save ───────────────────────────────────────────────────────
class TestIdempotentResave:
    def test_reput_same_values_no_dup_templates_or_instances(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            before_inst = {i['id'] for i in await _insts(patient['id'])}
            assert len(before_inst) == 3
            # Re-save the SAME schedule: PUT each template with its own values.
            async with make_client() as cli:
                for t in tpls:
                    r = await cli.put(f"/api/visits/{t['id']}", headers=sp_headers, json={
                        'name': t['name'], 'day_offset': t['day_offset'],
                        'window_days': t['window_days'], 'activities': t['activities'],
                    })
                    assert r.status_code == 200, r.text
            # Templates: same count, same ids.
            tpl_docs = await server.db.visits.find(
                {'trial_id': trial['id']}, {'_id': 0}).to_list(50)
            assert {t['id'] for t in tpl_docs} == {t['id'] for t in tpls}
            # Instances: same ids, no dup / new rows.
            after_inst = {i['id'] for i in await _insts(patient['id'])}
            assert after_inst == before_inst
        run(flow())


# ── PUT re-materialization ───────────────────────────────────────────────────
class TestUpdateRematerializes:
    def test_future_pending_instance_follows_template_edit(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            pdoc = await server.db.patients.find_one({'id': patient['id']}, {'_id': 0})
            base = _as_dt(pdoc['enrolled_date'])
            insts = await _insts(patient['id'])
            # seq2 (day 7) is future/pending; the raw stored status is a
            # static 'planned' until GET-time derives overdue/due/planned →
            # editable regardless.
            assert insts[1]['status'] == 'planned'
            async with make_client() as cli:
                r = await cli.put(f"/api/visits/{tpls[1]['id']}", headers=sp_headers, json={
                    'name': f'Week1 Updated {RUN_ID}', 'day_offset': 10,
                    'window_days': 5, 'activities': ['Vitals', 'ECG'],
                })
            assert r.status_code == 200, r.text
            inst2 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 2}, {'_id': 0})
            assert inst2['name'] == f'Week1 Updated {RUN_ID}'
            assert inst2['window_days'] == 5
            assert inst2['activities'] == ['Vitals', 'ECG']
            expected = (base + timedelta(days=10))
            assert _as_dt(inst2['scheduled_date']).date() == expected.date()
            assert _as_dt(inst2['window_start']) == expected - timedelta(days=5)
            assert _as_dt(inst2['window_end']) == expected + timedelta(days=5)
            assert inst2['status'] == 'planned'
            # template itself was updated
            fresh_tpl = await server.db.visits.find_one({'id': tpls[1]['id']}, {'_id': 0})
            assert fresh_tpl['day_offset'] == 10 and fresh_tpl['window_days'] == 5
            # audited
            row = await server.db.audit_logs.find_one(
                {'action': 'visit.update', 'target_id': tpls[1]['id']})
            assert row
        run(flow())

    def test_completed_and_past_instances_are_preserved(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            # seq3 (day 14) → mark completed with patient activity
            await server.db.visit_instances.update_one(
                {'patient_id': patient['id'], 'seq': 3},
                {'$set': {'status': 'completed', 'note': 'seen', 'updated_by': pi_user['id']}})
            before3 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 3}, {'_id': 0})
            # seq1 (day 0) is past its window, but the raw stored status
            # stays a static 'planned' — nothing auto-marks it 'missed'; a
            # visit only becomes 'missed' via an explicit staff PATCH.
            before1 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 1}, {'_id': 0})
            assert before1['status'] == 'planned'
            async with make_client() as cli:
                r3 = await cli.put(f"/api/visits/{tpls[2]['id']}", headers=sp_headers,
                                   json={'name': f'Final {RUN_ID}', 'day_offset': 99})
                r1 = await cli.put(f"/api/visits/{tpls[0]['id']}", headers=sp_headers,
                                   json={'name': f'Screen2 {RUN_ID}', 'day_offset': -1})
            assert r3.status_code == 200 and r1.status_code == 200
            # completed instance untouched (name/date/status all preserved)
            after3 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 3}, {'_id': 0})
            assert after3['status'] == 'completed'
            assert after3['name'] == before3['name']
            assert _as_dt(after3['scheduled_date']) == _as_dt(before3['scheduled_date'])
            # past instance untouched
            after1 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 1}, {'_id': 0})
            assert after1['status'] == 'planned'
            assert after1['name'] == before1['name']
            assert _as_dt(after1['scheduled_date']) == _as_dt(before1['scheduled_date'])
        run(flow())

    def test_foreign_sponsor_cannot_update(self, sponsor, other_sponsor):
        _, sp_headers = sponsor
        _, other_headers = other_sponsor
        async def flow():
            _, tpls = await _make_trial(sp_headers)
            async with make_client() as cli:
                r = await cli.put(f"/api/visits/{tpls[0]['id']}", headers=other_headers,
                                  json={'name': 'hax'})
            assert r.status_code == 403
        run(flow())

    def test_unknown_template_404(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            async with make_client() as cli:
                r = await cli.put(f'/api/visits/{uuid.uuid4()}', headers=sp_headers,
                                  json={'name': 'x'})
            assert r.status_code == 404
        run(flow())


# ── Finding 1: unique visit_number across an edit (delete-middle-then-add) ───
class TestUniqueVisitNumbers:
    def test_delete_middle_then_add_keeps_visit_numbers_unique(self, sponsor, pi):
        """Reproduces the collision: after deleting a middle template and adding
        one at the end, the frontend re-numbers every row (index+1) → all
        templates end with UNIQUE visit_numbers, and a patient enrolled AFTER the
        edit gets unique, correctly-ordered instance seqs."""
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)  # visit_numbers 1,2,3
            async with make_client() as cli:
                # delete the MIDDLE template (v2)
                rd = await cli.delete(f"/api/visits/{tpls[1]['id']}", headers=sp_headers)
                assert rd.status_code == 200, rd.text
                # survivor v3 slides to row index 1 → visit_number 2
                rp = await cli.put(f"/api/visits/{tpls[2]['id']}", headers=sp_headers,
                                   json={'visit_number': 2})
                assert rp.status_code == 200, rp.text
                # add a NEW visit at the end → row index 2 → visit_number 3
                rn = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': trial['id'], 'visit_number': 3, 'name': f'Added {RUN_ID}',
                    'day_offset': 21, 'window_days': 3, 'activities': ['Labs']})
                assert rn.status_code == 200, rn.text
            # all templates carry UNIQUE visit_numbers reflecting row order
            tpl_docs = await server.db.visits.find(
                {'trial_id': trial['id']}, {'_id': 0}).to_list(50)
            nums = sorted(t['visit_number'] for t in tpl_docs)
            assert nums == [1, 2, 3], nums
            assert len(nums) == len(set(nums))
            # a patient enrolled AFTER the edit → unique, ordered instance seqs
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'])
            insts = await _insts(patient['id'])
            seqs = [i['seq'] for i in insts]
            assert seqs == [1, 2, 3], seqs
            assert len(seqs) == len(set(seqs))
        run(flow())

    def test_put_visit_number_repoints_future_instance_seq(self, sponsor, pi):
        """Changing a template's visit_number updates the seq of its FUTURE
        pending instances; completed/past instances keep their original seq."""
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            # seq3 (day 14, future) → mark completed/touched → must NOT be repointed
            await server.db.visit_instances.update_one(
                {'patient_id': patient['id'], 'seq': 3},
                {'$set': {'status': 'completed', 'note': 'seen', 'updated_by': pi_user['id']}})
            # seq2 (day 7, future/pending) → its seq should follow the template
            async with make_client() as cli:
                r = await cli.put(f"/api/visits/{tpls[1]['id']}", headers=sp_headers,
                                  json={'visit_number': 7})
                assert r.status_code == 200, r.text
            inst2 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'visit_template_id': tpls[1]['id']}, {'_id': 0})
            assert inst2['seq'] == 7 and inst2['visit_number'] == 7
            # completed instance's seq untouched
            inst3 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'visit_template_id': tpls[2]['id']}, {'_id': 0})
            assert inst3['seq'] == 3 and inst3['status'] == 'completed'
        run(flow())


# ── Finding 2: a visit ADDED mid-trial materializes for enrolled patients ─────
class TestAddedVisitMaterializes:
    def test_new_template_materializes_for_enrolled_patient(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            before = await _insts(patient['id'])
            assert len(before) == 3
            # give one existing instance patient activity → must remain untouched
            await server.db.visit_instances.update_one(
                {'patient_id': patient['id'], 'seq': 1},
                {'$set': {'status': 'completed', 'note': 'done', 'updated_by': pi_user['id']}})
            before1 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 1}, {'_id': 0})
            # ADD a new visit to the in-flight schedule
            async with make_client() as cli:
                rn = await cli.post('/api/visits', headers=sp_headers, json={
                    'trial_id': trial['id'], 'visit_number': 4, 'name': f'Added {RUN_ID}',
                    'day_offset': 30, 'window_days': 3, 'activities': ['Labs']})
                assert rn.status_code == 200, rn.text
            new_tpl = rn.json()
            after = await _insts(patient['id'])
            # exactly ONE new instance, for the new template, future-dated
            assert len(after) == 4
            new_insts = [i for i in after if i['visit_template_id'] == new_tpl['id']]
            assert len(new_insts) == 1
            ni = new_insts[0]
            assert ni['seq'] == 4 and ni['status'] == 'planned'
            pdoc = await server.db.patients.find_one({'id': patient['id']}, {'_id': 0})
            base = _as_dt(pdoc['enrolled_date'])
            assert _as_dt(ni['scheduled_date']).date() == (base + timedelta(days=30)).date()
            # existing completed/touched instance untouched
            after1 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 1}, {'_id': 0})
            assert after1['status'] == 'completed' and after1['note'] == before1['note']
        run(flow())

    def test_new_template_no_instance_before_any_enrollment(self, sponsor):
        """POSTing templates on a trial with NO enrolled patients creates no
        instances (they're materialized at enrollment as before)."""
        _, sp_headers = sponsor
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            insts = await server.db.visit_instances.count_documents(
                {'trial_id': trial['id']})
            assert insts == 0
        run(flow())


# ── DELETE re-materialization ────────────────────────────────────────────────
class TestDeleteRematerializes:
    def test_delete_removes_template_and_future_pending_instances(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            # delete the day-7 (upcoming/future/pending) template
            async with make_client() as cli:
                r = await cli.delete(f"/api/visits/{tpls[1]['id']}", headers=sp_headers)
            assert r.status_code == 200, r.text
            # template gone
            assert await server.db.visits.find_one({'id': tpls[1]['id']}) is None
            # its future pending instance gone; the other two remain
            remaining = await _insts(patient['id'])
            seqs = sorted(i['seq'] for i in remaining)
            assert 2 not in seqs and seqs == [1, 3]
            row = await server.db.audit_logs.find_one(
                {'action': 'visit.delete', 'target_id': tpls[1]['id']})
            assert row
        run(flow())

    def test_delete_preserves_completed_instance(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            trial, tpls = await _make_trial(sp_headers)
            patient = await _enroll(pi_headers, trial['id'], pi_id=pi_user['id'], days_ago=5)
            await server.db.visit_instances.update_one(
                {'patient_id': patient['id'], 'seq': 3},
                {'$set': {'status': 'completed', 'updated_by': pi_user['id']}})
            async with make_client() as cli:
                r = await cli.delete(f"/api/visits/{tpls[2]['id']}", headers=sp_headers)
            assert r.status_code == 200, r.text
            # template removed, but the completed instance is history → preserved
            assert await server.db.visits.find_one({'id': tpls[2]['id']}) is None
            inst3 = await server.db.visit_instances.find_one(
                {'patient_id': patient['id'], 'seq': 3}, {'_id': 0})
            assert inst3 is not None and inst3['status'] == 'completed'
        run(flow())

    def test_foreign_sponsor_cannot_delete(self, sponsor, other_sponsor):
        _, sp_headers = sponsor
        _, other_headers = other_sponsor
        async def flow():
            _, tpls = await _make_trial(sp_headers)
            async with make_client() as cli:
                r = await cli.delete(f"/api/visits/{tpls[0]['id']}", headers=other_headers)
            assert r.status_code == 403
            # template still present
            assert await server.db.visits.find_one({'id': tpls[0]['id']}) is not None
        run(flow())

    def test_unknown_template_404(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            async with make_client() as cli:
                r = await cli.delete(f'/api/visits/{uuid.uuid4()}', headers=sp_headers)
            assert r.status_code == 404
        run(flow())
