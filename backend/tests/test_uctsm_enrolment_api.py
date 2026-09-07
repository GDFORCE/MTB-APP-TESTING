"""Creating a trial, then a patient, through the canonical HTTP boundary.

Two things are checked that only show up at this layer: creating a trial must not
touch patient tables at all, and enrolling a patient must PIN the schedule version
they were enrolled under. Without that pinned row an amendment silently re-dates a
patient who is already on study, and nothing in the data would say it happened.
"""

from collections.abc import Generator
from datetime import date
from pathlib import Path
import sys
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.uctsm import create_uctsm_router
from app.db import models as db
from app.db.base import Base, get_session
from app.services.canonical_bridge import import_schedule_definitions
from app.services.schedule_service import ScheduleReviewService

from test_canonical_journey import definition, review_every_field

ORG = "00000000-0000-0000-0000-000000000002"
ACTOR = "00000000-0000-0000-0000-000000000001"


async def authenticated_user():
    return {"id": ACTOR, "organization_id": ORG}


def build_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(create_uctsm_router(authenticated_user))
    app.dependency_overrides[get_session] = session_override
    return TestClient(app), factory


def approved_trial(factory):
    """An operational trial imported, reviewed and approved on the canonical side."""
    with factory() as session:
        trial, rows = import_schedule_definitions(
            session, organization_id=UUID(ORG), actor_id=UUID(ACTOR),
            external_trial_id="trial-1", protocol_number="ABC-123",
            study_title="A study", definitions=[definition()],
        )
        session.commit()
        version_id = rows[0].schedule_version_id
        service = ScheduleReviewService(session)
        service.submit_for_review(version_id, actor_id=UUID(ACTOR))
        review_every_field(session, version_id, reviewer=UUID(ACTOR))
        service.approve(version_id, reviewer_id=UUID(ACTOR))
        session.commit()
        return str(trial.id), str(version_id)


def test_creating_a_trial_touches_no_patient_records():
    client, factory = build_client()

    response = client.post("/api/uctsm/trials", json={"study_title": "A study"})

    assert response.status_code == 201, response.text
    with factory() as session:
        assert list(session.scalars(select(db.PatientScheduleAssignment))) == []
        assert list(session.scalars(select(db.Patient))) == []


def test_enrolling_a_patient_pins_the_version_they_were_enrolled_under():
    client, factory = build_client()
    trial_id, version_id = approved_trial(factory)

    response = client.post(
        f"/api/uctsm/trials/{trial_id}/patients",
        json={"patient_code": "P-001"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["schedule_version_id"] == version_id
    with factory() as session:
        assignments = list(session.scalars(select(db.PatientScheduleAssignment)))
        assert len(assignments) == 1
        assert str(assignments[0].schedule_version_id) == version_id
        assert assignments[0].assignment_type == "ENROLMENT"


def test_a_version_not_yet_in_force_does_not_take_enrolments():
    """Doc 9 s8: approved today is not the same as governing patients today."""
    client, factory = build_client()
    trial_id, version_id = approved_trial(factory)
    with factory() as session:
        version = session.get(db.ScheduleVersion, UUID(version_id))
        version.effective_from = date(2999, 1, 1)
        session.commit()

    response = client.post(
        f"/api/uctsm/trials/{trial_id}/patients", json={"patient_code": "P-002"})

    assert response.status_code == 409
    assert "2999-01-01" in response.json()["detail"]
