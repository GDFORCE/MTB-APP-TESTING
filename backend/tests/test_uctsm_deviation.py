"""Protocol deviation detection, driven by the requirement's own worked examples.

Sources: doc 1 s18 and s26, doc 2 s39, doc 3 s31, doc 4 s25, doc 5 s17,
doc 6 s38, doc 9 s27.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.deviation import (
    DeviationType, assess_activity, assess_confinement, assess_visit,
)

TODAY = date(2026, 10, 15)


def visit(**changes):
    values = {
        "logical_key": "POSTOP_7:0", "event_code": "POSTOP_7",
        "display_name": "Post-operative Day 7", "status": "COMPLETED",
        "planned_date": date(2026, 9, 27),
        "earliest_date": date(2026, 9, 25), "latest_date": date(2026, 9, 29),
        "actual_date": None, "today": TODAY,
    }
    values.update(changes)
    return assess_visit(**values)


# --- doc 1 s26: "Actual Post-op visit 30-Sep -> outside window, 1 day late" ------

def test_visit_outside_the_window_is_reported_late_from_the_window_edge():
    result = visit(actual_date=date(2026, 9, 30))

    assert result.deviation_type == DeviationType.LATE
    assert result.within_window is False
    # Measured from the window edge (29-Sep), exactly as the requirement states.
    assert result.outside_window_days == 1
    # The distance from the target is also available, for context.
    assert result.delta_from_planned_days == 3
    assert "outside the protocol window" in result.reason


# --- doc 1 s18: "Corrected expected 27-Sep, Actual 25-Sep -> early by 2 days" ----

def test_in_window_visit_still_reports_its_distance_from_the_expected_date():
    result = visit(actual_date=date(2026, 9, 25))

    # 25-Sep is inside the +/-2 window, so this is not a protocol deviation...
    assert result.deviation_type == DeviationType.NONE
    assert result.within_window is True
    assert result.outside_window_days == 0
    # ...but the requirement still wants "early by 2 days" visible.
    assert result.delta_from_planned_days == -2
    assert "2 day(s) early" in result.reason


def test_visit_on_the_expected_date_is_clean():
    result = visit(actual_date=date(2026, 9, 27))
    assert result.deviation_type == DeviationType.NONE
    assert result.delta_from_planned_days == 0
    assert "on the protocol-expected date" in result.reason


def test_visit_before_the_window_is_reported_early():
    result = visit(actual_date=date(2026, 9, 22))
    assert result.deviation_type == DeviationType.EARLY
    assert result.outside_window_days == 3
    assert "before the allowed window" in result.reason


# --- doc 2 s39: an unactivated conditional visit cannot deviate -----------------

def test_unactivated_conditional_visit_is_not_assessable():
    result = visit(status="WAITING_FOR_CONDITION", actual_date=None,
                   planned_date=None, earliest_date=None, latest_date=None)

    assert result.deviation_type == DeviationType.NOT_ASSESSABLE
    assert "never activated" in result.reason


def test_awaiting_anchor_visit_is_not_assessable_and_never_missed():
    """Doc 1 s23: a visit whose anchor has not occurred must not look overdue."""
    result = visit(status="WAITING_FOR_ANCHOR", actual_date=None,
                   planned_date=None, earliest_date=None, latest_date=None)
    assert result.deviation_type == DeviationType.NOT_ASSESSABLE
    assert result.deviation_type != DeviationType.MISSED


def test_cancelled_and_paused_occurrences_are_not_assessable():
    for status in ("CANCELLED", "PAUSED", "NOT_APPLICABLE"):
        result = visit(status=status, actual_date=None, planned_date=None)
        assert result.deviation_type == DeviationType.NOT_ASSESSABLE, status


# --- missed vs not yet due ------------------------------------------------------

def test_visit_whose_window_closed_with_no_actual_is_missed():
    result = visit(status="RESOLVED", actual_date=None)
    assert result.deviation_type == DeviationType.MISSED
    assert result.outside_window_days == (TODAY - date(2026, 9, 29)).days
    assert "window closed on 2026-09-29" in result.reason


def test_visit_still_inside_its_window_is_not_missed():
    result = visit(
        status="RESOLVED", actual_date=None,
        planned_date=date(2026, 10, 16),
        earliest_date=date(2026, 10, 14), latest_date=date(2026, 10, 18),
    )
    assert result.deviation_type == DeviationType.NONE
    assert result.reason == "not yet due"


# --- doc 4 s25: a legitimately moved date is not a deviation --------------------

def test_actual_previous_event_move_is_not_a_deviation():
    """C2 ran 7 days late, so C3 legitimately moved from 13-Oct to 20-Oct.

    Doc 4 s25: C3 must NOT be flagged merely because its original provisional date
    was 13-Oct. The comparison uses the CURRENT protocol-expected date.
    """
    result = assess_visit(
        logical_key="C3D1:0", event_code="C3D1", display_name="Cycle 3 Day 1",
        status="COMPLETED",
        planned_date=date(2026, 10, 20),          # recalculated from actual C2
        earliest_date=date(2026, 10, 17), latest_date=date(2026, 10, 23),
        actual_date=date(2026, 10, 20), today=TODAY,
    )
    assert result.deviation_type == DeviationType.NONE
    assert result.delta_from_planned_days == 0


def test_delayed_cycle_itself_is_still_reported_against_its_nominal_window():
    """Doc 3 s31 / doc 4 s25: C2 expected 22-Sep +/-3, actual 29-Sep -> deviation."""
    result = assess_visit(
        logical_key="C2D1:0", event_code="C2D1", display_name="Cycle 2 Day 1",
        status="COMPLETED", planned_date=date(2026, 9, 22),
        earliest_date=date(2026, 9, 19), latest_date=date(2026, 9, 25),
        actual_date=date(2026, 9, 29), today=TODAY,
    )
    assert result.deviation_type == DeviationType.LATE
    assert result.delta_from_planned_days == 7
    assert result.outside_window_days == 4


# --- doc 9 s27: the window comes from the patient's assigned version -------------

def test_the_same_actual_deviates_under_a_tighter_version_window():
    """Version 1 allows +/-3 days, version 2 allows +/-5. A v1 patient must be
    assessed against +/-3 even after v2 is approved."""
    common = {
        "logical_key": "W4:0", "event_code": "W4", "display_name": "Week 4",
        "status": "COMPLETED", "planned_date": date(2026, 9, 29),
        "actual_date": date(2026, 10, 3), "today": TODAY,
    }
    v1 = assess_visit(earliest_date=date(2026, 9, 26), latest_date=date(2026, 10, 2), **common)
    v2 = assess_visit(earliest_date=date(2026, 9, 24), latest_date=date(2026, 10, 4), **common)

    assert v1.deviation_type == DeviationType.LATE and v1.outside_window_days == 1
    assert v2.deviation_type == DeviationType.NONE


# --- doc 5 s17: activity-level timing deviation ---------------------------------

def utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 1, hour, minute, tzinfo=timezone.utc)


def activity(**changes):
    values = {
        "logical_key": "C1D1:0#PK_2H", "activity_code": "PK_2H",
        "display_name": "PK sample 2 hours post-dose", "status": "COMPLETED",
        "planned_time": utc(11, 30),
        "earliest_time": utc(11, 20), "latest_time": utc(11, 40),
        "actual_time": None,
    }
    values.update(changes)
    return assess_activity(**values)


def test_activity_collected_late_is_a_timing_deviation():
    """Planned 11:30, window 11:20-11:40, actual 11:48 -> deviation."""
    result = activity(actual_time=utc(11, 48))
    assert result.deviation_type == DeviationType.LATE
    assert result.outside_window_minutes == 8
    assert result.delta_from_planned_minutes == 18
    assert "activity timing deviation" in result.reason


def test_activity_inside_its_window_is_clean_but_still_reports_the_offset():
    result = activity(actual_time=utc(11, 38))
    assert result.deviation_type == DeviationType.NONE
    assert result.within_window is True
    assert result.delta_from_planned_minutes == 8
    assert result.outside_window_minutes == 0


def test_activity_marked_not_done_is_missed():
    result = activity(status="NOT_DONE", actual_time=None)
    assert result.deviation_type == DeviationType.MISSED


def test_activity_still_waiting_on_its_anchor_is_not_assessable():
    result = activity(status="WAITING_FOR_ANCHOR", planned_time=None, actual_time=None,
                      earliest_time=None, latest_time=None)
    assert result.deviation_type == DeviationType.NOT_ASSESSABLE


def test_activity_not_applicable_to_this_patient_is_not_assessable():
    result = activity(status="NOT_APPLICABLE")
    assert result.deviation_type == DeviationType.NOT_ASSESSABLE


# --- doc 6 s38: confinement admission and discharge ------------------------------

def test_late_admission_and_late_discharge_are_reported_separately():
    result = assess_confinement(
        episode_code="CONFINEMENT_1", display_name="Confinement Period 1",
        planned_admission=date(2026, 9, 10), actual_admission=date(2026, 9, 11),
        planned_discharge=date(2026, 9, 13), actual_discharge=date(2026, 9, 14),
        extended=False,
    )
    assert result.admission_deviation_days == 1
    assert result.discharge_deviation_days == 1
    assert "admission 1 day(s) late" in result.reason
    assert "discharge 1 day(s) late" in result.reason


def test_a_confirmed_extension_is_distinguished_from_an_unexplained_late_discharge():
    extended = assess_confinement(
        episode_code="CONFINEMENT_1", display_name="Confinement Period 1",
        planned_admission=date(2026, 9, 10), actual_admission=date(2026, 9, 10),
        planned_discharge=date(2026, 9, 14), actual_discharge=date(2026, 9, 14),
        extended=True,
    )
    assert extended.discharge_deviation_days == 0
    assert "extended under a confirmed protocol condition" in extended.reason


def test_confinement_matching_the_plan_reports_no_finding():
    result = assess_confinement(
        episode_code="CONFINEMENT_1", display_name="Confinement Period 1",
        planned_admission=date(2026, 9, 10), actual_admission=date(2026, 9, 10),
        planned_discharge=date(2026, 9, 13), actual_discharge=date(2026, 9, 13),
        extended=False,
    )
    assert result.reason == "admission and discharge matched the plan"
