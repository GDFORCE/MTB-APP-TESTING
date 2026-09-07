"""The cutover gate, end to end against the database.

Moving a trial's visit reads from the operational store to the engine changes
what a site sees for patients who are already enrolled. Almost every test here is
about the gate REFUSING, because a gate that opens when it should not is worse
than no gate: it carries the authority of having been checked.

The one asymmetry is deliberate and tested: moving BACK to the operational store
needs no gate at all. Reversing a cutover must never be harder than making one.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.parity import ParityVerdict
from app.domain.schedule.timing import (
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount, OffsetTiming,
    TemporalAmount, Window,
)
from app.services.parity_service import (
    READ_MODE_ENGINE, READ_MODE_LEGACY, ParityService, read_mode_for_trial,
)

BASELINE = date(2026, 9, 1)
HORIZON = date(2027, 6, 30)


def windowed(days: int, tolerance: int) -> NominalWindowTiming:
    return NominalWindowTiming(
        nominal=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=days, unit="DAY"),
        ),
        window=Window(
            before=NonNegativeTemporalAmount(value=tolerance, unit="DAY"),
            after=NonNegativeTemporalAmount(value=tolerance, unit="DAY"),
        ),
    )


def _schedule(protocol_version_id) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_ROW", page_number=3, source_text="SOA")
    events = [
        Event(code="SCREENING", protocol_label="V1", display_name="Screening",
              event_type="SITE_VISIT", timing=windowed(0, 3),
              evidence_refs=[evidence.id]),
        Event(code="WEEK_4", protocol_label="V2", display_name="Week 4",
              event_type="SITE_VISIT", timing=windowed(28, 2),
              evidence_refs=[evidence.id]),
    ]
    claims = [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path=path, confidence=1.0,
        )
        for event in events
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id,
            version_number=1, status=ScheduleStatus.APPROVED,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, evidence=[evidence], claim_evidence=claims,
    )


@pytest.fixture()
def fixture():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ids = {name: uuid4() for name in (
            "organization", "trial", "protocol", "protocol_version", "definition",
            "actor",
        )}
        session.add_all([
            db.Trial(id=ids["trial"], organization_id=ids["organization"],
                     external_trial_id="mongo-trial-1"),
            db.Protocol(id=ids["protocol"], trial_id=ids["trial"], protocol_number="P-1"),
            db.ProtocolVersion(
                id=ids["protocol_version"], protocol_id=ids["protocol"],
                version_label="1", document_name="protocol.pdf",
                document_uri="private://protocol.pdf", document_hash="a" * 64,
            ),
            db.ScheduleDefinition(
                id=ids["definition"], protocol_version_id=ids["protocol_version"],
                name="Primary",
            ),
        ])
        session.flush()
        schedule = _schedule(ids["protocol_version"])
        ScheduleRepository(session).persist_draft(
            schedule, schedule_definition_id=ids["definition"])
        patient = db.Patient(
            organization_id=ids["organization"], trial_id=ids["trial"],
            patient_code="P001", external_patient_id="mongo-patient-1",
            current_schedule_version_id=schedule.schedule_version_id,
        )
        session.add(patient)
        session.flush()
        session.add(db.PatientAnchor(
            patient_id=patient.id, anchor_definition_id=schedule.anchors[0].id,
            value_date=BASELINE, status="CONFIRMED",
        ))
        session.commit()
        yield session, ids, patient, schedule
    engine.dispose()


def matching_legacy_visits() -> list[dict[str, object]]:
    """What the operational store holds when both systems agree."""
    return [
        {"name": "Screening", "scheduled_date": "2026-09-01",
         "window_start": "2026-08-29", "window_end": "2026-09-04"},
        {"name": "Week 4", "scheduled_date": "2026-09-29",
         "window_start": "2026-09-27", "window_end": "2026-10-01"},
    ]


def check(session, ids, patient, legacy):
    return ParityService(session).compare_patient(
        patient.id, organization_id=ids["organization"], legacy_visits=legacy,
        horizon=HORIZON, actor_id=ids["actor"],
    )


def cutover(session, ids, mode=READ_MODE_ENGINE, reason="Parity proven"):
    return ParityService(session).set_read_mode(
        ids["trial"], organization_id=ids["organization"], mode=mode,
        actor_id=ids["actor"], reason=reason,
    )


# --- the gate refuses -------------------------------------------------------------

def test_a_trial_starts_reading_from_the_operational_store(fixture):
    session, ids, _patient, _schedule = fixture
    trial = session.get(db.Trial, ids["trial"])

    assert trial.schedule_read_mode == READ_MODE_LEGACY
    assert read_mode_for_trial(session, "mongo-trial-1") == READ_MODE_LEGACY


def test_cutover_is_refused_before_any_patient_has_been_compared(fixture):
    session, ids, _patient, _schedule = fixture

    with pytest.raises(ValueError, match="have not been compared"):
        cutover(session, ids)


def test_cutover_is_refused_while_a_difference_is_outstanding(fixture):
    session, ids, patient, _schedule = fixture
    report, _run = check(session, ids, patient, [
        {"name": "Screening", "scheduled_date": "2026-09-01",
         "window_start": "2026-08-29", "window_end": "2026-09-04"},
        # One day out. That is enough.
        {"name": "Week 4", "scheduled_date": "2026-09-30",
         "window_start": "2026-09-28", "window_end": "2026-10-02"},
    ])
    session.commit()

    assert report.passed is False
    with pytest.raises(ValueError, match="differences that need a decision"):
        cutover(session, ids)


def test_a_second_patient_nobody_checked_blocks_the_whole_trial(fixture):
    """Nine matching patients and one unchecked is not ready. The tenth is the
    one who ends up on the wrong day."""
    session, ids, patient, schedule = fixture
    check(session, ids, patient, matching_legacy_visits())
    other = db.Patient(
        organization_id=ids["organization"], trial_id=ids["trial"],
        patient_code="P002", current_schedule_version_id=schedule.schedule_version_id,
    )
    session.add(other)
    session.commit()

    status = ParityService(session).readiness(
        ids["trial"], organization_id=ids["organization"])
    assert status["ready"] is False
    assert "P002" in status["unchecked"]
    with pytest.raises(ValueError, match="have not been compared"):
        cutover(session, ids)


def test_a_cutover_needs_a_reason(fixture):
    session, ids, patient, _schedule = fixture
    check(session, ids, patient, matching_legacy_visits())
    session.commit()

    with pytest.raises(ValueError, match="needs a reason"):
        cutover(session, ids, reason="   ")


def test_an_unknown_read_mode_is_refused(fixture):
    session, ids, _patient, _schedule = fixture

    with pytest.raises(ValueError, match="read mode must be"):
        cutover(session, ids, mode="MAYBE")


# --- the gate opens ---------------------------------------------------------------

def test_a_matching_comparison_lets_the_trial_cut_over(fixture):
    session, ids, patient, _schedule = fixture
    report, run = check(session, ids, patient, matching_legacy_visits())
    session.commit()

    assert report.verdict == ParityVerdict.MATCH
    assert run is not None and run.verdict == "MATCH"

    result = cutover(session, ids)
    session.commit()

    assert result["read_mode"] == READ_MODE_ENGINE
    assert read_mode_for_trial(session, "mongo-trial-1") == READ_MODE_ENGINE


def test_the_evidence_survives_the_decision(fixture):
    """A cutover is a decision made on evidence; the evidence has to still exist."""
    session, ids, patient, _schedule = fixture
    check(session, ids, patient, matching_legacy_visits())
    session.commit()
    cutover(session, ids)
    session.commit()

    run = session.scalar(select(db.ParityRun).where(
        db.ParityRun.trial_id == ids["trial"]))
    assert run is not None
    assert run.compared == 2 and run.matched == 2
    audit = session.scalar(select(db.AuditEvent).where(
        db.AuditEvent.action == "OPERATIONAL_READ_MODE_CHANGED"))
    assert audit is not None
    assert audit.before["schedule_read_mode"] == READ_MODE_LEGACY
    assert audit.after["reason"] == "Parity proven"


def test_reversing_a_cutover_needs_no_gate(fixture):
    """If the engine turns out to disagree with reality, undo must be immediate."""
    session, ids, patient, _schedule = fixture
    check(session, ids, patient, matching_legacy_visits())
    session.commit()
    cutover(session, ids)
    session.commit()

    result = ParityService(session).set_read_mode(
        ids["trial"], organization_id=ids["organization"], mode=READ_MODE_LEGACY,
        actor_id=ids["actor"], reason="Site reported a wrong date",
    )
    session.commit()

    assert result["read_mode"] == READ_MODE_LEGACY
    assert read_mode_for_trial(session, "mongo-trial-1") == READ_MODE_LEGACY


# --- an amendment invalidates a previous approval ----------------------------------

def test_a_new_schedule_version_makes_an_earlier_parity_run_stop_counting(fixture):
    """A run against a superseded version proves nothing about the current one."""
    session, ids, patient, schedule = fixture
    check(session, ids, patient, matching_legacy_visits())
    session.commit()
    assert ParityService(session).readiness(
        ids["trial"], organization_id=ids["organization"])["ready"] is True

    amended = _schedule(ids["protocol_version"])
    amended.schedule_metadata.version_number = 2
    ScheduleRepository(session).persist_draft(
        amended, schedule_definition_id=ids["definition"])
    patient.current_schedule_version_id = amended.schedule_version_id
    session.commit()

    status = ParityService(session).readiness(
        ids["trial"], organization_id=ids["organization"])
    assert status["ready"] is False
    assert "P001" in status["unchecked"]


# --- an unlinked or unknown trial keeps today's behaviour ---------------------------

def test_an_unlinked_trial_reads_from_the_operational_store(fixture):
    """A caller that forgets to handle a new mode keeps the behaviour it has."""
    session, _ids, _patient, _schedule = fixture

    assert read_mode_for_trial(session, "not-a-linked-trial") == READ_MODE_LEGACY
    assert read_mode_for_trial(session, "") == READ_MODE_LEGACY


def test_a_patient_with_no_approved_version_is_not_comparable(fixture):
    session, ids, patient, _schedule = fixture
    patient.current_schedule_version_id = None
    session.commit()

    report, _run = check(session, ids, patient, matching_legacy_visits())

    assert report.verdict == ParityVerdict.NOT_COMPARABLE
    assert report.passed is False


# --- the read overlay: dates move, nothing else does --------------------------------

from app.services.operational_read import apply_engine_dates, unmatched_engine_visits


def instance(name: str, scheduled: str, **extra) -> dict:
    """An operational visit document, with the state the engine has no view of."""
    return {
        "id": f"vi-{name}", "patient_id": "p1", "name": name,
        "scheduled_date": scheduled,
        "window_start": "2026-09-27", "window_end": "2026-10-01",
        "status": "planned",
        "comments": [{"id": "c1", "text": "Patient rang to confirm"}],
        "clinical_tasks": [{"id": "t1", "done": True}],
        "completed_by_name": "A. Nurse",
        **extra,
    }


def engine_row(name: str, scheduled: str | None, **extra) -> dict:
    return {
        "name": name, "event_code": name.upper().replace(" ", "_"),
        "scheduled_date": scheduled,
        "window_start": "2026-09-28", "window_end": "2026-09-30",
        "status": "planned", "uctsm_logical_key": f"{name}:0",
        **extra,
    }


def test_the_overlay_replaces_dates_and_leaves_everything_else_alone():
    """A read must not be able to lose a site's comment thread."""
    result = apply_engine_dates(
        [instance("Week 4", "2026-09-29")], [engine_row("Week 4", "2026-10-05")])

    assert len(result) == 1
    row = result[0]
    assert row["scheduled_date"].date() == date(2026, 10, 5)
    assert row["window_start"].date() == date(2026, 9, 28)
    # Untouched operational state.
    assert row["id"] == "vi-Week 4"
    assert row["comments"] == [{"id": "c1", "text": "Patient rang to confirm"}]
    assert row["clinical_tasks"] == [{"id": "t1", "done": True}]
    assert row["completed_by_name"] == "A. Nurse"
    assert row["uctsm_source_of_truth"] is True


def test_an_instance_the_engine_does_not_produce_keeps_its_own_date():
    """Blanking it would drop a visit from a site's list on a disagreement."""
    result = apply_engine_dates(
        [instance("Ad-hoc safety check", "2026-10-12")],
        [engine_row("Week 4", "2026-09-29")],
    )

    assert result[0]["scheduled_date"] == "2026-10-12"
    assert result[0]["uctsm_source_of_truth"] is False
    assert "operational date" in result[0]["uctsm_read_note"]


def test_an_engine_visit_with_no_date_does_not_blank_the_operational_one():
    """"We cannot date this yet" must not read as "this visit has no date"."""
    result = apply_engine_dates(
        [instance("Post-op follow-up", "2026-10-12")],
        [engine_row("Post-op follow-up", None)],
    )

    assert result[0]["scheduled_date"] == "2026-10-12"
    assert result[0]["uctsm_source_of_truth"] is False
    assert "cannot date this visit yet" in result[0]["uctsm_read_note"]


def test_an_ambiguous_name_leaves_the_operational_dates_in_place():
    result = apply_engine_dates(
        [instance("Cycle Day 1", "2026-09-01")],
        [engine_row("Cycle Day 1", "2026-09-01"), engine_row("Cycle Day 1", "2026-09-22")],
    )

    assert result[0]["scheduled_date"] == "2026-09-01"
    assert result[0]["uctsm_source_of_truth"] is False


def test_the_overlay_never_creates_or_drops_a_visit():
    instances = [instance("Screening", "2026-09-01"), instance("Week 4", "2026-09-29")]
    result = apply_engine_dates(instances, [engine_row("Week 4", "2026-09-30")])

    assert [row["id"] for row in result] == [row["id"] for row in instances]


def test_an_engine_visit_with_no_document_is_reported_rather_than_invented():
    """Creating one on read would give it no workflow history and no audit trail."""
    missing = unmatched_engine_visits(
        [instance("Week 4", "2026-09-29")],
        [engine_row("Week 4", "2026-09-29"), engine_row("Week 8", "2026-10-27")],
    )

    assert missing == ["Week 8"]
