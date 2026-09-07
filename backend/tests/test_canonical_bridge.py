"""Add Trial must produce canonical draft versions, once, per schedule definition."""

from pathlib import Path
import sys
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.services.canonical_bridge import import_schedule_definitions, plan_fingerprint

from test_canonical_import import evidence_fact, simple_plan


ORG = uuid4()
ACTOR = uuid4()


def definition(external_id: str, *, label: str = "", plan=None) -> dict:
    return {
        "id": external_id,
        "canonical_plan": plan if plan is not None else simple_plan(),
        "evidence_facts": [evidence_fact("e1", "Day 1")],
        "option_label": label,
        "file_name": "protocol.pdf",
    }


def session_factory() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def run(session, definitions, *, external_trial_id="trial-1"):
    return import_schedule_definitions(
        session, organization_id=ORG, actor_id=ACTOR,
        external_trial_id=external_trial_id, protocol_number="ABC-123",
        study_title="A study", definitions=definitions,
    )


def test_import_creates_a_reviewable_draft_linked_to_the_operational_trial():
    with session_factory() as session:
        trial, imported = run(session, [definition("sd-1", label="Primary")])
        session.commit()

        assert trial.external_trial_id == "trial-1"
        assert len(imported) == 1
        row = imported[0]
        assert row.created is True
        assert row.version_number == 1
        # Doc 10: an AI draft is never approved on arrival.
        assert row.status == "VALIDATION_REQUIRED"

        definition_row = session.get(db.ScheduleDefinition, row.schedule_definition_id)
        assert definition_row.external_schedule_definition_id == "sd-1"

        schedule = ScheduleRepository(session).get(row.schedule_version_id)
        assert [event.code for event in schedule.events] == [
            "SCREENING", "C1D1", "UNSCHEDULED_SAFETY_VISIT"]


def test_reimporting_the_same_extraction_reuses_the_draft_under_review():
    with session_factory() as session:
        _, first = run(session, [definition("sd-1")])
        session.commit()
        _, second = run(session, [definition("sd-1")])
        session.commit()

        assert second[0].created is False
        assert second[0].schedule_version_id == first[0].schedule_version_id
        assert session.scalar(select(db.ScheduleVersion).where(
            db.ScheduleVersion.id == first[0].schedule_version_id)) is not None
        assert len(list(session.scalars(select(db.ScheduleVersion)))) == 1


def test_each_substudy_schedule_becomes_its_own_canonical_definition():
    with session_factory() as session:
        _, imported = run(session, [
            definition("sd-1", label="Substudy A"),
            definition("sd-2", label="Substudy B"),
        ])
        session.commit()

        assert [item.name for item in imported] == ["Substudy A", "Substudy B"]
        assert len({item.schedule_definition_id for item in imported}) == 2


def test_definition_without_a_canonical_plan_is_skipped_not_imported_empty():
    with session_factory() as session:
        _, imported = run(session, [
            {"id": "sd-1", "canonical_plan": None},
            {"id": "sd-2", "canonical_plan": {"events": []}},
        ])
        session.commit()
        assert imported == []
        assert list(session.scalars(select(db.ScheduleVersion))) == []


def test_identical_plans_address_the_same_protocol_version():
    plan = simple_plan()
    assert plan_fingerprint(plan) == plan_fingerprint(simple_plan())
    changed = simple_plan()
    changed["events"][1]["name"] = "C1D2"
    assert plan_fingerprint(plan) != plan_fingerprint(changed)


def test_a_second_extraction_adds_a_version_without_replacing_the_one_in_use():
    """An amendment must not silently re-point the protocol's current version."""
    amended = simple_plan()
    amended["events"][1]["timing"] = {
        "kind": "offset", "offset": {"value": 2, "unit": "day"}, "source_label": "Day 2"}
    with session_factory() as session:
        _, first = run(session, [definition("sd-1")])
        session.commit()
        protocol = session.scalar(select(db.Protocol))
        pinned = protocol.current_version_id

        _, second = run(session, [definition("sd-2", plan=amended)])
        session.commit()

        assert second[0].schedule_version_id != first[0].schedule_version_id
        session.refresh(protocol)
        assert protocol.current_version_id == pinned
        assert len(list(session.scalars(select(db.ProtocolVersion)))) == 2


def test_version_table_lists_every_version_with_its_patient_count():
    """Doc 9 / Case 10: a superseded version stays visible with its patients."""
    amended = simple_plan()
    amended["events"][1]["name"] = "C1D1 (amended)"
    with session_factory() as session:
        from app.services.canonical_bridge import list_schedule_versions

        trial, first = run(session, [definition("sd-1", label="Primary")])
        session.commit()
        session.add(db.Patient(
            organization_id=ORG, trial_id=trial.id, patient_code="P-001",
            current_schedule_version_id=first[0].schedule_version_id,
        ))
        session.commit()

        run(session, [definition("sd-2", label="Primary", plan=amended)])
        session.commit()

        rows = list_schedule_versions(
            session, organization_id=ORG, external_trial_id="trial-1")
        by_version = {row.schedule_version_id: row for row in rows}
        assert len(rows) == 2
        assert by_version[first[0].schedule_version_id].patients == 1
        assert all(row.status == "VALIDATION_REQUIRED" for row in rows)
        assert sum(1 for row in rows if row.is_current_protocol_version) == 1


def test_version_table_is_empty_for_a_trial_with_no_canonical_side():
    with session_factory() as session:
        from app.services.canonical_bridge import list_schedule_versions

        assert list_schedule_versions(
            session, organization_id=ORG, external_trial_id="nope") == []
