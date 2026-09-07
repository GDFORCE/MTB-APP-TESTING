"""Typed schedule-version comparison (MTB requirement doc 9 section 10).

A raw field diff says "these bytes differ". A reviewer deciding whether an
amendment affects patients already on the study needs to know that the Week 4
window narrowed from +/-7 to +/-3 days, because that can put people already booked
outside their window - while renaming the same visit cannot.

So the classification errs towards CLINICAL: anything that moves when a patient is
seen or changes what is done to them is significant, and anything unrecognised is
significant too, because an unclassified change is one nobody has looked at.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.diff import (
    ChangeType, Significance, compare_schedule_versions,
)
from app.domain.schedule.models import (
    Activity, Anchor, ApplicabilityRule, Event, ScheduleMetadata, StudyDimension,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, NominalWindowTiming, NonNegativeTemporalAmount, OffsetTiming,
    TemporalAmount, Window,
)


def windowed(days: int, before: int, after: int) -> NominalWindowTiming:
    return NominalWindowTiming(
        nominal=OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=days, unit="DAY"),
        ),
        window=Window(
            before=NonNegativeTemporalAmount(value=before, unit="DAY"),
            after=NonNegativeTemporalAmount(value=after, unit="DAY"),
        ),
    )


def visit(code: str, days: int, *, before: int = 7, after: int = 7, **changes) -> Event:
    values = {
        "code": code, "protocol_label": code,
        "display_name": code.replace("_", " ").title(),
        "event_type": "SITE_VISIT", "timing": windowed(days, before, after),
    }
    values.update(changes)
    return Event(**values)


def schedule(events: list[Event], *, anchors: list[Anchor] | None = None) -> UniversalSchedule:
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary"),
        anchors=anchors or [
            Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        arms=[StudyDimension(code="ARM_A", protocol_label="A", display_name="Arm A")],
        events=events,
    )


def types(diff) -> list[ChangeType]:
    return [item.change_type for item in diff.typed_changes]


def only(diff, change_type):
    return next(item for item in diff.typed_changes if item.change_type == change_type)


# --- added and removed ----------------------------------------------------------

def test_a_new_visit_is_typed_as_added_and_is_clinically_significant():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28)]),
        schedule([visit("WEEK_4", 28), visit("WEEK_8", 56)]),
    )

    change = only(diff, ChangeType.VISIT_ADDED)
    assert change.entity_code == "WEEK_8"
    assert change.significance == Significance.CLINICAL
    assert "new visit" in change.summary


def test_a_removed_visit_says_what_it_means_for_patients_already_booked():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28), visit("WEEK_8", 56)]),
        schedule([visit("WEEK_4", 28)]),
    )

    change = only(diff, ChangeType.VISIT_REMOVED)
    assert change.significance == Significance.CLINICAL
    assert "not removed from their history" in change.detail


# --- the distinction the requirement exists for ---------------------------------

def test_a_rename_is_administrative_not_clinical():
    """It changes what a screen says, not what happens to a patient."""
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28)]),
        schedule([visit("WEEK_4", 28, display_name="Week 4 (Safety)")]),
    )

    change = only(diff, ChangeType.VISIT_RENAMED)
    assert change.significance == Significance.ADMINISTRATIVE
    assert diff.clinically_significant() == []


def test_a_narrowed_window_is_clinically_significant():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, before=7, after=7)]),
        schedule([visit("WEEK_4", 28, before=3, after=3)]),
    )

    change = only(diff, ChangeType.WINDOW_NARROWED)
    assert change.significance == Significance.CLINICAL
    assert "narrowed from -7/+7 to -3/+3 days" in change.summary
    assert "already booked may now fall outside" in change.detail


def test_a_widened_window_is_administrative():
    """Nothing already booked can fall outside a wider window."""
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, before=3, after=3)]),
        schedule([visit("WEEK_4", 28, before=7, after=7)]),
    )

    change = only(diff, ChangeType.WINDOW_WIDENED)
    assert change.significance == Significance.ADMINISTRATIVE
    assert diff.clinically_significant() == []


def test_an_asymmetric_window_change_is_narrowing_if_either_side_shrinks():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, before=7, after=7)]),
        schedule([visit("WEEK_4", 28, before=10, after=3)]),
    )

    assert ChangeType.WINDOW_NARROWED in types(diff)


def test_moving_the_visit_itself_is_a_timing_change_not_a_window_change():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28)]),
        schedule([visit("WEEK_4", 35)]),
    )

    change = only(diff, ChangeType.TIMING_CHANGED)
    assert change.significance == Significance.CLINICAL
    assert "recalculated" in change.detail
    assert ChangeType.WINDOW_NARROWED not in types(diff)
    assert ChangeType.WINDOW_WIDENED not in types(diff)


# --- activities -----------------------------------------------------------------

def ecg() -> Activity:
    return Activity(code="ECG", protocol_label="ECG", display_name="ECG",
                    activity_type="ASSESSMENT")


def bloods() -> Activity:
    return Activity(code="BLOODS", protocol_label="Bloods", display_name="Bloods",
                    activity_type="SAMPLE")


def test_an_added_activity_names_the_visit_it_was_added_to():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, activities=[ecg()])]),
        schedule([visit("WEEK_4", 28, activities=[ecg(), bloods()])]),
    )

    change = only(diff, ChangeType.ACTIVITY_ADDED)
    assert change.significance == Significance.CLINICAL
    assert "Bloods was added to Week 4" in change.summary


def test_a_removed_activity_is_reported_per_activity_not_as_one_blob():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, activities=[ecg(), bloods()])]),
        schedule([visit("WEEK_4", 28, activities=[ecg()])]),
    )

    change = only(diff, ChangeType.ACTIVITY_REMOVED)
    assert "Bloods is no longer done at Week 4" in change.summary


def test_an_unchanged_activity_with_a_different_id_is_not_a_change():
    """Two runs generate different UUIDs; that is not an amendment."""
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, activities=[ecg()])]),
        schedule([visit("WEEK_4", 28, activities=[ecg()])]),
    )

    assert diff.typed_changes == []


# --- the rest of the vocabulary --------------------------------------------------

def test_a_changed_applicability_is_reported_as_a_different_group():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28)]),
        schedule([visit("WEEK_4", 28, applicability=[
            ApplicabilityRule(dimension="ARM", operator="IN", values=["ARM_A"])])]),
    )

    change = only(diff, ChangeType.APPLICABILITY_CHANGED)
    assert change.significance == Significance.CLINICAL
    assert "different group of patients" in change.summary


def test_a_changed_visit_mode_warns_about_travel():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28, visit_mode="TELEPHONE")]),
        schedule([visit("WEEK_4", 28, visit_mode="CLINIC")]),
    )

    change = only(diff, ChangeType.VISIT_MODE_CHANGED)
    assert change.significance == Significance.CLINICAL
    assert "told to travel" in change.detail


def test_a_new_anchor_says_sites_must_supply_it():
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28)]),
        schedule([visit("WEEK_4", 28)], anchors=[
            Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE"),
            Anchor(code="SURGERY", display_name="Surgery Date", anchor_type="SURGERY"),
        ]),
    )

    change = only(diff, ChangeType.ANCHOR_ADDED)
    assert change.entity_type == "ANCHOR"
    assert change.significance == Significance.CLINICAL
    assert "supply this date" in change.detail


def test_identical_versions_produce_nothing():
    plan = schedule([visit("WEEK_4", 28, activities=[ecg()])])
    diff = compare_schedule_versions(plan, plan)

    assert diff.typed_changes == []
    assert diff.clinically_significant() == []


def test_the_raw_field_changes_are_still_produced_for_existing_callers():
    """Backward compatibility: typed_changes is additive, not a replacement."""
    diff = compare_schedule_versions(
        schedule([visit("WEEK_4", 28)]),
        schedule([visit("WEEK_4", 35)]),
    )

    assert [item.field_path for item in diff.changes] == ["timing"]
    assert diff.added_events == [] and diff.removed_events == []
