from uuid import uuid4
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base
from app.db import models as db
from app.db.repositories import ScheduleRepository, unsupported_extension_handler_frequency
from app.domain.schedule.extension_handlers import register_timing_extension
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, OffsetTiming, ProtocolDefinedTiming, ProtocolExtension, TemporalAmount, UnresolvedTiming,
)
from app.domain.schedule.validator import ScheduleValidator
from app.services.schedule_service import ScheduleReviewService


def test_relational_schedule_round_trip_uses_typed_json_contracts():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    organization_id, trial_id, protocol_id = uuid4(), uuid4(), uuid4()
    protocol_version_id, definition_id = uuid4(), uuid4()
    with Session(engine) as session:
        trial = db.Trial(id=trial_id, organization_id=organization_id)
        protocol = db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="ABC-123")
        protocol_version = db.ProtocolVersion(
            id=protocol_version_id, protocol_id=protocol_id, version_label="1",
            document_name="protocol.pdf", document_uri="private://protocol.pdf", document_hash="a" * 64,
        )
        definition = db.ScheduleDefinition(
            id=definition_id, protocol_version_id=protocol_version_id, name="Primary",
        )
        session.add_all([trial, protocol, protocol_version, definition])
        session.flush()
        evidence = Evidence(evidence_type="TABLE_CELL", page_number=2, source_text="Day 30")
        event = Event(
            code="DAY_30", protocol_label="Day 30", display_name="Day 30", event_type="VISIT",
            timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=30, unit="DAY")),
            evidence_refs=[evidence.id],
        )
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(name="Primary", protocol_version_id=protocol_version_id),
            anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
            events=[event], evidence=[evidence],
            claim_evidence=[
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="display_name",
                ),
                ClaimEvidence(
                    evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                    claim_entity_id=event.id, claim_path="timing",
                ),
            ],
        )
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition_id)
        session.commit()

        loaded = ScheduleRepository(session).get(schedule.schedule_version_id)
        assert loaded.model_dump(exclude={"created_at"}) == schedule.model_dump(exclude={"created_at"})
        assert loaded.events[0].timing.type == "OFFSET"


def _unsupported_pattern_schedule(*, protocol_version_id, definition_id, handler_name, also_unrelated_issue=False):
    """A schedule whose event(s) name an unregistered PROTOCOL_DEFINED
    handler -- exactly what a protocol with an as-yet-unbuilt pattern
    produces. `also_unrelated_issue` adds a second, independent blocking issue
    unrelated to the named handler, to prove bulk revalidation only clears
    what the newly-registered handler actually fixes."""
    evidence = Evidence(evidence_type="TEXT", page_number=14, source_text="At least 21 days after Period I dosing")
    event = Event(
        code="PERIOD2_DOSE", protocol_label="Period II dosing", display_name="Period II dosing",
        event_type="VISIT", requires_review=True, evidence_refs=[evidence.id],
        timing=ProtocolDefinedTiming(
            handler=handler_name,
            extensions=[ProtocolExtension(type=handler_name, value={"note": "unbuilt pattern"})],
        ),
    )
    events = [event]
    if also_unrelated_issue:
        other_evidence = Evidence(evidence_type="TEXT", page_number=20, source_text="At each visit, as clinically indicated")
        other_event = Event(
            code="UNRELATED", protocol_label="Unrelated ambiguous visit", display_name="Unrelated ambiguous visit",
            event_type="VISIT", requires_review=True, evidence_refs=[other_evidence.id],
            timing=UnresolvedTiming(reason="No number and no anchor stated"),
        )
        events.append(other_event)
        evidence_list = [evidence, other_evidence]
    else:
        evidence_list = [evidence]
    claims = [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path="display_name",
        ),
        ClaimEvidence(
            evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path="timing",
        ),
    ]
    if also_unrelated_issue:
        claims += [
            ClaimEvidence(
                evidence_id=other_evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                claim_entity_id=other_event.id, claim_path="display_name",
            ),
            ClaimEvidence(
                evidence_id=other_evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                claim_entity_id=other_event.id, claim_path="timing",
            ),
        ]
    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="Primary", protocol_version_id=protocol_version_id, status=ScheduleStatus.DRAFT,
        ),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, evidence=evidence_list,
        claim_evidence=claims,
    )
    schedule.validation_issues = ScheduleValidator().validate(schedule)
    return schedule


def test_unsupported_handler_frequency_ranks_the_most_needed_missing_handler_first():
    """Proves the actual tracking mechanism: once real protocol extraction
    runs persist their validation issues (as every extraction already does via
    `persist_draft`), this single query tells you which missing deterministic
    handler to build next, ranked by how often it's actually blocking real
    schedules -- no separate tracking table or manual log review needed."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        trial = db.Trial(organization_id=uuid4())
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="RARE-1")
        session.add(protocol)
        session.flush()

        # Two different protocols both hit the frequent gap (WASHOUT); one
        # also independently hits a rarer, different unsupported pattern.
        for index, handler in enumerate(
            ["WASHOUT_INTERVAL", "WASHOUT_INTERVAL", "RARE_COHORT_SWITCH"],
        ):
            version = db.ProtocolVersion(
                protocol_id=protocol.id, version_label=str(index + 1),
                document_name=f"protocol-{index}.pdf", document_uri=f"private://protocol-{index}.pdf",
                document_hash=f"{index}" * 64,
            )
            session.add(version)
            session.flush()
            definition = db.ScheduleDefinition(protocol_version_id=version.id, name="Primary")
            session.add(definition)
            session.flush()
            schedule = _unsupported_pattern_schedule(
                protocol_version_id=version.id, definition_id=definition.id, handler_name=handler,
            )
            ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()

        ranking = unsupported_extension_handler_frequency(session)
        assert ranking[0]["handler"] == "WASHOUT_INTERVAL"
        assert ranking[0]["occurrence_count"] == 2
        assert ranking[0]["distinct_schedule_versions"] == 2
        assert ranking[1]["handler"] == "RARE_COHORT_SWITCH"
        assert ranking[1]["occurrence_count"] == 1


def test_resolved_or_registered_issues_drop_out_of_the_ranking():
    """Once a handler is built and the schedule revalidated (or a reviewer
    explicitly resolves the issue), it must disappear from the ranking --
    nothing to maintain or clean up by hand."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        trial = db.Trial(organization_id=uuid4())
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="RESOLVED-1")
        session.add(protocol)
        session.flush()
        version = db.ProtocolVersion(
            protocol_id=protocol.id, version_label="1", document_name="protocol.pdf",
            document_uri="private://protocol.pdf", document_hash="f" * 64,
        )
        session.add(version)
        session.flush()
        definition = db.ScheduleDefinition(protocol_version_id=version.id, name="Primary")
        session.add(definition)
        session.flush()
        schedule = _unsupported_pattern_schedule(
            protocol_version_id=version.id, definition_id=definition.id, handler_name="ONE_OFF_PATTERN",
        )
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()
        assert len(unsupported_extension_handler_frequency(session)) == 1

        session.execute(db.ValidationIssue.__table__.update().values(status="RESOLVED"))
        session.commit()
        assert unsupported_extension_handler_frequency(session) == []


def test_bulk_revalidation_processes_every_protocol_blocked_by_the_same_handler():
    """The full loop a new admin screen would trigger: a handler is built and
    registered, then ONE call processes every affected protocol -- clearing
    schedules that only had that one gap, while correctly leaving alone any
    schedule that still has a separate, unrelated blocking issue. No
    re-extraction, no re-calling the AI: only the deterministic re-check."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        trial = db.Trial(organization_id=uuid4())
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="BULK-1")
        session.add(protocol)
        session.flush()

        version_ids = {}
        for label, also_unrelated in (("clean", False), ("also-ambiguous", True)):
            version = db.ProtocolVersion(
                protocol_id=protocol.id, version_label=label, document_name=f"{label}.pdf",
                document_uri=f"private://{label}.pdf", document_hash=f"{label}".ljust(64, "0"),
            )
            session.add(version)
            session.flush()
            definition = db.ScheduleDefinition(protocol_version_id=version.id, name="Primary")
            session.add(definition)
            session.flush()
            schedule = _unsupported_pattern_schedule(
                protocol_version_id=version.id, definition_id=definition.id,
                handler_name="MINIMUM_GAP_AFTER_EVENT_V2", also_unrelated_issue=also_unrelated,
            )
            ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
            version_ids[label] = schedule.schedule_version_id
        session.commit()

        # Before the handler exists: both schedules are blocked by it.
        ranking = unsupported_extension_handler_frequency(session)
        assert next(r for r in ranking if r["handler"] == "MINIMUM_GAP_AFTER_EVENT_V2")["occurrence_count"] == 2

        # Engineering ships the handler (no schema/evaluator/validator change needed).
        register_timing_extension(
            "MINIMUM_GAP_AFTER_EVENT_V2",
            lambda expression, context: None,  # a real handler would resolve a date; irrelevant here
        )

        results = ScheduleReviewService(session).revalidate_for_handler("MINIMUM_GAP_AFTER_EVENT_V2")
        session.commit()

        by_version = {item["schedule_version_id"]: item for item in results}
        assert by_version[version_ids["clean"]]["fully_clear"] is True
        assert by_version[version_ids["also-ambiguous"]]["fully_clear"] is False
        assert by_version[version_ids["also-ambiguous"]]["remaining_blocking_issue_codes"] == ["AMBIGUOUS_TIMING"]

        # And the handler-frequency ranking reflects reality: nothing left
        # blocked by MINIMUM_GAP_AFTER_EVENT_V2, because it's installed now.
        ranking_after = unsupported_extension_handler_frequency(session)
        assert not any(r["handler"] == "MINIMUM_GAP_AFTER_EVENT_V2" for r in ranking_after)


def test_alert_payloads_direct_the_admin_with_real_impact_and_severity_and_never_duplicate():
    """Directive 1: the admin should receive a ready-to-act work item, not raw
    technical rows -- correctly severity-ranked by real impact, and never
    re-created for a handler that already has an open alert."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        trial = db.Trial(organization_id=uuid4())
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="ALERT-1")
        session.add(protocol)
        session.flush()

        # Five occurrences of the same gap -> "high" severity by real impact.
        for index in range(5):
            version = db.ProtocolVersion(
                protocol_id=protocol.id, version_label=f"v{index}", document_name=f"p{index}.pdf",
                document_uri=f"private://p{index}.pdf", document_hash=str(index) * 64,
            )
            session.add(version)
            session.flush()
            definition = db.ScheduleDefinition(protocol_version_id=version.id, name="Primary")
            session.add(definition)
            session.flush()
            schedule = _unsupported_pattern_schedule(
                protocol_version_id=version.id, definition_id=definition.id, handler_name="FREQUENT_GAP",
            )
            ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()

        from app.db.repositories import unsupported_pattern_alert_payloads

        payloads = unsupported_pattern_alert_payloads(session)
        assert len(payloads) == 1
        alert = payloads[0]
        assert alert["handler"] == "FREQUENT_GAP"
        assert alert["severity"] == "high"
        assert "5 issue(s)" in alert["affected"]
        assert alert["type"] == "unsupported_protocol_pattern"
        assert alert["status"] == "open"

        # Already-alerted handlers must never produce a duplicate ticket.
        assert unsupported_pattern_alert_payloads(session, already_alerted=frozenset({"FREQUENT_GAP"})) == []


def test_handler_evidence_returns_real_protocol_text_not_just_generic_messages():
    """The missing-detail-collection gap: a developer must see the actual
    protocol quotes behind a pattern, not just "handler X has no logic yet"."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        trial = db.Trial(organization_id=uuid4())
        session.add(trial)
        session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="EVIDENCE-1")
        session.add(protocol)
        session.flush()

        # Two protocols: one repeats the exact same quote (must deduplicate),
        # the other has genuinely different wording (must appear separately).
        quotes = [
            "At least 21 days after Period I dosing",
            "At least 21 days after Period I dosing",
            "Minimum 14-day interval required between doses",
        ]
        for index, quote in enumerate(quotes):
            version = db.ProtocolVersion(
                protocol_id=protocol.id, version_label=f"v{index}", document_name=f"e{index}.pdf",
                document_uri=f"private://e{index}.pdf", document_hash=f"e{index}".ljust(64, "0"),
            )
            session.add(version)
            session.flush()
            definition = db.ScheduleDefinition(protocol_version_id=version.id, name="Primary")
            session.add(definition)
            session.flush()
            evidence = Evidence(evidence_type="TEXT", page_number=10 + index, source_text=quote)
            event = Event(
                code="GAP_EVENT", protocol_label="Gap event", display_name="Gap event",
                event_type="VISIT", requires_review=True, evidence_refs=[evidence.id],
                timing=ProtocolDefinedTiming(
                    handler="EVIDENCE_TEST_HANDLER",
                    extensions=[ProtocolExtension(type="EVIDENCE_TEST_HANDLER", value={})],
                ),
            )
            schedule = UniversalSchedule(
                schedule_metadata=ScheduleMetadata(
                    name="Primary", protocol_version_id=version.id, status=ScheduleStatus.DRAFT,
                ),
                anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
                events=[event], evidence=[evidence],
                claim_evidence=[
                    ClaimEvidence(evidence_id=evidence.id, claim_type="EVENT_NAME", claim_entity_type="EVENT",
                                  claim_entity_id=event.id, claim_path="display_name"),
                    ClaimEvidence(evidence_id=evidence.id, claim_type="TIMING", claim_entity_type="EVENT",
                                  claim_entity_id=event.id, claim_path="timing"),
                ],
            )
            schedule.validation_issues = ScheduleValidator().validate(schedule)
            ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()

        from app.db.repositories import unsupported_handler_evidence

        found = unsupported_handler_evidence(session, "EVIDENCE_TEST_HANDLER")
        assert len(found) == 2  # deduplicated: 3 occurrences, 2 distinct quotes
        texts = {row["source_text"] for row in found}
        assert texts == {
            "At least 21 days after Period I dosing",
            "Minimum 14-day interval required between doses",
        }
