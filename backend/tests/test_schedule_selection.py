"""A protocol with several independent Schedules of Assessments (e.g. a
seamless Phase 2/3 design with one SoA table per substudy) must not be
silently merged into one schedule. Classification detects the separate
schedules and the pipeline stops for a reviewer choice instead of guessing.
"""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_agent import (  # noqa: E402
    EvidenceFact,
    ScheduleAudit,
    ScheduleChunkEvidence,
    ScheduleDocumentMap,
    _classification_guidance,
    run_protocol_extraction_agent,
    run_schedule_extraction_agent,
)
from protocol_extraction import ExtractedSchedule  # noqa: E402
from schedule_schema import DocumentTaskClassification, ScheduleOption  # noqa: E402

PDF = b"%PDF-schedule-selection-test"

SUBSTUDY_OPTIONS = [
    ScheduleOption(
        id="ssa-p2", label="Substudy A - Phase 2 (SSA-P2)",
        description="66-week Phase 2 induction and extension.",
        source_location="Table 30, pages 156-159"),
    ScheduleOption(
        id="ss3-m", label="Substudy 3 - Maintenance (SS3-M)",
        description="38-week maintenance for Week 14 responders.",
        source_location="Table 33, pages 168-170"),
]


def _classification(**overrides) -> DocumentTaskClassification:
    payload = {
        "document_type": "protocol",
        "analysis_task": "full_protocol_schedule",
        "schedule_archetypes": ["multi_phase"],
        "complexity": "complex",
        "has_schedule": True,
        "confidence": 0.95,
        "evidence": ["Table of Contents lists 5 Schedules of Assessments"],
    }
    payload.update(overrides)
    return DocumentTaskClassification.model_validate(payload)


def _document_map(**overrides) -> ScheduleDocumentMap:
    payload = {
        "has_schedule": True,
        "schedule_kind": "multi_phase",
        "schedule_locations": ["Table 30", "Table 33"],
        "baseline_anchor": "Day 1",
        "official_title": "Seamless Phase 2/3 study",
    }
    payload.update(overrides)
    return ScheduleDocumentMap.model_validate(payload)


def _fact(evidence_id: str, claim: str) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id, claim=claim,
        source_location="Table 33, page 168", source_quote=claim, confidence=0.99,
    )


def _schedule() -> ExtractedSchedule:
    # IDs match what evidence_sweep_node's merge actually produces: the single
    # chunk these tests exercise is always chunk index 0, so every evidence_id
    # minted by the mocked ScheduleChunkEvidence comes back prefixed "chunk0-".
    return ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "canonical_plan": {
            "anchors": [{
                "id": "anchor-baseline", "name": "Baseline",
                "anchor_type": "first_dose", "evidence_ids": ["chunk0-timing-01"],
            }],
            "events": [{
                "id": "event-baseline", "name": "Baseline", "event_type": "Baseline",
                "timing": {
                    "kind": "offset", "anchor_id": "anchor-baseline",
                    "offset": {"value": 0, "unit": "day"},
                    "source_label": "Day 1", "evidence_ids": ["chunk0-timing-01"],
                },
                "evidence_ids": ["chunk0-visit-01"],
            }],
        },
    })


def _approving_audit() -> ScheduleAudit:
    dimension = {
        "applicable": True, "accuracy": 0.98, "passed": True,
        "checked_items": ["Table 33, page 168"], "summary": "matches",
    }
    return ScheduleAudit.model_validate({
        "approved": True, "confidence": 0.97,
        "visit_coverage": dimension, "timing": dimension, "windows": dimension,
        "visit_types": dimension, "procedure_mapping": dimension,
        "overall_schedule": dimension,
        "verified_items": ["Table 33, page 168"], "issues": [],
        "summary": "complete",
    })


def _recording_generate(responses):
    queue = list(responses)
    prompts: list[str] = []

    async def generate(pdf_bytes, prompt, schema, *, system_instruction, max_tokens):
        prompts.append(prompt)
        return queue.pop(0)

    return generate, prompts, queue


def test_multiple_schedule_options_stop_before_discovery():
    generate, prompts, remaining = _recording_generate([
        _classification(schedule_options=SUBSTUDY_OPTIONS),
        # Nothing below may be consumed: discovery/timing/visit_evidence/
        # synthesis/confirmation/audit are all skipped.
        _document_map(), _schedule(), _schedule(), _approving_audit(),
    ])

    schedule = asyncio.run(run_schedule_extraction_agent(PDF, generate))

    assert len(prompts) == 1, "only classification may run before a selection is made"
    assert len(remaining) == 4
    assert schedule.requires_schedule_selection is True
    assert schedule.visits == []
    assert schedule.classification is not None
    assert [option.id for option in schedule.classification.schedule_options] == [
        "ssa-p2", "ss3-m"]
    assert any("ssa-p2" in item for item in schedule.assumptions)


def test_protocol_bundle_agent_survives_the_selection_short_circuit():
    """extract_bundle's caller must not crash reading document_map."""
    generate, prompts, _ = _recording_generate([
        _classification(schedule_options=SUBSTUDY_OPTIONS),
    ])

    details, schedule = asyncio.run(run_protocol_extraction_agent(PDF, generate))

    assert len(prompts) == 1
    assert schedule.requires_schedule_selection is True
    assert details.title == ""  # no discovery ran; details stay empty, not crash


def test_a_single_schedule_option_does_not_require_selection():
    generate, prompts, _ = _recording_generate([
        _classification(schedule_options=[SUBSTUDY_OPTIONS[0]]),
        _document_map(),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])

    schedule = asyncio.run(run_schedule_extraction_agent(
        PDF, generate, max_refinements=0))

    assert len(prompts) == 5, "one named schedule is not a selection to make"
    assert schedule.requires_schedule_selection is False
    assert schedule.visits


def test_selecting_an_option_runs_the_full_pipeline_with_focused_guidance():
    generate, prompts, _ = _recording_generate([
        _classification(schedule_options=SUBSTUDY_OPTIONS),
        _document_map(),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])

    schedule = asyncio.run(run_schedule_extraction_agent(
        PDF, generate, max_refinements=0, selected_schedule_option_id="ss3-m"))

    assert len(prompts) == 5, "a selection lets discovery through to audit run"
    assert schedule.requires_schedule_selection is False
    assert schedule.visits
    classify_prompt = prompts[0]
    assert "Substudy 3 - Maintenance" not in classify_prompt, (
        "the classifier decides schedule_options; it is not told the choice")
    for prompt in prompts[1:]:
        assert "Substudy 3 - Maintenance (SS3-M)" in prompt
        assert "ONLY for this selected schedule" in prompt


def test_guidance_names_the_selected_option_and_lists_options_when_unselected():
    with_selection = _classification_guidance(
        _classification(schedule_options=SUBSTUDY_OPTIONS), "ssa-p2")
    assert "Substudy A - Phase 2 (SSA-P2)" in with_selection
    assert "ONLY for this selected schedule" in with_selection

    without_selection = _classification_guidance(
        _classification(schedule_options=SUBSTUDY_OPTIONS))
    assert "ssa-p2" in without_selection and "ss3-m" in without_selection
    assert "No selection has been made" in without_selection

    single_option = _classification_guidance(
        _classification(schedule_options=[SUBSTUDY_OPTIONS[0]]))
    assert "independent Schedule of Assessments" not in single_option
