"""File upload/download/delete + storage abstraction — Task 5.1.

Covers: POST /api/files multipart upload (auth, 10 MB cap, type/extension +
magic-byte validation), GET /api/files/{id} scope-checked streaming download,
DELETE /api/files/{id} (owner/admin only). Storage is the local-disk backend by
default; the S3 path is unit-tested with moto when it is importable, else the
test is skipped (moto is NOT a hard dependency).

Same harness as test_visit_instances.py: in-process ASGITransport against the
real Atlas DB, RUN_ID-marked data, a single module-level event loop (Motor pins
its io_loop on first use), module teardown cleanup incl. the uploads/ blobs.
"""
import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402
import storage as storage_mod  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG_SPONSOR = f'FILEORG-{RUN_ID} Pharma'
ORG_SPONSOR_B = f'FILEORG-{RUN_ID} Rival'
ORG_SITE = f'FILEORG-{RUN_ID} Hospital'

LOOP = asyncio.new_event_loop()

_trial_ids = []
_file_ids = []
_keys = []

PDF_BYTES = b'%PDF-1.4\n' + b'round-trip payload ' * 8 + b'\n%%EOF'
PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None):
    email = f'file-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            'full_name': f'File {role.upper()} {RUN_ID}',
            'role': role, 'organization': org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


async def _make_trial(sponsor_headers):
    async with make_client() as cli:
        r = await cli.post('/api/trials', headers=sponsor_headers, json={
            'title': f'File Trial {RUN_ID}', 'protocol_id': f'FILE-{RUN_ID}-{uuid.uuid4().hex[:4]}',
            'phase': 'Phase II', 'condition': 'Testing', 'sponsor_name': ORG_SPONSOR,
        })
    assert r.status_code == 200, r.text
    trial = r.json()
    _trial_ids.append(trial['id'])
    return trial


async def _upload(headers, data, filename, content_type,
                  scope_type=None, scope_id=None):
    form = {}
    if scope_type is not None:
        form['scope_type'] = scope_type
    if scope_id is not None:
        form['scope_id'] = scope_id
    async with make_client() as cli:
        r = await cli.post('/api/files', headers=headers,
                           files={'file': (filename, data, content_type)},
                           data=form)
    if r.status_code == 200:
        j = r.json()
        _file_ids.append(j['id'])
    return r


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield

    async def clean():
        db = server.db
        # remove stored blobs for every file we created
        for f in await db.files.find({'id': {'$in': _file_ids}}, {'_id': 0, 'key': 1}).to_list(500):
            try:
                await storage_mod.LocalDiskStorage().delete(f['key'])
            except Exception:
                pass
        for k in _keys:
            try:
                await storage_mod.LocalDiskStorage().delete(k)
            except Exception:
                pass
        await db.files.delete_many({'id': {'$in': _file_ids}})
        await db.users.delete_many({'email': {'$regex': f'^file-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.patients.delete_many({'email': {'$regex': f'file-{RUN_ID}-'}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def sponsor():
    return run(_register('sponsor', org=ORG_SPONSOR))


@pytest.fixture(scope='module')
def sponsor_b():
    return run(_register('sponsor', org=ORG_SPONSOR_B))


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_SITE))


@pytest.fixture(scope='module')
def patient():
    return run(_register('patient'))


@pytest.fixture(scope='module')
def other_patient():
    return run(_register('patient'))


@pytest.fixture(scope='module')
def admin():
    # seeded admin account (Password1!) has the admin role
    async def login():
        async with make_client() as cli:
            r = await cli.post('/api/auth/login',
                               json={'email': 'admin@mtb.app', 'password': PASSWORD})
        assert r.status_code == 200, r.text
        return {'Authorization': f"Bearer {r.json()['access_token']}"}
    return run(login())


# ── Upload / download round-trip (local) ─────────────────────────────────────
class TestRoundTrip:
    def test_upload_then_download_returns_same_bytes(self, patient):
        _, headers = patient
        async def flow():
            r = await _upload(headers, PDF_BYTES, 'report.pdf', 'application/pdf')
            assert r.status_code == 200, r.text
            j = r.json()
            for key in ('id', 'name', 'size', 'content_type', 'url'):
                assert key in j, f'missing field {key}'
            assert j['name'] == 'report.pdf'
            assert j['size'] == len(PDF_BYTES)
            assert j['content_type'] == 'application/pdf'
            # local backend => served via the API, url() is None so the endpoint
            # hands back the API GET path
            assert j['url'] == f"/api/files/{j['id']}"
            async with make_client() as cli:
                d = await cli.get(f"/api/files/{j['id']}", headers=headers)
            assert d.status_code == 200, d.text
            assert d.content == PDF_BYTES
            assert d.headers['content-type'].startswith('application/pdf')
            # the db doc is well-formed
            doc = await server.db.files.find_one({'id': j['id']}, {'_id': 0})
            assert doc['owner_id'] == patient[0]['id']
            assert doc['scope'] == {'type': 'user', 'id': patient[0]['id']}
            uuid.UUID(doc['key'])
        run(flow())

    def test_png_round_trip(self, patient):
        _, headers = patient
        async def flow():
            r = await _upload(headers, PNG_BYTES, 'scan.png', 'image/png')
            assert r.status_code == 200, r.text
            async with make_client() as cli:
                d = await cli.get(f"/api/files/{r.json()['id']}", headers=headers)
            assert d.status_code == 200
            assert d.content == PNG_BYTES
        run(flow())

    def test_download_requires_auth(self, patient):
        _, headers = patient
        async def flow():
            r = await _upload(headers, PDF_BYTES, 'r.pdf', 'application/pdf')
            async with make_client() as cli:
                d = await cli.get(f"/api/files/{r.json()['id']}")   # no token
            assert d.status_code == 401
        run(flow())

    def test_missing_file_404(self, patient):
        _, headers = patient
        async def flow():
            async with make_client() as cli:
                d = await cli.get(f'/api/files/{uuid.uuid4()}', headers=headers)
            assert d.status_code == 404
        run(flow())


# ── Scope-checked download (no PHI leak) ─────────────────────────────────────
class TestScopeDenial:
    def test_user_scoped_file_denied_to_foreign_user(self, patient, other_patient):
        _, headers = patient
        _, foreign = other_patient
        async def flow():
            r = await _upload(headers, PDF_BYTES, 'private.pdf', 'application/pdf')
            fid = r.json()['id']
            async with make_client() as cli:
                mine = await cli.get(f'/api/files/{fid}', headers=headers)
                theirs = await cli.get(f'/api/files/{fid}', headers=foreign)
            assert mine.status_code == 200
            assert theirs.status_code == 403       # foreign scope → 403, no bytes
        run(flow())

    def test_trial_scoped_file_denied_to_foreign_sponsor(self, sponsor, sponsor_b):
        _, headers = sponsor
        _, foreign = sponsor_b
        async def flow():
            trial = await _make_trial(headers)
            r = await _upload(headers, PDF_BYTES, 'trial.pdf', 'application/pdf',
                              scope_type='trial', scope_id=trial['id'])
            assert r.status_code == 200, r.text
            fid = r.json()['id']
            async with make_client() as cli:
                owner = await cli.get(f'/api/files/{fid}', headers=headers)
                rival = await cli.get(f'/api/files/{fid}', headers=foreign)
            assert owner.status_code == 200        # owning-org sponsor
            assert rival.status_code == 403        # foreign org → 403
        run(flow())

    def test_admin_can_download_any_scope(self, patient, admin):
        _, headers = patient
        async def flow():
            r = await _upload(headers, PDF_BYTES, 'a.pdf', 'application/pdf')
            async with make_client() as cli:
                d = await cli.get(f"/api/files/{r.json()['id']}", headers=admin)
            assert d.status_code == 200
        run(flow())


# ── Validation: size + type/extension ────────────────────────────────────────
class TestValidation:
    def test_oversize_rejected(self, patient):
        _, headers = patient
        async def flow():
            big = b'%PDF-1.4\n' + b'0' * (10 * 1024 * 1024 + 1)
            r = await _upload(headers, big, 'big.pdf', 'application/pdf')
            assert r.status_code in (400, 413), r.text
        run(flow())

    def test_disallowed_extension_rejected(self, patient):
        _, headers = patient
        async def flow():
            r = await _upload(headers, b'hello world', 'note.txt', 'text/plain')
            assert r.status_code == 400, r.text
        run(flow())

    def test_content_type_extension_mismatch_rejected(self, patient):
        _, headers = patient
        async def flow():
            # .pdf extension but an executable/octet payload with a wrong magic
            r = await _upload(headers, b'MZ\x90\x00not a pdf', 'evil.pdf', 'application/pdf')
            assert r.status_code == 400, r.text
        run(flow())

    def test_empty_rejected(self, patient):
        _, headers = patient
        async def flow():
            r = await _upload(headers, b'', 'empty.pdf', 'application/pdf')
            assert r.status_code == 400, r.text
        run(flow())


# ── Delete authorization ─────────────────────────────────────────────────────
class TestDelete:
    def test_non_owner_denied_owner_ok(self, patient, other_patient):
        _, headers = patient
        _, foreign = other_patient
        async def flow():
            r = await _upload(headers, PDF_BYTES, 'del.pdf', 'application/pdf')
            fid = r.json()['id']
            key = (await server.db.files.find_one({'id': fid}, {'_id': 0, 'key': 1}))['key']
            async with make_client() as cli:
                bad = await cli.delete(f'/api/files/{fid}', headers=foreign)
                assert bad.status_code == 403           # non-owner → 403
                # still there
                still = await cli.get(f'/api/files/{fid}', headers=headers)
                assert still.status_code == 200
                ok = await cli.delete(f'/api/files/{fid}', headers=headers)
                assert ok.status_code in (200, 204)     # owner → ok
                gone = await cli.get(f'/api/files/{fid}', headers=headers)
                assert gone.status_code == 404
            # blob + doc removed
            assert await server.db.files.find_one({'id': fid}) is None
            assert not (storage_mod.LocalDiskStorage()._resolve(key)).exists()
            row = await server.db.audit_logs.find_one(
                {'action': 'file.delete', 'target_id': fid})
            assert row is not None, 'delete not audited'
        run(flow())

    def test_admin_can_delete(self, patient, admin):
        _, headers = patient
        async def flow():
            r = await _upload(headers, PDF_BYTES, 'adel.pdf', 'application/pdf')
            fid = r.json()['id']
            async with make_client() as cli:
                ok = await cli.delete(f'/api/files/{fid}', headers=admin)
            assert ok.status_code in (200, 204)
        run(flow())


# ── Storage unit tests ───────────────────────────────────────────────────────
class TestLocalStorage:
    def test_path_traversal_blocked(self):
        async def flow():
            st = storage_mod.LocalDiskStorage()
            for bad in ('../escape', '..\\escape', 'a/../../escape', '/etc/passwd'):
                with pytest.raises(ValueError):
                    await st.save(bad, b'x', 'application/pdf')
                with pytest.raises(ValueError):
                    await st.open(bad)
        run(flow())

    def test_save_open_delete_cycle(self):
        async def flow():
            st = storage_mod.LocalDiskStorage()
            key = uuid.uuid4().hex
            _keys.append(key)
            await st.save(key, b'blobdata', 'application/pdf')
            assert st.url(key) is None                 # local → served via API
            data, _ct = await st.open(key)
            assert data == b'blobdata'
            await st.delete(key)
            with pytest.raises(FileNotFoundError):
                await st.open(key)
        run(flow())

    def test_get_storage_defaults_local(self, monkeypatch):
        monkeypatch.delenv('STORAGE_BACKEND', raising=False)
        assert isinstance(storage_mod.get_storage(), storage_mod.LocalDiskStorage)
        monkeypatch.setenv('STORAGE_BACKEND', 'local')
        assert isinstance(storage_mod.get_storage(), storage_mod.LocalDiskStorage)


_HAS_MOTO = importlib.util.find_spec('moto') is not None


@pytest.mark.skipif(not _HAS_MOTO, reason='moto not installed — S3 path skipped')
class TestS3Storage:
    def test_s3_round_trip_with_moto(self, monkeypatch):
        import boto3
        from moto import mock_aws

        async def flow():
            with mock_aws():
                boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='mtb-test')
                monkeypatch.setenv('STORAGE_BACKEND', 's3')
                monkeypatch.setenv('S3_BUCKET', 'mtb-test')
                monkeypatch.setenv('AWS_REGION', 'us-east-1')
                monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'test')
                monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'test')
                st = storage_mod.get_storage()
                assert isinstance(st, storage_mod.S3Storage)
                key = uuid.uuid4().hex
                await st.save(key, b'cloudbytes', 'application/pdf')
                data, ct = await st.open(key)
                assert data == b'cloudbytes'
                assert ct == 'application/pdf'
                assert st.url(key).startswith('https://')   # presigned GET
                await st.delete(key)
        run(flow())
