"""LLM-as-judge grader for protocol -> visit-schedule extraction.

Given the *source* protocol PDF and the extractor's JSON output, an independent
grader (Gemini 3.6 Flash by default, prompted purely as a QA reviewer) scores the
extraction against a fixed rubric and returns pass / partial / fail. The grader
reads the source document, so it grades against ground truth rather than against
the extractor's own reasoning; no per-file expected output is hand-authored, so
the eval generalizes to any future protocol.

Env:
  GEMINI_API_KEY               required
  EXTRACTION_JUDGE_MODEL       override grader model (default gemini-3.6-flash)
"""
from __future__ import annotations

import os
from typing import List, Literal

from pydantic import BaseModel, Field

JUDGE_MODEL = os.getenv("EXTRACTION_JUDGE_MODEL", "gemini-3.6-flash")


class Grade(BaseModel):
    """Rubric-scored assessment of one extraction. Field descriptions are the
    grading instructions the model reads when producing the structured output."""

    doc_kind: Literal["schedule", "not_a_schedule"] = Field(
        description="'schedule' if the PDF contains a visit/assessment schedule (a "
        "Schedule of Assessments/Activities/Events, a visit table, or prose that lists "
        "visits with study days). 'not_a_schedule' for checklists, consent forms, generic "
        "overviews, or anything with no per-visit schedule — an EMPTY extraction is the "
        "correct answer for those.")
    visits_in_source: int = Field(
        description="Number of distinct scheduled visits/timepoints the source actually "
        "defines. Count enumerated per-cycle visits for cyclic designs and per-timepoint "
        "rows for hour-based intra-day schedules; 0 when not a schedule.")
    visits_extracted: int = Field(description="Number of visits present in the extraction JSON.")
    completeness: Literal["all", "most", "some", "none", "n/a"] = Field(
        description="Fraction of the source's visits captured by the extraction. 'n/a' only "
        "when the source is not a schedule.")
    day_offsets: Literal["correct", "mostly_correct", "several_wrong", "wrong", "n/a"] = Field(
        description="Correctness of the ABSOLUTE day_offset values (Day 1 = 0; screening/"
        "run-in negative; Week N -> ~N*7; Month N -> ~N*30 unless an explicit day is given; "
        "cyclic Cycle C Day D at length L -> (C-1)*L+(D-1); crossover continuous across "
        "periods and washout). Use 'n/a' when the source states no resolvable per-visit day "
        "(visit-only tables, or hour-based schedules) or when not a schedule.")
    windows: Literal["correct", "mostly_correct", "wrong", "n/a"] = Field(
        description="Are the +/- day visit windows parsed correctly where the source states "
        "them? 'n/a' if the source states no windows.")
    activities: Literal["good", "partial", "poor", "n/a"] = Field(
        description="Do the per-visit activities reasonably reflect the procedures marked "
        "for each visit's column? 'n/a' when not a schedule.")
    hallucination: Literal["none", "minor", "major"] = Field(
        description="Visits or activities invented by the extractor that are not supported "
        "by the source. 'major' = whole fabricated visits or a fabricated day model.")
    verdict: Literal["pass", "partial", "fail"] = Field(
        description="'pass' = a sponsor could save this draft after at most trivial edits: "
        "all/most visits captured, day_offsets correct or n/a, no major hallucination, and "
        "empty exactly when the doc is not a schedule. 'partial' = usable but needs moderate "
        "fixes (some missed visits, several wrong days, or minor hallucination). 'fail' = "
        "many missed visits, a wrong day model, major hallucination, empty when a schedule "
        "exists, or non-empty when the doc is not a schedule.")
    issues: List[str] = Field(
        default_factory=list,
        description="Short, specific problems, each <=15 words. Empty list if flawless.")


_JUDGE_SYSTEM = """You are a meticulous QA grader for a clinical-trial \
"protocol -> visit schedule" extractor. You receive (1) the source protocol PDF and \
(2) the JSON the extractor produced — a FLAT list of visits, each \
{name, day_offset, window_days, activities}. Grade how faithfully the JSON captures the \
source's Schedule of Assessments using the rubric fields. Grade against these conventions \
(which the extractor is required to follow), not your own preferences:

- day_offset is the ABSOLUTE study day with Day 1 = 0. Screening/run-in before baseline is \
NEGATIVE. Week N -> N*7 and Month N -> N*30 unless the source gives an explicit day. \
Cyclic: Cycle C Day D at cycle length L -> (C-1)*L + (D-1). Crossover: offsets run \
continuously across periods and washout.
- A FLAT list is correct. Cycle / arm / period structure is encoded by the absolute \
day_offset plus a self-describing name ("Cycle 2 Day 1", "Period 2 Day 1", "Arm B - \
Week 4"). Never penalize the output for being flat rather than nested.
- Multi-arm trials that share ONE Schedule of Assessments should be emitted ONCE (not \
duplicated per arm). Only genuinely divergent per-arm schedules should be split and \
arm-prefixed.
- If the document has NO per-visit schedule (a GCP inspection checklist, a consent form, a \
one-slide study overview, a data-collection field list), the CORRECT extraction is an \
EMPTY list: set doc_kind = not_a_schedule, and verdict = pass ONLY if the extraction is \
empty (fail if it invented visits).
- Some real schedules give no resolvable day per visit (visit-only tables like \
"Visit 1..7", or hour-based intra-day timepoints like Hour 0/6/12). There, set \
day_offsets = n/a and judge completeness/activities from the visit names and marked \
procedures; do NOT fail solely for imperfect day numbers.

Be STRICT about missed visits and invented visits. Be LENIENT about a day-range visit \
(e.g. "Day 14-17") represented as its start day plus a window, and about minor activity \
wording. The verdict must reflect real usability: 'pass' means a sponsor could save the \
draft after at most trivial edits."""


async def grade(pdf_bytes: bytes, extraction_json: str, *, model: str | None = None) -> Grade:
    """Grade one extraction against its source PDF. Raises on API / parse failure."""
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=api_key)
    async_client = client.aio
    user_text = (
        "Grade the extractor output below against the attached protocol PDF.\n\n"
        "EXTRACTOR OUTPUT (JSON):\n" + extraction_json)
    try:
        resp = await async_client.models.generate_content(
            model=model or JUDGE_MODEL,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                user_text,
            ],
            config=types.GenerateContentConfig(
                system_instruction=_JUDGE_SYSTEM,
                max_output_tokens=6000,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=Grade,
            ),
        )
    finally:
        await async_client.aclose()
        client.close()
    parsed = getattr(resp, "parsed", None)
    if parsed is None:
        raise RuntimeError("grader returned no parseable grade")
    return parsed if isinstance(parsed, Grade) else Grade.model_validate(parsed)
