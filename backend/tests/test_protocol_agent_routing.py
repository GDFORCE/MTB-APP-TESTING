"""Classification changes the workflow and every stage reads retrieved pages.

These are the two integration points the decomposed agent was missing: the
deterministic page index must reach the model, and the AI classification must
change what the graph actually does rather than only what the prompts say.
"""
import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_agent import (  # noqa: E402
    EvidenceFact,
    ScheduleAudit,
    ScheduleChunkEvidence,
    ScheduleDocumentMap,
    _classification_guidance,
    run_schedule_extraction_agent,
)
import protocol_agent  # noqa: E402
from protocol_document_index import (  # noqa: E402
    build_protocol_document_index,
)
from protocol_extraction import ExtractedSchedule  # noqa: E402
from schedule_schema import DocumentTaskClassification  # noqa: E402

PDF = b"%PDF-routing-test"


def _classification(**overrides) -> DocumentTaskClassification:
    payload = {
        "document_type": "protocol",
        "analysis_task": "full_protocol_schedule",
        "schedule_archetypes": ["linear"],
        "complexity": "simple",
        "has_schedule": True,
        "confidence": 0.99,
        "evidence": ["Schedule of Assessments, page 2"],
    }
    payload.update(overrides)
    return DocumentTaskClassification.model_validate(payload)


def _document_map(**overrides) -> ScheduleDocumentMap:
    payload = {
        "has_schedule": True,
        "schedule_kind": "linear",
        "schedule_locations": ["Schedule of Assessments, page 2"],
        "baseline_anchor": "Day 1",
        "official_title": "Routing study",
    }
    payload.update(overrides)
    return ScheduleDocumentMap.model_validate(payload)


def _fact(evidence_id: str, claim: str) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id,
        claim=claim,
        source_location="Schedule of Assessments, page 2",
        source_quote=claim,
        confidence=0.99,
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
        "checked_items": ["Schedule of Assessments, page 2"], "summary": "matches",
    }
    return ScheduleAudit.model_validate({
        "approved": True, "confidence": 0.97,
        "visit_coverage": dimension, "timing": dimension, "windows": dimension,
        "visit_types": dimension, "procedure_mapping": dimension,
        "overall_schedule": dimension,
        "verified_items": ["Schedule of Assessments, page 2"], "issues": [],
        "summary": "complete",
    })


def _recording_generate(responses):
    """Return a generate() double plus the list of prompts it received."""
    queue = list(responses)
    prompts: list[str] = []

    async def generate(pdf_bytes, prompt, schema, *, system_instruction, max_tokens):
        prompts.append(prompt)
        return queue.pop(0)

    return generate, prompts, queue


def _index_of(page_texts):
    return build_protocol_document_index(PDF, extracted_pages=page_texts)


SCHEDULE_PAGE = (
    "Schedule of Assessments\n"
    "Visit\tScreening\tBaseline\tFollow-up\n"
    "Study day\tDay -14\tDay 1\tDay 30\n"
    "Visit window\t±3 days\t±0 days\t±7 days\n"
    "Informed consent\tX\t\t\n"
    "Vital signs\tX\tX\tX\n"
)
NARRATIVE_PAGE = (
    "Investigator brochure summary. The investigational product is supplied as "
    "a lyophilised powder for reconstitution and is stored between 2 and 8 "
    "degrees Celsius in a controlled pharmacy refrigerator at each site."
)


def test_page_retrieval_reaches_classify_discover_and_evidence_sweep():
    """classify/discover get a keyword-scored excerpt; evidence_sweep gets its
    own full-coverage chunk instead; synthesize/audit get neither, since the
    merged evidence pool from evidence_sweep is already complete — a scored
    excerpt on top of it would be redundant cost with no coverage benefit
    (see protocol_agent._STAGES_WITHOUT_RETRIEVAL)."""
    generate, prompts, _ = _recording_generate([
        _classification(), _document_map(),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])
    index = _index_of([NARRATIVE_PAGE, SCHEDULE_PAGE])

    schedule = asyncio.run(run_schedule_extraction_agent(
        PDF, generate, max_refinements=0, page_index=index))

    assert schedule.visits, "the graph must still produce the projected schedule"
    # classify, discover, evidence_sweep (1 chunk for a 2-page doc), synthesize,
    # audit — timing/visit_evidence collapsed into one sweep stage, and there
    # is no separate confirmation stage any more.
    assert len(prompts) == 5
    classify_prompt, discover_prompt, sweep_prompt = prompts[0], prompts[1], prompts[2]

    for prompt in (classify_prompt, discover_prompt):
        assert "RETRIEVED SOURCE PAGES" in prompt
        assert "Schedule of Assessments" in prompt
        assert index.document_sha256 in prompt
    # The packet narrows attention without becoming the only permitted source:
    # the attached PDF must remain authoritative for pages it left out.
    assert "attached PDF remains" in classify_prompt
    assert "page-2-" in classify_prompt, "page evidence IDs must be citable"

    # evidence_sweep reads its own full-coverage chunk, not the scored excerpt.
    assert "RETRIEVED SOURCE PAGES" not in sweep_prompt
    assert "DOCUMENT PAGES FOR THIS CHUNK" in sweep_prompt
    assert "Schedule of Assessments" in sweep_prompt
    assert "CORE" in sweep_prompt

    # synthesize/audit rely on the merged evidence pool, not a re-scored
    # excerpt: no retrieval header, but the pool (with its chunk-namespaced
    # evidence IDs) does reach them.
    for prompt in prompts[3:]:
        assert "RETRIEVED SOURCE PAGES" not in prompt
        assert "DOCUMENT PAGES FOR THIS CHUNK" not in prompt
        assert "chunk0-timing-01" in prompt


def test_page_retrieval_failure_never_blocks_extraction():
    generate, prompts, _ = _recording_generate([
        _classification(), _document_map(),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])

    # No page index: an unreadable/scanned PDF must still extract from the PDF.
    schedule = asyncio.run(run_schedule_extraction_agent(
        PDF, generate, max_refinements=0))

    assert schedule.visits
    assert all("RETRIEVED SOURCE PAGES" not in prompt for prompt in prompts)


def test_page_index_from_another_pdf_is_rejected():
    generate, _prompts, _ = _recording_generate([])
    foreign = build_protocol_document_index(b"%PDF-other", extracted_pages=[SCHEDULE_PAGE])

    with pytest.raises(ValueError, match="different protocol PDF"):
        asyncio.run(run_schedule_extraction_agent(
            PDF, generate, max_refinements=0, page_index=foreign))


def test_no_schedule_classification_stops_after_discovery():
    generate, prompts, remaining = _recording_generate([
        _classification(
            document_type="unrelated", analysis_task="no_schedule",
            schedule_archetypes=[], has_schedule=False,
            evidence=["Investigator CV, page 1"]),
        _document_map(has_schedule=False, schedule_kind="none", schedule_locations=[]),
        # Nothing below may be consumed.
        _schedule(), _approving_audit(),
    ])

    schedule = asyncio.run(run_schedule_extraction_agent(PDF, generate))

    assert len(prompts) == 2, "evidence sweep/synthesize/audit must be skipped"
    assert len(remaining) == 2
    assert schedule.schedule_kind == "none"
    assert schedule.visits == []
    assert any("no visit schedule" in item for item in schedule.assumptions)


def test_discovery_disagreement_still_runs_full_schedule_extraction():
    """One stage claiming 'no schedule' must not silently discard a real one."""
    generate, prompts, _ = _recording_generate([
        _classification(
            document_type="reference", analysis_task="no_schedule",
            schedule_archetypes=[], has_schedule=False),
        _document_map(),  # discovery DID find a schedule table
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])

    schedule = asyncio.run(run_schedule_extraction_agent(
        PDF, generate, max_refinements=0))

    assert len(prompts) == 5
    assert schedule.visits, "a schedule discovery found must not be thrown away"


@pytest.mark.parametrize("archetype,expected", [
    ("crossover", "washout"),
    ("cyclic", "cycle templates"),
    ("intra_day", "elapsed hours"),
    ("event_driven", "patient-specific"),
    ("multi_arm", "branches"),
])
def test_archetype_changes_the_extraction_guidance(archetype, expected):
    guidance = _classification_guidance(
        _classification(schedule_archetypes=[archetype]))
    assert expected in guidance


def test_amendment_classification_requests_version_comparison():
    guidance = _classification_guidance(_classification(
        document_type="amendment", analysis_task="amendment_comparison",
        has_attached_reference=True, needs_version_comparison=True))
    assert "amendment" in guidance.lower()
    assert "version comparison" in guidance.lower()
    assert "appended" in guidance.lower()


def test_guidance_reaches_the_builder_but_not_the_classifier():
    generate, prompts, _ = _recording_generate([
        _classification(schedule_archetypes=["crossover"]),
        _document_map(),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])

    asyncio.run(run_schedule_extraction_agent(PDF, generate, max_refinements=0))

    classify_prompt, discover_prompt = prompts[0], prompts[1]
    assert "EXTRACTION GUIDANCE" not in classify_prompt, (
        "the classifier must not be told the answer it is about to produce")
    for prompt in prompts[1:]:
        assert "EXTRACTION GUIDANCE" in prompt
        assert "washout" in prompt
    assert "crossover" in discover_prompt


def test_page_index_is_cached_per_document_when_configured(tmp_path, monkeypatch):
    """One PDF is parsed once; a second extraction reuses the stored index."""
    monkeypatch.setenv("PROTOCOL_PAGE_INDEX_CACHE_DIR", str(tmp_path))
    builds = []
    real_build = protocol_agent.build_protocol_document_index

    def counting_build(pdf_bytes, **kwargs):
        builds.append(pdf_bytes)
        return real_build(pdf_bytes, extracted_pages=[SCHEDULE_PAGE])

    monkeypatch.setattr(protocol_agent, "build_protocol_document_index", counting_build)
    monkeypatch.setattr(
        "protocol_document_index.build_protocol_document_index", counting_build)

    for _ in range(2):
        generate, prompts, _ = _recording_generate([
            _classification(), _document_map(),
            ScheduleChunkEvidence(
                visit_timing=[_fact("timing-01", "Baseline is Day 1")],
                visit_columns=[_fact("visit-01", "Baseline")]),
            _schedule(), _approving_audit(),
        ])
        asyncio.run(run_schedule_extraction_agent(PDF, generate, max_refinements=0))
        assert "RETRIEVED SOURCE PAGES" in prompts[0]

    assert len(builds) == 1, "the second extraction must hit the page-index cache"
    assert list(tmp_path.glob("*.json")), "the index must be written to the cache"


def test_unwritable_page_index_cache_never_blocks_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOCOL_PAGE_INDEX_CACHE_DIR", str(tmp_path / "cache"))

    class _Failing:
        def __init__(self, directory):
            self.directory = directory

        def get_or_build(self, pdf_bytes):
            raise OSError("no space left on device")

    monkeypatch.setattr(protocol_agent, "ProtocolDocumentIndexCache", _Failing)
    monkeypatch.setattr(
        protocol_agent, "build_protocol_document_index",
        lambda pdf_bytes, **kwargs: build_protocol_document_index(
            pdf_bytes, extracted_pages=[SCHEDULE_PAGE]))

    generate, prompts, _ = _recording_generate([
        _classification(), _document_map(),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-01", "Baseline")]),
        _schedule(), _approving_audit(),
    ])

    schedule = asyncio.run(run_schedule_extraction_agent(
        PDF, generate, max_refinements=0))

    assert schedule.visits
    assert "RETRIEVED SOURCE PAGES" in prompts[0], (
        "a failed cache must fall back to building the index in memory")
