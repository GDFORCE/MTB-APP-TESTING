"""Anchor status vocabulary (MTB requirement doc 1 section 22).

Doc 1 asks for NOT_REQUIRED / AWAITING_EVENT / PLANNED / ACTUAL / CONFIRMED /
CORRECTED, and the reason it asks is the PLANNED-vs-ACTUAL distinction. A planned
surgery date is not evidence that surgery happened. If the system collapses the
two, a 30-day follow-up calculated from an expected date is shown to a site as
though it were anchored to something real, and nobody knows to re-check it when
the surgery moves.
"""

from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.anchor_status import (
    AnchorRecord, AnchorStatus, resolve_anchor_status,
)


def at(day: int) -> datetime:
    return datetime(2026, 9, day, 9, 0, tzinfo=timezone.utc)


def resolve(records, **changes):
    values = {
        "anchor_code": "SURGERY", "display_name": "Surgery Date",
        "records": records,
    }
    values.update(changes)
    return resolve_anchor_status(**values)


# --- nothing recorded ----------------------------------------------------------

def test_an_anchor_with_no_value_is_awaiting_its_event():
    view = resolve([])

    assert view.status == AnchorStatus.AWAITING_EVENT
    assert view.value is None
    assert view.confirmed is False


def test_an_event_derived_anchor_names_the_event_it_waits_on():
    """Doc 1 s3: "Awaiting Surgery Date" is what makes an empty row explicable."""
    view = resolve([], source_event_code="SURGERY_VISIT")

    assert view.status == AnchorStatus.AWAITING_EVENT
    assert view.awaiting_event_code == "SURGERY_VISIT"
    assert "SURGERY_VISIT" in view.reason


def test_an_anchor_no_applicable_visit_needs_is_not_required():
    """Asking a site for a surgery date on a non-surgical arm is noise."""
    view = resolve([], applicable=False)

    assert view.status == AnchorStatus.NOT_REQUIRED
    assert view.value is None


# --- the distinction the requirement exists for --------------------------------

def test_an_expected_date_is_planned_and_never_reported_as_actual():
    view = resolve([AnchorRecord(
        value=date(2026, 10, 1), record_status="PROVISIONAL",
        source_type="PLANNED_PROCEDURE", recorded_at=at(1),
    )])

    assert view.status == AnchorStatus.PLANNED
    assert view.status != AnchorStatus.ACTUAL
    assert view.value == date(2026, 10, 1)
    assert view.confirmed is False
    assert "has not been recorded as having happened" in view.reason


def test_an_unknown_source_type_is_treated_as_planned_not_actual():
    """The safe direction: never let an unrecognised source imply reality."""
    view = resolve([AnchorRecord(
        value=date(2026, 10, 1), record_status="PROVISIONAL",
        source_type="SOMETHING_NEW", recorded_at=at(1),
    )])

    assert view.status == AnchorStatus.PLANNED


def test_a_recorded_real_date_is_actual():
    view = resolve([AnchorRecord(
        value=date(2026, 10, 3), record_status="PROVISIONAL",
        source_type="SOURCE_DOCUMENT", recorded_at=at(4),
    )])

    assert view.status == AnchorStatus.ACTUAL
    assert view.value == date(2026, 10, 3)
    # Still not confirmed: recorded is not the same as reviewed.
    assert view.confirmed is False


def test_a_planned_date_replaced_by_the_real_one_becomes_actual():
    view = resolve([
        AnchorRecord(value=date(2026, 10, 1), record_status="PROVISIONAL",
                     source_type="PLANNED_PROCEDURE", recorded_at=at(1)),
        AnchorRecord(value=date(2026, 10, 3), record_status="PROVISIONAL",
                     source_type="SOURCE_DOCUMENT", recorded_at=at(4)),
    ])

    assert view.status == AnchorStatus.ACTUAL
    assert view.value == date(2026, 10, 3)


# --- confirmation and correction ------------------------------------------------

def test_a_confirmed_date_is_confirmed():
    view = resolve([AnchorRecord(
        value=date(2026, 10, 3), record_status="CONFIRMED",
        source_type="SOURCE_DOCUMENT", recorded_at=at(5),
    )])

    assert view.status == AnchorStatus.CONFIRMED
    assert view.confirmed is True


def test_changing_a_confirmed_date_reads_as_corrected_not_merely_confirmed():
    """A reviewer needs to see that a confirmed value was changed."""
    view = resolve([
        AnchorRecord(value=date(2026, 10, 3), record_status="CONFIRMED",
                     source_type="SOURCE_DOCUMENT", recorded_at=at(5),
                     superseded=True),
        AnchorRecord(value=date(2026, 10, 4), record_status="CONFIRMED",
                     source_type="SOURCE_DOCUMENT", recorded_at=at(9)),
    ])

    assert view.status == AnchorStatus.CORRECTED
    assert view.value == date(2026, 10, 4)
    assert view.confirmed is True


def test_a_first_confirmation_after_a_provisional_value_is_not_a_correction():
    """Confirming what was already there is not the same as changing it."""
    view = resolve([
        AnchorRecord(value=date(2026, 10, 3), record_status="PROVISIONAL",
                     source_type="SOURCE_DOCUMENT", recorded_at=at(5),
                     superseded=True),
        AnchorRecord(value=date(2026, 10, 3), record_status="CONFIRMED",
                     source_type="SOURCE_DOCUMENT", recorded_at=at(6)),
    ])

    assert view.status == AnchorStatus.CONFIRMED


def test_a_superseded_value_never_becomes_the_current_one():
    view = resolve([
        AnchorRecord(value=date(2026, 10, 3), record_status="CONFIRMED",
                     source_type="SOURCE_DOCUMENT", recorded_at=at(5),
                     superseded=True),
    ])

    assert view.status == AnchorStatus.AWAITING_EVENT
    assert view.value is None


def test_a_datetime_anchor_keeps_its_time_of_day():
    """Doc 5 needs the time, not just the date, for intra-day scheduling."""
    moment = datetime(2026, 10, 3, 14, 30, tzinfo=timezone.utc)
    view = resolve([AnchorRecord(
        value=moment, record_status="CONFIRMED", source_type="DOSE_ADMINISTRATION",
        recorded_at=at(5),
    )])

    assert view.value == moment
