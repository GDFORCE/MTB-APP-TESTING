"""Event types, visit mode, and unscheduled visits (MTB requirement doc 10).

Doc 10 sections 13-15 and 31-33.

The unscheduled-visit rule is a negative one and that is the point: "Unscheduled
Visit - may occur at any time for suspected toxicity" is a DEFINITION. Treating it
as a dated visit produces a reminder for a visit nobody scheduled and an overdue
flag for a visit that was never required.

The visit-mode rule is likewise about not guessing: an unstated mode stays
unstated, because defaulting to a clinic visit tells a patient to travel.
"""

from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.deviation import assess_visit
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.validator import ScheduleValidator
from app.domain.schedule.models import (
    Activity, Anchor, ClaimEvidence, Event, Evidence, PatientContext,
    PatientEventStatus, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
    UnscheduledOccurrence,
)
from app.domain.schedule.timing import (
    AnchorReference, ProtocolDayTiming, ProtocolDefinedTiming,
)

BASELINE = date(2026, 9, 1)
HORIZON = date(2027, 12, 31)


def unscheduled_definition() -> Event:
    """The doc 10 s13 example, with the assessments it carries when it happens."""
    return Event(
        code="UNSCHEDULED_TOX", protocol_label="Unscheduled Visit",
        display_name="Unscheduled Visit",
        event_type="UNSCHEDULED", activation="ON_DEMAND",
        visit_mode="CLINIC",
        timing=ProtocolDefinedTiming(handler="on-demand"),
        activities=[
            Activity(code="VITALS", protocol_label="Vital signs",
                     display_name="Vital signs", activity_type="ASSESSMENT"),
            Activity(code="AE_REVIEW", protocol_label="AE review",
                     display_name="Adverse event review", activity_type="ASSESSMENT"),
        ],
    )


def scheduled_visit(code: str, day: int, **changes) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.title(),
        event_type="SITE_VISIT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=day),
        **changes,
    )


def approved(events: list[Event]) -> UniversalSchedule:
    evidence = Evidence(evidence_type="SECTION", page_number=8, source_text="Protocol rule")
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=event.id, claim_path=path, confidence=0.9,
            ))
        for activity in event.activities:
            activity.evidence_refs = [evidence.id]
            claims.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type="ACTIVITY",
                claim_entity_type="ACTIVITY", claim_entity_id=activity.id, confidence=0.9,
            ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=events, evidence=[evidence], claim_evidence=claims,
    )


def context(schedule: UniversalSchedule, **changes) -> PatientContext:
    values = {
        "patient_id": uuid4(), "schedule_version_id": schedule.schedule_version_id,
        "anchors": {"BASELINE": BASELINE},
    }
    values.update(changes)
    return PatientContext(**values)


def evaluate(schedule, ctx):
    return ScheduleEvaluator().evaluate(schedule, ctx, horizon=HORIZON)


def of(result, code):
    return [item for item in result.events if item.event_code == code]


# --- doc 10 s13-s15: a definition is not a due visit ----------------------------

def test_an_unscheduled_definition_is_available_but_never_due():
    schedule = approved([unscheduled_definition(), scheduled_visit("WEEK_4", 28)])
    items = of(evaluate(schedule, context(schedule)), "UNSCHEDULED_TOX")

    assert len(items) == 1
    assert items[0].status == PatientEventStatus.AVAILABLE_ON_DEMAND
    assert items[0].timing is None
    assert "clinically indicated" in items[0].explanation["reason"]


def test_an_unscheduled_definition_is_not_confused_with_an_unresolvable_one():
    """Its timing is PROTOCOL_DEFINED, but that is by design, not a review item."""
    schedule = approved([unscheduled_definition()])
    item = of(evaluate(schedule, context(schedule)), "UNSCHEDULED_TOX")[0]

    assert item.status != PatientEventStatus.UNRESOLVED
    assert item.status != PatientEventStatus.WAITING_FOR_ANCHOR
    assert item.explanation["on_demand"] is True


def test_an_unscheduled_visit_that_never_happened_is_never_a_deviation():
    """Doc 10 s15: it was not required, so it cannot be early, late, or missed."""
    schedule = approved([unscheduled_definition()])
    item = of(evaluate(schedule, context(schedule)), "UNSCHEDULED_TOX")[0]
    verdict = assess_visit(
        logical_key="UNSCHEDULED_TOX#0", event_code=item.event_code,
        display_name="Unscheduled Visit", status=item.status.value,
        planned_date=None, earliest_date=None, latest_date=None,
        actual_date=None, today=date(2027, 1, 1),
    )

    assert verdict.deviation_type.value == "NOT_ASSESSABLE"
    assert "never required" in (verdict.reason or "")


def test_creating_one_produces_a_dated_visit_carrying_its_activities():
    schedule = approved([unscheduled_definition()])
    ctx = context(schedule, unscheduled_occurrences=[UnscheduledOccurrence(
        event_code="UNSCHEDULED_TOX", occurred_on=date(2026, 10, 14),
        reason="Grade 3 rash reported by telephone",
    )])
    items = of(evaluate(schedule, ctx), "UNSCHEDULED_TOX")

    created = [item for item in items if item.status == PatientEventStatus.RESOLVED]
    assert len(created) == 1
    assert created[0].timing.nominal_start == date(2026, 10, 14)
    assert created[0].unscheduled_reason == "Grade 3 rash reported by telephone"
    assert {item.activity_code for item in created[0].activities} == {"VITALS", "AE_REVIEW"}


def test_the_definition_survives_alongside_the_visits_created_from_it():
    """A site can create a second one; the definition stays available."""
    schedule = approved([unscheduled_definition()])
    ctx = context(schedule, unscheduled_occurrences=[
        UnscheduledOccurrence(
            event_code="UNSCHEDULED_TOX", occurred_on=date(2026, 10, 14),
            reason="Grade 3 rash"),
        UnscheduledOccurrence(
            event_code="UNSCHEDULED_TOX", occurred_on=date(2026, 11, 2),
            reason="Recheck of liver enzymes"),
    ])
    items = of(evaluate(schedule, ctx), "UNSCHEDULED_TOX")

    statuses = [item.status for item in items]
    assert statuses.count(PatientEventStatus.AVAILABLE_ON_DEMAND) == 1
    assert statuses.count(PatientEventStatus.RESOLVED) == 2
    dated = [item for item in items if item.timing]
    assert [item.timing.nominal_start for item in dated] == [
        date(2026, 10, 14), date(2026, 11, 2)]
    assert [item.occurrence_index for item in dated] == [0, 1]


def test_one_patients_unscheduled_visit_does_not_reach_another_patient():
    """Creating a visit is patient data, not a protocol change."""
    schedule = approved([unscheduled_definition()])
    created = context(schedule, unscheduled_occurrences=[UnscheduledOccurrence(
        event_code="UNSCHEDULED_TOX", occurred_on=date(2026, 10, 14),
        reason="Grade 3 rash")])
    other = context(schedule)

    assert len(of(evaluate(schedule, created), "UNSCHEDULED_TOX")) == 2
    assert len(of(evaluate(schedule, other), "UNSCHEDULED_TOX")) == 1


# --- doc 10 s31-s33: visit mode is preserved, never guessed ---------------------

def test_a_stated_visit_mode_reaches_the_evaluated_visit():
    schedule = approved([scheduled_visit("WEEK_4", 28, visit_mode="TELEPHONE")])
    item = of(evaluate(schedule, context(schedule)), "WEEK_4")[0]

    assert item.visit_mode == "TELEPHONE"


def test_a_hybrid_visit_preserves_every_allowed_mode_and_defaults_to_none():
    """Doc 10 s31-s32: 'clinic or telephone' keeps both and picks neither."""
    schedule = approved([scheduled_visit(
        "WEEK_4", 28, allowed_visit_modes=["CLINIC", "TELEPHONE"])])
    item = of(evaluate(schedule, context(schedule)), "WEEK_4")[0]

    assert item.allowed_visit_modes == ["CLINIC", "TELEPHONE"]
    assert item.visit_mode is None


def test_an_unstated_mode_is_left_unstated():
    """Doc 10 s18 and s33: no default, because the default sends people to travel."""
    schedule = approved([scheduled_visit("WEEK_4", 28)])
    item = of(evaluate(schedule, context(schedule)), "WEEK_4")[0]

    assert item.visit_mode is None
    assert item.allowed_visit_modes == []


def test_the_mode_recorded_for_an_unscheduled_visit_overrides_the_definition():
    """A toxicity check the protocol expects in clinic can be done by telephone."""
    schedule = approved([unscheduled_definition()])
    ctx = context(schedule, unscheduled_occurrences=[UnscheduledOccurrence(
        event_code="UNSCHEDULED_TOX", occurred_on=date(2026, 10, 14),
        reason="Grade 3 rash", visit_mode="TELEPHONE")])
    created = next(
        item for item in of(evaluate(schedule, ctx), "UNSCHEDULED_TOX")
        if item.status == PatientEventStatus.RESOLVED
    )

    assert created.visit_mode == "TELEPHONE"


# --- doc 10 s18 and s32: the validator refuses to pick a mode for us ------------

def codes(schedule, *, blocking_only=False):
    issues = ScheduleValidator().validate(schedule)
    return {
        item.issue_code for item in issues
        if not blocking_only or item.blocking
    }


def test_a_mode_outside_the_permitted_set_blocks_approval():
    """Two readings disagree; choosing one silently misdirects a patient."""
    schedule = approved([scheduled_visit(
        "WEEK_4", 28, visit_mode="HOME_NURSE",
        allowed_visit_modes=["CLINIC", "TELEPHONE"])])

    assert "CONFLICTING_SOURCE" in codes(schedule, blocking_only=True)


def test_a_hybrid_visit_is_surfaced_for_selection_without_blocking_approval():
    """The protocol is legitimate; someone just has to choose per patient."""
    schedule = approved([scheduled_visit(
        "WEEK_4", 28, allowed_visit_modes=["CLINIC", "TELEPHONE"])])
    issues = ScheduleValidator().validate(schedule)
    finding = next(
        item for item in issues
        if item.issue_code == "UNRESOLVED_QUALIFIER"
        and "more than one mode" in item.message
    )

    assert finding.blocking is False
    assert finding.details["allowed_visit_modes"] == ["CLINIC", "TELEPHONE"]
    assert ScheduleValidator.blocking(issues) == []


def test_a_single_stated_mode_raises_nothing():
    schedule = approved([scheduled_visit("WEEK_4", 28, visit_mode="TELEPHONE")])
    assert ScheduleValidator.blocking(ScheduleValidator().validate(schedule)) == []


def test_an_on_demand_visit_with_a_calculated_date_is_contradictory():
    """It cannot be both 'occurs at any time' and 'Day 28'."""
    schedule = approved([scheduled_visit("WEEK_4", 28, activation="ON_DEMAND")])

    assert "CONFLICTING_SOURCE" in codes(schedule, blocking_only=True)


def test_an_on_demand_visit_without_a_date_does_not_block_approval():
    """Doc 10 s13: having no computable date is the requirement, not a failure."""
    schedule = approved([unscheduled_definition()])
    assert ScheduleValidator.blocking(ScheduleValidator().validate(schedule)) == []
