"""Completes two disclosed gaps from the §36 scorecard, both pure information
that the plan already carried but the adapter previously discarded or
mislabeled - no new inference, no engine change.

1. A footnote/condition narrowed to specific cycles and/or arms is a CELL of
   the schedule of assessments table, not the whole VISIT row or ACTIVITY
   column. Before this, "PK sampling only in Arm B, cycles 2/4/6" and a plain
   unconditional "PK sampling" qualifier were both labelled the identical
   generic ACTIVITY scope, and an event-level narrowing (occurrence_numbers on
   a condition targeting a whole visit) was not even carried into the
   qualifier's own details, unlike the activity-level case.

2. ``Evidence.page_number`` was always null because the adapter never parsed
   the page number already embedded in ``page_evidence_id`` (built as
   ``f"page-{position}-{digest}"`` by protocol_document_index.py and copied
   verbatim by the extraction prompt) - not an extraction-quality gap, an
   adapter one.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.models import QualifierScope  # noqa: E402
from app.services.canonical_import import (  # noqa: E402
    _page_number_from_evidence_id,
    universal_schedule_from_plan,
)

from test_canonical_import import evidence_fact  # noqa: E402


def base_plan() -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [{"id": "armB", "name": "Arm B", "branch_type": "arm",
                      "evidence_ids": ["e1"]}],
        "phases": [],
        "activities": [{"id": "act", "name": "PK sampling", "evidence_ids": ["e1"]}],
        "events": [{
            "id": "ev1", "name": "Cycle 1 Day 1", "event_type": "site visit",
            "timing": {"kind": "offset", "anchor_id": "a1",
                       "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
            "activity_ids": ["act"], "evidence_ids": ["e1"],
        }],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def build(plan: dict, evidence_ids=("e1",)):
    return universal_schedule_from_plan(
        plan, name="P",
        evidence_facts=[evidence_fact(eid, "Schedule of Assessments") for eid in evidence_ids])


def all_qualifiers(schedule):
    found = []
    for event in schedule.events:
        found.extend(event.qualifiers)
        for activity in event.activities:
            found.extend(activity.qualifiers)
    return found


# --- CELL scope: activity-level -----------------------------------------------

def test_an_activity_condition_narrowed_by_cycles_and_arm_becomes_a_cell():
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Only in Arm B, cycles 2 and 4",
        "applies_to_ids": ["act"], "occurrence_numbers": [2, 4],
        "applies_to_branch_ids": ["armB"], "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    matches = [q for q in all_qualifiers(schedule) if "Arm B" in q.text]
    assert matches and matches[0].scope == QualifierScope.CELL
    assert matches[0].details["occurrence_numbers"] == [2, 4]
    assert matches[0].details["applies_to_branch_ids"] == ["armB"]


def test_an_unnarrowed_activity_condition_stays_activity_scope():
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Central lab only",
        "applies_to_ids": ["act"], "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    matches = [q for q in all_qualifiers(schedule) if q.text == "Central lab only"]
    assert matches and matches[0].scope == QualifierScope.ACTIVITY


# --- CELL scope: event-level (previously silently lost) -----------------------

def test_an_event_condition_narrowed_by_arm_becomes_a_cell_not_a_bare_visit():
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Only for Arm B",
        "applies_to_ids": ["ev1"], "applies_to_branch_ids": ["armB"],
        "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    matches = [q for q in all_qualifiers(schedule) if q.text == "Only for Arm B"]
    assert matches and matches[0].scope == QualifierScope.CELL
    assert matches[0].details["applies_to_branch_ids"] == ["armB"]


def test_an_event_condition_narrowed_by_occurrence_is_no_longer_lost():
    """Before this fix, occurrence_numbers on an event-targeted condition
    were read for nothing - not applicability, not the qualifier. Now they at
    least survive as visible, traceable qualifier detail."""
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Only during cycles 2 and 4",
        "applies_to_ids": ["ev1"], "occurrence_numbers": [2, 4],
        "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    matches = [q for q in all_qualifiers(schedule) if q.text == "Only during cycles 2 and 4"]
    assert matches and matches[0].scope == QualifierScope.CELL
    assert matches[0].details["occurrence_numbers"] == [2, 4]


def test_an_unnarrowed_event_condition_stays_visit_scope():
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Only for measurable disease",
        "applies_to_ids": ["ev1"], "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    matches = [q for q in all_qualifiers(schedule) if q.text == "Only for measurable disease"]
    assert matches and matches[0].scope == QualifierScope.VISIT
    assert matches[0].details == {}


# --- evidence page numbers ------------------------------------------------------

def test_page_number_is_parsed_from_the_well_formed_page_evidence_id():
    assert _page_number_from_evidence_id("page-42-abc123def456") == 42
    assert _page_number_from_evidence_id("page-1-000000000000") == 1
    # protocol_document_index.py always appends "-<digest>", but the shared
    # test helper (and, defensively, any producer that ever omitted it) uses
    # a bare "page-<N>" - still an unambiguous page number.
    assert _page_number_from_evidence_id("page-7") == 7


def test_a_malformed_or_blank_page_evidence_id_yields_none_not_a_guess():
    assert _page_number_from_evidence_id("") is None
    assert _page_number_from_evidence_id("not-a-page-id") is None
    assert _page_number_from_evidence_id("table-3-hash") is None


def test_the_adapter_populates_evidence_page_number_end_to_end():
    plan = base_plan()
    schedule, _issues = build(plan)

    evidence = next(e for e in schedule.evidence if e.source_locator["legacy_evidence_id"] == "e1")
    # test_canonical_import.evidence_fact() hardcodes page_evidence_id="page-1".
    assert evidence.page_number == 1


def test_an_evidence_fact_with_no_page_evidence_id_leaves_page_number_null():
    plan = base_plan()
    schedule, _issues = universal_schedule_from_plan(
        plan, name="P",
        evidence_facts=[{
            "evidence_id": "e1", "claim": "x", "source_location": "p.1",
            "source_quote": "x",
        }])

    assert schedule.evidence[0].page_number is None
