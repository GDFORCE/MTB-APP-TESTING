"""Which protocol groups a patient can be enrolled into (doc 8 s10, s15-s18).

Protocols nest their groups: "Part A, Cohort 1", "Substudy: Cardiac". Offering
every cohort in the protocol regardless of the part already chosen produces
assignments that cannot exist, and an assignment that cannot exist produces a
schedule for a patient nobody can find in the protocol.

This module is pure. It answers "given what has been chosen so far, what may be
chosen next", and never writes an assignment.
"""

from __future__ import annotations

from pydantic import Field

from .models import StudyDimension, UniversalSchedule
from .timing import StrictModel


class EnrolmentOption(StrictModel):
    code: str
    display_name: str
    description: str | None = None
    parent_dimension_type: str | None = None
    parent_code: str | None = None


class EnrolmentDimension(StrictModel):
    """One selectable dimension, with only the values still reachable."""

    dimension_type: str
    display_name: str
    options: list[EnrolmentOption] = Field(default_factory=list)
    #: The dimension that must be chosen first, when this one nests inside it.
    depends_on: str | None = None
    #: True when a parent choice is still outstanding, so nothing can be picked.
    blocked: bool = False
    reason: str | None = None


DIMENSION_LABELS = {
    "ARM": "Treatment arm",
    "COHORT": "Cohort",
    "POPULATION": "Population",
    "PART": "Study part",
    "SEQUENCE": "Treatment sequence",
    "SUBSTUDY": "Substudy",
    "COUNTRY": "Country",
    "EPOCH": "Study period",
}


def _label(dimension_type: str) -> str:
    return DIMENSION_LABELS.get(
        dimension_type, dimension_type.replace("_", " ").capitalize())


def all_dimensions(schedule: UniversalSchedule) -> dict[str, list[StudyDimension]]:
    """Every dimension the schedule defines, keyed by type."""
    grouped: dict[str, list[StudyDimension]] = {}
    for dimension_type, items in (
        ("ARM", schedule.arms),
        ("COHORT", schedule.cohorts),
        ("POPULATION", schedule.populations),
    ):
        if items:
            grouped[dimension_type] = list(items)
    for item in schedule.dimensions:
        grouped.setdefault(item.dimension_type.upper(), []).append(item)
    return grouped


def enrolment_options(
    schedule: UniversalSchedule,
    selected: dict[str, str | None] | None = None,
) -> list[EnrolmentDimension]:
    """Doc 8 s15-s18: the dependent dropdowns an enrolment form needs.

    A dimension whose values all nest inside a parent is BLOCKED until that
    parent is chosen, rather than showing every value with no way to tell which
    are valid. Once the parent is chosen, only its children are offered.
    """
    chosen = {
        key.strip().upper(): value
        for key, value in (selected or {}).items() if value
    }
    grouped = all_dimensions(schedule)
    output: list[EnrolmentDimension] = []

    for dimension_type in sorted(grouped):
        items = grouped[dimension_type]
        parents = {
            item.parent_dimension_type.upper()
            for item in items if item.parent_dimension_type
        }
        # A dimension whose values disagree about their parent type is not a
        # hierarchy we can navigate; offer everything rather than hide values.
        depends_on = next(iter(parents)) if len(parents) == 1 else None

        if depends_on is not None and depends_on not in chosen:
            output.append(EnrolmentDimension(
                dimension_type=dimension_type, display_name=_label(dimension_type),
                depends_on=depends_on, blocked=True,
                reason=f"choose a {_label(depends_on).lower()} first",
            ))
            continue

        available = items
        if depends_on is not None:
            parent_code = chosen[depends_on]
            available = [
                item for item in items
                if item.parent_code == parent_code
            ]

        output.append(EnrolmentDimension(
            dimension_type=dimension_type,
            display_name=_label(dimension_type),
            depends_on=depends_on,
            options=[
                EnrolmentOption(
                    code=item.code, display_name=item.display_name,
                    description=item.description,
                    parent_dimension_type=item.parent_dimension_type,
                    parent_code=item.parent_code,
                )
                for item in sorted(available, key=lambda value: value.code)
            ],
            reason=(
                None if available
                else f"no {_label(dimension_type).lower()} exists for the selection above"
            ),
        ))
    return output


def validate_assignment(
    schedule: UniversalSchedule, assignment: dict[str, str | None],
) -> list[str]:
    """Reasons an assignment cannot exist in this protocol, empty when it can.

    Checked rather than assumed, because an assignment that names a cohort from a
    different part looks plausible and silently produces the wrong visits.
    """
    grouped = all_dimensions(schedule)
    normalized = {
        key.strip().upper(): value for key, value in assignment.items()
    }
    problems: list[str] = []
    for dimension_type, code in normalized.items():
        items = grouped.get(dimension_type)
        if items is None:
            problems.append(f"{dimension_type} is not a dimension this protocol defines")
            continue
        if code is None:
            continue
        match = next((item for item in items if item.code == code), None)
        if match is None:
            problems.append(f"{code!r} is not a defined {dimension_type}")
            continue
        if match.parent_dimension_type is None:
            continue
        parent_type = match.parent_dimension_type.upper()
        parent_choice = normalized.get(parent_type)
        if parent_choice is None:
            problems.append(
                f"{code!r} belongs to {parent_type} {match.parent_code!r}, "
                f"which has not been selected"
            )
        elif parent_choice != match.parent_code:
            problems.append(
                f"{code!r} belongs to {parent_type} {match.parent_code!r}, "
                f"not {parent_choice!r}"
            )
    return problems
