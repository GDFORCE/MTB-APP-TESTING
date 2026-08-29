"""The schedule agent audits, repairs, and stops within its configured bound."""
import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_agent import (  # noqa: E402
    MIN_ACCEPT_CONFIDENCE,
    EvidenceFact,
    ScheduleAudit,
    ScheduleChunkEvidence,
    ScheduleDocumentMap,
    ScheduleVisitEvidence,
    _activity_day_gap_issues,
    _structural_issues,
    _visit_coverage_issues,
    run_protocol_extraction_agent,
    run_schedule_extraction_agent,
)
from protocol_extraction import ExtractionError, ExtractedSchedule  # noqa: E402
from schedule_schema import (  # noqa: E402
    CanonicalSchedulePlan,
    DocumentTaskClassification,
    RecurrenceRule,
    ScheduleAnchor,
    ScheduleEvent,
    TemporalAmount,
    TimingExpression,
)


def _schedule(name: str, day: int) -> ExtractedSchedule:
    # IDs match what evidence_sweep_node's merge actually produces: the single
    # chunk these tests exercise is always chunk index 0, so every evidence_id
    # minted by the mocked ScheduleChunkEvidence comes back prefixed "chunk0-".
    return ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "visits": [{
            "name": name,
            "day_offset": day,
            "field_evidence": [
                {"field": "name", "evidence_ids": ["chunk0-visit-p12-01"]},
                {"field": "timing", "evidence_ids": ["chunk0-timing-p12-01"]},
            ],
        }],
        "source_notes": "Schedule table",
    })


def _fact(evidence_id: str, claim: str) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id,
        claim=claim,
        source_location="Schedule of Assessments, page 12",
        source_quote=claim,
        confidence=0.99,
    )


def _audit(*, approved: bool, finding: str | None = None) -> ScheduleAudit:
    issues = [] if finding is None else [{
        "severity": "major",
        "category": "missing_visit",
        "finding": finding,
        "evidence": "Schedule of Assessments, page 12",
        "repair_instruction": "Add the omitted follow-up visit on Day 30.",
    }]
    passing = {
        "applicable": True,
        "accuracy": 0.98,
        "passed": True,
        "checked_items": ["Schedule of Assessments, page 12"],
        "summary": "matches",
    }
    failing = {
        "applicable": True,
        "accuracy": 0.70,
        "passed": False,
        "checked_items": ["Schedule of Assessments, page 12"],
        "summary": "missing follow-up",
    }
    return ScheduleAudit.model_validate({
        "approved": approved,
        "confidence": 0.96 if approved else 0.75,
        "visit_coverage": passing if approved else failing,
        "timing": passing,
        "windows": passing,
        "visit_types": passing,
        "procedure_mapping": passing,
        "overall_schedule": passing if approved else failing,
        "verified_items": ["visit columns", "timing", "footnotes"],
        "issues": issues,
        "summary": "complete" if approved else "repair required",
    })


def _decomposition_responses():
    return [
        DocumentTaskClassification(
            document_type="protocol",
            analysis_task="full_protocol_schedule",
            schedule_archetypes=["linear"],
            complexity="simple",
            has_schedule=True,
            confidence=0.99,
            evidence=["Schedule of Assessments, page 12"],
        ),
        ScheduleDocumentMap(
            has_schedule=True,
            schedule_kind="linear",
            schedule_locations=["Schedule of Assessments, page 12"],
            baseline_anchor="Day 1",
            ctri_number="CTRI/2026/08/123456",
            official_title="Combined extraction study",
            phase="Phase II",
            indications=["Oncology"],
            investigational_drug="Compound X",
            planned_duration="12 months",
            target_enrollment=80,
            stated_total_visits=2,
        ),
        ScheduleChunkEvidence(
            visit_timing=[_fact("timing-p12-01", "Baseline is Day 1")],
            visit_columns=[_fact("visit-p12-01", "Baseline")],
        ),
    ]


def test_agent_repairs_then_reaudits_until_verified():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0),
        _audit(approved=False, finding="Day 30 follow-up is missing."),
        _schedule("Follow-up", 30),
        _audit(approved=True),
    ]
    calls = []

    async def generate(pdf_bytes, prompt, schema, **kwargs):
        calls.append((pdf_bytes, prompt, schema, kwargs))
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=2))

    assert [call[2].__name__ for call in calls] == [
        "DocumentTaskClassification", "ScheduleDocumentMap", "ScheduleChunkEvidence",
        "ExtractedSchedule", "ScheduleAudit",
        "ExtractedSchedule", "ScheduleAudit"]
    assert result.visits[0].name == "Follow-up"
    assert result.verification_status == "verified"
    assert result.verification_iterations == 1
    assert result.verification_issues == []
    assert result.verification_scores["timing"] == 0.98


def test_agent_stops_at_bound_and_surfaces_unresolved_issue():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0),
        _audit(approved=False, finding="Day 30 follow-up is missing."),
    ]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert result.verification_status == "needs_review"
    assert result.verification_iterations == 0
    assert result.verification_issues == ["Day 30 follow-up is missing."]
    assert any("Day 30 follow-up is missing" in note for note in result.assumptions)


def test_high_procedure_accuracy_cannot_hide_bad_overall_schedule():
    passing = {
        "applicable": True,
        "accuracy": 0.95,
        "passed": True,
        "checked_items": ["Schedule table, page 12"],
        "summary": "matches",
    }
    weak_timing = {
        "applicable": True,
        "accuracy": 0.72,
        "passed": False,
        "checked_items": ["Treatment plan, page 8"],
        "summary": "cycle cadence is wrong",
    }
    audit = ScheduleAudit.model_validate({
        "approved": True,
        "confidence": 0.97,
        "visit_coverage": passing,
        "timing": weak_timing,
        "windows": passing,
        "visit_types": passing,
        "procedure_mapping": passing,
        "overall_schedule": weak_timing,
        "verified_items": ["visits", "timing", "windows", "procedures"],
        "issues": [],
        "summary": "Procedures match, but the schedule cadence does not.",
    })
    responses = _decomposition_responses() + [_schedule("Baseline", 0), audit]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert audit.procedure_mapping.accuracy == 0.95
    assert not audit.accepted
    assert result.verification_status == "needs_review"
    assert result.verification_scores["timing"] == 0.72
    assert result.verification_scores["overall_schedule"] == 0.72


def test_metadata_and_schedule_share_one_agent_workflow():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0),
        _audit(approved=True),
    ]
    calls = []

    async def generate(pdf_bytes, prompt, schema, **kwargs):
        calls.append(schema.__name__)
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    details, schedule = asyncio.run(run_protocol_extraction_agent(
        b"%PDF-test", generate, max_refinements=2))

    assert calls == [
        "DocumentTaskClassification", "ScheduleDocumentMap", "ScheduleChunkEvidence",
        "ExtractedSchedule", "ScheduleAudit",
    ]
    assert details.title == "Combined extraction study"
    assert details.ctri_number == "CTRI/2026/08/123456"
    assert details.target_enrollment == 80
    assert details.total_visits == 2
    assert schedule.verification_status == "verified"


def test_visit_coverage_issue_when_columns_outnumber_built_visits():
    """The schedule under-counts relative to the evidence catalog.

    This is the direction the pre-existing evidence checks never covered:
    every cited evidence ID is valid, but the schedule silently dropped
    columns the visit-evidence stage found.
    """
    visit_evidence = ScheduleVisitEvidence(visit_columns=[
        _fact("visit-01", "Baseline"),
        _fact("visit-02", "Week 4"),
        _fact("visit-03", "Week 8"),
        _fact("visit-04", "Week 12"),
    ])
    candidate = _schedule("Baseline", 0)

    issues = _visit_coverage_issues(candidate, visit_evidence)

    assert len(issues) == 1
    assert "4 distinct visit column(s)" in issues[0]
    assert "produced only 1 visit(s)" in issues[0]


def test_visit_coverage_issue_not_raised_when_counts_match():
    visit_evidence = ScheduleVisitEvidence(visit_columns=[
        _fact("visit-01", "Baseline"),
    ])
    candidate = _schedule("Baseline", 0)

    assert _visit_coverage_issues(candidate, visit_evidence) == []


def _dated_schedule(*visits: tuple[str, int]) -> ExtractedSchedule:
    return ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "anchor_study_day": 0,
        "includes_day_zero": True,
        "visits": [{
            "name": name,
            "day_offset": day,
            "field_evidence": [
                {"field": "name", "evidence_ids": [f"chunk0-visit-{index}"]},
            ],
        } for index, (name, day) in enumerate(visits)],
        "source_notes": "Table of Events",
    })


def test_activity_day_gap_issue_when_a_named_day_has_no_visit_at_all():
    """CT25-007-shaped: the Table of Events states 'PK Sampling Day 30 & 31',
    but the schedule only built a visit for Day 30 — Day 31 has no visit
    whatsoever. This is the strict, zero-guesswork case the check targets:
    a day named alongside a covered day that has no visit at all."""
    visit_evidence = ScheduleVisitEvidence(activity_assignments=[
        _fact("act-01", "PK sample collection: PK Sampling Day 30 & 31"),
    ])
    candidate = _dated_schedule(("Day 30 Dosing & PK Sampling", 30))

    issues = _activity_day_gap_issues(candidate, visit_evidence)

    assert len(issues) == 1
    assert "day(s) 31 have no visit at all" in issues[0]


def test_activity_day_gap_issue_not_raised_when_the_named_day_has_a_different_visit():
    """Deliberate boundary, not a false negative: Day 31 DOES have a visit
    (Check-out) — it is just missing the PK-sampling activity specifically.
    Matching a free-text claim against a schedule's activity names is left
    to the audit LLM (see _AUDIT_PROMPT), not this deterministic check, so
    this must NOT be flagged here."""
    visit_evidence = ScheduleVisitEvidence(activity_assignments=[
        _fact("act-01", "PK sample collection: PK Sampling Day 30 & 31"),
    ])
    candidate = _dated_schedule(
        ("Day 30 Dosing & PK Sampling", 30),
        ("Day 31 Assessment & Check-out", 31),
    )

    assert _activity_day_gap_issues(candidate, visit_evidence) == []


def test_activity_day_gap_issue_requires_anchor_metadata():
    candidate = _schedule("Day 30 Dosing & PK Sampling", 30)  # no anchor_study_day set
    visit_evidence = ScheduleVisitEvidence(activity_assignments=[
        _fact("act-01", "PK sample collection: PK Sampling Day 30 & 31"),
    ])

    assert _activity_day_gap_issues(candidate, visit_evidence) == []


def test_agent_stays_needs_review_when_evidence_outnumbers_visits_even_if_audit_approves():
    """An approving audit must not be enough on its own to reach 'verified'.

    Mirrors the real-world failure the visit-coverage check targets: the
    schedule collapses a wide table down to one visit, and the audit
    (working from the schedule, not the raw evidence catalog) approves
    anyway — the deterministic check catches it regardless.
    """
    responses = [
        _decomposition_responses()[0],
        _decomposition_responses()[1],
        ScheduleChunkEvidence(visit_columns=[
            _fact("visit-01", "Baseline"),
            _fact("visit-02", "Week 4"),
            _fact("visit-03", "Week 8"),
        ]),
        _schedule("Baseline", 0),
        _audit(approved=True),
    ]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert result.verification_status == "needs_review"
    assert any("distinct visit column(s)" in issue for issue in result.verification_issues)


def test_structural_issues_flags_recurrence_generated_occurrence_names():
    """A recurrence rule wrongly used for individually-numbered columns.

    Real-world case: Week 4, 8, 12... are each their own printed column, but
    the model covered them with one recurrence rule on an event named
    "Week 4". The projection can only substitute a bare 1, 2, 3... index, so
    occurrence 2 renders as "Week 4 (Occurrence 2)" instead of "Week 8" — a
    garbled duplicate-looking name that must be caught deterministically
    rather than relying on the model following the prompt correctly.
    """
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(id="anchor-bl", name="Baseline", anchor_type="first_dose")],
        events=[ScheduleEvent(
            id="event-week4",
            name="Week 4",
            timing=TimingExpression(
                kind="offset", anchor_id="anchor-bl",
                offset=TemporalAmount(value=28, unit="day"),
                source_label="Week 4"),
        )],
        recurrences=[RecurrenceRule(
            id="rec-1", event_ids=["event-week4"],
            frequency=TemporalAmount(value=28, unit="day"),
            start_occurrence=1, end_occurrence=3,
        )],
    )
    schedule = ExtractedSchedule.model_validate({
        "schedule_kind": "linear", "canonical_plan": plan.model_dump(),
    })

    issues = _structural_issues(schedule)

    assert any("(Occurrence 2)" in issue for issue in issues)
    assert any("recurrence" in issue for issue in issues)


def test_structural_issues_flags_unclassified_visit_type():
    """A visit left at the generic default event_type 'visit' is a real miss.

    The prompt says every event's role should be determinable from the
    protocol text or its position in the schedule, and forbids leaving the
    generic 'visit' default. Catch it deterministically rather than trusting
    that instruction was followed.
    """
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(id="anchor-bl", name="Baseline", anchor_type="first_dose")],
        events=[
            ScheduleEvent(
                id="event-bl", name="Baseline", event_type="baseline",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-bl",
                    offset=TemporalAmount(value=0, unit="day"), source_label="Day 1"),
            ),
            ScheduleEvent(
                id="event-wk4", name="Week 4",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-bl",
                    offset=TemporalAmount(value=28, unit="day"), source_label="Week 4"),
            ),
        ],
    )
    schedule = ExtractedSchedule.model_validate({
        "schedule_kind": "linear", "canonical_plan": plan.model_dump(),
    })

    issues = _structural_issues(schedule)

    assert any("Week 4" in issue and "generic default" in issue for issue in issues)
    assert not any("Baseline" in issue and "generic default" in issue for issue in issues)


def test_agent_cannot_verify_a_deterministically_corrected_day_offset():
    corrected = ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "anchor_study_day": 1,
        "includes_day_zero": False,
        "visits": [{
            "name": "Day 8",
            "source_day_label": "Day 8",
            "day_offset": 8,
            "field_evidence": [
                {"field": "name", "evidence_ids": ["visit-p12-01"]},
                {"field": "timing", "evidence_ids": ["timing-p12-01"]},
            ],
        }],
    })
    responses = _decomposition_responses() + [corrected, _audit(approved=True)]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert result.visits[0].day_offset == 7
    assert result.verification_status == "needs_review"
    assert any("maps to 7" in issue for issue in result.verification_issues)


def test_agent_retains_valid_candidate_when_repair_output_is_malformed():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0),
        _audit(approved=False, finding="Day 30 follow-up is missing."),
    ]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        if schema is ExtractedSchedule and not responses:
            raise ExtractionError("invalid repair JSON")
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=2))

    assert result.visits[0].name == "Baseline"
    assert result.verification_status == "needs_review"
    assert any("last valid schedule draft" in item for item in result.assumptions)
    assert any("correction pass" in item for item in result.verification_issues)


def test_agent_returns_review_draft_when_audit_is_unavailable():
    responses = _decomposition_responses() + [_schedule("Baseline", 0)]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        if schema is ScheduleAudit:
            raise ExtractionError("invalid audit JSON")
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=2))

    assert result.visits[0].name == "Baseline"
    assert result.verification_status == "needs_review"
    assert result.verification_confidence == 0
    assert result.verification_scores["overall_schedule"] is None
    assert any("verification could not be completed" in item
               for item in result.verification_issues)


def test_missing_field_evidence_blocks_verification():
    unsupported = ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "visits": [{"name": "Baseline", "day_offset": 0}],
    })
    responses = _decomposition_responses() + [unsupported, _audit(approved=True)]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert result.verification_status == "needs_review"
    assert any("no evidence for name" in issue for issue in result.verification_issues)
    assert any("no evidence for timing" in issue for issue in result.verification_issues)


def test_below_threshold_evidence_blocks_verification():
    responses = _decomposition_responses()
    responses[2].visit_timing[0].confidence = MIN_ACCEPT_CONFIDENCE - 0.01
    responses += [_schedule("Baseline", 0), _audit(approved=True)]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert result.verification_status == "needs_review"
    assert any("below-threshold confidence evidence" in issue
               for issue in result.verification_issues)


def test_agent_retries_only_the_failed_stage():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0), _audit(approved=True)]
    calls: list[str] = []
    failed_once = False

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        nonlocal failed_once
        calls.append(schema.__name__)
        if schema is DocumentTaskClassification and not failed_once:
            failed_once = True
            raise ExtractionError("temporary 503")
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-retry", generate, max_refinements=0,
        stage_max_attempts=2, retry_base_delay_seconds=0))

    assert calls[:3] == [
        "DocumentTaskClassification", "DocumentTaskClassification", "ScheduleDocumentMap"]
    assert calls.count("ScheduleDocumentMap") == 1
    assert result.verification_status == "verified"


def test_agent_resumes_completed_stages_from_json_checkpoint():
    checkpoint: dict = {}
    first_responses = _decomposition_responses()
    first_calls: list[str] = []

    async def interrupted_generate(_pdf_bytes, _prompt, schema, **_kwargs):
        first_calls.append(schema.__name__)
        if schema is ExtractedSchedule:
            raise ExtractionError("synthesis temporarily unavailable")
        response = first_responses.pop(0)
        assert isinstance(response, schema)
        return response

    with pytest.raises(ExtractionError):
        asyncio.run(run_schedule_extraction_agent(
            b"%PDF-checkpoint", interrupted_generate, max_refinements=0,
            stage_checkpoint=checkpoint, stage_max_attempts=1,
            retry_base_delay_seconds=0))

    assert {"classify", "discover", "evidence_sweep:0"}.issubset(checkpoint)
    assert "synthesize" not in checkpoint

    second_responses = [_schedule("Baseline", 0), _audit(approved=True)]
    resumed_calls: list[str] = []

    async def resumed_generate(_pdf_bytes, _prompt, schema, **_kwargs):
        resumed_calls.append(schema.__name__)
        response = second_responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-checkpoint", resumed_generate, max_refinements=0,
        stage_checkpoint=checkpoint, stage_max_attempts=1,
        retry_base_delay_seconds=0))

    assert resumed_calls == ["ExtractedSchedule", "ScheduleAudit"]
    assert result.verification_status == "verified"
