"""Nested protocol groups and dependent enrolment (MTB requirement doc 8).

Doc 8 sections 10 and 15-18. Protocols nest their groups - "Part A, Cohort 1" -
and the failure this guards against is quiet: offering every cohort in the
protocol regardless of the part already chosen lets someone record an assignment
that does not exist, which then produces a schedule for a patient nobody can
locate in the protocol.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.enrolment import (
    enrolment_options, validate_assignment,
)
from app.domain.schedule.models import (
    Anchor, GenericDimension, ScheduleMetadata, StudyDimension, UniversalSchedule,
)


def dimension(code: str, name: str, **changes) -> StudyDimension:
    return StudyDimension(
        code=code, protocol_label=code, display_name=name, **changes)


def generic(dimension_type: str, code: str, name: str, **changes) -> GenericDimension:
    return GenericDimension(
        dimension_type=dimension_type, code=code, protocol_label=code,
        display_name=name, **changes,
    )


def schedule() -> UniversalSchedule:
    """Two parts, each with its own cohorts, plus an unnested substudy."""
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary"),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE")],
        cohorts=[
            dimension("C1", "Cohort 1", parent_dimension_type="PART", parent_code="A"),
            dimension("C2", "Cohort 2", parent_dimension_type="PART", parent_code="A"),
            dimension("C3", "Cohort 3", parent_dimension_type="PART", parent_code="B"),
        ],
        dimensions=[
            generic("PART", "A", "Part A"),
            generic("PART", "B", "Part B"),
            generic("SUBSTUDY", "CARDIAC", "Cardiac substudy"),
        ],
        events=[],
    )


def by_type(options) -> dict:
    return {item.dimension_type: item for item in options}


# --- doc 8 s15-s18: dependent dropdowns ----------------------------------------

def test_a_nested_dimension_is_blocked_until_its_parent_is_chosen():
    cohort = by_type(enrolment_options(schedule()))["COHORT"]

    assert cohort.blocked is True
    assert cohort.depends_on == "PART"
    assert cohort.options == []
    assert "study part" in cohort.reason


def test_choosing_the_parent_offers_only_that_parents_children():
    cohort = by_type(enrolment_options(schedule(), {"PART": "A"}))["COHORT"]

    assert cohort.blocked is False
    assert [item.code for item in cohort.options] == ["C1", "C2"]


def test_a_different_parent_offers_a_different_set():
    cohort = by_type(enrolment_options(schedule(), {"PART": "B"}))["COHORT"]

    assert [item.code for item in cohort.options] == ["C3"]


def test_an_unnested_dimension_is_always_selectable():
    """A substudy that belongs to no part must not be hidden behind one."""
    substudy = by_type(enrolment_options(schedule()))["SUBSTUDY"]

    assert substudy.blocked is False
    assert substudy.depends_on is None
    assert [item.code for item in substudy.options] == ["CARDIAC"]


def test_the_parent_dimension_itself_lists_every_part():
    part = by_type(enrolment_options(schedule()))["PART"]

    assert [item.code for item in part.options] == ["A", "B"]


def test_a_parent_with_no_children_says_so_rather_than_appearing_broken():
    plan = schedule()
    plan.dimensions.append(generic("PART", "C", "Part C"))
    cohort = by_type(enrolment_options(plan, {"PART": "C"}))["COHORT"]

    assert cohort.options == []
    assert cohort.blocked is False
    assert "no cohort exists" in cohort.reason


def test_an_empty_selection_is_the_same_as_no_selection():
    assert by_type(enrolment_options(schedule(), {"PART": None}))["COHORT"].blocked is True


# --- doc 8 s10 and s30: an assignment that cannot exist is refused --------------

def test_a_consistent_assignment_has_no_problems():
    assert validate_assignment(schedule(), {"PART": "A", "COHORT": "C1"}) == []


def test_a_cohort_from_the_wrong_part_is_rejected():
    """The mistake that looks plausible and produces the wrong visits."""
    problems = validate_assignment(schedule(), {"PART": "A", "COHORT": "C3"})

    assert len(problems) == 1
    assert "'B'" in problems[0] and "'A'" in problems[0]


def test_a_nested_cohort_without_its_part_is_rejected():
    problems = validate_assignment(schedule(), {"COHORT": "C1"})

    assert problems and "has not been selected" in problems[0]


def test_an_undefined_value_is_rejected_rather_than_becoming_no_group():
    """Doc 8 s30: silently dropping an unknown group widens who gets the visit."""
    problems = validate_assignment(schedule(), {"COHORT": "C9"})

    assert problems == ["'C9' is not a defined COHORT"]


def test_an_undefined_dimension_is_rejected():
    assert validate_assignment(schedule(), {"PLANET": "MARS"}) == [
        "PLANET is not a dimension this protocol defines"
    ]


def test_clearing_an_assignment_is_allowed():
    assert validate_assignment(schedule(), {"COHORT": None}) == []


# --- doc 8 s10: a half-stated hierarchy is refused at the model boundary --------

def test_a_parent_type_without_a_parent_code_is_rejected():
    with pytest.raises(ValueError, match="both its type and its code"):
        dimension("C1", "Cohort 1", parent_dimension_type="PART")


def test_a_parent_code_without_a_parent_type_is_rejected():
    with pytest.raises(ValueError, match="both its type and its code"):
        dimension("C1", "Cohort 1", parent_code="A")
