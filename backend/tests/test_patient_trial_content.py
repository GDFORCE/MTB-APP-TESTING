"""Patient-safe About Trial content and assigned contact mapping."""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG = f'TESTORG-{RUN_ID} Hospital'
IDS = {'users': [], 'trials': [], 'patients': []}


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url='http://testserver',
    )


async def register(role):
    async with make_client() as cli:
        response = await cli.post('/api/auth/register', json={
            'email': f'test-{RUN_ID}-{role}-{uuid.uuid4().hex[:5]}@example.com',
            'password': PASSWORD,
            'full_name': f'Content {role.upper()} {RUN_ID}',
            'role': role,
            'organization': ORG,
        })
    assert response.status_code == 200, response.text
    data = response.json()
    IDS['users'].append(data['user']['id'])
    return data['user'], {'Authorization': f"Bearer {data['access_token']}"}


def teardown_module():
    async def clean():
        await server.db.users.delete_many({'id': {'$in': IDS['users']}})
        await server.db.trials.delete_many({'id': {'$in': IDS['trials']}})
        await server.db.visits.delete_many({'trial_id': {'$in': IDS['trials']}})
        await server.db.patients.delete_many({'id': {'$in': IDS['patients']}})
        await server.db.visit_instances.delete_many({'trial_id': {'$in': IDS['trials']}})
        await server.db.organizations.delete_many({'name': ORG})
    run(clean())
    LOOP.close()


def test_patient_receives_structured_trial_content_schedule_and_contacts():
    async def flow():
        pi, pi_headers = await register('pi')
        crc, _ = await register('crc')
        patient_user, patient_headers = await register('patient')
        emergency = {
            'name': '24-hour study safety line',
            'phone': '+911800123456',
            'email': f'safety-{RUN_ID}@example.com',
            'instructions': 'Call for urgent study-related symptoms.',
        }
        async with make_client() as cli:
            trial_response = await cli.post('/api/trials', headers=pi_headers, json={
                'title': f'Patient-safe trial {RUN_ID}',
                'protocol_id': f'SAFE-{RUN_ID}',
                'phase': 'Phase II',
                'condition': 'Hypertension',
                'description': 'Approved patient-facing overview.',
                'drug': 'Study medicine',
                'duration': '12 weeks',
                'risks': ['Temporary injection-site discomfort'],
                'side_effects': ['Headache'],
                'emergency_contact': emergency,
            })
            assert trial_response.status_code == 200, trial_response.text
            trial = trial_response.json()
            IDS['trials'].append(trial['id'])
            visit_response = await cli.post('/api/visits', headers=pi_headers, json={
                'trial_id': trial['id'],
                'visit_number': 1,
                'name': 'Baseline',
                'day_offset': 2,
                'window_days': 1,
                'visit_type': 'On-site',
                'location': ORG,
                'activities': ['Vital signs'],
                'procedures': [{
                    'id': 'vitals',
                    'label': 'Vital signs',
                    'description': 'Blood pressure and pulse',
                }],
                'checklist': ['Bring your patient ID card'],
            })
            assert visit_response.status_code == 200, visit_response.text
            patient_response = await cli.post('/api/patients', headers=pi_headers, json={
                'full_name': patient_user['full_name'],
                'email': patient_user['email'],
                'trial_id': trial['id'],
                'pi_id': pi['id'],
                'crc_id': crc['id'],
            })
            assert patient_response.status_code == 200, patient_response.text
            patient = patient_response.json()
            IDS['patients'].append(patient['id'])
            await server.db.patients.update_one(
                {'id': patient['id']}, {'$set': {'user_id': patient_user['id']}})

            trials = await cli.get('/api/trials', headers=patient_headers)
            visits = await cli.get('/api/visits/mine', headers=patient_headers)
            recipients = await cli.get('/api/messaging/recipients', headers=patient_headers)

        assert trials.status_code == 200, trials.text
        patient_trial = next(row for row in trials.json() if row['id'] == trial['id'])
        assert patient_trial['risks'] == ['Temporary injection-site discomfort']
        assert patient_trial['side_effects'] == ['Headache']
        assert patient_trial['emergency_contact'] == emergency

        assert visits.status_code == 200, visits.text
        visit = visits.json()[0]
        assert visit['visit_type'] == 'On-site'
        assert visit['location'] == ORG
        assert visit['procedures'][0]['label'] == 'Vital signs'
        assert visit['checklist'] == ['Bring your patient ID card']
        assert visit['pi_id'] == pi['id']
        assert visit['crc_id'] == crc['id']

        assert recipients.status_code == 200, recipients.text
        recipient_ids = {row['id'] for row in recipients.json()}
        assert pi['id'] in recipient_ids
        assert crc['id'] in recipient_ids

    run(flow())

