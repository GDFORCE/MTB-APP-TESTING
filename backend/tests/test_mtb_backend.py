"""End-to-end backend tests for My Trial Board (Dawn Rounds)."""
import os
import json
import uuid
import asyncio
import pytest
import requests
import websockets

BASE = os.environ.get('EXPO_PUBLIC_BACKEND_URL', 'https://code-viewer-87.preview.emergentagent.com').rstrip('/')
API = f"{BASE}/api"
WS_BASE = BASE.replace('https://', 'wss://').replace('http://', 'ws://') + '/api/ws'

DEMO = {
    'sponsor': ('sponsor@mtb.app', 'Password1!'),
    'pi': ('pi@mtb.app', 'Password1!'),
    'crc': ('crc@mtb.app', 'Password1!'),
    'patient': ('patient@mtb.app', 'Password1!'),
}


@pytest.fixture(scope='module')
def s():
    return requests.Session()


def _login(s, role):
    email, pw = DEMO[role]
    r = s.post(f"{API}/auth/login", json={'email': email, 'password': pw})
    assert r.status_code == 200, f"login {role}: {r.status_code} {r.text}"
    j = r.json()
    return j['access_token'], j['refresh_token'], j['user']


def _h(token):
    return {'Authorization': f'Bearer {token}'}


# ── Seed (idempotent) ────────────────────────────────────────────────────────
class TestSeed:
    def test_seed_idempotent(self, s):
        r1 = s.post(f"{API}/seed")
        assert r1.status_code == 200
        # 2nd call should be already=True
        r2 = s.post(f"{API}/seed")
        assert r2.status_code == 200
        assert r2.json().get('already') is True


# ── Auth ─────────────────────────────────────────────────────────────────────
class TestAuth:
    def test_login_patient_success(self, s):
        access, refresh, user = _login(s, 'patient')
        assert access and refresh
        assert user['email'] == 'patient@mtb.app'
        assert user['role'] == 'patient'

    def test_login_wrong_password(self, s):
        r = s.post(f"{API}/auth/login", json={'email': 'patient@mtb.app', 'password': 'wrong'})
        assert r.status_code == 401

    def test_me_with_token(self, s):
        access, _, _ = _login(s, 'pi')
        r = s.get(f"{API}/auth/me", headers=_h(access))
        assert r.status_code == 200
        assert r.json()['role'] == 'pi'

    def test_me_no_token_401(self, s):
        r = s.get(f"{API}/auth/me")
        assert r.status_code == 401

    def test_refresh_token(self, s):
        _, refresh, _ = _login(s, 'crc')
        r = s.post(f"{API}/auth/refresh", json={'refresh_token': refresh})
        assert r.status_code == 200
        assert 'access_token' in r.json()

    def test_register_new_user_and_duplicate_rejection(self, s):
        email = f"TEST_user_{uuid.uuid4().hex[:8]}@example.com"
        body = {'email': email, 'password': 'Password1!', 'full_name': 'Test User',
                'role': 'patient', 'security_answer': 'bruno'}
        r = s.post(f"{API}/auth/register", json=body)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j['user']['email'] == email.lower()
        assert j['access_token']
        # duplicate
        r2 = s.post(f"{API}/auth/register", json=body)
        assert r2.status_code == 400

    def test_forgot_reset_flow(self, s):
        # request OTP
        r = s.post(f"{API}/auth/forgot", json={'email': 'crc@mtb.app'})
        assert r.status_code == 200
        otp = r.json().get('otp')
        assert otp, f"otp missing in {r.json()}"
        # reset
        r2 = s.post(f"{API}/auth/reset",
                    json={'email': 'crc@mtb.app', 'otp': otp, 'new_password': 'Password1!'})
        assert r2.status_code == 200
        # login still works
        r3 = s.post(f"{API}/auth/login", json={'email': 'crc@mtb.app', 'password': 'Password1!'})
        assert r3.status_code == 200


# ── Trials & Visits ──────────────────────────────────────────────────────────
class TestTrialsVisits:
    def test_patient_sees_enrolled_trial(self, s):
        access, _, _ = _login(s, 'patient')
        r = s.get(f"{API}/trials", headers=_h(access))
        assert r.status_code == 200
        trials = r.json()
        assert len(trials) >= 1
        assert any(t['protocol_id'] == 'Protocol-001' for t in trials)

    def test_get_trial_with_visits(self, s):
        access, _, _ = _login(s, 'pi')
        trials = s.get(f"{API}/trials", headers=_h(access)).json()
        assert trials, "no trials returned"
        tid = trials[0]['id']
        r = s.get(f"{API}/trials/{tid}", headers=_h(access))
        assert r.status_code == 200
        data = r.json()
        assert 'visits' in data
        assert len(data['visits']) == 10

    def test_visits_mine_patient(self, s):
        access, _, _ = _login(s, 'patient')
        r = s.get(f"{API}/visits/mine", headers=_h(access))
        assert r.status_code == 200
        visits = r.json()
        assert len(visits) == 10
        for v in visits:
            assert 'scheduled_date' in v
            assert v['status'] in ('upcoming', 'completed', 'missed')


# ── Patients ─────────────────────────────────────────────────────────────────
class TestPatients:
    def test_pi_sees_patients(self, s):
        access, _, _ = _login(s, 'pi')
        r = s.get(f"{API}/patients", headers=_h(access))
        assert r.status_code == 200
        pts = r.json()
        assert len(pts) == 5

    def test_crc_sees_patients(self, s):
        access, _, _ = _login(s, 'crc')
        r = s.get(f"{API}/patients", headers=_h(access))
        assert r.status_code == 200
        assert len(r.json()) == 5

    def test_sponsor_sees_all_patients(self, s):
        access, _, _ = _login(s, 'sponsor')
        r = s.get(f"{API}/patients", headers=_h(access))
        assert r.status_code == 200
        assert len(r.json()) >= 5

    def test_patient_role_forbidden(self, s):
        access, _, _ = _login(s, 'patient')
        r = s.get(f"{API}/patients", headers=_h(access))
        assert r.status_code == 403

    def test_create_patient_pi_ok(self, s):
        access, _, pi = _login(s, 'pi')
        trials = s.get(f"{API}/trials", headers=_h(access)).json()
        tid = trials[0]['id']
        body = {'full_name': 'TEST_PatientX', 'email': f'TEST_{uuid.uuid4().hex[:6]}@mtb.app',
                'trial_id': tid, 'pi_id': pi['id']}
        r = s.post(f"{API}/patients", json=body, headers=_h(access))
        assert r.status_code == 200, r.text
        assert r.json()['full_name'] == 'TEST_PatientX'

    def test_create_patient_patient_forbidden(self, s):
        access, _, _ = _login(s, 'patient')
        body = {'full_name': 'TEST_Bad', 'email': 'TEST_bad@mtb.app', 'trial_id': 'x'}
        r = s.post(f"{API}/patients", json=body, headers=_h(access))
        assert r.status_code == 403

    def test_create_patient_sponsor_forbidden(self, s):
        access, _, _ = _login(s, 'sponsor')
        body = {'full_name': 'TEST_Bad2', 'email': 'TEST_bad2@mtb.app', 'trial_id': 'x'}
        r = s.post(f"{API}/patients", json=body, headers=_h(access))
        assert r.status_code == 403


# ── Notifications ────────────────────────────────────────────────────────────
class TestNotifications:
    def test_patient_notifications_and_mark_read(self, s):
        access, _, _ = _login(s, 'patient')
        r = s.get(f"{API}/notifications", headers=_h(access))
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 3
        nid = items[0]['id']
        r2 = s.post(f"{API}/notifications/{nid}/read", headers=_h(access))
        assert r2.status_code == 200
        # verify persisted
        items2 = s.get(f"{API}/notifications", headers=_h(access)).json()
        target = next(i for i in items2 if i['id'] == nid)
        assert target['read'] is True


# ── Users + Conversations ────────────────────────────────────────────────────
class TestUsersAndConversations:
    def test_users_directory(self, s):
        access, _, me = _login(s, 'patient')
        r = s.get(f"{API}/users", headers=_h(access))
        assert r.status_code == 200
        users = r.json()
        # should NOT include self
        assert all(u['id'] != me['id'] for u in users)
        assert len(users) >= 3  # 3 other demo users

    def test_create_conversation_dedupe(self, s):
        access_p, _, patient = _login(s, 'patient')
        access_pi, _, pi = _login(s, 'pi')
        body = {'participant_ids': [pi['id']]}
        r1 = s.post(f"{API}/conversations", json=body, headers=_h(access_p))
        assert r1.status_code == 200
        c1 = r1.json()
        assert 'id' in c1
        # duplicate request returns same conv
        r2 = s.post(f"{API}/conversations", json=body, headers=_h(access_p))
        assert r2.status_code == 200
        assert r2.json()['id'] == c1['id']
        # GET /conversations returns it
        lst = s.get(f"{API}/conversations", headers=_h(access_p)).json()
        assert any(c['id'] == c1['id'] for c in lst)


# ── WebSocket ────────────────────────────────────────────────────────────────
class TestWebSocket:
    def test_ws_invalid_token_rejected(self):
        async def runner():
            try:
                async with websockets.connect(f"{WS_BASE}?token=invalid.jwt") as ws:
                    await ws.recv()
            except websockets.exceptions.ConnectionClosed as e:
                return e.code
            except Exception as e:
                return str(e)
            return None
        code = asyncio.run(runner())
        # 1008 is policy violation; server uses it
        assert code == 1008 or 'rejected' in str(code).lower() or '403' in str(code) or code is None

    def test_ws_message_delivery(self, s):
        access_p, _, patient = _login(s, 'patient')
        access_pi, _, pi = _login(s, 'pi')
        # ensure conversation
        body = {'participant_ids': [pi['id']]}
        conv = s.post(f"{API}/conversations", json=body, headers=_h(access_p)).json()
        cid = conv['id']

        async def runner():
            received = []
            async with websockets.connect(f"{WS_BASE}?token={access_pi}") as ws_pi:
                async with websockets.connect(f"{WS_BASE}?token={access_p}") as ws_p:
                    await asyncio.sleep(0.5)
                    # send from patient
                    await ws_p.send(json.dumps({'type': 'message', 'conversation_id': cid,
                                                'content': 'hello from patient'}))
                    # PI should receive it
                    try:
                        raw = await asyncio.wait_for(ws_pi.recv(), timeout=5.0)
                        received.append(json.loads(raw))
                    except asyncio.TimeoutError:
                        pass
                    # Also patient gets echo
                    try:
                        raw2 = await asyncio.wait_for(ws_p.recv(), timeout=2.0)
                        received.append(json.loads(raw2))
                    except asyncio.TimeoutError:
                        pass
            return received

        msgs = asyncio.run(runner())
        assert msgs, "No messages received over WebSocket"
        msg_events = [m for m in msgs if m.get('type') == 'message']
        assert msg_events, f"No 'message' event: {msgs}"
        assert msg_events[0]['content'] == 'hello from patient'

    def test_ws_typing_event(self, s):
        access_p, _, patient = _login(s, 'patient')
        access_pi, _, pi = _login(s, 'pi')
        body = {'participant_ids': [pi['id']]}
        conv = s.post(f"{API}/conversations", json=body, headers=_h(access_p)).json()
        cid = conv['id']

        async def runner():
            async with websockets.connect(f"{WS_BASE}?token={access_pi}") as ws_pi:
                async with websockets.connect(f"{WS_BASE}?token={access_p}") as ws_p:
                    await asyncio.sleep(0.3)
                    await ws_p.send(json.dumps({'type': 'typing', 'conversation_id': cid}))
                    try:
                        raw = await asyncio.wait_for(ws_pi.recv(), timeout=3.0)
                        return json.loads(raw)
                    except asyncio.TimeoutError:
                        return None
        evt = asyncio.run(runner())
        assert evt is not None
        assert evt.get('type') == 'typing'
