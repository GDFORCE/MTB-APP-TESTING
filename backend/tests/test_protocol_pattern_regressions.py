"""Offline regression fixtures shaped like the supplied clinical protocols.

These tests deliberately avoid an AI provider.  They verify that the canonical
schedule graph can retain the meanings observed in the protocol corpus and
that its mobile/legacy projection does not silently turn constraints into
visit dates or procedure tolerances into visit windows.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from schedule_schema import (  # noqa: E402
    ActivityTemplate,
    CanonicalSchedulePlan,
    RecurrenceRule,
    ScheduleAnchor,
    ScheduleBranch,
    ScheduleCondition,
    ScheduleConflict,
    ScheduleEvent,
    SchedulePhase,
    TemporalAmount,
    TimingExpression,
    TransitionRule,
    WindowSpec,
    apply_temporal_amount,
    project_canonical_plan,
    validate_canonical_plan,
)


def amount(value: float, unit: str = "day") -> TemporalAmount:
    return TemporalAmount(value=value, unit=unit)


def baseline_anchor(anchor_type: str = "first_dose") -> ScheduleAnchor:
    return ScheduleAnchor(
        id="anchor-baseline",
        name="Baseline",
        anchor_type=anchor_type,
        source_label="Day 1",
    )


def test_crossover_washout_is_a_transition_constraint_not_a_fake_visit():
    """CRD/09: Period II is earliest Day 21; washout is not an encounter."""
    plan = CanonicalSchedulePlan(
        protocol_id="CRD/09",
        anchors=[baseline_anchor()],
        phases=[
            SchedulePhase(id="phase-treatment", name="Treatment", phase_type="treatment"),
            SchedulePhase(id="phase-washout", name="Washout", phase_type="washout"),
        ],
        branches=[
            ScheduleBranch(id="period-1", name="Period I", branch_type="period"),
            ScheduleBranch(id="period-2", name="Period II", branch_type="period"),
        ],
        events=[
            ScheduleEvent(
                id="event-dose-1",
                name="Period I Dosing",
                phase_id="phase-treatment",
                period_id="period-1",
                timing=TimingExpression(
                    kind="offset",
                    anchor_id="anchor-baseline",
                    offset=amount(0),
                    source_label="Period I Day 1",
                ),
            ),
            ScheduleEvent(
                id="event-dose-2",
                name="Period II Dosing",
                phase_id="phase-treatment",
                period_id="period-2",
                timing=TimingExpression(
                    kind="relative",
                    anchor_id="event-dose-1",
                    offset=amount(21),
                    relation="after",
                    qualifier="minimum",
                    source_label="At least 21 days after Period I dosing",
                ),
            ),
        ],
        transitions=[
            TransitionRule(
                id="transition-washout",
                from_event_id="event-dose-1",
                to_event_id="event-dose-2",
                relation="minimum_gap",
                amount=amount(21),
            )
        ],
    )

    rows, warnings = project_canonical_plan(plan)

    assert validate_canonical_plan(plan) == []
    assert warnings == []
    assert [row["name"] for row in rows] == ["Period I Dosing", "Period II Dosing"]
    assert [row["day_offset"] for row in rows] == [0, 21]
    assert [row["period"] for row in rows] == ["Period I", "Period II"]
    assert not any("washout" in row["name"].lower() for row in rows)
    assert "minimum gap 21 days Period I Dosing" in rows[1]["operational_constraints"]


def test_babe_2x2_crossover_sequences_keep_treatment_mapping_and_pk_timing_distinct():
    """BA/BE 2x2 crossover: two randomized sequences, each with its own dosing
    day per period and a dense intra-day PK draw repeating in both periods.

    Two things a period/arm-only row schema cannot express on its own:
    1. Which treatment a given period actually dosed differs BY SEQUENCE
       (Sequence AB: Period 1 = Test, Period 2 = Reference; Sequence BA is the
       reverse) — a period branch nested under a sequence branch via
       parent_branch_id must resolve to a distinguishable row, not silently
       read as the same "Period 1"/"Period 2" for every sequence.
    2. An identical intra-day PK offset ("Hour 4 post-dose") recurs once per
       period, each anchored to THAT period's own dosing day, not to the
       study's absolute baseline — Period 2's Hour 4 must not collapse onto
       Period 1's Hour 4 just because they share the same raw hour value.
    """
    plan = CanonicalSchedulePlan(
        protocol_id="BABE-2x2",
        anchors=[baseline_anchor("randomization")],
        branches=[
            ScheduleBranch(id="seq-ab", name="Sequence AB", branch_type="sequence"),
            ScheduleBranch(id="seq-ba", name="Sequence BA", branch_type="sequence"),
            ScheduleBranch(id="per1-ab", name="Period 1", branch_type="period",
                           parent_branch_id="seq-ab"),
            ScheduleBranch(id="per2-ab", name="Period 2", branch_type="period",
                           parent_branch_id="seq-ab"),
            ScheduleBranch(id="per1-ba", name="Period 1", branch_type="period",
                           parent_branch_id="seq-ba"),
            ScheduleBranch(id="per2-ba", name="Period 2", branch_type="period",
                           parent_branch_id="seq-ba"),
        ],
        events=[
            # Sequence AB: Period 1 = Test, Period 2 = Reference.
            ScheduleEvent(
                id="dose-ab-1", name="Test Dosing", period_id="per1-ab",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Period 1 Day 1")),
            ScheduleEvent(
                id="pk-ab-1-4h", name="PK Hour 4", period_id="per1-ab",
                timing=TimingExpression(
                    kind="offset", anchor_id="dose-ab-1", offset=amount(4, "hour"),
                    source_label="Hour 4 post-dose")),
            ScheduleEvent(
                id="dose-ab-2", name="Reference Dosing", period_id="per2-ab",
                timing=TimingExpression(
                    kind="relative", anchor_id="dose-ab-1", offset=amount(14),
                    relation="after", qualifier="minimum",
                    source_label="At least 14 days after Period 1 dosing")),
            ScheduleEvent(
                id="pk-ab-2-4h", name="PK Hour 4", period_id="per2-ab",
                timing=TimingExpression(
                    kind="offset", anchor_id="dose-ab-2", offset=amount(4, "hour"),
                    source_label="Hour 4 post-dose")),
            # Sequence BA: Period 1 = Reference, Period 2 = Test (reversed).
            ScheduleEvent(
                id="dose-ba-1", name="Reference Dosing", period_id="per1-ba",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Period 1 Day 1")),
            ScheduleEvent(
                id="pk-ba-1-4h", name="PK Hour 4", period_id="per1-ba",
                timing=TimingExpression(
                    kind="offset", anchor_id="dose-ba-1", offset=amount(4, "hour"),
                    source_label="Hour 4 post-dose")),
            ScheduleEvent(
                id="dose-ba-2", name="Test Dosing", period_id="per2-ba",
                timing=TimingExpression(
                    kind="relative", anchor_id="dose-ba-1", offset=amount(14),
                    relation="after", qualifier="minimum",
                    source_label="At least 14 days after Period 1 dosing")),
            ScheduleEvent(
                id="pk-ba-2-4h", name="PK Hour 4", period_id="per2-ba",
                timing=TimingExpression(
                    kind="offset", anchor_id="dose-ba-2", offset=amount(4, "hour"),
                    source_label="Hour 4 post-dose")),
        ],
        transitions=[
            TransitionRule(id="washout-ab", from_event_id="dose-ab-1",
                            to_event_id="dose-ab-2", relation="minimum_gap",
                            amount=amount(14)),
            TransitionRule(id="washout-ba", from_event_id="dose-ba-1",
                            to_event_id="dose-ba-2", relation="minimum_gap",
                            amount=amount(14)),
        ],
    )

    rows, warnings = project_canonical_plan(plan)
    assert validate_canonical_plan(plan) == []
    by_key = {(row["arm"], row["period"], row["name"]): row for row in rows}

    # Sequence-scoped treatment mapping survives — the same "Period 1"/
    # "Period 2" labels resolve to opposite treatments per sequence, and each
    # row is tagged with which sequence it belongs to.
    assert by_key[("Sequence AB", "Period 1", "Test Dosing")]["day_offset"] == 0
    assert by_key[("Sequence AB", "Period 2", "Reference Dosing")]["day_offset"] == 14
    assert by_key[("Sequence BA", "Period 1", "Reference Dosing")]["day_offset"] == 0
    assert by_key[("Sequence BA", "Period 2", "Test Dosing")]["day_offset"] == 14

    # Each period's "Hour 4" PK draw resets against ITS OWN dosing day instead
    # of colliding with the other period's identical raw hour value.
    p1_pk = by_key[("Sequence AB", "Period 1", "PK Hour 4")]
    p2_pk = by_key[("Sequence AB", "Period 2", "PK Hour 4")]
    assert (p1_pk["day_offset"], p1_pk["hour_offset"], p1_pk["hour_offset_basis"]) == (
        0, 4.0, "within_day")
    assert (p2_pk["day_offset"], p2_pk["hour_offset"], p2_pk["hour_offset_basis"]) == (
        14, 4.0, "within_day")
    total_elapsed_hours = lambda row: row["day_offset"] * 24 + row["hour_offset"]
    assert total_elapsed_hours(p1_pk) == 4
    assert total_elapsed_hours(p2_pk) == 340
    assert total_elapsed_hours(p1_pk) < total_elapsed_hours(p2_pk)


def test_3way_crossover_generalizes_beyond_the_2x2_case():
    """A 3-way Williams design (sequences ABC, BCA; three periods each) proves
    the BA/BE machinery is not secretly hardcoded to exactly two periods:

    1. Period 3 must anchor to Period 2's dosing (chained, consecutive), never
       straight back to Period 1 or the study baseline — day offsets should
       accumulate 0 -> 14 -> 28, not collapse or double-count.
    2. The same period NUMBER under two different sequences must still stay
       distinguishable at three periods deep, not just at two.
    """
    plan = CanonicalSchedulePlan(
        protocol_id="3WAY-WILLIAMS",
        anchors=[baseline_anchor("randomization")],
        branches=[
            ScheduleBranch(id="seq-abc", name="Sequence ABC", branch_type="sequence"),
            ScheduleBranch(id="seq-bca", name="Sequence BCA", branch_type="sequence"),
            ScheduleBranch(id="per1-abc", name="Period 1", branch_type="period",
                           parent_branch_id="seq-abc"),
            ScheduleBranch(id="per2-abc", name="Period 2", branch_type="period",
                           parent_branch_id="seq-abc"),
            ScheduleBranch(id="per3-abc", name="Period 3", branch_type="period",
                           parent_branch_id="seq-abc"),
            ScheduleBranch(id="per1-bca", name="Period 1", branch_type="period",
                           parent_branch_id="seq-bca"),
            ScheduleBranch(id="per2-bca", name="Period 2", branch_type="period",
                           parent_branch_id="seq-bca"),
            ScheduleBranch(id="per3-bca", name="Period 3", branch_type="period",
                           parent_branch_id="seq-bca"),
        ],
        events=[
            # Sequence ABC: Period 1=A, Period 2=B, Period 3=C.
            ScheduleEvent(id="dose-abc-1", name="Treatment A Dosing", period_id="per1-abc",
                timing=TimingExpression(kind="offset", anchor_id="anchor-baseline",
                                         offset=amount(0), source_label="Period 1 Day 1")),
            ScheduleEvent(id="dose-abc-2", name="Treatment B Dosing", period_id="per2-abc",
                timing=TimingExpression(kind="relative", anchor_id="dose-abc-1",
                                         offset=amount(14), relation="after",
                                         qualifier="minimum",
                                         source_label="At least 14 days after Period 1")),
            ScheduleEvent(id="dose-abc-3", name="Treatment C Dosing", period_id="per3-abc",
                timing=TimingExpression(kind="relative", anchor_id="dose-abc-2",
                                         offset=amount(14), relation="after",
                                         qualifier="minimum",
                                         source_label="At least 14 days after Period 2")),
            # Sequence BCA: Period 1=B, Period 2=C, Period 3=A (rotated).
            ScheduleEvent(id="dose-bca-1", name="Treatment B Dosing", period_id="per1-bca",
                timing=TimingExpression(kind="offset", anchor_id="anchor-baseline",
                                         offset=amount(0), source_label="Period 1 Day 1")),
            ScheduleEvent(id="dose-bca-2", name="Treatment C Dosing", period_id="per2-bca",
                timing=TimingExpression(kind="relative", anchor_id="dose-bca-1",
                                         offset=amount(14), relation="after",
                                         qualifier="minimum",
                                         source_label="At least 14 days after Period 1")),
            ScheduleEvent(id="dose-bca-3", name="Treatment A Dosing", period_id="per3-bca",
                timing=TimingExpression(kind="relative", anchor_id="dose-bca-2",
                                         offset=amount(14), relation="after",
                                         qualifier="minimum",
                                         source_label="At least 14 days after Period 2")),
        ],
        transitions=[
            TransitionRule(id="washout-abc-12", from_event_id="dose-abc-1",
                            to_event_id="dose-abc-2", relation="minimum_gap", amount=amount(14)),
            TransitionRule(id="washout-abc-23", from_event_id="dose-abc-2",
                            to_event_id="dose-abc-3", relation="minimum_gap", amount=amount(14)),
            TransitionRule(id="washout-bca-12", from_event_id="dose-bca-1",
                            to_event_id="dose-bca-2", relation="minimum_gap", amount=amount(14)),
            TransitionRule(id="washout-bca-23", from_event_id="dose-bca-2",
                            to_event_id="dose-bca-3", relation="minimum_gap", amount=amount(14)),
        ],
    )

    rows, warnings = project_canonical_plan(plan)
    assert validate_canonical_plan(plan) == []
    by_key = {(row["arm"], row["period"], row["name"]): row for row in rows}

    # Day offsets accumulate consecutively (0, 14, 28), not collapsed onto
    # Period 1 or doubled by anchoring straight back to baseline.
    assert by_key[("Sequence ABC", "Period 1", "Treatment A Dosing")]["day_offset"] == 0
    assert by_key[("Sequence ABC", "Period 2", "Treatment B Dosing")]["day_offset"] == 14
    assert by_key[("Sequence ABC", "Period 3", "Treatment C Dosing")]["day_offset"] == 28

    # The rotated sequence keeps its own, different treatment-to-period
    # mapping, still distinguishable three periods deep.
    assert by_key[("Sequence BCA", "Period 1", "Treatment B Dosing")]["day_offset"] == 0
    assert by_key[("Sequence BCA", "Period 2", "Treatment C Dosing")]["day_offset"] == 14
    assert by_key[("Sequence BCA", "Period 3", "Treatment A Dosing")]["day_offset"] == 28


def test_2x2_factorial_shares_one_visit_timeline_with_arm_scoped_activities():
    """A 2x2 factorial (Drug A present/absent x Drug B present/absent) is
    NOT a crossover: one shared visit timeline, four flat combination arms,
    and a factor-specific activity gated by ScheduleCondition.applies_to_branch_ids
    instead of the whole event graph being duplicated once per arm.
    """
    plan = CanonicalSchedulePlan(
        protocol_id="FACTORIAL-2x2",
        anchors=[baseline_anchor("randomization")],
        branches=[
            ScheduleBranch(id="arm-apbp", name="A+B+", branch_type="arm"),
            ScheduleBranch(id="arm-apbn", name="A+B-", branch_type="arm"),
            ScheduleBranch(id="arm-anbp", name="A-B+", branch_type="arm"),
            ScheduleBranch(id="arm-anbn", name="A-B-", branch_type="arm"),
        ],
        activities=[
            ActivityTemplate(id="act-drug-a", name="Drug A dispensing"),
            ActivityTemplate(id="act-drug-b", name="Drug B dispensing"),
            ActivityTemplate(id="act-vitals", name="Vital signs"),
        ],
        events=[
            ScheduleEvent(
                id=f"visit1-{arm}", name="Visit 1", arm_id=f"arm-{arm}",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Day 1"),
                activity_ids=["act-drug-a", "act-drug-b", "act-vitals"],
            )
            for arm in ("apbp", "apbn", "anbp", "anbn")
        ],
        conditions=[
            ScheduleCondition(
                id="cond-drug-a", expression="Drug A dispensed only in factor-A-positive arms",
                applies_to_ids=["act-drug-a"],
                applies_to_branch_ids=["arm-apbp", "arm-apbn"]),
            ScheduleCondition(
                id="cond-drug-b", expression="Drug B dispensed only in factor-B-positive arms",
                applies_to_ids=["act-drug-b"],
                applies_to_branch_ids=["arm-apbp", "arm-anbp"]),
        ],
    )

    rows, warnings = project_canonical_plan(plan)
    assert validate_canonical_plan(plan) == []
    assert len(rows) == 4  # one shared visit per arm, not a duplicated schedule
    by_arm = {row["arm"]: set(row["activities"]) for row in rows}

    assert by_arm["A+B+"] == {"Drug A dispensing", "Drug B dispensing", "Vital signs"}
    assert by_arm["A+B-"] == {"Drug A dispensing", "Vital signs"}
    assert by_arm["A-B+"] == {"Drug B dispensing", "Vital signs"}
    assert by_arm["A-B-"] == {"Vital signs"}


def test_open_ended_oncology_cycle_is_previewed_but_never_made_finite():
    """CG03/PICN: repeat every 21 days until a clinical stopping event."""
    plan = CanonicalSchedulePlan(
        protocol_id="OPEN-CYCLE",
        anchors=[baseline_anchor()],
        events=[
            ScheduleEvent(
                id="event-cycle-dose",
                name="Cycle {cycle} Day 1",
                timing=TimingExpression(
                    kind="offset",
                    anchor_id="anchor-baseline",
                    offset=amount(0),
                    source_label="Cycle 1 Day 1",
                ),
            ),
            ScheduleEvent(
                id="event-progression",
                name="Confirmed progression",
                required=False,
                timing=TimingExpression(
                    kind="event_driven",
                    anchor_id="anchor-baseline",
                    source_label="When progression is confirmed",
                ),
            ),
        ],
        recurrences=[
            RecurrenceRule(
                id="recurrence-treatment",
                event_ids=["event-cycle-dose"],
                frequency=amount(21),
                start_occurrence=1,
                end_occurrence=None,
                until_event_id="event-progression",
                source_label="Every 21 days until progression or unacceptable toxicity",
            )
        ],
    )

    rows, warnings = project_canonical_plan(plan, open_ended_preview_count=4)

    assert validate_canonical_plan(plan) == []
    assert plan.recurrences[0].end_occurrence is None
    assert [row["name"] for row in rows[:4]] == [
        "Cycle 1 Day 1",
        "Cycle 2 Day 1",
        "Cycle 3 Day 1",
        "Cycle 4 Day 1",
    ]
    assert [row["day_offset"] for row in rows[:4]] == [0, 21, 42, 63]
    assert len(warnings) == 1
    assert "open-ended" in warnings[0]
    assert rows[-1]["name"] == "Confirmed progression"
    assert rows[-1]["day_offset"] is None
    assert rows[-1]["review_status"] == "pending"


def test_picn_collapsed_open_cycle_projects_as_cycle_specific_visit_block():
    """CLR_10_13: canonical production path expands Cycle 2 & Next Cycles.

    This deliberately uses the generic label observed in the failed live run,
    not an already-perfect ``{cycle}`` template.  Projection must still render
    real cycle names, retain same-cycle relative anchors, and limit conditional
    assessments to their source-stated cycles.
    """
    plan = CanonicalSchedulePlan(
        protocol_id="CLR_10_13",
        anchors=[baseline_anchor("randomization")],
        activities=[
            ActivityTemplate(id="activity-imaging", name="Imaging / RECIST assessment"),
            ActivityTemplate(id="activity-interim", name="Interim efficacy/safety analysis"),
        ],
        events=[
            ScheduleEvent(
                id="event-screening", name="Screening", event_type="screening",
                timing=TimingExpression(
                    kind="range", anchor_id="anchor-baseline",
                    range_start=amount(-7), range_end=amount(-1),
                    source_label="Day -7 to -1"),
            ),
            ScheduleEvent(
                id="event-cycle-1-dose", name="Cycle 1 (Randomization + Dosing)",
                event_type="treatment",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Cycle 1"),
            ),
            ScheduleEvent(
                id="event-cycle-1-ic1", name="Cycle 1 Post Cycle Intra-Cycle Visit IC-1",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-1-dose", offset=amount(7),
                    relation="after", source_label="7 days after Cycle 1 dosing"),
            ),
            ScheduleEvent(
                id="event-cycle-1-ic2", name="Cycle 1 Post Cycle Intra-Cycle Visit IC-2",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-1-dose", offset=amount(14),
                    relation="after", source_label="14 days after Cycle 1 dosing"),
            ),
            ScheduleEvent(
                id="event-cycle-1-ic3", name="Cycle 1 Post Cycle Intra-Cycle Visit IC-3",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-1-dose", offset=amount(21),
                    relation="after", source_label="21 days after Cycle 1 dosing"),
            ),
            ScheduleEvent(
                id="event-cycle-dose", name="Cycle 2 & Next Cycles Dosing Visit",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-1-ic3", offset=amount(0),
                    relation="after", source_label="Within 3 days after prior cycle IC-3"),
                window=WindowSpec(
                    state="stated", early=amount(0), late=amount(3),
                    source_label="within 3 days after intra-cycle visit 3"),
            ),
            ScheduleEvent(
                id="event-cycle-ic1",
                name="Cycle 2 & Next Cycles Post Cycle Intra-Cycle Visit IC-1",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-dose", offset=amount(7),
                    relation="after", source_label="7 days after cycle dosing"),
            ),
            ScheduleEvent(
                id="event-cycle-ic2",
                name="Cycle 2 & Next Cycles Post Cycle Intra-Cycle Visit IC-2",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-dose", offset=amount(14),
                    relation="after", source_label="14 days after cycle dosing"),
            ),
            ScheduleEvent(
                id="event-cycle-ic3",
                name="Cycle 2 & Next Cycles Post Cycle Intra-Cycle Visit IC-3",
                event_type="treatment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-cycle-dose", offset=amount(21),
                    relation="after", source_label="21 days after cycle dosing"),
                activity_ids=["activity-imaging", "activity-interim"],
            ),
            ScheduleEvent(
                id="event-progression", name="Confirmed disease progression",
                event_type="unscheduled", required=False,
                timing=TimingExpression(
                    kind="event_driven", anchor_id="anchor-baseline",
                    source_label="Confirmed disease progression"),
            ),
        ],
        recurrences=[RecurrenceRule(
            id="recurrence-cycle-2-onward",
            event_ids=[
                "event-cycle-dose", "event-cycle-ic1", "event-cycle-ic2",
                "event-cycle-ic3",
            ],
            frequency=amount(21), start_occurrence=2, end_occurrence=None,
            until_event_id="event-progression",
            source_label=(
                "Cycle 2 & Next Cycles every 3 weeks until disease progression "
                "or unacceptable toxicity"),
        )],
        conditions=[
            ScheduleCondition(
                id="condition-imaging-cycles", expression="Imaging at cycles 2, 4, and 6",
                applies_to_ids=["activity-imaging"], occurrence_numbers=[2, 4, 6]),
            ScheduleCondition(
                id="condition-cycle-6-interim",
                expression="Interim efficacy/safety analysis at the end of Cycle 6",
                applies_to_ids=["activity-interim"], occurrence_numbers=[6]),
        ],
    )

    rows, warnings = project_canonical_plan(plan, open_ended_preview_count=5)
    scheduled = [row for row in rows if row["day_offset"] is not None]
    by_name = {row["name"]: row for row in rows}

    assert validate_canonical_plan(plan) == []
    assert len(scheduled) == 25
    assert not any("Occurrence" in row["name"] for row in rows)
    assert [by_name[name]["day_offset"] for name in (
        "Cycle 1 (Randomization + Dosing)",
        "Cycle 1 Post Cycle Intra-Cycle Visit IC-1",
        "Cycle 1 Post Cycle Intra-Cycle Visit IC-2",
        "Cycle 1 Post Cycle Intra-Cycle Visit IC-3",
        "Cycle 2 Dosing Visit",
        "Cycle 2 Post Cycle Intra-Cycle Visit IC-1",
        "Cycle 2 Post Cycle Intra-Cycle Visit IC-2",
        "Cycle 2 Post Cycle Intra-Cycle Visit IC-3",
        "Cycle 3 Dosing Visit",
    )] == [0, 7, 14, 21, 21, 28, 35, 42, 42]
    assert by_name["Cycle 3 Post Cycle Intra-Cycle Visit IC-1"]["relative_to"] == (
        "Cycle 3 Dosing Visit")
    for cycle in range(2, 7):
        dose = by_name[f"Cycle {cycle} Dosing Visit"]
        assert dose["window_before"] == 0
        assert dose["window_after"] == 3
    imaging_cycles = {
        cycle for cycle in range(2, 7)
        if "Imaging / RECIST assessment" in by_name[
            f"Cycle {cycle} Post Cycle Intra-Cycle Visit IC-3"]["activities"]
    }
    interim_cycles = {
        cycle for cycle in range(2, 7)
        if "Interim efficacy/safety analysis" in by_name[
            f"Cycle {cycle} Post Cycle Intra-Cycle Visit IC-3"]["activities"]
    }
    assert imaging_cycles == {2, 4, 6}
    assert interim_cycles == {6}
    assert plan.recurrences[0].end_occurrence is None
    assert len(warnings) == 1 and "open-ended" in warnings[0]


def test_multi_arm_schedule_keeps_assignment_without_duplicating_shared_meaning():
    """Parallel studies need arm membership retained on otherwise same-day visits."""
    plan = CanonicalSchedulePlan(
        protocol_id="MULTI-ARM",
        anchors=[baseline_anchor("randomization")],
        branches=[
            ScheduleBranch(id="arm-a", name="Arm A", branch_type="arm"),
            ScheduleBranch(id="arm-b", name="Arm B", branch_type="arm"),
        ],
        events=[
            ScheduleEvent(
                id="event-arm-a-dose",
                name="Arm A first dose",
                arm_id="arm-a",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Day 1",
                ),
            ),
            ScheduleEvent(
                id="event-arm-b-dose",
                name="Arm B first dose",
                arm_id="arm-b",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Day 1",
                ),
            ),
        ],
        conditions=[
            ScheduleCondition(
                id="condition-randomization",
                expression="Generate only the event for the participant's randomized arm",
                applies_to_ids=["event-arm-a-dose", "event-arm-b-dose"],
            )
        ],
    )

    rows, _ = project_canonical_plan(plan)

    assert validate_canonical_plan(plan) == []
    assert {(row["arm"], row["day_offset"]) for row in rows} == {
        ("Arm A", 0),
        ("Arm B", 0),
    }
    assert plan.conditions[0].applies_to_ids == ["event-arm-a-dose", "event-arm-b-dose"]


def test_event_driven_discharge_stays_unknown_while_surgery_day_30_is_resolved():
    """CORONARY: discharge is a real encounter but has no invented fixed day."""
    plan = CanonicalSchedulePlan(
        protocol_id="CORONARY",
        anchors=[baseline_anchor("other")],
        events=[
            ScheduleEvent(
                id="event-surgery",
                name="CABG Surgery",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="OR Day",
                ),
            ),
            ScheduleEvent(
                id="event-discharge",
                name="Discharge",
                timing=TimingExpression(
                    kind="event_driven",
                    anchor_id="event-surgery",
                    source_label="At actual hospital discharge",
                ),
            ),
            ScheduleEvent(
                id="event-day-30",
                name="Day 30 Follow-up",
                timing=TimingExpression(
                    kind="relative",
                    anchor_id="event-surgery",
                    offset=amount(30),
                    relation="after",
                    source_label="Day 30 after surgery",
                ),
            ),
        ],
    )

    rows, _ = project_canonical_plan(plan)
    by_name = {row["name"]: row for row in rows}

    assert validate_canonical_plan(plan) == []
    assert by_name["CABG Surgery"]["day_offset"] == 0
    assert by_name["Discharge"]["day_offset"] is None
    assert by_name["Discharge"]["source_day_label"] == "At actual hospital discharge"
    assert by_name["Discharge"]["review_status"] == "pending"
    assert by_name["Day 30 Follow-up"]["day_offset"] == 30


def test_intra_day_procedure_tolerance_does_not_become_a_visit_window():
    """CRD/09: 0.5-hour vitals have a minute tolerance, not a day window."""
    plan = CanonicalSchedulePlan(
        protocol_id="CRD/09",
        anchors=[baseline_anchor()],
        activities=[
            ActivityTemplate(
                id="activity-vitals-30m",
                name="Vitals at 0.5 hours",
                timing=TimingExpression(
                    kind="relative",
                    anchor_id="event-dose",
                    offset=amount(0.5, "hour"),
                    relation="after",
                    source_label="0.5 hours post-dose",
                ),
                window=WindowSpec(
                    scope="activity",
                    state="stated",
                    early=amount(10, "minute"),
                    late=amount(10, "minute"),
                    source_label="within 10 minutes of scheduled time",
                ),
            )
        ],
        events=[
            ScheduleEvent(
                id="event-dose",
                name="Period I Dosing",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline", offset=amount(0),
                    source_label="Day 1",
                ),
                window=WindowSpec(state="not_stated"),
                activity_ids=["activity-vitals-30m"],
            )
        ],
    )

    rows, _ = project_canonical_plan(plan)
    row = rows[0]

    assert validate_canonical_plan(plan) == []
    assert row["window_days"] is None
    assert row["window_before"] is None
    assert row["window_after"] is None
    assert row["procedures"] == [
        {
            "id": "activity-vitals-30m",
            "name": "Vitals at 0.5 hours",
            "timing": "0.5 hours post-dose",
            "window": "within 10 minutes of scheduled time",
            "condition": "",
            "evidence_ids": [],
            "constraints": [],
        }
    ]


def test_calendar_month_and_year_followups_get_an_approximate_day_offset():
    """CORONARY: Month 6/Year 1 get a 30-day/365-day estimate, but the exact
    calendar operation (apply_temporal_amount) is still what per-patient
    enrollment-date resolution uses, and stays unaffected by the estimate."""
    plan = CanonicalSchedulePlan(
        protocol_id="CORONARY",
        anchors=[baseline_anchor("other")],
        events=[
            ScheduleEvent(
                id="event-month-6",
                name="Month 6 Follow-up",
                timing=TimingExpression(
                    kind="calendar_offset",
                    anchor_id="anchor-baseline",
                    offset=amount(6, "month"),
                    relation="after",
                    source_label="Month 6",
                ),
            ),
            ScheduleEvent(
                id="event-year-1",
                name="Year 1 Follow-up",
                timing=TimingExpression(
                    kind="calendar_offset",
                    anchor_id="anchor-baseline",
                    offset=amount(1, "year"),
                    relation="after",
                    source_label="Year 1",
                ),
            ),
        ],
    )

    rows, _ = project_canonical_plan(plan)

    assert validate_canonical_plan(plan) == []
    assert [row["day_offset"] for row in rows] == [180, 365]
    assert [row["source_day_label"] for row in rows] == ["Month 6", "Year 1"]
    for row in rows:
        assert any("approximated" in note for note in row["operational_constraints"])
    assert apply_temporal_amount(date(2024, 8, 31), amount(6, "month")) == date(2025, 2, 28)
    assert apply_temporal_amount(date(2024, 2, 29), amount(1, "year")) == date(2025, 2, 28)


def test_conditional_visit_and_source_conflict_are_visible_and_force_review():
    """CG03: Week 96 versus 18 months must be adjudicated, never averaged."""
    plan = CanonicalSchedulePlan(
        protocol_id="CG03-EOC",
        anchors=[baseline_anchor("randomization")],
        events=[
            ScheduleEvent(
                id="event-follow-up-10",
                name="F10 / End of Study",
                required=False,
                conditional_text="Generate only if the participant remains in follow-up",
                timing=TimingExpression(
                    kind="offset",
                    anchor_id="anchor-baseline",
                    offset=amount(96, "week"),
                    source_label="Week 96",
                    alternative_source_labels=["18 months"],
                ),
                window=WindowSpec(
                    state="conflicting",
                    source_label="General follow-up says +/-7 to 14 days; imaging says +/-7 days",
                ),
            )
        ],
        conditions=[
            ScheduleCondition(
                id="condition-follow-up-active",
                expression="Participant has not died, withdrawn consent, or been lost to follow-up",
                applies_to_ids=["event-follow-up-10"],
            )
        ],
        conflicts=[
            ScheduleConflict(
                id="conflict-week-month",
                field_path="events.event-follow-up-10.timing",
                description="The protocol labels F10 as both Week 96 and 18 months",
            ),
            ScheduleConflict(
                id="conflict-window-scope",
                field_path="events.event-follow-up-10.window",
                description="General visit and imaging windows have different scopes",
            ),
        ],
    )

    rows, _ = project_canonical_plan(plan)
    issues = validate_canonical_plan(plan)

    assert len(issues) == 2
    assert all("Unresolved source conflict" in issue for issue in issues)
    assert rows[0]["day_offset"] == 672
    assert rows[0]["review_status"] == "pending"
    assert rows[0]["extraction_warning"] is True
    assert "Generate only if the participant remains in follow-up" in rows[0][
        "operational_constraints"
    ]
    assert "18 months" in rows[0]["operational_constraints"]
    assert rows[0]["window_days"] is None



def test_bounded_screening_window_is_not_projected_as_an_exact_visit():
    """"Within 28 days prior to randomization" is a permitted range, not a date."""
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor("randomization")],
        events=[
            ScheduleEvent(
                id="event-screening",
                name="Screening",
                event_type="Screening",
                timing=TimingExpression(
                    kind="offset",
                    anchor_id="anchor-baseline",
                    offset=amount(28),
                    relation="before",
                    qualifier="maximum",
                    source_label="Within 28 days prior to randomization",
                ),
            ),
            ScheduleEvent(
                id="event-baseline",
                name="Baseline",
                timing=TimingExpression(
                    kind="offset",
                    anchor_id="anchor-baseline",
                    offset=amount(0),
                    source_label="Day 1",
                ),
            ),
        ],
    )

    rows, _warnings = project_canonical_plan(plan)
    screening, baseline = rows[0], rows[1]

    # The boundary is still shown so the row sorts before baseline, but the
    # visit must not read as a confirmed Day -28 appointment.
    assert screening["day_offset"] == -28
    assert screening["source_day_label"] == "Within 28 days prior to randomization"
    assert screening["review_status"] == "pending"
    assert screening["extraction_warning"] is True
    assert any("bounded, not an exact day" in item
               for item in screening["operational_constraints"])
    # An exact visit alongside it stays exact.
    assert baseline["review_status"] == "ok"
    assert baseline["operational_constraints"] == []


def test_range_timing_keeps_both_ends_and_is_flagged_for_review():
    """"Day 14 to Day 17" is a multi-day window, never a single Day 14 visit."""
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor()],
        events=[ScheduleEvent(
            id="event-admission",
            name="Inpatient admission",
            timing=TimingExpression(
                kind="range",
                anchor_id="anchor-baseline",
                range_start=amount(14),
                range_end=amount(17),
                source_label="Day 14-17",
            ),
        )],
    )

    rows, _warnings = project_canonical_plan(plan)

    assert rows[0]["day_offset"] == 14
    assert rows[0]["day_end"] == 17
    # Both ends survive, so the row represents the stay faithfully and does not
    # need review — but it must still read as a range, never as a Day 14 visit.
    assert rows[0]["review_status"] == "ok"
    assert any("range, not an exact day" in item
               for item in rows[0]["operational_constraints"])


def test_relative_before_offset_stays_negative_in_the_legacy_row():
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor()],
        events=[
            ScheduleEvent(
                id="event-surgery",
                name="Surgery",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(30), source_label="Day 30"),
            ),
            ScheduleEvent(
                id="event-pre-op",
                name="Pre-operative assessment",
                timing=TimingExpression(
                    kind="relative", anchor_id="event-surgery",
                    offset=amount(7), relation="before",
                    source_label="7 days before surgery"),
            ),
        ],
    )

    rows, _warnings = project_canonical_plan(plan)
    pre_op = next(row for row in rows if row["name"] == "Pre-operative assessment")

    assert pre_op["relative_to"] == "Surgery"
    assert pre_op["relative_offset_days"] == -7
    assert pre_op["day_offset"] == 23


def test_window_widening_across_the_schedule_is_preserved_per_visit():
    """BP11-301: +/-3 days early, +/-5 mid-study, +/-7 for the late visits."""
    plan = CanonicalSchedulePlan(
        protocol_id="BP11-301",
        anchors=[baseline_anchor()],
        events=[
            ScheduleEvent(
                id=f"event-week-{week}",
                name=f"Week {week}",
                event_type="Telephonic" if week == 32 else "Site",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(day), source_label=f"Week {week} / Day {day + 1}"),
                window=WindowSpec(
                    state="stated", early=amount(tolerance), late=amount(tolerance),
                    source_label=f"+/-{tolerance} days"),
            )
            for week, day, tolerance in (
                (2, 14, 3), (16, 112, 5), (32, 224, 7),
            )
        ],
    )

    rows, warnings = project_canonical_plan(plan)

    assert warnings == []
    assert [(row["day_offset"], row["window_days"]) for row in rows] == [
        (14, 3), (112, 5), (224, 7)]
    assert all(row["window_before"] is None for row in rows)
    # A telephone contact is a real visit, not an activity of another visit.
    assert rows[2]["visit_type"] == "Telephonic"
    assert all(row["review_status"] == "ok" for row in rows)


def test_undated_early_termination_and_unscheduled_visits_survive_projection():
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor()],
        events=[
            ScheduleEvent(
                id="event-eot", name="End of treatment",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(84), source_label="Day 85"),
            ),
            ScheduleEvent(
                id="event-et", name="Early termination",
                event_type="Early Termination",
                timing=TimingExpression(
                    kind="unresolved",
                    source_label="At the time of premature discontinuation"),
                required=False,
                conditional_text="Only if the subject withdraws early",
            ),
            ScheduleEvent(
                id="event-unscheduled", name="Unscheduled visit",
                event_type="Unscheduled",
                timing=TimingExpression(
                    kind="unresolved", source_label="As clinically indicated"),
                required=False,
            ),
        ],
    )

    rows, _warnings = project_canonical_plan(plan)
    by_name = {row["name"]: row for row in rows}

    assert set(by_name) == {
        "End of treatment", "Early termination", "Unscheduled visit"}
    for name in ("Early termination", "Unscheduled visit"):
        assert by_name[name]["day_offset"] is None
        assert by_name[name]["review_status"] == "pending"
    assert by_name["Early termination"]["source_day_label"] == (
        "At the time of premature discontinuation")
    assert "Only if the subject withdraws early" in (
        by_name["Early termination"]["operational_constraints"])


def test_daily_diary_recurrence_expands_without_inventing_an_end():
    """TRC160334/benznidazole: a bounded daily assessment block."""
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor()],
        events=[ScheduleEvent(
            id="event-diary", name="Daily diary Day {occurrence}",
            event_type="Diary",
            timing=TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=amount(0), source_label="Daily from Day 1"),
        )],
        recurrences=[RecurrenceRule(
            id="recurrence-diary", event_ids=["event-diary"],
            frequency=amount(1), start_occurrence=1, end_occurrence=7,
            source_label="Daily for 7 days")],
    )

    rows, warnings = project_canonical_plan(plan)

    assert warnings == [], "a bounded recurrence must not warn about open ends"
    assert [row["day_offset"] for row in rows] == [0, 1, 2, 3, 4, 5, 6]
    assert rows[3]["name"] == "Daily diary Day 4"
    assert validate_canonical_plan(plan) == []


def test_independent_overlapping_cadences_stay_separate_schedules():
    """Etrasimod/everolimus style: visits q4w while labs run q12w."""
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor()],
        events=[
            ScheduleEvent(
                id="event-clinic", name="Clinic visit {occurrence}",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(0), source_label="Every 4 weeks"),
            ),
            ScheduleEvent(
                id="event-labs", name="Safety laboratory {occurrence}",
                event_type="Laboratory",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(0), source_label="Every 12 weeks"),
            ),
        ],
        recurrences=[
            RecurrenceRule(
                id="recurrence-clinic", event_ids=["event-clinic"],
                frequency=amount(4, "week"), end_occurrence=3,
                source_label="Every 4 weeks"),
            RecurrenceRule(
                id="recurrence-labs", event_ids=["event-labs"],
                frequency=amount(12, "week"), end_occurrence=2,
                source_label="Every 12 weeks"),
        ],
    )

    rows, _warnings = project_canonical_plan(plan)
    clinic = [row["day_offset"] for row in rows if row["visit_type"] == "visit"]
    labs = [row["day_offset"] for row in rows if row["visit_type"] == "Laboratory"]

    assert clinic == [0, 28, 56]
    assert labs == [0, 84]


def test_same_day_merge_and_lab_gated_delay_are_constraints_not_new_visits():
    """APD334-202/PICN: co-located visits and result-gated dosing rules."""
    plan = CanonicalSchedulePlan(
        anchors=[baseline_anchor()],
        events=[
            ScheduleEvent(
                id="event-dosing", name="Dosing visit",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(21), source_label="Day 22"),
            ),
            ScheduleEvent(
                id="event-pk", name="PK sampling",
                event_type="Laboratory",
                timing=TimingExpression(
                    kind="offset", anchor_id="anchor-baseline",
                    offset=amount(21), source_label="Day 22"),
            ),
        ],
        transitions=[TransitionRule(
            id="transition-colocated", from_event_id="event-pk",
            to_event_id="event-dosing", relation="same_day")],
        conditions=[ScheduleCondition(
            id="condition-anc",
            expression="Hold the dose and repeat in 7 days if ANC is below 1.5",
            applies_to_ids=["event-dosing"])],
    )

    rows, _warnings = project_canonical_plan(plan)
    dosing = next(row for row in rows if row["name"] == "Dosing visit")

    assert len(rows) == 2, "a same-day rule must not create a third visit"
    assert any("same day" in item for item in dosing["operational_constraints"])
    assert validate_canonical_plan(plan) == []


def test_amendment_version_lineage_conflict_blocks_silent_resolution():
    """BP11-301 v2.1 India amendment 1.1: two governing windows, no winner."""
    plan = CanonicalSchedulePlan(
        protocol_id="BP11-301",
        protocol_version="2.1 (India Amendment 1.1)",
        anchors=[baseline_anchor()],
        events=[ScheduleEvent(
            id="event-week-24", name="Week 24",
            timing=TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=amount(168), source_label="Week 24 / Day 169"),
            window=WindowSpec(
                state="conflicting",
                source_label="+/-5 days (v2.0) vs +/-7 days (India Amendment 1.1)"),
        )],
        conflicts=[ScheduleConflict(
            id="conflict-week-24-window",
            field_path="events.event-week-24.window",
            description=(
                "Version 2.0 states +/-5 days and the India amendment states "
                "+/-7 days; the governing version is not stated in this bundle."),
            status="unresolved")],
    )

    issues = validate_canonical_plan(plan)
    rows, _warnings = project_canonical_plan(plan)

    assert any("Unresolved source conflict" in issue for issue in issues)
    assert rows[0]["window_days"] is None
    assert rows[0]["window_before"] is None and rows[0]["window_after"] is None
    assert rows[0]["review_status"] == "pending"
    assert any("+/-5 days (v2.0)" in item
               for item in rows[0]["operational_constraints"])


def test_procedure_prose_timing_is_kept_unresolved_instead_of_failing():
    """"Pre-dose" has no number and no anchor; it must not sink the schedule."""
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [{
            "id": "anchor-baseline", "name": "First dose",
            "anchor_type": "first_dose",
        }],
        "activities": [{
            "id": "activity-ecg", "name": "12-lead ECG",
            # The shape a real model emits: an 'offset' kind with no amount.
            "timing": {"kind": "offset", "source_label": "Pre-dose"},
        }],
        "events": [{
            "id": "event-c1d1", "name": "Cycle 1 Day 1",
            "timing": {
                "kind": "offset", "anchor_id": "anchor-baseline",
                "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1",
            },
            "activity_ids": ["activity-ecg"],
        }],
    })

    timing = plan.activities[0].timing
    assert timing is not None
    assert timing.kind == "unresolved"
    assert timing.source_label == "Pre-dose", "the exact wording must survive"
    assert timing.offset is None, "no offset may be invented"
    assert "no offset amount" in timing.notes

    rows, _warnings = project_canonical_plan(plan)
    procedure = rows[0]["procedures"][0]

    # The visit itself is still exactly Day 1 — one vague procedure must not
    # make the whole visit unreviewable.
    assert rows[0]["day_offset"] == 0
    assert rows[0]["review_status"] == "ok"
    assert procedure["timing"] == "Pre-dose"
    assert any("no offset amount" in item for item in procedure["constraints"])


def test_relative_timing_without_an_anchor_becomes_unresolved():
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [{
            "id": "anchor-baseline", "name": "Baseline",
            "anchor_type": "first_dose",
        }],
        "events": [{
            "id": "event-followup", "name": "Follow-up",
            "timing": {
                "kind": "relative", "offset": {"value": 30, "unit": "day"},
                "source_label": "30 days after the last dose",
            },
        }],
    })

    rows, _warnings = project_canonical_plan(plan)

    assert plan.events[0].timing.kind == "unresolved"
    assert rows[0]["day_offset"] is None, "an unanchored offset must not be dated"
    assert rows[0]["review_status"] == "pending"
    assert rows[0]["source_day_label"] == "30 days after the last dose"
    assert any("no anchor" in item for item in rows[0]["operational_constraints"])


def test_a_well_formed_timing_is_left_completely_alone():
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [{
            "id": "anchor-baseline", "name": "Baseline",
            "anchor_type": "first_dose",
        }],
        "events": [{
            "id": "event-week-4", "name": "Week 4",
            "timing": {
                "kind": "offset", "anchor_id": "anchor-baseline",
                "offset": {"value": 28, "unit": "day"}, "source_label": "Day 29",
            },
        }],
    })

    timing = plan.events[0].timing
    assert timing.kind == "offset"
    assert timing.notes == ""
    rows, _warnings = project_canonical_plan(plan)
    assert rows[0]["day_offset"] == 28
    assert rows[0]["review_status"] == "ok"


def test_valueless_stated_window_becomes_unclear_not_a_default():
    """A window asserted without a magnitude must never become a number."""
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [{
            "id": "anchor-baseline", "name": "Baseline",
            "anchor_type": "first_dose",
        }],
        "events": [{
            "id": "event-week-4", "name": "Week 4",
            "timing": {
                "kind": "offset", "anchor_id": "anchor-baseline",
                "offset": {"value": 28, "unit": "day"}, "source_label": "Day 29",
            },
            "window": {"state": "stated"},
        }],
    })

    window = plan.events[0].window
    assert window.state == "unclear"
    assert window.early is None and window.late is None

    rows, _warnings = project_canonical_plan(plan)

    assert rows[0]["window_days"] is None, "no default window may be invented"
    assert rows[0]["window_before"] is None and rows[0]["window_after"] is None
    assert rows[0]["review_status"] == "pending"
    assert any("no magnitude" in item
               for item in rows[0]["operational_constraints"])


def test_plural_and_abbreviated_units_are_normalised():
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [{
            "id": "anchor-baseline", "name": "Baseline",
            "anchor_type": "first_dose",
        }],
        "events": [{
            "id": "event-week-2", "name": "Week 2",
            "timing": {
                "kind": "offset", "anchor_id": "anchor-baseline",
                "offset": {"value": 2, "unit": "weeks"}, "source_label": "Week 2",
            },
            "window": {
                "state": "stated",
                "early": {"value": 3, "unit": "days"},
                "late": {"value": 3, "unit": "days"},
            },
        }],
    })

    rows, _warnings = project_canonical_plan(plan)

    assert plan.events[0].timing.offset.unit == "week"
    assert rows[0]["day_offset"] == 14
    assert rows[0]["window_days"] == 3
    assert rows[0]["review_status"] == "ok"


def test_activities_split_into_clinical_and_administrative_columns():
    """The editor has two columns; the protocol supplies one undifferentiated list."""
    from schedule_schema import classify_visit_activities

    clinical, admin = classify_visit_activities([
        "Vital signs", "Blood draw", "ECG", "Informed Consent", "eCRF",
        "Randomization", "Housing / confinement", "Check-out", "Washout period",
        "Drug accountability", "Eligibility criteria review", "PK sampling",
        "Vital signs",
    ])

    assert clinical == ["Vital signs", "Blood draw", "ECG", "PK sampling"], (
        "assessments stay clinical and duplicates collapse")
    assert admin == [
        "Informed Consent", "eCRF", "Randomization", "Housing / confinement",
        "Check-out", "Washout period", "Drug accountability",
        "Eligibility criteria review",
    ], "paperwork and site logistics belong in the administrative column"


def test_unrecognised_activities_default_to_clinical():
    """Mis-filing a real assessment as paperwork is the dangerous direction."""
    from schedule_schema import classify_visit_activities

    clinical, admin = classify_visit_activities([
        "Bone marrow aspirate", "Tumour assessment (RECIST 1.1)", "Some novel assay",
    ])

    assert admin == []
    assert len(clinical) == 3


def test_task_classification_never_invents_or_rewrites_a_name():
    from schedule_schema import classify_visit_activities

    original = ["12-lead ECG", "Informed Consent (re-consent if amended)"]
    clinical, admin = classify_visit_activities(original)

    assert clinical + admin == original or set(clinical + admin) == set(original)
    assert all(item in original for item in clinical + admin)


def test_period_dosing_anchor_is_dated_from_its_own_study_day_label():
    """A real crossover names two anchors per period: check-in and dosing.

    The graph never states the gap between them, but each anchor prints its own
    study day. Reading that keeps Period I dated instead of collapsing every
    dosing-anchored visit to a dash.
    """
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [
            {"id": "p1_start", "name": "Period I check-in",
             "anchor_type": "period_start", "source_label": "Day 0 of each period"},
            {"id": "p1_dose", "name": "Period I dosing",
             "anchor_type": "first_dose", "source_label": "Day 1 of Period I"},
            {"id": "p2_start", "name": "Period II check-in",
             "anchor_type": "period_start", "source_label": "Day 0 of each period"},
            {"id": "p2_dose", "name": "Period II dosing",
             "anchor_type": "dose", "source_label": "Day 1 of Period II"},
        ],
        "branches": [
            {"id": "period_1", "name": "Period I", "branch_type": "period"},
            {"id": "period_2", "name": "Period II", "branch_type": "period"},
        ],
        "events": [
            {"id": "p1_d0", "name": "Period I Day 0", "period_id": "period_1",
             "timing": {"kind": "offset", "anchor_id": "p1_start",
                        "offset": {"value": 0, "unit": "day"},
                        "source_label": "Day 0 of each period"}},
            {"id": "p1_d1", "name": "Period I Day 1", "period_id": "period_1",
             "timing": {"kind": "offset", "anchor_id": "p1_dose",
                        "offset": {"value": 0, "unit": "day"},
                        "source_label": "Day 1 of each period"}},
            {"id": "p1_d2", "name": "Period I Day 2", "period_id": "period_1",
             "timing": {"kind": "offset", "anchor_id": "p1_dose", "relation": "after",
                        "offset": {"value": 1, "unit": "day"},
                        "source_label": "Day 2 of each period"}},
            {"id": "p2_d0", "name": "Period II Day 0", "period_id": "period_2",
             "timing": {"kind": "offset", "anchor_id": "p2_start",
                        "offset": {"value": 0, "unit": "day"},
                        "source_label": "Day 0 of each period"}},
            {"id": "p2_d1", "name": "Period II Day 1", "period_id": "period_2",
             "timing": {"kind": "offset", "anchor_id": "p2_dose",
                        "offset": {"value": 0, "unit": "day"},
                        "source_label": "Day 1 of each period"}},
        ],
        # The washout length is genuinely absent from the protocol.
        "transitions": [{"id": "t1", "from_event_id": "p1_d2",
                         "to_event_id": "p2_d0", "relation": "before"}],
    })

    rows, _warnings = project_canonical_plan(plan)
    days = {row["name"]: row["day_offset"] for row in rows}

    # Period I is fully dated from the two anchors' own printed study days.
    assert days["Period I Day 0"] == 0
    assert days["Period I Day 1"] == 1
    assert days["Period I Day 2"] == 2

    # Period II cannot be dated: the washout duration is not stated, and
    # "Day 0 of each period" must NOT leak across from Period I.
    assert days["Period II Day 0"] is None
    assert days["Period II Day 1"] is None
    period_two = next(row for row in rows if row["name"] == "Period II Day 0")
    assert period_two["review_status"] == "pending"
    assert any("not stated in the protocol" in item
               for item in period_two["operational_constraints"])


def test_anchor_without_a_study_day_label_stays_unresolved():
    """No printed day means no derivation - never a guess."""
    plan = CanonicalSchedulePlan.model_validate({
        "anchors": [
            {"id": "baseline", "name": "First dose", "anchor_type": "first_dose",
             "source_label": "Day 1"},
            {"id": "last_dose", "name": "Last study drug administration",
             "anchor_type": "last_dose", "source_label": "last study drug administration"},
        ],
        "branches": [{"id": "main", "name": "Treatment", "branch_type": "period"}],
        "events": [
            {"id": "dosing", "name": "Dosing", "period_id": "main",
             "timing": {"kind": "offset", "anchor_id": "baseline",
                        "offset": {"value": 0, "unit": "day"}, "source_label": "Day 1"}},
            {"id": "followup", "name": "Telephonic follow-up", "period_id": "main",
             "timing": {"kind": "relative", "anchor_id": "last_dose", "relation": "after",
                        "offset": {"value": 30, "unit": "day"},
                        "source_label": "30 days after the last dose"}},
        ],
    })

    rows, _warnings = project_canonical_plan(plan)
    days = {row["name"]: row["day_offset"] for row in rows}

    assert days["Dosing"] == 0
    assert days["Telephonic follow-up"] is None
