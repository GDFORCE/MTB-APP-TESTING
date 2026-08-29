from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.extraction.graph import DocumentPage, run_extraction


class FixtureProvider:
    def __init__(self, schedule):
        self.schedule = schedule

    def document_structure(self, state):
        return {"sections": [{"title": "Schedule of Assessments", "page": 1}], "tables": []}

    def discover_schedule_evidence(self, state):
        return [item.model_dump(mode="json") for item in self.schedule.evidence]

    def extract_claims(self, category, state):
        if category != "timing":
            return []
        claim = next(item for item in self.schedule.claim_evidence if item.claim_type == "TIMING")
        return [{
            "claim_id": "timing-1", "claim_type": "TIMING", "statement": "Day 30",
            "evidence_ids": [str(claim.evidence_id)], "candidate": {"type": "OFFSET"},
        }]

    def build_relationships(self, state):
        return {}

    def completeness_check(self, state):
        return []

    def consistency_check(self, state):
        return []

    def assemble_schedule(self, state):
        return self.schedule.model_dump(mode="json")


def test_langgraph_pipeline_is_claim_based_and_returns_typed_schedule():
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=1, source_text="Day 30")
    event = Event(
        code="DAY_30", protocol_label="Day 30", display_name="Day 30", event_type="VISIT",
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=TemporalAmount(value=30, unit="DAY")),
        evidence_refs=[evidence.id],
    )
    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary"),
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
    result = run_extraction(
        FixtureProvider(schedule), document_hash="a" * 64,
        pages=[DocumentPage(page_number=1, text="Schedule of Assessments: Day 30")],
    )
    assert result.schedule == schedule
    assert not result.issues
    assert [item["node"] for item in result.extraction_trace][:2] == ["DOCUMENT_STRUCTURE", "FIND_SCHEDULE_SECTIONS"]
    assert result.extraction_trace[-1]["node"] == "FINAL_VALIDATION"
