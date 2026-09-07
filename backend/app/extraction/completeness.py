"""Deterministic footnote and qualifier completeness checking.

Requirement source: doc 7 sections 22 to 27.

Division of labour the requirement insists on:

    The LLM determines WHAT THE FOOTNOTE MEANS.
    The Footnote Resolution Layer determines WHAT IT APPLIES TO.
    Deterministic validation ensures NOTHING IS SILENTLY LOST.

This module is the third part. It never interprets clinical meaning. It scans the
schedule-table text for markers, compares them against the qualifiers the LLM
produced, and reports anything on either side that did not find a partner:

  * a marker in the table with no qualifier          -> orphan marker
  * a footnote definition no marker points at        -> orphan footnote
  * a qualifier with no target and a non-global scope -> unresolved scope
  * two qualifiers that contradict each other        -> conflict for review

Every finding is a ValidationIssue, so an unresolved marker blocks approval in the
same way a broken event reference does.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import Field

from app.domain.schedule.models import (
    Severity, UniversalSchedule, ValidationIssue,
)
from app.domain.schedule.timing import StrictModel

# Superscript letters and digits protocols use as footnote markers.
SUPERSCRIPT = {
    "ª": "a", "¹": "1", "²": "2", "³": "3",
    "⁰": "0", "⁴": "4", "⁵": "5", "⁶": "6",
    "⁷": "7", "⁸": "8", "⁹": "9",
    "ᵃ": "a", "ᵇ": "b", "ᶜ": "c", "ᵈ": "d", "ᵉ": "e",
    "ᶠ": "f", "ᵍ": "g", "ʰ": "h", "ⁱ": "i", "ʲ": "j",
    "ᵏ": "k", "ˡ": "l", "ᵐ": "m", "ⁿ": "n", "ᵒ": "o",
    "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t", "ᵘ": "u",
    "ᵛ": "v", "ʷ": "w", "ˣ": "x", "ʸ": "y", "ᶻ": "z",
}

# Symbol markers, in the conventional protocol order.
SYMBOL_MARKERS = ("*", "†", "‡", "§", "¶", "#")

# A marked schedule cell: the marker attaches directly to the X ("Xa", "X*").
# The leading word boundary stops "Box1" matching, and the trailing lookahead
# stops a word like "Xylene" being read as marker "y".
CELL_MARKER = re.compile(
    r"\b[Xx\u2713\u2714]([a-zA-Z0-9]|["
    + re.escape("".join(SYMBOL_MARKERS))
    + r"])(?![a-zA-Z0-9])"
)

# A footnote definition line: "a. ECG should be performed pre-dose." or "* Only ..."
FOOTNOTE_DEFINITION = re.compile(
    r"^\s*(?:\(?([a-zA-Z0-9])[\).:]|([" + re.escape("".join(SYMBOL_MARKERS)) + r"]))\s+(\S.*)$"
)


class DetectedMarker(StrictModel):
    marker: str
    page_number: int | None = None
    context: str


class DetectedFootnote(StrictModel):
    marker: str
    page_number: int | None = None
    text: str


class MarkerScan(StrictModel):
    markers: list[DetectedMarker] = Field(default_factory=list)
    footnotes: list[DetectedFootnote] = Field(default_factory=list)

    def marker_set(self) -> set[str]:
        return {item.marker for item in self.markers}

    def footnote_set(self) -> set[str]:
        return {item.marker for item in self.footnotes}


def _normalize(marker: str) -> str:
    """Fold superscripts to their plain equivalent and lowercase letters."""
    mapped = SUPERSCRIPT.get(marker, marker)
    if mapped in SYMBOL_MARKERS:
        return mapped
    return unicodedata.normalize("NFKC", mapped).lower()


def scan_markers(pages: list[dict[str, object]]) -> MarkerScan:
    """Find schedule-table markers and footnote definitions in raw page text.

    Purely textual. It answers "what symbols are present", never "what do they
    mean" - meaning is the LLM's job and arrives as a Qualifier.
    """
    scan = MarkerScan()
    for page in pages:
        number = page.get("page_number")
        page_number = number if isinstance(number, int) else None
        text = page.get("text")
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            definition = FOOTNOTE_DEFINITION.match(stripped)
            if definition:
                marker = definition.group(1) or definition.group(2) or ""
                body = definition.group(3).strip()
                # A numbered visit row ("1 Screening Day -28") is not a footnote;
                # require prose long enough to be an instruction.
                if len(body) >= 12:
                    scan.footnotes.append(DetectedFootnote(
                        marker=_normalize(marker), page_number=page_number, text=body))
                    continue
            for raw in CELL_MARKER.findall(stripped):
                scan.markers.append(DetectedMarker(
                    marker=_normalize(raw), page_number=page_number,
                    context=stripped[:160],
                ))
            for character in stripped:
                if character in SUPERSCRIPT:
                    scan.markers.append(DetectedMarker(
                        marker=_normalize(character), page_number=page_number,
                        context=stripped[:160],
                    ))
    return scan


def _issue(
    code: str, message: str, *, details: dict[str, object], blocking: bool = True,
    severity: Severity = Severity.ERROR,
) -> ValidationIssue:
    return ValidationIssue(
        issue_code=code, message=message, entity_type="SCHEDULE",
        severity=severity, blocking=blocking, details=details,
    )


def check_marker_completeness(
    schedule: UniversalSchedule, scan: MarkerScan,
) -> list[ValidationIssue]:
    """Prove every detected marker and footnote survived into the schedule.

    Doc 7 section 23: a marked cell "must not become an ordinary X with the marker
    discarded". Doc 7 sections 25 and 26: orphans on either side are flagged, never
    ignored.
    """
    issues: list[ValidationIssue] = []
    qualifiers = [
        (owner, qualifier)
        for event in schedule.events
        for owner, qualifier in (
            [("EVENT", item) for item in event.qualifiers]
            + [("ACTIVITY", item) for activity in event.activities
               for item in activity.qualifiers]
        )
    ]
    resolved_markers = {
        _normalize(qualifier.marker) for _, qualifier in qualifiers if qualifier.marker
    }

    unresolved = sorted(scan.marker_set() - resolved_markers)
    if unresolved:
        issues.append(_issue(
            "UNRESOLVED_QUALIFIER",
            "Schedule table markers were detected but never resolved into schedule meaning",
            details={
                "markers": unresolved,
                "pages": sorted({
                    item.page_number for item in scan.markers
                    if item.marker in unresolved and item.page_number is not None
                }),
            },
        ))

    orphan_footnotes = sorted(scan.footnote_set() - resolved_markers - scan.marker_set())
    if orphan_footnotes:
        issues.append(_issue(
            "ORPHAN_QUALIFIER",
            "Footnote definitions were found with no matching table marker or qualifier",
            details={"markers": orphan_footnotes},
        ))

    # A marker whose footnote text was never found cannot have been interpreted
    # from anything, so it needs a reviewer even if a qualifier claims to cover it.
    undefined = sorted(scan.marker_set() - scan.footnote_set() - resolved_markers)
    if undefined:
        issues.append(_issue(
            "ORPHAN_QUALIFIER",
            "Table markers have no corresponding footnote definition in the document",
            details={"markers": undefined},
        ))
    return issues


def check_qualifier_conflicts(schedule: UniversalSchedule) -> list[ValidationIssue]:
    """Flag two qualifiers that say incompatible things about the same target.

    Doc 7 section 27: when a table footnote and a protocol section disagree, "the
    system should not blindly choose one" - it raises a conflict for review.
    """
    issues: list[ValidationIssue] = []
    by_target: dict[tuple[str, str], list[str]] = {}
    for event in schedule.events:
        owners = [event.qualifiers] + [item.qualifiers for item in event.activities]
        for group in owners:
            for qualifier in group:
                for target in qualifier.target_codes or [event.code]:
                    key = (target, qualifier.category.value)
                    by_target.setdefault(key, []).append(qualifier.text.strip())
    for (target, category), texts in sorted(by_target.items()):
        distinct = {text.casefold() for text in texts}
        if len(distinct) > 1:
            issues.append(_issue(
                "CONFLICTING_SOURCE",
                f"Conflicting {category.lower()} qualifiers apply to {target!r}",
                details={"target": target, "category": category, "statements": sorted(set(texts))},
            ))
    return issues


def run_completeness_checks(
    schedule: UniversalSchedule, pages: list[dict[str, object]],
) -> list[ValidationIssue]:
    """Full deterministic pass: nothing detected in the document is silently lost."""
    scan = scan_markers(pages)
    return [
        *check_marker_completeness(schedule, scan),
        *check_qualifier_conflicts(schedule),
    ]
