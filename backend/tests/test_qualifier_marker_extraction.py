"""Qualifier markers survive extraction into UCTSM (doc s4/s27/§36 item 4).

A footnote is identified by its MARKER as printed - a superscript "a", a "*",
a "†" - not by its meaning. Before this, the canonical plan schema had nowhere
to put that symbol: a footnote's text and evidence survived, but the marker
itself was discarded, so a reviewer saw "Protocol note" instead of
"Footnote a" and could not tell two different footnotes with similar wording
apart, or confirm a marker against the printed table.

These tests prove the marker is preserved end to end for every place a
footnote can attach: a visit's own conditional text, a condition targeting a
visit, a condition targeting an activity, an activity's own conditional text,
and a window's footnote.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.canonical_import import universal_schedule_from_plan  # noqa: E402

from test_canonical_import import evidence_fact  # noqa: E402


def base_plan() -> dict:
    return {
        "schema_version": "2.0",
        "anchors": [{"id": "a1", "name": "Baseline", "anchor_type": "first_dose",
                     "evidence_ids": ["e1"]}],
        "branches": [], "phases": [],
        "activities": [{"id": "act", "name": "Labs", "evidence_ids": ["e1"]}],
        "events": [{
            "id": "ev1", "name": "Cycle 1 Day 1", "event_type": "site visit",
            "timing": {"kind": "offset", "anchor_id": "a1",
                       "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"},
            "activity_ids": ["act"], "evidence_ids": ["e1"],
        }],
        "recurrences": [], "transitions": [], "conditions": [], "conflicts": [],
    }


def build(plan: dict):
    return universal_schedule_from_plan(
        plan, name="P", evidence_facts=[evidence_fact("e1", "Schedule of Assessments")])


def all_qualifiers(schedule):
    found = []
    for event in schedule.events:
        found.extend(event.qualifiers)
        for activity in event.activities:
            found.extend(activity.qualifiers)
    return found


# --- a visit's own conditional_text ------------------------------------------

def test_a_visits_own_footnote_marker_survives():
    plan = base_plan()
    plan["events"][0]["conditional_text"] = "Only if clinically indicated"
    plan["events"][0]["marker"] = "a"
    schedule, _issues = build(plan)

    qualifiers = all_qualifiers(schedule)
    assert len(qualifiers) == 1
    assert qualifiers[0].marker == "a"
    assert qualifiers[0].text == "Only if clinically indicated"


def test_a_visit_with_no_marker_stays_markerless():
    """Prose with no table marker must not be given a fabricated one."""
    plan = base_plan()
    plan["events"][0]["conditional_text"] = "Perform per local SOC"
    schedule, _issues = build(plan)

    assert all_qualifiers(schedule)[0].marker is None


# --- a condition targeting a visit -------------------------------------------

def test_a_condition_targeting_a_visit_keeps_its_marker():
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Only for measurable disease",
        "applies_to_ids": ["ev1"], "marker": "†", "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    qualifiers = [item for item in all_qualifiers(schedule)
                  if item.text == "Only for measurable disease"]
    assert qualifiers and qualifiers[0].marker == "†"


# --- a condition targeting an activity ---------------------------------------

def test_a_condition_targeting_an_activity_keeps_its_marker():
    plan = base_plan()
    plan["conditions"] = [{
        "id": "c1", "expression": "Only if ANC < 1000",
        "applies_to_ids": ["act"], "marker": "2", "evidence_ids": ["e1"],
    }]
    schedule, _issues = build(plan)

    activity = schedule.events[0].activities[0]
    qualifiers = [item for item in activity.qualifiers if item.text == "Only if ANC < 1000"]
    assert qualifiers and qualifiers[0].marker == "2"


# --- an activity's own conditional_text --------------------------------------

def test_an_activitys_own_footnote_marker_survives():
    plan = base_plan()
    plan["activities"][0]["conditional_text"] = "Central lab only"
    plan["activities"][0]["marker"] = "*"
    schedule, _issues = build(plan)

    activity = schedule.events[0].activities[0]
    assert len(activity.qualifiers) == 1
    assert activity.qualifiers[0].marker == "*"


# --- a window's own marker ----------------------------------------------------

def test_a_windows_footnote_marker_survives_even_when_unresolved():
    plan = base_plan()
    plan["events"][0]["window"] = {
        "scope": "visit", "state": "stated", "window_type": "validity",
        "early": {"value": 28, "unit": "day"}, "marker": "c",
        "source_label": "Labs valid for 28 days",
    }
    schedule, _issues = build(plan)

    window_qualifiers = [item for item in all_qualifiers(schedule) if item.category.value == "WINDOW"]
    assert window_qualifiers and window_qualifiers[0].marker == "c"


# --- markers distinguish otherwise-similar footnotes -------------------------

def test_two_different_markers_on_similar_text_stay_distinguishable():
    """The actual reason this matters: two footnotes with near-identical
    wording must remain two separate, identifiable qualifiers, not merge into
    one generic 'Protocol note' a reviewer cannot tell apart."""
    plan = base_plan()
    plan["conditions"] = [
        {"id": "c1", "expression": "Only if clinically indicated (screening)",
         "applies_to_ids": ["ev1"], "marker": "a", "evidence_ids": ["e1"]},
    ]
    plan["activities"][0]["conditional_text"] = "Only if clinically indicated (on-treatment)"
    plan["activities"][0]["marker"] = "b"
    schedule, _issues = build(plan)

    markers = {item.marker for item in all_qualifiers(schedule)}
    assert markers == {"a", "b"}
