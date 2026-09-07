"""The LLM-backed extraction provider, end to end without a network call.

The provider is driven by a scripted client, so these tests are about the
CONTRACT between the model and the pipeline rather than about model quality:

  * does a model answer become a canonical schedule with its traceability intact;
  * and, more importantly, what happens when the model is wrong.

The second question is the one that matters. A model that cites a passage that
does not exist, invents a visit code, or returns prose instead of JSON must
produce a visible extraction issue - never a schedule that looks finished and
quietly cannot be traced back to the protocol.
"""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.models import UniversalSchedule
from app.domain.schedule.validator import ScheduleValidator
from app.extraction.claude_provider import ClaudeExtractionProvider
from app.extraction.graph import DocumentPage, run_extraction
from app.extraction.llm_client import (
    ExtractionModelError, parse_json_list, parse_json_object,
)

PAGES = [DocumentPage(
    page_number=8,
    text=(
        "Table 2 Schedule of Assessments\n"
        "Activity        Screening   C1D1    Week 4\n"
        "ECG             X           Xa      X\n"
        "a. ECG must be performed pre-dose.\n"
    ),
)]


class ScriptedClient:
    """Answers each prompt from a canned script, keyed by a phrase in the prompt."""

    def __init__(self, script: dict[str, object], *, default: object = None):
        self.script = script
        self.default = default if default is not None else {"claims": []}
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int = 0) -> str:
        self.prompts.append(prompt)
        for marker, answer in self.script.items():
            if marker in prompt:
                return answer if isinstance(answer, str) else json.dumps(answer)
        return json.dumps(self.default)


EVIDENCE_ANSWER = {
    "evidence": [
        {
            "ref": "e1", "evidence_type": "TABLE_COLUMN", "page_number": 8,
            "table_title": "Schedule of Assessments", "column_identifier": "C1D1",
            "source_text": "C1D1",
        },
        {
            "ref": "e2", "evidence_type": "FOOTNOTE", "page_number": 8,
            "source_text": "a. ECG must be performed pre-dose.",
        },
    ]
}


def script(**overrides) -> dict[str, object]:
    """A minimal but complete model transcript for one visit."""
    base: dict[str, object] = {
        "Identify the parts of this protocol": {"sections": [], "tables": []},
        "List the specific passages": EVIDENCE_ANSWER,
        "Extract the schedule's identity": {"claims": [{
            "claim_id": "m1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {"name": "Schedule of Assessments", "schedule_type": "PRIMARY"},
        }]},
        "Extract the reference dates": {"claims": [{
            "claim_id": "a1", "evidence_ids": ["e1"], "confidence": 0.95,
            "candidate": {
                "code": "FIRST_DOSE", "display_name": "First Dose",
                "anchor_type": "FIRST_DOSE",
            },
        }]},
        "Extract every visit, contact": {"claims": [{
            "claim_id": "v1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "code": "C1D1", "protocol_label": "C1D1",
                "display_name": "Cycle 1 Day 1", "event_type": "SITE_VISIT",
            },
        }]},
        "state how the patient attends": {"claims": [{
            "claim_id": "t1", "evidence_ids": ["e1"], "confidence": 0.8,
            "candidate": {
                "event_code": "C1D1", "visit_mode": "CLINIC",
                "allowed_visit_modes": [], "activation": "SCHEDULED",
            },
        }]},
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"},
                "day": 1,
            }},
        }]},
        "Extract the assessments performed": {"claims": [{
            "claim_id": "ac1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "code": "ECG", "protocol_label": "ECG",
                "display_name": "ECG", "activity_type": "ASSESSMENT",
                "requiredness": "REQUIRED",
            },
        }]},
        "Extract footnotes and table markers": {"claims": [{
            "claim_id": "q1", "evidence_ids": ["e2"], "confidence": 0.85,
            "candidate": {
                "marker": "a", "text": "ECG must be performed pre-dose.",
                "scope": "CELL", "category": "TIMING", "target_codes": ["C1D1"],
                "resolved": True,
            },
        }]},
    }
    base.update(overrides)
    return base


def extract(client) -> tuple[UniversalSchedule | None, list, list]:
    result = run_extraction(
        ClaudeExtractionProvider(client), document_hash="a" * 64, pages=PAGES,
    )
    return result.schedule, result.issues, result.extraction_trace


def codes(issues) -> set[str]:
    return {item.issue_code for item in issues}


# --- the happy path -------------------------------------------------------------

def test_a_model_transcript_becomes_a_canonical_schedule():
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    event = next(item for item in schedule.events if item.code == "C1D1")
    assert event.display_name == "Cycle 1 Day 1"
    assert event.timing.type == "PROTOCOL_DAY"
    assert event.timing.day == 1
    assert [item.code for item in schedule.anchors] == ["FIRST_DOSE"]
    assert [item.display_name for item in event.activities] == ["ECG"]


def test_every_extracted_rule_can_be_traced_back_to_a_quoted_passage():
    """Traceability is generated mechanically, so it cannot be forgotten."""
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    evidence_ids = {item.id for item in schedule.evidence}
    assert evidence_ids
    assert all(item.evidence_id in evidence_ids for item in schedule.claim_evidence)

    event = schedule.events[0]
    claim_types = {
        item.claim_type for item in schedule.claim_evidence
        if item.claim_entity_id == event.id
    }
    assert {"EVENT_NAME", "TIMING"} <= claim_types


def test_the_source_text_reaches_the_schedule_verbatim():
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    footnote = next(
        item for item in schedule.evidence if item.evidence_type == "FOOTNOTE")
    assert footnote.source_text == "a. ECG must be performed pre-dose."
    assert footnote.page_number == 8


def test_the_model_is_asked_one_focused_question_per_category():
    client = ScriptedClient(script())
    extract(client)

    # Structure, evidence, and one per semantic category.
    assert len(client.prompts) >= 18
    assert any("state WHEN it occurs" in item for item in client.prompts)
    assert any("inpatient stays" in item for item in client.prompts)


def test_claims_can_only_cite_passages_the_model_was_given():
    client = ScriptedClient(script())
    extract(client)

    timing_prompt = next(
        item for item in client.prompts if "state WHEN it occurs" in item)
    assert "Cite only these passages" in timing_prompt
    assert "e1:" in timing_prompt


# --- what happens when the model is wrong ---------------------------------------

def test_a_claim_citing_a_passage_that_does_not_exist_is_dropped_and_reported():
    """A dangling citation is worse than a missing rule: it looks checkable."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e-does-not-exist"],
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert "MISSING_EVIDENCE" in codes(issues)
    assert schedule is not None
    # The visit survives, but undated - never dated from an uncheckable claim.
    assert schedule.events[0].timing.type == "UNRESOLVED"


def test_a_visit_with_no_timing_is_unresolved_and_blocks_approval():
    """Doc 1: no anchor means no date. Blocking is the correct outcome."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": []},
    })))

    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"
    assert "AMBIGUOUS_TIMING" in codes(issues)
    assert any("Cycle 1 Day 1" in item.message for item in issues)
    assert ScheduleValidator.blocking(ScheduleValidator().validate(schedule))


def test_a_timing_the_model_expressed_wrongly_does_not_become_a_near_miss():
    """A malformed rule stays unresolved rather than being coerced into a date."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "PROTOCOL_DAY", "day": "the day of first dose",
            }},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"
    assert "UNSUPPORTED_PROTOCOL_CONSTRUCT" in codes(issues)


def test_a_claim_about_a_visit_that_was_never_extracted_is_reported():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "WEEK_99", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert "UNRESOLVED_REFERENCE" in codes(issues)
    assert any("WEEK_99" in json.dumps(item.details) for item in issues)
    assert schedule is not None


def test_prose_instead_of_json_becomes_an_issue_not_a_silent_empty_answer():
    """An empty answer reads as 'the protocol does not say this'. It is not."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": "I could not find the timing for this visit.",
    })))

    assert "UNSUPPORTED_PROTOCOL_CONSTRUCT" in codes(issues)
    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"


def test_a_model_that_fails_entirely_produces_issues_not_a_plausible_schedule():
    """An unreachable model must not look like a protocol with nothing in it."""
    class BrokenClient:
        def complete(self, *, system, prompt, max_tokens=0):
            raise ExtractionModelError("provider is unavailable")

    schedule, issues, _trace = extract(BrokenClient())

    assert issues
    assert any("provider is unavailable" in item.message for item in issues)
    # Nothing was extracted, so nothing can be approved.
    assert schedule is None or not schedule.events


def test_an_unusable_evidence_entry_is_reported_rather_than_stored():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "List the specific passages": {"evidence": [
            {"ref": "e1", "evidence_type": "TABLE_COLUMN", "page_number": 0,
             "source_text": "C1D1"},
        ]},
    })))

    assert "MISSING_EVIDENCE" in codes(issues)
    assert schedule is not None
    assert schedule.evidence == []


# --- unstated things stay unstated -----------------------------------------------

def test_an_unstated_visit_mode_is_not_defaulted_to_a_clinic_visit():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "state how the patient attends": {"claims": [{
            "claim_id": "t1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "visit_mode": None,
                          "allowed_visit_modes": [], "activation": "SCHEDULED"},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].visit_mode is None
    assert schedule.events[0].allowed_visit_modes == []


def test_a_hybrid_mode_keeps_both_options_and_selects_neither():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "state how the patient attends": {"claims": [{
            "claim_id": "t1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "visit_mode": None,
                          "allowed_visit_modes": ["CLINIC", "TELEPHONE"],
                          "activation": "SCHEDULED"},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].allowed_visit_modes == ["CLINIC", "TELEPHONE"]
    assert schedule.events[0].visit_mode is None


def test_an_unstated_dependency_mode_stays_absent_rather_than_defaulting():
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    assert schedule.events[0].dependency_mode is None


def test_an_open_ended_repeat_keeps_no_invented_maximum():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "Extract visits that repeat": {"claims": [{
            "claim_id": "r1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "recurrence": {
                "interval": {"value": 21, "unit": "DAY"},
                "start_reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"},
                "termination": {"type": "HORIZON"},
            }},
        }]},
    })))

    assert schedule is not None
    recurrence = schedule.events[0].recurrence
    assert recurrence is not None
    assert recurrence.termination.type == "HORIZON"
    assert recurrence.termination.count is None


# --- the JSON reader --------------------------------------------------------------

def test_a_fenced_reply_is_read_normally():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_a_reply_with_a_preface_keeps_the_object():
    assert parse_json_object('Here you go:\n{"a": 1}') == {"a": 1}


def test_an_empty_reply_raises_rather_than_returning_nothing():
    with pytest.raises(ExtractionModelError, match="empty"):
        parse_json_object("")


def test_a_bare_list_is_accepted_where_a_keyed_list_was_asked_for():
    assert parse_json_list('[{"a": 1}]', "claims") == [{"a": 1}]


# --- the runner's document handling ----------------------------------------------

def test_plain_text_becomes_one_page_per_form_feed():
    from app.extraction.runner import pages_from_text

    pages = pages_from_text("page one\fpage two")

    assert [item.page_number for item in pages] == [1, 2]
    assert pages[1].text == "page two"


def test_the_document_the_model_sees_carries_page_markers_it_can_cite():
    from app.extraction.runner import document_text

    rendered = document_text(pages_from_text_helper())

    assert "--- page 1 ---" in rendered
    assert "--- page 2 ---" in rendered


def pages_from_text_helper():
    from app.extraction.runner import pages_from_text

    return pages_from_text("alpha\fbeta")


def test_an_oversized_document_is_truncated_and_says_so():
    """Silently dropping pages would make a partial extraction look complete."""
    from app.extraction import runner

    original = runner.MAX_DOCUMENT_CHARS
    runner.MAX_DOCUMENT_CHARS = 60
    try:
        rendered = runner.document_text(runner.pages_from_text("a" * 100 + "\f" + "b" * 100))
    finally:
        runner.MAX_DOCUMENT_CHARS = original

    assert "pages omitted" in rendered


def test_extraction_reports_that_it_cannot_run_without_a_key(monkeypatch):
    from app.extraction.runner import provider_configured

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_configured() is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert provider_configured() is True
