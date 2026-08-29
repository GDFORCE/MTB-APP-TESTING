"""Structural invariants every extracted schedule must satisfy.

The LLM judge in judge.py answers "is this extraction faithful to THIS
document?" — it is accurate but costs a paid call per protocol. These checks
answer a different, cheaper question: "is this extraction *structurally
coherent at all*?"

That distinction is what makes them useful for robustness. A judge can only
grade documents you have paid to grade; an invariant fires on any schedule the
system will ever produce, including protocols nobody has seen yet. They run
offline, for free, over results.json, so a regression that mangles day
arithmetic or collapses every visit onto baseline is caught without spending a
cent.

Each check returns a list of human-readable violations. Empty list = clean.
"""
from __future__ import annotations

from typing import Iterable

# Bounds chosen to be generous — a long oncology follow-up can genuinely run
# years, and screening can start months early. These catch nonsense, not
# unusual-but-real schedules.
MIN_DAY, MAX_DAY = -400, 4000
MAX_WINDOW_DAYS = 90
MAX_VISITS = 400


def _dated(visits: Iterable[dict]) -> list[dict]:
    return [v for v in visits if v.get("day_offset") is not None]


def check_visit_fields(visits: list[dict]) -> list[str]:
    """Per-visit sanity: a visit the app cannot render is a bug, not a draft."""
    out: list[str] = []
    for i, v in enumerate(visits):
        label = v.get("name") or f"<unnamed #{i}>"
        if not (v.get("name") or "").strip():
            out.append(f"visit #{i} has no name")
        if "{" in (v.get("name") or "") or "}" in (v.get("name") or ""):
            out.append(f"'{label}' still contains an unexpanded name template")

        day = v.get("day_offset")
        if day is not None and not (MIN_DAY <= day <= MAX_DAY):
            out.append(f"'{label}' has an implausible day_offset ({day})")

        end = v.get("day_end")
        if end is not None and day is not None and end < day:
            out.append(f"'{label}' ends (day {end}) before it starts (day {day})")

        win = v.get("window_days")
        if win is not None and (win < 0 or win > MAX_WINDOW_DAYS):
            out.append(f"'{label}' has an implausible window (+/-{win} days)")

        for field in ("window_before", "window_after"):
            val = v.get(field)
            if val is not None and val < 0:
                out.append(f"'{label}' has a negative {field} ({val})")

        hour_end, hour = v.get("hour_end"), v.get("hour_offset")
        if hour_end is not None and hour is not None and hour_end < hour:
            out.append(f"'{label}' hour range ends before it starts")

        acts = v.get("activities") or []
        if any(not str(a).strip() for a in acts):
            out.append(f"'{label}' has a blank activity entry")
        if len(acts) != len(set(acts)):
            out.append(f"'{label}' lists a duplicate activity")
    return out


def check_no_duplicates(visits: list[dict]) -> list[str]:
    """Two identical visits means a patient gets booked twice for one thing."""
    seen: set = set()
    out: list[str] = []
    for v in visits:
        key = ((v.get("name") or "").strip().lower(), v.get("day_offset"),
               v.get("hour_offset"))
        if key in seen:
            out.append(f"duplicate visit '{v.get('name')}' at day {v.get('day_offset')}")
        seen.add(key)
    return out


def check_ordering(visits: list[dict]) -> list[str]:
    """Dated visits must come back chronologically, undated ones last."""
    out: list[str] = []
    days = [v.get("day_offset") for v in visits]
    dated = [d for d in days if d is not None]
    if dated != sorted(dated):
        out.append("visits are not in chronological order")
    first_undated = next((i for i, d in enumerate(days) if d is None), None)
    if first_undated is not None:
        if any(d is not None for d in days[first_undated:]):
            out.append("a dated visit appears after an undated one")
    return out


def check_not_collapsed(visits: list[dict]) -> list[str]:
    """Catch the classic failure: every visit landing on the same day.

    This is what a null-day bug looks like from the outside — a schedule where
    the patient is told to attend eight visits all on their baseline date.
    """
    dated = _dated(visits)
    if len(dated) >= 3 and len({v["day_offset"] for v in dated}) == 1:
        return [f"all {len(dated)} dated visits share day {dated[0]['day_offset']} "
                "— day offsets were probably lost"]
    return []


def check_schedule_kind(rec: dict) -> list[str]:
    """`schedule_kind` and the visit list must tell the same story."""
    out: list[str] = []
    kind = rec.get("schedule_kind")
    n = len(rec.get("visits") or [])
    if kind == "none" and n:
        out.append(f"schedule_kind is 'none' but {n} visits were returned")
    if kind and kind != "none" and n == 0:
        out.append(f"schedule_kind is '{kind}' but no visits were returned")
    return out


def check_volume(visits: list[dict]) -> list[str]:
    if len(visits) > MAX_VISITS:
        return [f"{len(visits)} visits exceeds the {MAX_VISITS} cap"]
    return []


def check_screening_sign(visits: list[dict]) -> list[str]:
    """Screening precedes baseline, so its offset should not be positive.

    Advisory rather than absolute — a few protocols do screen on Day 1 — so it
    is reported as a soft finding for review.
    """
    out: list[str] = []
    for v in visits:
        name = (v.get("name") or "").lower()
        vtype = (v.get("visit_type") or "").lower()
        day = v.get("day_offset")
        if day is not None and day > 0 and ("screen" in name or "screen" in vtype):
            out.append(f"'{v.get('name')}' looks like screening but sits at day {day}")
    return out


ALL_CHECKS = (
    ("fields", lambda r: check_visit_fields(r.get("visits") or [])),
    ("duplicates", lambda r: check_no_duplicates(r.get("visits") or [])),
    ("ordering", lambda r: check_ordering(r.get("visits") or [])),
    ("collapsed", lambda r: check_not_collapsed(r.get("visits") or [])),
    ("schedule_kind", check_schedule_kind),
    ("volume", lambda r: check_volume(r.get("visits") or [])),
    ("screening_sign", lambda r: check_screening_sign(r.get("visits") or [])),
)


def check_record(rec: dict) -> list[str]:
    """Run every invariant against one extraction result."""
    if rec.get("error"):
        return []          # extraction failed; nothing structural to judge
    out: list[str] = []
    for name, fn in ALL_CHECKS:
        for msg in fn(rec):
            out.append(f"[{name}] {msg}")
    return out


def report(recs: list[dict]) -> tuple[int, list[str]]:
    """Check every record. Returns (clean_count, formatted violation lines)."""
    lines: list[str] = []
    clean = 0
    for rec in recs:
        if rec.get("error"):
            continue
        violations = check_record(rec)
        if violations:
            lines.append(f"{rec.get('file', '?')}:")
            lines.extend(f"    {v}" for v in violations)
        else:
            clean += 1
    return clean, lines


def main() -> int:
    import json
    import os
    import sys

    path = (sys.argv[1] if len(sys.argv) > 1
            else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "results", "results.json"))
    if not os.path.exists(path):
        print(f"No results at {path}. Run corpus_eval.py first.")
        return 2
    with open(path, encoding="utf-8") as f:
        recs = json.load(f)
    clean, lines = report(recs)
    graded = sum(1 for r in recs if not r.get("error"))
    if lines:
        print("\n".join(lines))
        print()
    print(f"structurally clean: {clean}/{graded}")
    return 0 if clean == graded else 1


if __name__ == "__main__":
    raise SystemExit(main())
