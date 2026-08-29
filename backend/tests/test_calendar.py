"""Team calendar tests — Task 3.2.

Covers GET /api/calendar/team: pi/crc-only access, own-patient scoping
(pi_id / crc_id, same as GET /patients), bounded date ranges (default =
current month, hard cap of 100 days), and the privacy-safe response shape
(patient initials + subject label + trial protocol — never full names).

Runs in-process against the FastAPI app via httpx.ASGITransport, hitting the
real (Atlas) database from backend/.env. All test data carries a per-run
marker (RUN_ID) and is deleted in module teardown. Motor pins its io_loop on
first use, so every coroutine runs on the single module-level LOOP.
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

LOOP = asyncio.new_event_loop()

_patient_ids = []
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


async def _get(headers, params=None):
    async with make_client() as cli:
        return await cli.get('/api/calendar/team', headers=headers, params=params or {})


def _seed_instance(patient_id, trial_id, seq, name, sched, status):
    """Raw visit_instances row matching materialize_visit_instances shape."""
    return {
        'id': str(uuid.uuid4()),
        'patient_id': patient_id,
        'trial_id': trial_id,
        'visit_template_id': f'test-{RUN_ID}-tpl-{seq}',
        'name': name,
        'seq': seq,
        'visit_number': seq,
        'activities': ['Vitals'],
        'window_days': 3,
        'scheduled_date': sched,
        'window_start': sched - timedelta(days=3),
        'window_end': sched + timedelta(days=3),
        'status': status,
        'note': '', 'updated_by': None,
        'updated_at': server.now(), 'created_at': server.now(),
    }


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.patients.delete_many({'id': {'$in': _patient_ids}})
        await db.visit_instances.delete_many({'patient_id': {'$in': _patient_ids}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def ctx():
    """One PI + CRC on site A (sharing patient p1), one PI on site B (owns p2),
    and one PI on site B with NO patients. Instances for p1 land today,
    today+5, today+40 and today-3; p2 has one instance today."""
    async def build():
        db = server.db
        pi_a, pi_a_h = await _register('pi', org=ORG_SITE_A)
        crc_a, crc_a_h = await _register('crc', org=ORG_SITE_A)
        pi_b, pi_b_h = await _register('pi', org=ORG_SITE_B)
        pi_empty, pi_empty_h = await _register('pi', org=ORG_SITE_B)

        trial_id = str(uuid.uuid4())
        _trial_ids.append(trial_id)
        await db.trials.insert_one({
            'id': trial_id, 'protocol_id': f'TESTPROT-{RUN_ID}',
            'title': f'Test Trial {RUN_ID}', 'phase': 'Phase II',
            'condition': 'Test Condition', 'status': 'active',
            'created_at': server.now(),
        })

        def patient(full_name, pi_user, crc_user=None):
            pid = str(uuid.uuid4())
            _patient_ids.append(pid)
            return {
                'id': pid, 'full_name': full_name,
                'email': f'test-{RUN_ID}-pt-{uuid.uuid4().hex[:6]}@example.com',
                'phone': '', 'trial_id': trial_id,
                'pi_id': pi_user['id'],
                'crc_id': crc_user['id'] if crc_user else None,
                'enrolled_date': server.now().date().isoformat(),
                'completed_visit_ids': [],
                'avatar_initials': ''.join(w[0].upper() for w in full_name.split()[:2]),
                'created_at': server.now(),
            }

        p1 = patient(f'Asha Verma {RUN_ID}', pi_a, crc_a)
        p2 = patient(f'Bilal Khan {RUN_ID}', pi_b)
        await db.patients.insert_many([dict(p1), dict(p2)])

        today = server.now().replace(hour=12, minute=0, second=0, microsecond=0)
        i_today = _seed_instance(p1['id'], trial_id, 1, 'Baseline', today, 'upcoming')
        i_plus5 = _seed_instance(p1['id'], trial_id, 2, 'Week 1', today + timedelta(days=5), 'upcoming')
        i_plus40 = _seed_instance(p1['id'], trial_id, 3, 'Week 6', today + timedelta(days=40), 'upcoming')
        i_minus3 = _seed_instance(p1['id'], trial_id, 4, 'Screening', today - timedelta(days=3), 'missed')
        i_p2 = _seed_instance(p2['id'], trial_id, 1, 'Baseline', today, 'upcoming')
        await db.visit_instances.insert_many([
            dict(i_today), dict(i_plus5), dict(i_plus40), dict(i_minus3), dict(i_p2)])

        return {
            'pi_a': pi_a, 'pi_a_h': pi_a_h, 'crc_a': crc_a, 'crc_a_h': crc_a_h,
            'pi_b': pi_b, 'pi_b_h': pi_b_h, 'pi_empty_h': pi_empty_h,
            'p1': p1, 'p2': p2, 'today': today,
            'i_today': i_today, 'i_plus5': i_plus5, 'i_plus40': i_plus40,
            'i_minus3': i_minus3, 'i_p2': i_p2,
        }
    return run(build())


def ymd(dt):
    return dt.date().isoformat()


# ── Access control ───────────────────────────────────────────────────────────
class TestAccess:
    def test_requires_auth(self):
        async def flow():
            async with make_client() as cli:
                r = await cli.get('/api/calendar/team')
            assert r.status_code == 401
        run(flow())

    def test_patient_role_forbidden(self):
        async def flow():
            _, headers = await _register('patient')
            r = await _get(headers)
            assert r.status_code == 403, r.text
        run(flow())


# ── Scoping ──────────────────────────────────────────────────────────────────
class TestScoping:
    def test_pi_sees_only_own_patients(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_a_h'], {'from': ymd(t - timedelta(days=5)),
                                           'to': ymd(t + timedelta(days=10))})
            assert r.status_code == 200, r.text
            items = r.json()
            pids = {i['patient_id'] for i in items}
            assert pids == {ctx['p1']['id']}, 'PI must see exactly their own patients'
            assert ctx['p2']['id'] not in pids
        run(flow())

    def test_other_site_pi_sees_nothing(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_empty_h'], {'from': ymd(t - timedelta(days=5)),
                                               'to': ymd(t + timedelta(days=10))})
            assert r.status_code == 200, r.text
            assert r.json() == []
        run(flow())

    def test_pi_b_sees_only_their_patient(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_b_h'], {'from': ymd(t), 'to': ymd(t)})
            items = r.json()
            assert {i['patient_id'] for i in items} == {ctx['p2']['id']}
        run(flow())

    def test_crc_scoped_by_crc_id(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['crc_a_h'], {'from': ymd(t), 'to': ymd(t)})
            assert r.status_code == 200, r.text
            items = r.json()
            assert {i['patient_id'] for i in items} == {ctx['p1']['id']}
        run(flow())


# ── Range handling ───────────────────────────────────────────────────────────
class TestRange:
    def test_range_filter_inclusive(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_a_h'], {'from': ymd(t), 'to': ymd(t + timedelta(days=10))})
            ids = {i['id'] for i in r.json()}
            assert ctx['i_today']['id'] in ids            # from-bound inclusive
            assert ctx['i_plus5']['id'] in ids
            assert ctx['i_plus40']['id'] not in ids       # beyond `to`
            assert ctx['i_minus3']['id'] not in ids       # before `from`
        run(flow())

    def test_default_range_is_current_month(self, ctx):
        async def flow():
            r = await _get(ctx['pi_a_h'])                 # no params
            assert r.status_code == 200, r.text
            ids = {i['id'] for i in r.json()}
            assert ctx['i_today']['id'] in ids
            assert ctx['i_plus40']['id'] not in ids       # today+40 is never this month
        run(flow())

    def test_reversed_range_rejected(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_a_h'], {'from': ymd(t), 'to': ymd(t - timedelta(days=1))})
            assert r.status_code == 400, r.text
        run(flow())

    def test_span_capped_at_100_days(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_a_h'], {'from': ymd(t), 'to': ymd(t + timedelta(days=120))})
            assert r.status_code == 400, r.text
            r2 = await _get(ctx['pi_a_h'], {'from': ymd(t), 'to': ymd(t + timedelta(days=99))})
            assert r2.status_code == 200, r2.text
        run(flow())

    def test_bad_date_format_rejected(self, ctx):
        async def flow():
            r = await _get(ctx['pi_a_h'], {'from': 'not-a-date'})
            assert r.status_code == 400, r.text
        run(flow())


# ── Response shape (privacy-safe join) ───────────────────────────────────────
class TestShape:
    def test_items_are_privacy_safe_and_joined(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['crc_a_h'], {'from': ymd(t - timedelta(days=5)),
                                            'to': ymd(t + timedelta(days=10))})
            items = r.json()
            assert items, 'expected visits in range'
            for i in items:
                for key in ('id', 'patient_id', 'trial_id', 'name', 'seq',
                            'scheduled_date', 'window_start', 'window_end', 'status',
                            'patient_initials', 'subject_label', 'protocol_id',
                            'condition', 'pi_name', 'site'):
                    assert key in i, f'missing field {key}'
                # privacy: no full patient name anywhere in the payload
                assert 'full_name' not in i
                assert ctx['p1']['full_name'] not in str(i.values())
                assert '_id' not in i
            one = next(i for i in items if i['id'] == ctx['i_today']['id'])
            assert one['patient_initials'] == ctx['p1']['avatar_initials']
            assert one['subject_label'].startswith('SUBJ-')
            assert one['protocol_id'] == f'TESTPROT-{RUN_ID}'
            assert one['condition'] == 'Test Condition'
            assert one['pi_name'] == ctx['pi_a']['full_name']   # CRC view shows the PI
            assert one['status'] == 'upcoming'
        run(flow())

    def test_sorted_by_scheduled_date(self, ctx):
        async def flow():
            t = ctx['today']
            r = await _get(ctx['pi_a_h'], {'from': ymd(t - timedelta(days=5)),
                                           'to': ymd(t + timedelta(days=50))})
            dates = [i['scheduled_date'] for i in r.json()]
            assert dates == sorted(dates)
        run(flow())
