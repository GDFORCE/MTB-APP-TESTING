"""The structural invariants that guard extraction on UNSEEN protocols.

These checks are the robustness net: the paid LLM judge can only grade
documents someone has paid to grade, whereas an invariant fires on any schedule
the system ever produces. That only works if the invariants themselves are
sound — hence this file. Offline, no API key, no database.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "eval"))

import pytest  # noqa: E402

import invariants as inv  # noqa: E402


def rec(visits: list, **extra) -> dict:
    return {"file": "t.pdf", "visits": visits, **extra}


def v(name: str = "Visit", day: "int | None" = 0, **kw) -> dict:
    base = {"name": name, "day_offset": day, "window_days": 3, "activities": []}
    base.update(kw)
    return base


# ───────────────────────────── clean baseline ─────────────────────────────

def test_a_well_formed_schedule_is_clean():
    r = rec([
        v("Screening", -14, activities=["Consent"]),
        v("Baseline", 0),
        v("Week 4", 28),
        v("Early Termination", None),
    ], schedule_kind="linear")
    assert inv.check_record(r) == []


def test_failed_extraction_is_not_judged_structurally():
    """An extraction that errored has nothing to check — don't double-report."""
    assert inv.check_record({"file": "t.pdf", "error": "boom"}) == []


# ───────────────────────────── field sanity ─────────────────────────────

def test_unnamed_visit_is_flagged():
    assert any("no name" in m for m in inv.check_visit_fields([v("")]))


def test_unexpanded_template_is_flagged():
    """'Cycle {cycle} Day 1' reaching the UI means expansion silently failed."""
    msgs = inv.check_visit_fields([v("Cycle {cycle} Day 1")])
    assert any("unexpanded name template" in m for m in msgs)


def test_implausible_day_is_flagged():
    assert any("implausible day_offset" in m for m in inv.check_visit_fields([v("X", 99999)]))
    assert any("implausible day_offset" in m for m in inv.check_visit_fields([v("X", -9999)]))


def test_long_but_real_followup_is_not_flagged():
    """A 2-year oncology follow-up is unusual, not invalid."""
    assert inv.check_visit_fields([v("Month 24", 720)]) == []


def test_visit_ending_before_it_starts_is_flagged():
    msgs = inv.check_visit_fields([v("Washout", 10, day_end=4)])
    assert any("ends" in m and "before it starts" in m for m in msgs)


def test_negative_and_absurd_windows_are_flagged():
    assert any("implausible window" in m for m in inv.check_visit_fields([v("X", 0, window_days=-1)]))
    assert any("implausible window" in m for m in inv.check_visit_fields([v("X", 0, window_days=500)]))
    assert any("negative window_before" in m
               for m in inv.check_visit_fields([v("X", 0, window_before=-2)]))


def test_reversed_hour_range_is_flagged():
    msgs = inv.check_visit_fields([v("Hour 0 to -4", 0, hour_offset=0, hour_end=-4)])
    assert any("hour range" in m for m in msgs)


def test_valid_hour_range_is_clean():
    assert inv.check_visit_fields([v("Hour -4 to 0", 0, hour_offset=-4, hour_end=0)]) == []


def test_blank_and_duplicate_activities_are_flagged():
    assert any("blank activity" in m
               for m in inv.check_visit_fields([v("X", 0, activities=["ECG", "  "])]))
    assert any("duplicate activity" in m
               for m in inv.check_visit_fields([v("X", 0, activities=["ECG", "ECG"])]))


# ─────────────────────── duplicates, ordering, collapse ───────────────────────

def test_duplicate_visits_are_flagged():
    msgs = inv.check_no_duplicates([v("Cycle 2 Day 1", 21), v("Cycle 2 Day 1", 21)])
    assert any("duplicate visit" in m for m in msgs)


def test_same_name_on_different_days_is_not_a_duplicate():
    assert inv.check_no_duplicates([v("Imaging", 42), v("Imaging", 84)]) == []


def test_out_of_order_visits_are_flagged():
    assert any("chronological" in m for m in inv.check_ordering([v("B", 30), v("A", 10)]))


def test_dated_visit_after_undated_is_flagged():
    msgs = inv.check_ordering([v("A", 0), v("ET", None), v("B", 30)])
    assert any("after an undated" in m for m in msgs)


def test_undated_visits_trailing_is_clean():
    assert inv.check_ordering([v("A", 0), v("B", 30), v("ET", None), v("Uns", None)]) == []


def test_all_visits_on_one_day_is_flagged():
    """The signature of lost day offsets — patient told to attend 8 visits at once."""
    msgs = inv.check_not_collapsed([v(f"V{i}", 0) for i in range(8)])
    assert any("day offsets were probably lost" in m for m in msgs)


def test_two_visits_on_the_same_day_is_legitimate():
    """Genuinely co-scheduled visits must not trip the collapse check."""
    assert inv.check_not_collapsed([v("Dosing", 0), v("PK draw", 0)]) == []


# ──────────────────────── kind / volume / screening ────────────────────────

def test_kind_none_with_visits_is_contradictory():
    msgs = inv.check_schedule_kind(rec([v("X", 0)], schedule_kind="none"))
    assert any("'none' but 1 visits" in m for m in msgs)


def test_kind_set_with_no_visits_is_contradictory():
    msgs = inv.check_schedule_kind(rec([], schedule_kind="cyclic"))
    assert any("no visits were returned" in m for m in msgs)


def test_checklist_document_extracting_to_empty_is_correct():
    """A GCP checklist SHOULD produce nothing — that is a pass, not a failure."""
    assert inv.check_record(rec([], schedule_kind="none")) == []


def test_runaway_volume_is_flagged():
    assert any("exceeds the 400 cap" in m
               for m in inv.check_volume([v(f"V{i}", i) for i in range(401)]))


def test_positive_day_screening_is_flagged():
    msgs = inv.check_screening_sign([v("Screening", 14)])
    assert any("looks like screening" in m for m in msgs)


def test_screening_by_visit_type_is_also_checked():
    msgs = inv.check_screening_sign([v("Visit 1", 5, visit_type="Screening")])
    assert any("looks like screening" in m for m in msgs)


def test_normal_screening_is_clean():
    assert inv.check_screening_sign([v("Screening", -14)]) == []
    assert inv.check_screening_sign([v("Screening", 0)]) == []


# ────────────────────────────── reporting ──────────────────────────────

def test_report_counts_clean_and_lists_violations():
    recs = [
        rec([v("A", 0)], schedule_kind="linear"),
        rec([v("B", 30), v("A", 10)], schedule_kind="linear"),
        {"file": "broken.pdf", "error": "extract failed"},
    ]
    clean, lines = inv.report(recs)
    assert clean == 1                    # errored record is excluded, not counted clean
    assert any("chronological" in ln for ln in lines)


def test_every_named_check_is_wired_into_check_record():
    """A check that exists but is never run is worse than no check."""
    r = rec([v("Cycle {cycle}", 99999, window_days=-1)], schedule_kind="none")
    prefixes = {m.split("]")[0].lstrip("[") for m in inv.check_record(r)}
    assert {"fields", "schedule_kind"} <= prefixes
    assert len(inv.ALL_CHECKS) == 7


# ───────────────── cross-layer: expansion must satisfy invariants ─────────────────

def test_expanded_real_protocol_passes_every_invariant():
    """The two layers must agree.

    If `expand_schedule` can emit a schedule the invariants reject, one of them
    is wrong — this test is what stops the guard and the producer drifting apart.
    """
    from test_protocol_expansion import picn_schedule            # noqa: PLC0415
    from protocol_extraction import expand_schedule              # noqa: PLC0415

    expanded = expand_schedule(picn_schedule())
    r = rec([v.model_dump() for v in expanded.visits],
            schedule_kind=expanded.schedule_kind)
    assert inv.check_record(r) == []


def test_expanded_open_ended_and_relative_schedules_pass_invariants():
    from protocol_extraction import (  # noqa: PLC0415
        ExtractedSchedule, ExtractedVisit, RepeatMember, RepeatingBlock, expand_schedule,
    )

    sched = ExtractedSchedule(
        schedule_kind="cyclic",
        visits=[
            ExtractedVisit(name="Screening", day_offset=-21, visit_type="Screening"),
            ExtractedVisit(name="Unscheduled", visit_type="Unscheduled"),
            ExtractedVisit(name="Safety Follow-up", relative_to="Screening",
                           relative_offset_days=400),
        ],
        repeating_blocks=[
            RepeatingBlock(from_cycle=1, to_cycle=None, cycle_length_days=28,
                           first_cycle_start_day=0,
                           members=[RepeatMember(name_template="Cycle {cycle} Day 1",
                                                 day_within_cycle=0)]),
        ],
    )
    expanded = expand_schedule(sched)
    r = rec([x.model_dump() for x in expanded.visits],
            schedule_kind=expanded.schedule_kind)
    assert inv.check_record(r) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
