import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_extraction import ExtractedSchedule, expand_schedule  # noqa: E402
from schedule_schema import (  # noqa: E402
    CanonicalSchedulePlan,
    DocumentTaskClassification,
    RecurrenceRule,
    ScheduleAnchor,
    ScheduleConflict,
    ScheduleEvent,
    TemporalAmount,
    TimingExpression,
    TransitionRule,
    WindowSpec,
    apply_temporal_amount,
    project_canonical_plan,
    validate_canonical_plan,
)


def test_calendar_month_is_preserved_instead_of_converted_to_thirty_days():
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-baseline", name="Baseline", anchor_type="randomization")],
        events=[ScheduleEvent(
            id="event-month-1", name="Month 1",
            timing=TimingExpression(
                kind="calendar_offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=1, unit="month"),
                source_label="Month 1"))])

    assert plan.events[0].timing.offset.unit == "month"
    assert plan.events[0].timing.calendar_mode == "calendar"
    assert validate_canonical_plan(plan) == []


def test_calendar_resolver_handles_month_lengths_and_leap_years():
    one_month = TemporalAmount(value=1, unit="month")
    one_year = TemporalAmount(value=1, unit="year")

    assert apply_temporal_amount(date(2024, 1, 31), one_month) == date(2024, 2, 29)
    assert apply_temporal_amount(date(2023, 1, 31), one_month) == date(2023, 2, 28)
    assert apply_temporal_amount(date(2024, 2, 29), one_year) == date(2025, 2, 28)


def test_calendar_month_row_carries_exact_offset_for_later_patient_scheduling():
    """The flat day_offset is a 30-day/month approximation for the template
    view. project_canonical_plan must also emit the original (value, unit) so
    a real patient anchor date can be scheduled with exact calendar math
    instead of the approximation (see server.py _calculate_template_datetime).
    """
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-baseline", name="Baseline", anchor_type="randomization")],
        events=[ScheduleEvent(
            id="event-month-1", name="Month 1",
            timing=TimingExpression(
                kind="calendar_offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=1, unit="month"),
                source_label="Month 1"))])

    rows, _warnings = project_canonical_plan(plan)

    assert rows[0]["day_offset"] == 30  # flat 30-day/month approximation
    assert rows[0]["calendar_offset_value"] == 1
    assert rows[0]["calendar_offset_unit"] == "month"

    # For a patient whose real baseline lands near a month boundary, the
    # approximation and exact calendar math genuinely diverge — this is why
    # (value, unit) is worth preserving instead of just the approximate
    # day_offset (see server.py _calculate_template_datetime).
    anchor_date = date(2024, 1, 31)
    approx_date = anchor_date + timedelta(days=rows[0]["day_offset"])
    exact_date = apply_temporal_amount(
        anchor_date, TemporalAmount(value=1, unit="month"))
    assert exact_date == date(2024, 2, 29)
    assert exact_date != approx_date


def test_calendar_offset_is_not_carried_for_a_before_relation_or_chained_event():
    """A visit BEFORE baseline gets a negative calendar_offset_value; an event
    chained onto another event's timing (no single real anchor date to apply
    exact math against at the per-patient stage) gets none at all."""
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-baseline", name="Baseline", anchor_type="randomization")],
        events=[
            ScheduleEvent(
                id="event-screening", name="Screening",
                timing=TimingExpression(
                    kind="calendar_offset", anchor_id="anchor-baseline",
                    relation="before",
                    offset=TemporalAmount(value=1, unit="month"),
                    source_label="1 month before baseline")),
            ScheduleEvent(
                id="event-chained", name="1 month after screening",
                timing=TimingExpression(
                    kind="calendar_offset", anchor_id="event-screening",
                    offset=TemporalAmount(value=1, unit="month"),
                    source_label="1 month after screening")),
        ])

    rows, _warnings = project_canonical_plan(plan)
    by_id = {row["canonical_event_id"]: row for row in rows}

    assert by_id["event-screening"]["calendar_offset_value"] == -1
    assert by_id["event-screening"]["calendar_offset_unit"] == "month"
    assert by_id["event-chained"]["calendar_offset_value"] is None
    assert by_id["event-chained"]["calendar_offset_unit"] is None


def test_event_driven_visit_and_asymmetric_window_are_first_class():
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-discharge", name="Discharge", anchor_type="discharge")],
        events=[ScheduleEvent(
            id="event-call", name="Safety call",
            timing=TimingExpression(
                kind="relative", anchor_id="anchor-discharge", relation="after",
                offset=TemporalAmount(value=7, unit="day"),
                source_label="7 days after discharge"),
            window=WindowSpec(
                state="stated",
                early=TemporalAmount(value=2, unit="day"),
                late=TemporalAmount(value=3, unit="day"),
                source_label="-2/+3 days"))])

    assert plan.events[0].window.early.value == 2
    assert plan.events[0].window.late.value == 3
    assert validate_canonical_plan(plan) == []


def test_open_ended_recurrence_is_retained_and_forces_review():
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-baseline", name="Baseline", anchor_type="first_dose")],
        events=[ScheduleEvent(
            id="event-cycle", name="Cycle Day 1",
            timing=TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=0, unit="day")))],
        recurrences=[RecurrenceRule(
            id="recurrence-treatment", event_ids=["event-cycle"],
            frequency=TemporalAmount(value=3, unit="week"),
            source_label="Every 3 weeks until progression")])

    issues = validate_canonical_plan(plan)
    assert any("open-ended" in issue for issue in issues)


def test_conflicts_and_invalid_references_are_not_silently_accepted():
    plan = CanonicalSchedulePlan(
        events=[ScheduleEvent(
            id="event-follow-up", name="Follow-up",
            timing=TimingExpression(
                kind="relative", anchor_id="event-missing", relation="after",
                offset=TemporalAmount(value=30, unit="day")))],
        transitions=[TransitionRule(
            id="transition-1", from_event_id="event-missing",
            to_event_id="event-follow-up", relation="after")],
        conflicts=[ScheduleConflict(
            id="conflict-1", field_path="events.follow-up.timing",
            description="Synopsis says 28 days; schedule table says 30 days")])

    issues = validate_canonical_plan(plan)
    assert any("unknown timing anchor" in issue for issue in issues)
    assert any("unknown transition event" in issue for issue in issues)
    assert any("Unresolved source conflict" in issue for issue in issues)


def test_flat_schedule_gets_backward_compatible_canonical_fallback():
    schedule = ExtractedSchedule(
        schedule_kind="linear", anchor_study_day=1,
        classification=DocumentTaskClassification(
            document_type="protocol", analysis_task="full_protocol_schedule",
            schedule_archetypes=["linear"], complexity="simple",
            has_schedule=True, confidence=0.98),
        visits=[{"name": "Baseline", "day_offset": 0, "window_days": 3}])

    expanded = expand_schedule(schedule)

    assert expanded.visits[0].name == "Baseline"
    assert expanded.canonical_plan.schema_version == "2.0"
    assert expanded.canonical_plan.events[0].window.early.value == 3
    assert expanded.classification.analysis_task == "full_protocol_schedule"


def test_canonical_projection_separates_visit_window_from_procedure_constraint():
    from schedule_schema import ActivityTemplate

    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-dose", name="Dose", anchor_type="first_dose")],
        activities=[ActivityTemplate(
            id="activity-infusion", name="Infusion",
            timing=TimingExpression(
                kind="constraint", source_label="Infusion over 30 minutes"),
            window=WindowSpec(
                scope="activity", state="stated",
                early=TemporalAmount(value=1, unit="minute"),
                late=TemporalAmount(value=1, unit="minute"),
                source_label="±1 minute"))],
        events=[ScheduleEvent(
            id="event-dose", name="Dosing Visit",
            timing=TimingExpression(
                kind="offset", anchor_id="anchor-dose",
                offset=TemporalAmount(value=0, unit="day"),
                source_label="Day 1"),
            window=WindowSpec(state="not_stated"),
            activity_ids=["activity-infusion"])])

    rows, warnings = project_canonical_plan(plan)

    assert warnings == []
    assert rows[0]["window_days"] is None
    assert rows[0]["procedures"][0]["window"] == "±1 minute"
    assert rows[0]["operational_constraints"] == [
        "Infusion — timing: Infusion over 30 minutes; window: ±1 minute"]


def test_canonical_projection_converts_week_unit_windows_to_days():
    """A tolerance window stated in weeks (e.g. Table 1's "Week 2 (±1 week)",
    "Month 4 (±2 weeks)") must resolve into window_days/window_before/
    window_after like a day-stated window does, instead of being dropped to
    an operational_constraint note — the window UNIT is explicit and exact
    here, unlike an ordinal "Week N" visit label, which is never assumed to
    mean seven days."""
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-baseline", name="Baseline", anchor_type="randomization")],
        events=[
            ScheduleEvent(
                id="event-week2", name="Week 2",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=TemporalAmount(value=14, unit="day"), source_label="Week 2"),
                window=WindowSpec(
                    state="stated",
                    early=TemporalAmount(value=1, unit="week"),
                    late=TemporalAmount(value=1, unit="week"), source_label="±1 week")),
            ScheduleEvent(
                id="event-month4", name="Month 4",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=TemporalAmount(value=120, unit="day"), source_label="Month 4"),
                window=WindowSpec(
                    state="stated",
                    early=TemporalAmount(value=1, unit="week"),
                    late=TemporalAmount(value=2, unit="week"), source_label="-1/+2 weeks")),
        ])

    rows, warnings = project_canonical_plan(plan)

    assert warnings == []
    week2, month4 = rows
    assert week2["window_days"] == 7
    assert week2["window_before"] is None and week2["window_after"] is None
    assert month4["window_days"] is None
    assert month4["window_before"] == 7
    assert month4["window_after"] == 14


def test_canonical_projection_approximates_calendar_month_timing_as_days():
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-last-dose", name="Last dose", anchor_type="last_dose")],
        events=[ScheduleEvent(
            id="event-follow-up", name="Follow-up",
            timing=TimingExpression(
                kind="calendar_offset", anchor_id="anchor-last-dose",
                offset=TemporalAmount(value=1, unit="month"),
                relation="after", source_label="1 month after last dose"))])

    rows, _ = project_canonical_plan(plan)

    # A calendar-only label ("Month 1") states no day count, so the Day column
    # is populated with the standard 30-day/month scheduling approximation
    # instead of staying blank — but the protocol's own label is preserved
    # verbatim and an operational-constraint note flags the value as estimated.
    assert rows[0]["day_offset"] == 30
    assert rows[0]["source_day_label"] == "1 month after last dose"
    assert rows[0]["review_status"] == "ok"
    assert any("approximated" in note for note in rows[0]["operational_constraints"])


def test_expand_schedule_uses_canonical_plan_not_conflicting_ai_flat_rows():
    plan = CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-dose", name="Dose", anchor_type="first_dose")],
        events=[ScheduleEvent(
            id="event-baseline", name="Canonical Baseline",
            timing=TimingExpression(
                kind="offset", anchor_id="anchor-dose",
                offset=TemporalAmount(value=0, unit="day"),
                source_label="Day 1"))])
    schedule = ExtractedSchedule(
        schedule_kind="linear", canonical_plan=plan,
        visits=[{"name": "Wrong duplicate", "day_offset": 999}])

    expanded = expand_schedule(schedule)

    assert [(item.name, item.day_offset) for item in expanded.visits] == [
        ("Canonical Baseline", 0)]


def _housing_plan(range_start_day: float, range_end_day: float) -> CanonicalSchedulePlan:
    """A single multi-day pre-dose event shaped like CT25-007's own Visit 2:
    check-in on Day 11, then a shared pre-dose-sample event spanning Days
    12-14 (see protocol_extraction 'MULTI-DAY CONFINEMENT/HOUSING' pattern).
    range_start_day/range_end_day are the (possibly wrong) elapsed-day values
    the AI put in the timing, independent of what source_label says.
    """
    return CanonicalSchedulePlan(
        anchors=[ScheduleAnchor(
            id="anchor-baseline", name="Randomization", anchor_type="randomization")],
        events=[ScheduleEvent(
            id="event-predose", name="Pre-dose Blood Samples (Days 12-14)",
            timing=TimingExpression(
                kind="range", anchor_id="anchor-baseline",
                range_start=TemporalAmount(value=range_start_day, unit="day"),
                range_end=TemporalAmount(value=range_end_day, unit="day"),
                source_label="Days 12-14"))])


def test_canonical_range_timing_is_corrected_to_match_its_own_source_label():
    """Reproduces the exact CT25-007 bug: the model wrote source_label 'Days
    12-14' but put 11/13 in the timing's own range_start/range_end. Before
    this check existed, project_canonical_plan trusted the wrong numbers
    outright — normalize_extracted_timing already caught this shape for the
    legacy flat-visits path, but never ran on the canonical_plan path every
    real AI extraction actually uses."""
    plan = _housing_plan(range_start_day=11, range_end_day=13)

    rows, warnings = project_canonical_plan(
        plan, anchor_study_day=0, includes_day_zero=True)

    assert rows[0]["day_offset"] == 12
    assert rows[0]["day_end"] == 14
    assert any("corrected deterministically" in warning for warning in warnings)


def test_canonical_range_timing_correction_is_a_silent_noop_when_already_correct():
    plan = _housing_plan(range_start_day=12, range_end_day=14)

    rows, warnings = project_canonical_plan(
        plan, anchor_study_day=0, includes_day_zero=True)

    assert rows[0]["day_offset"] == 12
    assert rows[0]["day_end"] == 14
    assert warnings == []


def test_canonical_range_timing_correction_requires_anchor_metadata():
    """No stated Day 0/Day 1 convention means there is no reliable way to
    convert 'Days 12-14' into an offset, so the wrong 11/13 must be left
    exactly as the AI produced it — never guessed at — same as
    simple_day_label_range_offsets' own None-anchor_study_day contract."""
    plan = _housing_plan(range_start_day=11, range_end_day=13)

    rows, warnings = project_canonical_plan(plan)

    assert rows[0]["day_offset"] == 11
    assert rows[0]["day_end"] == 13
    assert warnings == []
