"""Bulk field confirmation is honest about being bulk (doc s14).

"The current 'Confirm All' behavior must NOT be treated as equivalent to
field-level review... do not claim 'every field individually reviewed' when
the user only pressed one bulk button."

This does not remove the bulk action - a reviewer legitimately wants a fast
path. It makes the bulk path (a) require a stated reason, (b) write a comment
on every field decision that plainly says it was bulk-confirmed, and (c)
record ONE distinctly-named audit event (``SCHEDULE_BULK_CONFIRMED``) so the
audit trail never looks like N separate, individual reviews happened.
"""

from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.domain.schedule.exceptions import ImmutableScheduleError  # noqa: E402
from app.services.canonical_bridge import import_schedule_definitions  # noqa: E402
from app.services.schedule_service import ScheduleReviewService  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402
import test_canonical_journey as journey  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        yield active


def imported(session):
    _trial, rows = import_schedule_definitions(
        session, organization_id=uuid4(), actor_id=journey.REVIEWER,
        external_trial_id="trial-bulk", protocol_number="ABC-123",
        study_title="A study", definitions=[journey.definition("sd-bulk")],
    )
    session.commit()
    return rows[0]


def test_bulk_confirm_requires_a_reason(session):
    row = imported(session)
    with pytest.raises(ValueError, match="record why"):
        ScheduleReviewService(session).bulk_confirm_all_fields(
            row.schedule_version_id, reviewer_id=journey.REVIEWER, reason="  ")


def test_bulk_confirm_writes_field_decisions_labelled_as_bulk(session):
    row = imported(session)
    service = ScheduleReviewService(session)

    confirmed = service.bulk_confirm_all_fields(
        row.schedule_version_id, reviewer_id=journey.REVIEWER,
        reason="Sponsor pre-approved this extraction; confirming to unblock enrolment.")
    session.commit()

    decisions = list(session.scalars(select(db.ReviewDecision).where(
        db.ReviewDecision.schedule_version_id == row.schedule_version_id)))
    assert len(decisions) == confirmed > 0
    assert all(item.decision == "CONFIRM" for item in decisions)
    # Every single decision's own comment says plainly it was bulk, not
    # individually reviewed - this is the actual requirement, not merely
    # "an audit row exists somewhere".
    assert all("bulk" in (item.comment or "").lower() for item in decisions)
    assert all("not reviewed individually" in (item.comment or "").lower()
               for item in decisions)


def test_bulk_confirm_writes_one_distinct_audit_event(session):
    row = imported(session)
    service = ScheduleReviewService(session)

    service.bulk_confirm_all_fields(
        row.schedule_version_id, reviewer_id=journey.REVIEWER,
        reason="Reviewed the printed schedule table directly; confirming in bulk.")
    session.commit()

    bulk_events = list(session.scalars(select(db.AuditEvent).where(
        db.AuditEvent.action == "SCHEDULE_BULK_CONFIRMED")))
    assert len(bulk_events) == 1
    event = bulk_events[0]
    assert event.actor_id == journey.REVIEWER
    assert event.after["fields_confirmed"] > 0
    assert "confirming in bulk" in event.reason.lower()

    # Individual REVIEW_DECISION_RECORDED events must NOT ALSO fire for the
    # same fields - that would be the exact false impression the requirement
    # forbids: the audit trail showing both a bulk action AND N individual
    # reviews for the same work.
    individual_events = list(session.scalars(select(db.AuditEvent).where(
        db.AuditEvent.action == "REVIEW_DECISION_RECORDED")))
    assert not individual_events


def test_bulk_confirm_still_satisfies_the_approval_field_gate(session):
    """The fast path stays fast: approval's per-field check still passes."""
    row = imported(session)
    service = ScheduleReviewService(session)

    service.submit_for_review(row.schedule_version_id, actor_id=journey.REVIEWER)
    service.bulk_confirm_all_fields(
        row.schedule_version_id, reviewer_id=journey.REVIEWER,
        reason="Confirmed via the bulk action after reviewing the schedule table.")
    session.commit()

    service.approve(row.schedule_version_id, reviewer_id=journey.REVIEWER)
    version = session.get(db.ScheduleVersion, row.schedule_version_id)
    assert version.status == "APPROVED"


def test_bulk_confirm_is_refused_on_an_approved_version(session):
    row = imported(session)
    service = ScheduleReviewService(session)
    journey.approve(session, row.schedule_version_id)

    with pytest.raises(ImmutableScheduleError):
        service.bulk_confirm_all_fields(
            row.schedule_version_id, reviewer_id=journey.REVIEWER, reason="too late")
