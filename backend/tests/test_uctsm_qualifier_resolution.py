"""A footnote can be resolved, and only a reviewer can resolve it (doc s6).

Extraction refuses to interpret a table marker: it carries the marker, its text
and its evidence and marks it UNRESOLVED, which blocks approval. That half was
already right. The other half did not exist - no endpoint and no UI could ever
clear the block - so any protocol with a footnote produced a draft that could
never be approved.

These tests pin the workflow AND its refusals: a resolution that states no
meaning is rejected, an escalation stays blocking on purpose, and every outcome
is auditable.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.repositories import ScheduleRepository  # noqa: E402
from app.domain.schedule.exceptions import ImmutableScheduleError  # noqa: E402
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.services.canonical_bridge import import_schedule_definitions  # noqa: E402
from app.services.schedule_service import ScheduleReviewService  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402
import test_canonical_journey as journey  # noqa: E402

ORG = uuid4()
REVIEWER = journey.REVIEWER


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as active:
        yield active


def imported_with_footnote(session):
    """A schedule whose activity carries a footnote, as a real protocol would."""
    payload = journey.plan()
    payload["activities"][0]["conditional_text"] = (
        "Footnote a: haematology only if clinically indicated")
    _trial, rows = import_schedule_definitions(
        session, organization_id=ORG, actor_id=REVIEWER,
        external_trial_id="trial-q", protocol_number="ABC-123",
        study_title="A study",
        definitions=[journey.definition("sd-q", payload=payload)],
    )
    session.commit()
    return rows[0]


def all_qualifiers(session, version_id):
    """Every qualifier on the version.

    The fixture's activity template is referenced by two events, so it becomes
    two Activity rows and therefore two qualifiers - one per visit it governs.
    That is correct (a footnote applies per visit) and it is why "resolve the
    footnote" means resolving each place it lands.
    """
    schedule = ScheduleRepository(session).get(version_id)
    found = []
    for event in schedule.events:
        found.extend(event.qualifiers)
        for activity in event.activities:
            found.extend(activity.qualifiers)
    assert found, "the fixture should have produced at least one qualifier"
    return found


def only_qualifier(session, version_id):
    return all_qualifiers(session, version_id)[0]


def resolve_every_qualifier(session, version_id, **kwargs):
    service = ScheduleReviewService(session)
    for item in all_qualifiers(session, version_id):
        service.resolve_qualifier(version_id, item.id, reviewer_id=REVIEWER, **kwargs)
    session.commit()


def unresolved_count(session, version_id) -> int:
    schedule = ScheduleRepository(session).get(version_id)
    return len([
        item for item in ScheduleValidator().validate(schedule)
        if item.issue_code == "UNRESOLVED_QUALIFIER"
    ])


# --- the block exists, and it can now be cleared ------------------------------

def test_an_extracted_footnote_starts_unresolved_and_blocks(session):
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    assert qualifier.resolved is False
    assert qualifier.category.value in {"UNRESOLVED", "CONDITION"}
    assert unresolved_count(session, row.schedule_version_id) >= 1


def test_accepting_a_footnote_clears_the_block_and_records_the_decision(session):
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    resolve_every_qualifier(
        session, row.schedule_version_id, decision="ACCEPT", category="CONDITION",
        reason="Confirmed against the Schedule of Assessments, footnote a.",
    )

    assert unresolved_count(session, row.schedule_version_id) == 0
    after = only_qualifier(session, row.schedule_version_id)
    assert after.resolved is True

    decision = session.scalar(select(db.ReviewDecision).where(
        db.ReviewDecision.entity_type == "QUALIFIER"))
    assert decision is not None
    assert decision.decision == "ACCEPT"
    assert decision.reviewer_id == REVIEWER
    assert decision.previous_value["resolved"] is False
    assert decision.new_value["resolved"] is True
    assert "footnote a" in decision.reason.lower()

    audit = session.scalar(select(db.AuditEvent).where(
        db.AuditEvent.action == "QUALIFIER_RESOLVED"))
    assert audit is not None and audit.actor_id == REVIEWER
    assert audit.before["resolved"] is False and audit.after["resolved"] is True


def test_editing_replaces_the_reviewers_own_reading(session):
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    ScheduleReviewService(session).resolve_qualifier(
        row.schedule_version_id, qualifier.id, reviewer_id=REVIEWER,
        decision="EDIT", category="TIMING",
        text="Haematology is drawn pre-dose on dosing days only.",
        reason="The extracted wording lost the pre-dose restriction.",
    )
    session.commit()

    after = only_qualifier(session, row.schedule_version_id)
    assert after.resolved is True
    assert after.category.value == "TIMING"
    assert after.text == "Haematology is drawn pre-dose on dosing days only."


def test_marking_not_applicable_is_a_decision_not_a_deletion(session):
    """The footnote survives; what changes is the reviewer's ruling on it."""
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)
    original_text = qualifier.text

    resolve_every_qualifier(
        session, row.schedule_version_id, decision="NOT_APPLICABLE",
        reason="Footnote a governs the paediatric substudy, which this trial excludes.",
    )

    after = only_qualifier(session, row.schedule_version_id)
    assert after.resolved is True
    assert after.category.value == "NOT_APPLICABLE"
    assert after.text == original_text, "the protocol's own words are never discarded"
    assert unresolved_count(session, row.schedule_version_id) == 0


# --- the refusals -------------------------------------------------------------

def test_escalating_deliberately_keeps_the_block(session):
    """"I cannot answer this" must not become "approved"."""
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    ScheduleReviewService(session).resolve_qualifier(
        row.schedule_version_id, qualifier.id, reviewer_id=REVIEWER,
        decision="ESCALATE",
        reason="Referred to the sponsor; the footnote contradicts section 6.2.",
    )
    session.commit()

    assert only_qualifier(session, row.schedule_version_id).resolved is False
    assert unresolved_count(session, row.schedule_version_id) >= 1


def test_a_resolution_must_say_what_the_footnote_means(session):
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)
    service = ScheduleReviewService(session)

    # The import categorises a plain conditional_text footnote as CONDITION, so
    # force the genuinely uninterpreted case to prove the guard.
    schedule = ScheduleRepository(session).get(row.schedule_version_id)
    event = next(item for item in schedule.events
                 if any(q.id == qualifier.id for q in item.qualifiers)
                 or any(q.id == qualifier.id
                        for a in item.activities for q in a.qualifiers))
    for owner in [event, *event.activities]:
        for item in owner.qualifiers:
            if item.id == qualifier.id:
                item.category = item.category.__class__("UNRESOLVED")
    ScheduleRepository(session).replace_event(row.schedule_version_id, event)
    session.flush()

    with pytest.raises(ValueError, match="category other than UNRESOLVED"):
        service.resolve_qualifier(
            row.schedule_version_id, qualifier.id, reviewer_id=REVIEWER,
            decision="ACCEPT", reason="Looks fine.",
        )


def test_a_resolution_must_record_why(session):
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    with pytest.raises(ValueError, match="why it was made"):
        ScheduleReviewService(session).resolve_qualifier(
            row.schedule_version_id, qualifier.id, reviewer_id=REVIEWER,
            decision="ACCEPT", category="CONDITION", reason="   ",
        )


def test_an_unknown_decision_is_refused(session):
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    with pytest.raises(ValueError, match="decision must be one of"):
        ScheduleReviewService(session).resolve_qualifier(
            row.schedule_version_id, qualifier.id, reviewer_id=REVIEWER,
            decision="LOOKS_OK", reason="because",
        )


def test_an_approved_version_cannot_have_its_footnotes_rewritten(session):
    """Doc s18: an approved version is immutable, footnotes included."""
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)
    service = ScheduleReviewService(session)
    resolve_every_qualifier(
        session, row.schedule_version_id, decision="ACCEPT",
        category="CONDITION", reason="Confirmed.")
    journey.approve(session, row.schedule_version_id)

    with pytest.raises(ImmutableScheduleError):
        service.resolve_qualifier(
            row.schedule_version_id, qualifier.id, reviewer_id=REVIEWER,
            decision="EDIT", category="TIMING", text="changed after approval",
            reason="should not be possible")


# --- the whole point: approval becomes reachable -------------------------------

def test_a_protocol_with_a_footnote_can_now_be_approved_and_reach_a_patient(session):
    """Before this workflow existed, this journey was impossible to complete."""
    row = imported_with_footnote(session)
    qualifier = only_qualifier(session, row.schedule_version_id)

    resolve_every_qualifier(
        session, row.schedule_version_id, decision="ACCEPT", category="CONDITION",
        reason="Confirmed against footnote a on the Schedule of Assessments.")

    journey.approve(session, row.schedule_version_id)
    version = session.get(db.ScheduleVersion, row.schedule_version_id)

    assert version is not None and version.status == "APPROVED"
