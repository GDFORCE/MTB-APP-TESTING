"""The canonical schedule survives the API boundary.

Covers the contract the mobile editor and reviewers depend on: procedure-level
timing stays out of the visit-tolerance field, operational constraints round-trip
through visit create/update, and the immutable canonical draft is persisted and
readable through GET /trials/{trial_id}/schedule-definition.
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import protocol_extraction as pe  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
ORG = f"SCHEDDEF-{RUN_ID}"
PASSWORD = "Password1!"
LOOP = asyncio.new_event_loop()
USER_IDS: list[str] = []
TRIAL_IDS: list[str] = []


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


@pytest.fixture(scope="module")
def headers():
    async def build():
        async with client() as cli:
            response = await cli.post("/api/auth/register", json={
                "email": f"scheddef-{RUN_ID}@example.com",
                "password": PASSWORD,
                "full_name": "Schedule Sponsor",
                "role": "sponsor",
                "organization": ORG,
            })
        assert response.status_code == 200, response.text
        USER_IDS.append(response.json()["user"]["id"])
        return {"Authorization": f"Bearer {response.json()['access_token']}"}
    return run(build())


@pytest.fixture(scope="module")
def trial_id(headers):
    async def build():
        async with client() as cli:
            response = await cli.post("/api/trials", headers=headers, json={
                "title": "Canonical schedule trial",
                "protocol_id": f"SCHEDDEF-{RUN_ID}",
                "phase": "Phase II",
                "condition": "Oncology",
            })
        assert response.status_code == 200, response.text
        TRIAL_IDS.append(response.json()["id"])
        return response.json()["id"]
    return run(build())


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield

    async def clean():
        await server.db.visits.delete_many({"trial_id": {"$in": TRIAL_IDS}})
        await server.db.schedule_definitions.delete_many(
            {"trial_id": {"$in": TRIAL_IDS}})
        await server.db.trials.delete_many({"id": {"$in": TRIAL_IDS}})
        await server.db.users.delete_many({"id": {"$in": USER_IDS}})
        await server.db.organizations.delete_many({"name": ORG})
        await server.db.audit_logs.delete_many({"actor_id": {"$in": USER_IDS}})
    run(clean())
    LOOP.close()


# A PK visit whose ECG must happen 30 minutes pre-dose and whose infusion runs
# for two hours. None of that is a visit-level +/- tolerance.
CANONICAL_PAYLOAD = {
    "schedule_kind": "linear",
    "anchor_study_day": 1,
    "canonical_plan": {
        "anchors": [{
            "id": "anchor-baseline", "name": "First dose",
            "anchor_type": "first_dose", "evidence_ids": ["timing-01"],
        }],
        "activities": [
            {
                "id": "activity-ecg", "name": "12-lead ECG",
                "timing": {
                    "kind": "offset", "anchor_id": "anchor-baseline",
                    "offset": {"value": 30, "unit": "minute"},
                    "relation": "before", "source_label": "30 minutes pre-dose",
                    "evidence_ids": ["timing-02"],
                },
                "window": {
                    "scope": "activity", "state": "stated",
                    "early": {"value": 10, "unit": "minute"},
                    "late": {"value": 10, "unit": "minute"},
                    "source_label": "±10 minutes", "evidence_ids": ["window-02"],
                },
                "evidence_ids": ["activity-01"],
            },
            {
                "id": "activity-infusion", "name": "Study drug infusion",
                "operational_constraints": ["Infuse over 2 hours"],
                "evidence_ids": ["activity-02"],
            },
        ],
        "events": [{
            "id": "event-c1d1", "name": "Cycle 1 Day 1",
            "event_type": "Treatment",
            "timing": {
                "kind": "offset", "anchor_id": "anchor-baseline",
                "offset": {"value": 0, "unit": "day"},
                "source_label": "Day 1", "evidence_ids": ["timing-01"],
            },
            "window": {
                "state": "stated",
                "early": {"value": 1, "unit": "day"},
                "late": {"value": 3, "unit": "day"},
                "source_label": "-1/+3 days", "evidence_ids": ["window-01"],
            },
            "activity_ids": ["activity-ecg", "activity-infusion"],
            "operational_constraints": ["Overnight housing required"],
            "evidence_ids": ["visit-01"],
        }],
    },
    "evidence_facts": [
        {
            "evidence_id": evidence_id, "claim": claim,
            "source_location": "Schedule of Assessments, page 42",
            "source_quote": claim, "confidence": 0.99,
        }
        for evidence_id, claim in (
            ("timing-01", "Cycle 1 Day 1 is the first dose day"),
            ("timing-02", "ECG 30 minutes pre-dose"),
            ("window-01", "-1/+3 days"),
            ("window-02", "±10 minutes"),
            ("visit-01", "Cycle 1 Day 1"),
            ("activity-01", "12-lead ECG"),
            ("activity-02", "Study drug infusion"),
        )
    ],
    "verification_status": "verified",
    "verification_confidence": 0.97,
    "verification_scores": {"overall_schedule": 0.97},
}


def _extracted_schedule() -> pe.ExtractedSchedule:
    return pe.expand_schedule(
        pe.ExtractedSchedule.model_validate(CANONICAL_PAYLOAD))


@pytest.fixture(scope="module")
def extracted(headers, trial_id):
    """Run extraction once; the persistence assertions all read this result."""
    async def exercise():
        async with client() as cli:
            return await cli.post(
                f"/api/trials/{trial_id}/extract-schedule",
                headers=headers,
                files={"file": ("protocol.pdf", b"%PDF-test", "application/pdf")},
            )

    class _Extractor:
        async def extract(self, data):
            return _extracted_schedule()

        async def extract_all(self, data):
            return [(None, _extracted_schedule())]

    original = pe.get_extractor
    pe.get_extractor = lambda: _Extractor()
    try:
        response = run(exercise())
    finally:
        pe.get_extractor = original
    assert response.status_code == 200, response.text
    return response.json()


def test_procedure_timing_never_becomes_a_visit_window(extracted):
    visit = extracted["visits"][0]
    # The visit's own asymmetric tolerance is the only thing in the window
    # fields; the 30-minute pre-dose ECG rule must not leak into them.
    assert visit["window_before"] == 1
    assert visit["window_after"] == 3
    assert visit["window_days"] is None
    procedures = {item["name"]: item for item in visit["procedures"]}
    assert procedures["12-lead ECG"]["timing"] == "30 minutes pre-dose"
    assert procedures["12-lead ECG"]["window"] == "±10 minutes"
    assert procedures["Study drug infusion"]["constraints"] == ["Infuse over 2 hours"]


def test_operational_constraints_are_exposed_separately(extracted):
    constraints = extracted["visits"][0]["operational_constraints"]
    assert "Overnight housing required" in constraints
    assert any("Infuse over 2 hours" in item for item in constraints)
    assert any("30 minutes pre-dose" in item for item in constraints)
    assert extracted["visits"][0]["canonical_event_id"] == "event-c1d1"


def test_extraction_response_carries_the_canonical_plan(extracted):
    assert extracted["schema_version"] == "2.0"
    assert extracted["schedule_definition_id"]
    plan = extracted["canonical_plan"]
    assert [item["id"] for item in plan["events"]] == ["event-c1d1"]
    assert {item["id"] for item in plan["activities"]} == {
        "activity-ecg", "activity-infusion"}
    assert extracted["canonical_validation"] == []
    assert extracted["verification"]["status"] == "verified"


def test_canonical_draft_is_persisted_and_readable(headers, trial_id, extracted):
    async def exercise():
        async with client() as cli:
            return await cli.get(
                f"/api/trials/{trial_id}/schedule-definition", headers=headers)

    response = run(exercise())
    assert response.status_code == 200, response.text
    definition = response.json()
    assert definition["id"] == extracted["schedule_definition_id"]
    assert definition["trial_id"] == trial_id
    assert definition["schema_version"] == "2.0"
    assert definition["status"] == "draft_review"
    assert definition["canonical_plan"]["events"][0]["id"] == "event-c1d1"
    assert {item["evidence_id"] for item in definition["evidence_facts"]} >= {
        "timing-01", "window-01", "activity-01"}
    assert definition["compatibility_visits"][0]["canonical_event_id"] == "event-c1d1"
    assert definition["verification"]["status"] == "verified"


def test_repeated_extraction_of_one_source_reuses_its_definition(
    headers, trial_id, extracted,
):
    """The AI draft is immutable: re-consuming a source must not fork it."""
    async def exercise():
        definition_id = await server._persist_schedule_definition(
            trial_id, _extracted_schedule(),
            {"id": USER_IDS[0]},
            source_extraction_id="stable-source-1",
        )
        again = await server._persist_schedule_definition(
            trial_id, _extracted_schedule(),
            {"id": USER_IDS[0]},
            source_extraction_id="stable-source-1",
        )
        count = await server.db.schedule_definitions.count_documents(
            {"trial_id": trial_id, "source_extraction_id": "stable-source-1"})
        return definition_id, again, count

    definition_id, again, count = run(exercise())
    assert definition_id == again
    assert count == 1


def test_visit_create_and_update_round_trip_operational_constraints(
    headers, trial_id, extracted,
):
    async def exercise():
        row = extracted["visits"][0]
        async with client() as cli:
            created = await cli.post("/api/visits", headers=headers, json={
                "trial_id": trial_id,
                "visit_number": 1,
                "name": row["name"],
                "day_offset": row["day_offset"],
                "window_before": row["window_before"],
                "window_after": row["window_after"],
                "activities": row["activities"],
                "procedures": row["procedures"],
                "operational_constraints": row["operational_constraints"],
                "visit_type": row["visit_type"],
                "extracted_from_protocol": True,
            })
            assert created.status_code == 200, created.text
            visit_id = created.json()["id"]
            updated = await cli.put(
                f"/api/visits/{visit_id}", headers=headers, json={
                    "operational_constraints": [
                        "Overnight housing required",
                        "Minimum 21 days since the previous dose",
                    ],
                })
        return created.json(), updated

    created, updated = run(exercise())
    assert "Overnight housing required" in created["operational_constraints"]
    assert any(item["name"] == "12-lead ECG" for item in created["procedures"])
    assert created["window_before"] == 1 and created["window_after"] == 3
    assert updated.status_code == 200, updated.text
    assert updated.json()["operational_constraints"] == [
        "Overnight housing required",
        "Minimum 21 days since the previous dose",
    ]


def test_schedule_definition_requires_trial_access(headers):
    async def exercise():
        async with client() as cli:
            return await cli.get(
                f"/api/trials/missing-{RUN_ID}/schedule-definition", headers=headers)

    response = run(exercise())
    assert response.status_code == 404
