from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.models import (
    Anchor, Dependency, Event, Qualifier, ScheduleMetadata, UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.domain.schedule.validator import ScheduleValidator


def _event(**changes) -> Event:
    values = {
        "code": "VISIT_2",
        "protocol_label": "Visit 2",
        "display_name": "Visit 2",
        "event_type": "VISIT",
        "timing": OffsetTiming(
            reference=AnchorReference(code="BASELINE"),
            offset=TemporalAmount(value=7, unit="DAY"),
        ),
    }
    values.update(changes)
    return Event(**values)


def _schedule(event: Event) -> UniversalSchedule:
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Test"),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        events=[event],
    )


def test_dependency_without_explicit_mode_is_blocking():
    event = _event(dependencies=[
        Dependency(source_event_code="VISIT_1", dependency_type="SEQUENCE"),
    ])
    schedule = _schedule(event)
    schedule.events.insert(0, _event(code="VISIT_1", protocol_label="Visit 1", display_name="Visit 1"))

    issues = ScheduleValidator().validate(schedule)

    assert any(issue.issue_code == "UNCLEAR_DEPENDENCY_MODE" and issue.blocking for issue in issues)


def test_orphan_unresolved_qualifier_is_blocking():
    schedule = _schedule(_event(qualifiers=[
        Qualifier(marker="a", text="See footnote", scope="VISIT"),
    ]))

    issue_codes = {issue.issue_code for issue in ScheduleValidator().validate(schedule)}

    assert {"ORPHAN_QUALIFIER", "UNRESOLVED_QUALIFIER"} <= issue_codes


def test_same_marker_with_conflicting_meaning_is_blocking():
    schedule = _schedule(_event(qualifiers=[
        Qualifier(
            marker="a", text="Perform only for Arm A", scope="VISIT",
            category="APPLICABILITY", target_codes=["VISIT_2"], resolved=True,
        ),
        Qualifier(
            marker="a", text="Perform only for Arm B", scope="VISIT",
            category="APPLICABILITY", target_codes=["VISIT_2"], resolved=True,
        ),
    ]))

    issues = ScheduleValidator().validate(schedule)

    assert any(
        issue.issue_code == "CONFLICTING_EVIDENCE" and issue.blocking
        for issue in issues
    )
