"""The gate that has to pass before visit reads move to the engine.

This is the riskiest change in the project: it swaps the scheduling engine under
patients who are already enrolled. The tests below are almost all about REFUSING
to cut over, because that is the behaviour that matters. A gate that passes when
it should not is worse than no gate, since it carries the authority of having
been checked.
"""

from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.parity import (
    ComparableVisit, DifferenceKind, ParityVerdict, compare_schedules,
    normalize_key, to_comparable,
)


def visit(name: str, scheduled: str | None, before: str | None = None,
          after: str | None = None) -> ComparableVisit:
    return ComparableVisit(
        key=normalize_key(name), name=name,
        scheduled_date=date.fromisoformat(scheduled) if scheduled else None,
        window_start=date.fromisoformat(before) if before else None,
        window_end=date.fromisoformat(after) if after else None,
    )


def kinds(report) -> list[DifferenceKind]:
    return [item.kind for item in report.differences]


# --- the only case that may pass ------------------------------------------------

def test_two_identical_schedules_match():
    schedule = [
        visit("Screening", "2026-09-01", "2026-08-29", "2026-09-04"),
        visit("Week 4", "2026-09-29", "2026-09-27", "2026-10-01"),
    ]
    report = compare_schedules(schedule, list(schedule))

    assert report.verdict == ParityVerdict.MATCH
    assert report.passed is True
    assert report.matched == 2
    assert report.differences == []


def test_naming_differences_alone_do_not_block():
    """"Week 4" and "WEEK-4" are the same visit; the words are what matter."""
    report = compare_schedules(
        [visit("Week 4", "2026-09-29")],
        [ComparableVisit(key=normalize_key("WEEK-4"), name="WEEK-4",
                         scheduled_date=date(2026, 9, 29))],
    )

    assert report.passed is True


# --- everything else blocks ------------------------------------------------------

def test_a_single_day_of_disagreement_blocks_the_cutover():
    """There is no tolerance setting. "Close enough" is how a window gets missed."""
    report = compare_schedules(
        [visit("Week 4", "2026-09-29")],
        [visit("Week 4", "2026-09-30")],
    )

    assert report.verdict == ParityVerdict.DIFFERENCES_FOUND
    assert report.passed is False
    assert kinds(report) == [DifferenceKind.DATE_DIFFERS]
    assert "disagree by 1 day" in report.differences[0].detail


def test_a_visit_the_engine_does_not_produce_blocks():
    """Two systems disagreeing about which visits EXIST is the worst failure."""
    report = compare_schedules(
        [visit("Week 4", "2026-09-29"), visit("Week 8", "2026-10-27")],
        [visit("Week 4", "2026-09-29")],
    )

    assert kinds(report) == [DifferenceKind.MISSING_IN_ENGINE]
    assert report.differences[0].name == "Week 8"


def test_a_visit_only_the_engine_produces_blocks():
    report = compare_schedules(
        [visit("Week 4", "2026-09-29")],
        [visit("Week 4", "2026-09-29"), visit("Week 8", "2026-10-27")],
    )

    assert kinds(report) == [DifferenceKind.MISSING_IN_LEGACY]


def test_the_engine_refusing_to_date_a_visit_still_needs_a_decision():
    """Usually the engine is right - but a site currently sees a date."""
    report = compare_schedules(
        [visit("Post-op follow-up", "2026-10-01")],
        [visit("Post-op follow-up", None)],
    )

    assert kinds(report) == [DifferenceKind.ENGINE_UNDATED]
    assert "usually correct" in report.differences[0].detail
    assert report.passed is False


def test_the_engine_dating_something_legacy_left_blank_also_blocks():
    report = compare_schedules(
        [visit("Week 4", None)],
        [visit("Week 4", "2026-09-29")],
    )

    assert kinds(report) == [DifferenceKind.LEGACY_UNDATED]


def test_a_window_difference_blocks_even_when_the_date_agrees():
    """A visit in window in one system can be out of window in the other."""
    report = compare_schedules(
        [visit("Week 4", "2026-09-29", "2026-09-22", "2026-10-06")],
        [visit("Week 4", "2026-09-29", "2026-09-27", "2026-10-01")],
    )

    assert kinds(report) == [DifferenceKind.WINDOW_DIFFERS]
    assert report.matched == 0


def test_two_visits_with_the_same_name_are_never_paired_by_position():
    """Pairing by list order is exactly the quiet guess this gate prevents."""
    report = compare_schedules(
        [visit("Cycle Day 1", "2026-09-01"), visit("Cycle Day 1", "2026-09-22")],
        [visit("Cycle Day 1", "2026-09-01"), visit("Cycle Day 1", "2026-09-22")],
    )

    assert kinds(report) == [DifferenceKind.AMBIGUOUS_MATCH]
    assert report.passed is False


def test_an_empty_comparison_is_not_a_pass():
    """Nothing to compare proves nothing. It must not read as agreement."""
    report = compare_schedules([], [])

    assert report.verdict == ParityVerdict.NOT_COMPARABLE
    assert report.passed is False


def test_every_difference_is_reported_not_just_the_first():
    report = compare_schedules(
        [visit("Week 4", "2026-09-29"), visit("Week 8", "2026-10-27"),
         visit("Week 12", "2026-11-24")],
        [visit("Week 4", "2026-09-30"), visit("Week 8", "2026-10-27")],
    )

    assert set(kinds(report)) == {
        DifferenceKind.DATE_DIFFERS, DifferenceKind.MISSING_IN_ENGINE}
    assert report.matched == 1
    assert report.compared == 3


# --- reading either system's documents --------------------------------------------

def test_operational_documents_reduce_to_the_comparable_shape():
    rows = to_comparable(
        [{
            "name": "Week 4", "scheduled_date": "2026-09-29T00:00:00+00:00",
            "window_start": "2026-09-27T00:00:00+00:00",
            "window_end": "2026-10-01T00:00:00+00:00",
        }],
        key_fields=("name",),
    )

    assert rows[0].scheduled_date == date(2026, 9, 29)
    assert rows[0].window_start == date(2026, 9, 27)


def test_a_document_with_no_usable_date_reads_as_undated_not_as_today():
    rows = to_comparable([{"name": "Week 4", "scheduled_date": ""}], key_fields=("name",))

    assert rows[0].scheduled_date is None


def test_an_unparseable_date_is_undated_rather_than_guessed():
    rows = to_comparable(
        [{"name": "Week 4", "scheduled_date": "next Tuesday"}], key_fields=("name",))

    assert rows[0].scheduled_date is None


def test_a_nameless_document_still_gets_a_key_rather_than_being_dropped():
    rows = to_comparable([{"scheduled_date": "2026-09-29"}], key_fields=("name",))

    assert rows[0].key == "unnamed"
    assert len(rows) == 1
