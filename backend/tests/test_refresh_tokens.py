"""Refresh-token rotation, logout, and theft detection."""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx
import jwt

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url='http://testserver',
    )


def test_session_rotates_and_reuse_revokes_family():
    user_id = str(uuid.uuid4())
    email = f'remember-{RUN_ID}@example.com'

    async def flow():
        await server.db.users.insert_one({
            'id': user_id,
            'email': email,
            'full_name': 'Refresh Token Test',
            'role': 'pi',
            'phone': '',
            'organization': f'Remember Org {RUN_ID}',
            'hashed_password': server.pwd_ctx.hash(PASSWORD),
            'created_at': server.now(),
        })
        try:
            async with make_client() as cli:
                login = await cli.post('/api/auth/login', json={
                    'email': email,
                    'password': PASSWORD,
                })
                assert login.status_code == 200, login.text
                first = login.json()['refresh_token']
                assert first.count('.') == 0, 'refresh token must be opaque, not a JWT'
                access_claims = jwt.decode(
                    login.json()['access_token'], server.JWT_SECRET,
                    algorithms=[server.ALGO])
                assert 850 <= access_claims['exp'] - access_claims['iat'] <= 950

                first_doc = await server.db.refresh_tokens.find_one({
                    'token_hash': server._refresh_token_hash(first)})
                assert first_doc['status'] == 'active'
                assert 29 <= (first_doc['expires_at'] - first_doc['created_at']).days <= 30

                rotated = await cli.post('/api/auth/refresh', json={
                    'refresh_token': first})
                assert rotated.status_code == 200, rotated.text
                second = rotated.json()['refresh_token']
                assert second != first
                consumed = await server.db.refresh_tokens.find_one({
                    'token_hash': server._refresh_token_hash(first)})
                assert consumed['status'] == 'consumed'

                reuse = await cli.post('/api/auth/refresh', json={
                    'refresh_token': first})
                assert reuse.status_code == 401, reuse.text
                revoked = await cli.post('/api/auth/refresh', json={
                    'refresh_token': second})
                assert revoked.status_code == 401, revoked.text

            event = await server.db.audit_logs.find_one({
                'user_id': user_id,
                'action': 'auth.refresh_reuse_detected',
            })
            assert event and event['status'] == 'failure'
        finally:
            await server.db.refresh_tokens.delete_many({'user_id': user_id})
            await server.db.audit_logs.delete_many({'user_id': user_id})
            await server.db.users.delete_one({'id': user_id})

    run(flow())


def test_logout_revokes_refresh_family():
    user_id = str(uuid.uuid4())
    email = f'logout-{RUN_ID}@example.com'

    async def flow():
        await server.db.users.insert_one({
            'id': user_id,
            'email': email,
            'full_name': 'Logout Test',
            'role': 'patient',
            'phone': '',
            'organization': '',
            'hashed_password': server.pwd_ctx.hash(PASSWORD),
            'created_at': server.now(),
        })
        try:
            async with make_client() as cli:
                login = await cli.post('/api/auth/login', json={
                    'email': email, 'password': PASSWORD})
                token = login.json()['refresh_token']
                logged_out = await cli.post('/api/auth/logout', json={
                    'refresh_token': token})
                assert logged_out.status_code == 200, logged_out.text
                refresh = await cli.post('/api/auth/refresh', json={
                    'refresh_token': token})
                assert refresh.status_code == 401, refresh.text
                token_doc = await server.db.refresh_tokens.find_one({
                    'token_hash': server._refresh_token_hash(token)})
                assert token_doc['status'] == 'revoked'
        finally:
            await server.db.refresh_tokens.delete_many({'user_id': user_id})
            await server.db.audit_logs.delete_many({'user_id': user_id})
            await server.db.users.delete_one({'id': user_id})

    run(flow())
