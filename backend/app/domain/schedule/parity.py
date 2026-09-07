"""Comparing the canonical engine against the live operational schedule.

This is the gate that has to pass before any real patient's visits are read from
the engine instead of from the operational store. Routing reads without it means
discovering a disagreement when a site turns up on the wrong day.

The comparison is deliberately unforgiving:

  * a visit present on one side and not the other is a DIFFERENCE, never an
    omission - the two systems disagreeing about which visits exist is the worst
    failure available, and it is the easiest one to hide by matching loosely;
  * a date that differs by one day is a DIFFERENCE. There is no tolerance
    setting, because "close enough" is how a protocol window gets missed;
  * the engine refusing to date a visit the legacy path DID date is still a
    difference. It is usually the engine being right - legacy substituted a date
    where no anchor existed - but it changes what a site sees, so a person signs
    it off rather than the system deciding for them.

Nothing here decides to cut over. It produces evidence a human acts on.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
import re

from pydantic import Field

from .timing import StrictModel


class DifferenceKind(StrEnum):
    """Why two schedules disagree, in the order a reviewer cares about."""

    MISSING_IN_ENGINE = "MISSING_IN_ENGINE"
    MISSING_IN_LEGACY = "MISSING_IN_LEGACY"
    DATE_DIFFERS = "DATE_DIFFERS"
    WINDOW_DIFFERS = "WINDOW_DIFFERS"
    ENGINE_UNDATED = "ENGINE_UNDATED"
    LEGACY_UNDATED = "LEGACY_UNDATED"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"


class ParityVerdict(StrEnum):
    MATCH = "MATCH"
    DIFFERENCES_FOUND = "DIFFERENCES_FOUND"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class ComparableVisit(StrictModel):
    """One visit, reduced to what both systems can be compared on."""

    key: str = Field(min_length=1)
    name: str = ""
    scheduled_date: date | None = None
    window_start: date | None = None
    window_end: date | None = None


class ParityDifference(StrictModel):
    kind: DifferenceKind
    key: str
    name: str
    legacy: str | None = None
    engine: str | None = None
    detail: str


class ParityReport(StrictModel):
    """What a reviewer signs off before reads move to the engine."""

    verdict: ParityVerdict
    compared: int = 0
    matched: int = 0
    differences: list[ParityDifference] = Field(default_factory=list)
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == ParityVerdict.MATCH


def normalize_key(value: str) -> str:
    """Fold a visit name to something both systems produce identically.

    Case, punctuation and spacing differ freely between an operational template
    name and a canonical display name; the words do not. Anything beyond that is
    a real difference and must not be normalised away.
    """
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def to_comparable(
    rows: list[dict[str, object]],
    *,
    key_fields: tuple[str, ...] = ("event_code", "name"),
) -> list[ComparableVisit]:
    """Reduce either system's visit documents to the comparable shape."""
    output: list[ComparableVisit] = []
    for row in rows:
        raw_key = ""
        for field in key_fields:
            candidate = row.get(field)
            if candidate:
                raw_key = str(candidate)
                break
        name = str(row.get("name") or raw_key or "")
        output.append(ComparableVisit(
            key=normalize_key(raw_key or name) or "unnamed",
            name=name,
            scheduled_date=_as_date(row.get("scheduled_date")),
            window_start=_as_date(row.get("window_start")),
            window_end=_as_date(row.get("window_end")),
        ))
    return output


def _show(value: date | None) -> str:
    return value.isoformat() if value else "no date"


def compare_schedules(
    legacy: list[ComparableVisit], engine: list[ComparableVisit],
) -> ParityReport:
    """Compare one patient's schedule across both systems.

    Both sides are indexed by normalized key. A key appearing more than once on
    either side is reported as AMBIGUOUS_MATCH rather than being paired by
    position: pairing two same-named visits by their order in a list is exactly
    the kind of quiet guess this gate exists to prevent.
    """
    if not legacy and not engine:
        return ParityReport(
            verdict=ParityVerdict.NOT_COMPARABLE,
            note="neither system produced any visit for this patient",
        )

    legacy_index = _index(legacy)
    engine_index = _index(engine)
    differences: list[ParityDifference] = []
    matched = 0

    for key in sorted(set(legacy_index) | set(engine_index)):
        left = legacy_index.get(key)
        right = engine_index.get(key)

        if (left is not None and len(left) > 1) or (right is not None and len(right) > 1):
            present = left or right or []
            name = present[0].name if present else key
            differences.append(ParityDifference(
                kind=DifferenceKind.AMBIGUOUS_MATCH, key=key, name=name,
                legacy=f"{len(left or [])} visits", engine=f"{len(right or [])} visits",
                detail=(
                    "more than one visit carries this name, so the two systems "
                    "cannot be matched without guessing which is which"
                ),
            ))
            continue

        if right is None:
            item = left[0]
            differences.append(ParityDifference(
                kind=DifferenceKind.MISSING_IN_ENGINE, key=key, name=item.name,
                legacy=_show(item.scheduled_date), engine=None,
                detail="the operational schedule has this visit and the engine does not",
            ))
            continue
        if left is None:
            item = right[0]
            differences.append(ParityDifference(
                kind=DifferenceKind.MISSING_IN_LEGACY, key=key, name=item.name,
                legacy=None, engine=_show(item.scheduled_date),
                detail="the engine produces this visit and the operational schedule does not",
            ))
            continue

        found = _compare_one(left[0], right[0], key)
        if found:
            differences.extend(found)
        else:
            matched += 1

    compared = len(set(legacy_index) | set(engine_index))
    if differences:
        return ParityReport(
            verdict=ParityVerdict.DIFFERENCES_FOUND, compared=compared,
            matched=matched, differences=differences,
            note=(
                f"{len(differences)} difference(s) need a person to look at them "
                "before this patient's visits are read from the engine"
            ),
        )
    return ParityReport(
        verdict=ParityVerdict.MATCH, compared=compared, matched=matched,
        note="both systems produce the same visits on the same dates",
    )


def _index(items: list[ComparableVisit]) -> dict[str, list[ComparableVisit]]:
    grouped: dict[str, list[ComparableVisit]] = {}
    for item in items:
        grouped.setdefault(item.key, []).append(item)
    return grouped


def _compare_one(
    legacy: ComparableVisit, engine: ComparableVisit, key: str,
) -> list[ParityDifference]:
    differences: list[ParityDifference] = []

    if legacy.scheduled_date != engine.scheduled_date:
        if engine.scheduled_date is None:
            differences.append(ParityDifference(
                kind=DifferenceKind.ENGINE_UNDATED, key=key, name=legacy.name,
                legacy=_show(legacy.scheduled_date), engine="no date",
                detail=(
                    "the engine will not date this visit. That is usually correct - "
                    "the operational schedule substituted a date where the protocol "
                    "anchor is missing - but a site currently sees a date, so this "
                    "needs a decision rather than a silent change"
                ),
            ))
        elif legacy.scheduled_date is None:
            differences.append(ParityDifference(
                kind=DifferenceKind.LEGACY_UNDATED, key=key, name=engine.name,
                legacy="no date", engine=_show(engine.scheduled_date),
                detail="the engine dates a visit the operational schedule left undated",
            ))
        else:
            delta = (engine.scheduled_date - legacy.scheduled_date).days
            differences.append(ParityDifference(
                kind=DifferenceKind.DATE_DIFFERS, key=key, name=legacy.name,
                legacy=_show(legacy.scheduled_date), engine=_show(engine.scheduled_date),
                detail=(
                    f"the two systems disagree by {abs(delta)} day"
                    f"{'' if abs(delta) == 1 else 's'}"
                ),
            ))

    if (legacy.window_start, legacy.window_end) != (engine.window_start, engine.window_end):
        differences.append(ParityDifference(
            kind=DifferenceKind.WINDOW_DIFFERS, key=key, name=legacy.name,
            legacy=f"{_show(legacy.window_start)} to {_show(legacy.window_end)}",
            engine=f"{_show(engine.window_start)} to {_show(engine.window_end)}",
            detail="the allowed window differs, so a visit in window in one system may be out of window in the other",
        ))
    return differences
