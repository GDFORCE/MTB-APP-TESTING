"""Run a safe, self-contained UCTSM scheduling demonstration.

This uses no database, LLM, protocol upload, or existing patient data. It exercises
the same typed validator and deterministic evaluator used by the API.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.condition import (  # noqa: E402
    ComparisonCondition, FieldOperand, LiteralOperand,
)
from app.domain.schedule.evaluator import ScheduleEvaluator  # noqa: E402
from app.domain.schedule.models import (  # noqa: E402
    Anchor, ClaimEvidence, Event, Evidence, PatientContext,
    ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (  # noqa: E402
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount,
    OffsetTiming, PositiveTemporalAmount, TemporalAmount, TriggeredTiming,
    TriggerWithin, Window,
)
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402


def build_schedule() -> UniversalSchedule:
    evidence = Evidence(
        evidence_type="DEMO_PROTOCOL_TEXT", page_number=84,
        source_text=(
            "Safety follow-up occurs 30 days after last dose, ±5 days. "
            "Following disease progression, perform assessment within 7 days."
        ),
    )
    safety = Event(
        code="SAFETY_FOLLOW_UP", protocol_label="Safety Follow-up",
        display_name="Safety Follow-up", event_type="SAFETY_ASSESSMENT",
        timing=NominalWindowTiming(
            nominal=OffsetTiming(
                reference=AnchorReference(code="LAST_DOSE"),
                offset=TemporalAmount(value=30, unit="DAY"),
            ),
            window=Window(
                before=NonNegativeTemporalAmount(value=5, unit="DAY"),
                after=NonNegativeTemporalAmount(value=5, unit="DAY"),
            ),
        ),
        evidence_refs=[evidence.id],
    )
    progression = Event(
        code="PROGRESSION_ASSESSMENT", protocol_label="Progression Assessment",
        display_name="Progression Assessment", event_type="ASSESSMENT",
        timing=TriggeredTiming(
            trigger=AnchorReference(code="PROGRESSION"),
            timing_after_trigger=TriggerWithin(
                duration=PositiveTemporalAmount(value=7, unit="DAY"),
            ),
        ),
        conditions=[ComparisonCondition(
            operator="EQUALS",
            left=FieldOperand(field="patient.progression"),
            right=LiteralOperand(value=True),
        )],
        evidence_refs=[evidence.id],
    )
    claims = []
    for event in (safety, progression):
        claims.extend([
            ClaimEvidence(
                evidence_id=evidence.id, claim_type="EVENT_NAME",
                claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path="display_name", confidence=1,
            ),
            ClaimEvidence(
                evidence_id=evidence.id, claim_type="TIMING",
                claim_entity_type="EVENT", claim_entity_id=event.id,
                claim_path="timing", confidence=1,
            ),
        ])
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="CONDITION",
        claim_entity_type="EVENT", claim_entity_id=progression.id,
        claim_path="conditions", confidence=1,
    ))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name="UCTSM demonstration", status=ScheduleStatus.APPROVED,
        ),
        anchors=[
            Anchor(code="LAST_DOSE", display_name="Last Dose", anchor_type="LAST_DOSE"),
            Anchor(code="PROGRESSION", display_name="Progression", anchor_type="PROGRESSION"),
        ],
        events=[safety, progression], evidence=[evidence], claim_evidence=claims,
    )


def run_case(schedule: UniversalSchedule, name: str, context: PatientContext) -> None:
    result = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2027, 12, 31))
    print(f"\n=== {name} ===")
    for event in result.events:
        output = {
            "event": event.event_code,
            "status": event.status.value,
            "timing": event.timing.model_dump(mode="json") if event.timing else None,
            "reason": event.explanation.get("reason"),
            "evidence_refs": event.explanation.get("evidence_refs"),
        }
        print(json.dumps(output, indent=2))


def main() -> None:
    schedule = build_schedule()
    issues = ScheduleValidator().validate(schedule)
    if issues:
        raise SystemExit(json.dumps([item.model_dump(mode="json") for item in issues], indent=2))
    print("UCTSM validation: PASSED (0 blocking issues)")
    run_case(schedule, "Known last dose; progression not recorded", PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"LAST_DOSE": date(2026, 12, 15)}, state={},
    ))
    run_case(schedule, "Progression recorded; last dose missing", PatientContext(
        patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
        anchors={"PROGRESSION": date(2026, 9, 12)}, state={"progression": True},
    ))


if __name__ == "__main__":
    main()

