from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ApplicabilityRule, ClaimEvidence, Event, Evidence, GenericDimension,
    PatientContext, PatientEventStatus, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.domain.schedule.validator import ScheduleValidator


def _schedule(dimension="SUBSTUDY", value="CARDIAC"):
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Cardiac substudy")
    event = Event(
        code="ECHO", protocol_label="Echo", display_name="Echocardiogram", event_type="ASSESSMENT",
        timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=7, unit="DAY"),
        ),
        applicability=[ApplicabilityRule(dimension=dimension, values=[value])],
        evidence_refs=[evidence.id],
    )
    claims = [ClaimEvidence(
        evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
        claim_entity_id=event.id, claim_path=path,
    ) for claim_type, path in (
        ("EVENT_NAME", "display_name"), ("TIMING", "timing"),
        ("APPLICABILITY", "applicability"),
    )]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.APPROVED),
        dimensions=[GenericDimension(
            dimension_type="SUBSTUDY", code="CARDIAC", protocol_label="Cardiac",
            display_name="Cardiac substudy",
        )],
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event], evidence=[evidence], claim_evidence=claims,
    )


def test_generic_substudy_dimension_controls_patient_applicability():
    schedule = _schedule()
    matching = PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"BASELINE": date(2026, 1, 1)},
        dimension_values={"SUBSTUDY": ["CARDIAC"]},
    )
    other = matching.model_copy(update={"dimension_values": {"SUBSTUDY": ["PK"]}})

    assert ScheduleEvaluator().evaluate(
        schedule, matching, horizon=date(2026, 12, 31),
    ).events[0].status == PatientEventStatus.RESOLVED
    assert ScheduleEvaluator().evaluate(
        schedule, other, horizon=date(2026, 12, 31),
    ).events[0].status == PatientEventStatus.NOT_APPLICABLE


def test_unknown_generic_dimension_is_blocking():
    schedule = _schedule(dimension="PERIOD", value="EXTENSION")

    assert any(
        issue.issue_code == "UNRESOLVED_REFERENCE" and "dimension" in issue.message
        for issue in ScheduleValidator().validate(schedule)
    )
