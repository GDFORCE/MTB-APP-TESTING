"""Recurrence of a condition, and when a schedule version takes effect.

Doc 2 s14 (RECURRED) and doc 9 s8 (effective date).

Both are about a distinction the system was collapsing:

  * a toxicity that came back is not the same clinical picture as one occurring
    for the first time, even though the protocol response is identical;
  * a version approved today is not necessarily in force today, and enrolling a
    patient onto an amendment that has not started yet puts them on a schedule
    the protocol says does not apply to them.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import ComparisonCondition, FieldOperand, LiteralOperand
from app.domain.schedule.conditional import (
    build_conditional_plan, condition_requires_attention,
)
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, ConditionState, ConditionalAction, ConditionalDefinition,
    Event, Evidence, PatientConditionStatus, PatientContext, ScheduleMetadata,
    ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, ProtocolDayTiming
from tests.test_uctsm_unscheduled_api import API, client  # noqa: F401

BASELINE = date(2026, 9, 1)


def anc_below(value: int) -> ComparisonCondition:
    return ComparisonCondition(
        operator="LT", left=FieldOperand(field="anc"), right=LiteralOperand(value=value),
    )


def schedule_with_conditional() -> UniversalSchedule:
    evidence = Evidence(evidence_type="SECTION", page_number=4, source_text="ANC rule")
    event = Event(
        code="REPEAT_CBC", protocol_label="Repeat CBC", display_name="Repeat CBC",
        event_type="ASSESSMENT",
        timing=ProtocolDayTiming(reference=AnchorReference(code="BASELINE"), day=1),
        evidence_refs=[evidence.id],
    )
    definition = ConditionalDefinition(
        code="ANC_LOW", protocol_label="ANC low", display_name="ANC below 1000",
        condition=anc_below(1000), evidence_refs=[evidence.id],
        actions=[ConditionalAction(
            action_type="ADD_EVENT", target_code="REPEAT_CBC", condition=anc_below(1000))],
        resolution_condition=ComparisonCondition(
            operator="GTE", left=FieldOperand(field="anc"),
            right=LiteralOperand(value=1000)),
    )
    claims = [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path=path, confidence=0.9,
        )
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ] + [
        ClaimEvidence(
            evidence_id=evidence.id, claim_type=claim_type,
            claim_entity_type="CONDITION", claim_entity_id=definition.id, confidence=0.9,
        )
        for claim_type in ("CONDITION", "CONDITIONAL_ACTION", "CONDITIONAL_RESOLUTION")
    ]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], conditional_definitions=[definition],
        evidence=[evidence], claim_evidence=claims,
    )


def context(schedule, **conditions) -> PatientContext:
    return PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"BASELINE": BASELINE}, conditions=conditions,
    )


# --- doc 2 s14: a recurrence is distinguishable but behaves the same -------------

def test_a_recurred_condition_generates_the_same_visits_as_a_first_occurrence():
    """The protocol response to low ANC does not change because it happened before."""
    schedule = schedule_with_conditional()
    first = build_conditional_plan(schedule, context(schedule, ANC_LOW=PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.ACTIVE,
        occurrence_date=date(2026, 9, 15))))
    again = build_conditional_plan(schedule, context(schedule, ANC_LOW=PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.RECURRED,
        occurrence_date=date(2026, 11, 2), occurrence_index=1)))

    assert first.is_active("REPEAT_CBC")
    assert again.is_active("REPEAT_CBC")


def test_a_recurrence_anchors_visits_on_its_own_date_not_the_first_occurrence():
    schedule = schedule_with_conditional()
    plan = build_conditional_plan(schedule, context(schedule, ANC_LOW=PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.RECURRED,
        occurrence_date=date(2026, 11, 2), occurrence_index=1)))

    assert plan.condition_anchors["ANC_LOW"] == date(2026, 11, 2)


def test_a_recurrence_is_a_distinct_state_not_merely_active_again():
    """A reviewer deciding on dose reduction needs to see that it came back."""
    status = PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.RECURRED,
        occurrence_date=date(2026, 11, 2), occurrence_index=1)

    assert status.state != ConditionState.ACTIVE
    assert status.state.value == "RECURRED"


def test_a_recurrence_at_index_zero_is_refused():
    """Index 0 is the first occurrence, which is ACTIVE by definition."""
    with pytest.raises(ValueError, match="occurrence index of the recurrence"):
        PatientConditionStatus(
            condition_code="ANC_LOW", state=ConditionState.RECURRED,
            occurrence_date=date(2026, 11, 2), occurrence_index=0)


def test_a_recurrence_still_requires_its_date():
    with pytest.raises(ValueError, match="occurrence date"):
        PatientConditionStatus(
            condition_code="ANC_LOW", state=ConditionState.RECURRED, occurrence_index=1)


def test_a_recurrence_still_needs_its_resolution_watched():
    schedule = schedule_with_conditional()
    definition = schedule.conditional_definitions[0]
    conditions = {"ANC_LOW": PatientConditionStatus(
        condition_code="ANC_LOW", state=ConditionState.RECURRED,
        occurrence_date=date(2026, 11, 2), occurrence_index=1)}

    assert condition_requires_attention(definition, conditions) is True


# --- doc 9 s8: approved is not the same as in force ------------------------------

def approve_and_enrol(client, api, effective_from: str | None):
    """Take the demo schedule to APPROVED with a given effective date."""
    workspace = client.post(f"{api}/demo/seed").json()
    version_id = workspace["schedule_version_id"]
    schedule = client.get(f"{api}/schedule-versions/{version_id}").json()
    client.post(f"{api}/schedule-versions/{version_id}/validate")
    client.post(f"{api}/schedule-versions/{version_id}/submit-review")
    for event in schedule["events"]:
        fields = ["display_name", "timing"]
        for name in ("conditions", "activities", "applicability", "recurrence"):
            if event.get(name):
                fields.append(name)
        for field_path in fields:
            client.post(f"{api}/schedule-versions/{version_id}/review-decisions", json={
                "decision": "CONFIRM", "entity_type": "EVENT",
                "entity_id": event["id"], "field_path": field_path,
                "comment": "Confirmed by the test.",
            })
    body = {"decision": "APPROVE", "comment": "reviewed"}
    if effective_from is not None:
        body["effective_from"] = effective_from
    approval = client.post(f"{api}/schedule-versions/{version_id}/review", json=body)
    assert approval.status_code == 200, approval.text
    return workspace


def test_a_version_with_no_effective_date_is_in_force_immediately(client):
    workspace = approve_and_enrol(client, API, None)
    listed = client.get(f"{API}/trials/{workspace['trial_id']}/approved-schedules").json()

    assert listed[0]["effective_from"] is None
    assert listed[0]["in_force"] is True


def test_a_future_effective_date_is_reported_as_not_yet_in_force(client):
    future = (date.today() + timedelta(days=30)).isoformat()
    workspace = approve_and_enrol(client, API, future)
    listed = client.get(f"{API}/trials/{workspace['trial_id']}/approved-schedules").json()

    assert listed[0]["effective_from"] == future
    assert listed[0]["in_force"] is False


def test_enrolment_refuses_a_version_that_has_not_taken_effect(client):
    """Otherwise a patient is put on an amendment the protocol says is not live."""
    future = (date.today() + timedelta(days=30)).isoformat()
    workspace = approve_and_enrol(client, API, future)

    response = client.post(f"{API}/trials/{workspace['trial_id']}/patients", json={
        "patient_code": "P-LATE",
    })

    assert response.status_code == 409
    assert "does not take effect until" in response.json()["detail"]
    assert future in response.json()["detail"]


def test_enrolment_works_once_the_version_is_in_force(client):
    past = (date.today() - timedelta(days=1)).isoformat()
    workspace = approve_and_enrol(client, API, past)

    response = client.post(f"{API}/trials/{workspace['trial_id']}/patients", json={
        "patient_code": "P-OK",
    })

    assert response.status_code == 201, response.text
