"""Protocol -> visit-schedule extraction.

Reads a clinical-trial protocol (full protocol, synopsis, EC deck, or a bare
Schedule-of-Assessments page) and returns its visit schedule as a flat list of
visit templates that pre-fill the sponsor's visit-schedule editor. Extraction is
provider-abstracted behind ``ProtocolExtractor`` so the default Gemini backend
can later be swapped for a self-hosted vision model without touching the API
endpoint or the frontend.

DESIGN: declare structure, don't enumerate
------------------------------------------
Real protocols collapse repetition. The Schedule of Assessments prints columns
like ``Cycle 2 & Next Cycles`` or ``every 8th week thereafter``, and the numbers
needed to expand them (cycle length, cycle count, intra-cycle spacing) live in
prose on *other pages* — in the PICN protocol the table is on p42 while the
cycle length is on p15 and the expansion rule on p24.

Asking a model to emit an already-flattened list therefore asks it to do
multi-page arithmetic in its head, silently, with no way to check the result.
Instead the model emits the *structure* it read — repeating blocks with a cycle
length and a member layout, relative anchors, conditional activities — and
:func:`expand_schedule` does the arithmetic in Python, where it is deterministic
and unit-testable without an API key.

The model-facing schema is therefore richer than the frontend contract, and
``extract()`` returns an already-expanded ``ExtractedSchedule.visits`` so callers
(``POST /api/trials/{id}/extract-schedule``) keep consuming the same flat shape
they always did.

Every expansion that required an assumption (an open-ended "until progression"
tail, an unresolvable relative anchor) is recorded on ``assumptions`` /
``warnings`` so the sponsor reviews it before saving. Extraction is always a
draft — nothing is written to the trial without human confirmation.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import os
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import List, Literal, Optional, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from schedule_schema import (
    _SIMPLE_DAY_LABEL,
    _SIMPLE_DAY_RANGE_LABEL,
    CanonicalSchedulePlan,
    DocumentTaskClassification,
    ScheduleOption,
    SourceEvidence,
    canonical_from_flat,
    project_canonical_plan,
    simple_day_label_offset,
    simple_day_label_range_offsets,
    study_day_to_offset,
    validate_canonical_plan,
)

log = logging.getLogger(__name__)

# Google's newest stable multimodal model supports native PDF input and
# schema-constrained output for cross-page protocol reconstruction.
DEFAULT_MODEL = "gemini-3.6-flash"
LEGACY_CLAUDE_MODEL = "claude-opus-5"
DEFAULT_OPENROUTER_MODEL = "~deepseek/deepseek-v4-flash-latest"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_PROVIDER = "gemini"
DEFAULT_OLLAMA_MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

# Keep vision batches and the KV cache small on an 8 GB development machine.
# Large PDFs are processed sequentially and checkpointed after every batch.
OLLAMA_CONTEXT_TOKENS = 8192
OLLAMA_PAGES_PER_BATCH = 2
OLLAMA_FINAL_EVIDENCE_CHARS = 48000

# Guardrail: refuse absurdly large uploads before they ever reach the model.
# Gemini accepts inline PDFs up to 50 MB; keep the app's stricter limit.
MAX_PDF_BYTES = 25 * 1024 * 1024

# Output cap. The declarative schema keeps responses small (a 6-cycle protocol
# is ~8 rows + one repeating block, not 25 enumerated rows), but the canonical
# schedule graph (anchors/phases/branches/events/activities/recurrences/
# conditions/conflicts, each carrying evidence_ids) is verbose per visit, and a
# real multi-arm/crossover protocol can still hit this cap mid-object, which
# yields invalid JSON rather than a short schedule. Raised from 16000; shared
# by the Claude (legacy) path too, so stays under Anthropic's non-streaming
# duration guard.
MAX_OUTPUT_TOKENS = 24000

# How far to expand a repetition the protocol leaves open-ended ("continue until
# progression", "every 8th week thereafter"). Bounded so one vague protocol
# cannot materialize thousands of visits per patient; always recorded as an
# assumption for the reviewer.
OPEN_ENDED_CYCLE_CAP = 12

# Sanity ceiling on a single expansion, independent of the cap above.
MAX_EXPANDED_VISITS = 400


# ─────────────────────────── model-facing schema ───────────────────────────
# Field descriptions double as extraction instructions — the model reads them
# when producing structured output, so they carry real weight.

class ConditionalActivity(BaseModel):
    """An assessment that happens only in SOME repetitions of a cycle.

    e.g. "imaging (CT/MRI) will be performed after cycles 2, 4 and 6" — the
    visit recurs every cycle but this assessment does not.
    """
    name: str = Field(description="The assessment / procedure name.")
    cycles: List[int] = Field(
        default_factory=list,
        description="The 1-based cycle numbers this assessment applies to, e.g. [2, 4, 6].")


class FieldEvidence(BaseModel):
    """Evidence IDs supporting one extracted field or field group."""

    field: str = Field(
        description="Supported field: name, timing, window, activities, arm, or period.")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="IDs from the evidence packets that directly support this field.")


class RepeatMember(BaseModel):
    """One visit inside a repeating cycle."""
    name_template: str = Field(
        description="Visit name with '{cycle}' where the cycle number belongs, e.g. "
        "'Cycle {cycle} Day 1', 'Cycle {cycle} Intra-cycle Visit 2'.")
    source_day_label_template: Optional[str] = Field(
        default=None,
        description="Exact protocol timing label with '{cycle}' where applicable. "
        "null when the source did not print a distinct timing label.")
    day_within_cycle: int = Field(
        description="0-based day offset from the START of the cycle. The cycle's "
        "first/dosing day is 0; a visit 7 days later is 7.")
    day_end_within_cycle: Optional[int] = Field(
        default=None,
        description="0-based end-day offset for a multi-day member; null otherwise.")
    hour_offset: Optional[float] = None
    hour_offset_basis: Optional[Literal["absolute", "within_day"]] = None
    hour_end: Optional[float] = None
    visit_type: Optional[str] = Field(default=None, description="See ExtractedVisit.visit_type.")
    window_days: Optional[int] = Field(
        default=None, ge=0,
        description="Visit window as +/- days; null when the protocol does not state one.")
    window_before: Optional[int] = Field(default=None, ge=0)
    window_after: Optional[int] = Field(default=None, ge=0)
    arm: Optional[str] = None
    period: Optional[str] = None
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments performed at this visit in EVERY cycle.")
    conditional_activities: List[ConditionalActivity] = Field(
        default_factory=list,
        description="Assessments performed only in specific cycles.")
    field_evidence: List[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence IDs supporting this repeating visit's fields.")


class RepeatingBlock(BaseModel):
    """A cycle the protocol prints once and tells you to repeat.

    This is the single most important field in the schema. Use it whenever the
    Schedule of Assessments collapses repetition — a column headed 'Cycle 2 &
    Next Cycles', 'each subsequent cycle', 'Cycles 3-6', or prose like 'every
    3 weeks for 6 cycles'. Do NOT enumerate those cycles as individual visits;
    describe the block and the server will expand it exactly.
    """
    from_cycle: int = Field(description="First cycle number this block covers (1-based).")
    to_cycle: Optional[int] = Field(
        default=None,
        description="Last cycle number covered. Use null ONLY when the protocol is "
        "genuinely open-ended ('until disease progression', 'every 8th week "
        "thereafter') — the server will expand a bounded number and flag it.")
    cycle_length_days: int = Field(
        description="Length of one cycle in days. Read from the treatment plan when "
        "the schedule table does not state it (e.g. 'every 3 weekly' -> 21, "
        "'q4w' -> 28, '28-day cycle' -> 28).")
    first_cycle_start_day: int = Field(
        description="Absolute calendar offset from baseline (0 = baseline) on which "
        "cycle `from_cycle` STARTS. "
        "e.g. if cycle 1 starts at baseline and cycles are 21 days, then a block "
        "beginning at cycle 2 has first_cycle_start_day = 21.")
    members: List[RepeatMember] = Field(
        default_factory=list,
        description="The visits that occur within each cycle of this block.")


class ExtractedProcedure(BaseModel):
    """One procedure plus its own timing/window, separate from visit tolerance."""

    id: str = ""
    name: str
    timing: str = ""
    window: str = ""
    condition: str = ""
    constraints: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)


class ExtractedVisit(BaseModel):
    """One scheduled visit / timepoint from the Schedule of Assessments."""
    name: str = Field(
        description="Self-describing visit name. Use the protocol's own label and make "
        "structure explicit: 'Visit 1 - Screening', 'Cycle 2 Day 1', 'Period 2 Day 1', "
        "'Arm B - Week 4', 'Week 12', 'Early Termination', 'Unscheduled', 'Follow-up'.")
    visit_type: Optional[str] = Field(
        default=None,
        description="Category of the visit when stated or clearly inferable: 'Screening', "
        "'Baseline', 'Randomization', 'Treatment', 'Follow-up', 'End of Treatment', "
        "'End of Study', 'Early Termination', 'Unscheduled', or 'Telephonic' (a phone/"
        "telephone-icon contact). Use the protocol's own visit-type codes when given "
        "(e.g. 'SS' study-site, 'V' virtual, 'T/C' telephone). null if not determinable.")
    day_offset: Optional[int] = Field(
        default=None,
        description="Absolute calendar displacement in days from the baseline anchor, "
        "where 0 is the baseline date regardless of its printed study-day label. "
        "Screening / run-in visits before baseline are NEGATIVE. Convert Week/Month "
        "labels only when the protocol explicitly defines their anchor/cadence; Week 1 "
        "must not be assumed to mean seven days. For a calendar-date schedule, use the "
        "day count from the baseline/randomization date. "
        "Leave null when the visit's timing is expressed RELATIVE to another visit (use "
        "relative_to instead) or when the protocol genuinely does not specify a day "
        "(Early Termination, Unscheduled) — keep the visit either way.")
    source_day_label: Optional[str] = Field(
        default=None,
        description="Exact timing text printed by the protocol, such as 'Day 0', "
        "'Day 8', 'Week 4', 'Cycle 2 Day 1', or 'Unscheduled'. Do not rewrite it "
        "as an offset. null only when no timing label is present.")
    calendar_offset_value: Optional[float] = Field(
        default=None,
        description="Set only when day_offset was approximated from a calendar "
        "Month/Year label with no exact day count (30 days/month, 365 days/year). "
        "The protocol's own number, e.g. 3 for 'Month 3' (negative if before "
        "baseline). null whenever day_offset is exact or unset.")
    calendar_offset_unit: Optional[Literal["month", "year"]] = Field(
        default=None,
        description="Unit paired with calendar_offset_value: 'month' or 'year'. "
        "null whenever calendar_offset_value is null.")
    day_end: Optional[int] = Field(
        default=None,
        description="For a visit that spans MULTIPLE consecutive days as a single entry "
        "(e.g. 'Day 14-17', a period's 'Check-in / Day 1 / Check-out'), the absolute end "
        "day (same Day 1 = 0 basis). null for single-day visits.")
    hour_offset: Optional[float] = Field(
        default=None,
        description="For INTRA-DAY timepoints (PK sampling, hourly assessments), hours "
        "from dosing/Hour 0. May be negative for pre-dose. This is an absolute elapsed "
        "hour, so Hour 26 is 26 total hours and is not added on top of its containing "
        "day_offset. Only use for a genuinely hour-level schedule.")
    hour_offset_basis: Optional[Literal["absolute", "within_day"]] = Field(
        default=None,
        description="'absolute' when hour_offset is elapsed time from the schedule "
        "anchor. 'within_day' is reserved for an hour adjustment to day_offset.")
    hour_end: Optional[float] = Field(
        default=None,
        description="End of an hour RANGE, e.g. 'Hour -4 to Hour 0' -> hour_offset=-4, "
        "hour_end=0. null for a single timepoint.")
    window_days: Optional[int] = Field(
        default=None, ge=0,
        description="Visit window as a single +/- number of days (e.g. '+/- 3 days' -> 3). "
        "For an asymmetric window use the larger side here and also set window_before / "
        "window_after. null when no window is stated; never invent a default.")
    window_before: Optional[int] = Field(
        default=None,
        description="Days the visit may occur EARLY, when the protocol gives an asymmetric "
        "window (e.g. a '+3 days only' window -> window_before=0, window_after=3). null "
        "when the window is symmetric or unstated.")
    window_after: Optional[int] = Field(
        default=None,
        description="Days the visit may occur LATE for an asymmetric window. null when "
        "symmetric or unstated.")
    relative_to: Optional[str] = Field(
        default=None,
        description="When the protocol times this visit against ANOTHER visit rather than "
        "against baseline ('within 3 days after intra-cycle visit 3', '28 days after the "
        "last dose'), put that other visit's exact `name` here and the gap in "
        "relative_offset_days. The server resolves it to an absolute day.")
    relative_offset_days: Optional[int] = Field(
        default=None,
        description="Days after the `relative_to` visit (negative for before).")
    arm: Optional[str] = Field(
        default=None,
        description="Arm / cohort label when the protocol prints genuinely DIFFERENT "
        "schedules per arm. null when all arms share one schedule.")
    period: Optional[str] = Field(
        default=None,
        description="Period / phase label for crossover or multi-phase studies "
        "('Period 1', 'Washout 1', 'Extension'). null when not applicable.")
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments / procedures marked (X or a footnote symbol) in this "
        "visit's column, using the protocol's own procedure names, deduplicated and "
        "concise (e.g. 'Vitals', 'ECG', 'PK sampling', 'Randomization').")
    procedures: List[ExtractedProcedure] = Field(
        default_factory=list,
        description="Structured activity details, including activity-level timing and windows.")
    operational_constraints: List[str] = Field(
        default_factory=list,
        description="Non-visit-window constraints such as housing minimums, infusion duration, "
                    "washout gaps, PK tolerances, and conditional rules.")
    canonical_event_id: Optional[str] = None
    extraction_warning: bool = False
    review_status: Literal["pending", "ok"] = "ok"
    field_evidence: List[FieldEvidence] = Field(
        default_factory=list,
        description="Evidence IDs supporting this visit's name, timing, window, "
                    "activities, arm, and period. Unsupported fields must remain null.")

    @model_validator(mode="after")
    def default_extracted_hour_semantics(self):
        # Extracted Hour N values are absolute elapsed time. Persist the mode so
        # scheduling cannot count the day portion twice. Historical database
        # rows lack this field and retain their legacy day-plus-hour behavior.
        if self.hour_offset is not None and self.hour_offset_basis is None:
            self.hour_offset_basis = "absolute"
        return self


class ScheduleDraft(BaseModel):
    """AI-authored schedule fields supported by Gemini's structured-output API."""
    schedule_kind: Optional[str] = Field(
        default=None,
        description="One of: 'linear' (fixed visit list), 'cyclic' (repeating cycles), "
        "'crossover' (periods + washout), 'multi_arm' (different schedule per arm), "
        "'intra_day' (hour-level timepoints only), 'none' (document has no schedule).")
    # Deliberately a plain int rather than Literal[0, 1]: Gemini's structured-output
    # Schema only accepts string enum values, so an integer Literal makes the SDK
    # reject the entire request before it is sent. The validator below keeps the
    # 0/1 domain that the rest of the pipeline relies on.
    anchor_study_day: Optional[int] = Field(
        default=None,
        description="Protocol study-day number on the baseline/randomization anchor "
        "date: must be 0 or 1. null when the document is ambiguous.")
    includes_day_zero: Optional[bool] = Field(
        default=None,
        description="Whether the protocol explicitly includes Day 0. This must be "
        "known to convert non-positive labels around a Day 1 anchor.")
    visits: List[ExtractedVisit] = Field(
        default_factory=list,
        description="Explicitly-scheduled visits: screening, baseline, the visits of any "
        "cycle printed in full, end-of-treatment, follow-up, Early Termination, "
        "Unscheduled. Do NOT enumerate cycles covered by a repeating_block.")
    repeating_blocks: List[RepeatingBlock] = Field(
        default_factory=list,
        description="Cycles the protocol collapsed instead of printing. See RepeatingBlock.")
    total_cycles: Optional[int] = Field(
        default=None,
        description="Planned maximum number of treatment cycles, when stated anywhere in "
        "the document (e.g. 'maximum 6 cycles').")
    assumptions: List[str] = Field(
        default_factory=list,
        description="Any inference you had to make that a reviewer should verify — a cycle "
        "length read from a different section, an open-ended tail you bounded, an "
        "ambiguous arm structure. One short sentence each. Be honest and specific.")
    source_notes: Optional[str] = Field(
        default=None,
        description="Where in the document the schedule came from (e.g. 'Appendix I, p42; "
        "cycle length from section 2.5, p15'). Helps the reviewer check your work.")
    classification: Optional[DocumentTaskClassification] = Field(
        default=None,
        description="AI classification completed before schedule extraction.")
    canonical_plan: Optional[CanonicalSchedulePlan] = Field(
        default=None,
        description="Version-2 schedule graph preserving temporal and protocol semantics.")
    evidence_facts: List[SourceEvidence] = Field(
        default_factory=list,
        description="Atomic source facts used by the canonical plan and compatibility rows.")

    @field_validator("anchor_study_day", mode="before")
    @classmethod
    def constrain_anchor_study_day(cls, value):
        """Enforce the 0/1 domain the schema can no longer express.

        An anchor outside that domain means the model misread the convention, so
        treat it as unknown — the downstream day math already handles null, and
        failing the parse would discard an otherwise usable schedule.
        """
        if isinstance(value, str):
            value = value.strip()
            value = int(value) if value in ("0", "1") else None
        if isinstance(value, bool) or value not in (0, 1):
            return None
        return value

    @model_validator(mode="after")
    def validate_day_numbering(self):
        if self.anchor_study_day == 0:
            if self.includes_day_zero is False:
                raise ValueError(
                    "A Day 0 anchor cannot use a convention that excludes Day 0")
            self.includes_day_zero = True
        return self


class ExtractedSchedule(ScheduleDraft):
    """Schedule draft plus server-authored verification metadata."""
    verification_status: Literal["not_run", "verified", "needs_review"] = "not_run"
    verification_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    verification_iterations: int = Field(default=0, ge=0)
    verification_issues: List[str] = Field(default_factory=list)
    verification_scores: dict[str, Optional[float]] = Field(default_factory=dict)
    canonical_validation: List[str] = Field(default_factory=list)
    requires_schedule_selection: bool = Field(
        default=False,
        description="True when this document prints more than one independent "
        "Schedule of Assessments (see classification.schedule_options) and no "
        "selection was made yet. Extraction stopped after classification; "
        "visits is empty and the caller must re-run with the chosen "
        "schedule_options[].id before a real schedule is produced.")


class CanonicalScheduleResponse(BaseModel):
    """Compact provider contract: the AI authors one schedule, not two copies."""

    schedule_kind: Optional[str] = None
    anchor_study_day: Optional[int] = None
    includes_day_zero: Optional[bool] = None
    canonical_plan: CanonicalSchedulePlan
    assumptions: List[str] = Field(default_factory=list)
    source_notes: Optional[str] = None

    @field_validator("anchor_study_day", mode="before")
    @classmethod
    def constrain_anchor_study_day(cls, value):
        if isinstance(value, str):
            value = value.strip()
            value = int(value) if value in ("0", "1") else None
        return value if not isinstance(value, bool) and value in (0, 1) else None


class ExtractedTrialDetails(BaseModel):
    """Creation-form fields read from a protocol before a trial exists."""
    ctri_number: str = ""
    title: str = ""
    phase: str = ""
    indications: List[str] = Field(default_factory=list)
    drug: str = ""
    duration: str = ""
    target_enrollment: Optional[int] = None
    total_visits: Optional[int] = None
    status: str = "active"


class ExtractionError(Exception):
    """Extraction attempted but failed (bad response, upstream error)."""


class ExtractionNotConfigured(ExtractionError):
    """No credentials/backend configured — surfaced to the caller as 503."""


class ExtractionUnavailable(ExtractionError):
    """Provider reachable but refusing work (billing, quota, rate limit).

    Separated from ExtractionError so the API can tell the sponsor *why* the
    button did nothing instead of a generic 'could not extract'.
    """


# ──────────────────────────── expansion (pure) ────────────────────────────
# Deterministic, no network, no API key — this is where all schedule arithmetic
# lives so it can be unit-tested against real protocols offline.
#
# _SIMPLE_DAY_LABEL/_SIMPLE_DAY_RANGE_LABEL, study_day_to_offset,
# simple_day_label_offset and simple_day_label_range_offsets now live in
# schedule_schema.py so the SAME printed-day-number cross-check protects both
# schedule shapes: this module's legacy flat-visits path below, and the
# canonical_plan path's build_row (schedule_schema.project_canonical_plan).


def normalize_extracted_timing(
    schedule: ExtractedSchedule,
) -> tuple[List[ExtractedVisit], List[str]]:
    """Return copied visits with evidence-backed simple Day labels normalized.

    Existing schedules with no numbering metadata remain untouched. When the
    extraction supplies both a simple label and a complete convention, the
    deterministic result replaces conflicting AI arithmetic and is recorded as
    a review warning.
    """
    visits = [visit.model_copy(deep=True) for visit in schedule.visits]
    warnings: List[str] = []
    for visit in visits:
        label = str(visit.source_day_label or "")
        exact_day_label = bool(
            _SIMPLE_DAY_LABEL.fullmatch(label)
            or _SIMPLE_DAY_RANGE_LABEL.fullmatch(label)
        )
        try:
            derived = simple_day_label_offset(
                visit.source_day_label,
                anchor_study_day=schedule.anchor_study_day,
                includes_day_zero=schedule.includes_day_zero,
            )
            derived_range = simple_day_label_range_offsets(
                visit.source_day_label,
                anchor_study_day=schedule.anchor_study_day,
                includes_day_zero=schedule.includes_day_zero,
            )
        except ValueError as exc:
            warnings.append(f"'{visit.name}' has invalid day numbering: {exc}.")
            visit.day_offset = None
            visit.day_end = None
            continue
        if derived_range is not None:
            start, end = derived_range
            if visit.day_offset not in (None, start) or visit.day_end not in (None, end):
                warnings.append(
                    f"'{visit.name}' timing was corrected deterministically from "
                    f"{visit.source_day_label} and flagged for review.")
            visit.day_offset, visit.day_end = start, end
            continue
        if derived is None:
            if exact_day_label and (
                schedule.anchor_study_day is None
                or schedule.includes_day_zero is None
            ):
                warnings.append(
                    f"'{visit.name}' uses {visit.source_day_label}, but the protocol's "
                    "Day 0/Day 1 convention is incomplete; retained the extracted "
                    "offset and flagged it for review.")
            continue
        if visit.day_offset is None:
            visit.day_offset = derived
        elif visit.day_offset != derived:
            warnings.append(
                f"'{visit.name}' has day_offset {visit.day_offset}, but "
                f"{visit.source_day_label} maps to {derived}; corrected it "
                "deterministically and flagged it for review.")
            visit.day_offset = derived
    return visits, warnings


def canonical_elapsed_time(
    day_offset: Optional[int],
    hour_offset: Optional[float],
    hour_offset_basis: Optional[str],
) -> timedelta:
    """Return elapsed time without double-counting absolute Hour 26 values.

    New extraction rows declare ``absolute``. A legacy row with no basis keeps
    its old day-plus-hour behavior so existing saved dates never move.
    """
    hours = float(hour_offset or 0)
    if not math.isfinite(hours):
        raise ValueError("hour_offset must be a finite number")
    if hour_offset_basis not in (None, "absolute", "within_day"):
        raise ValueError("hour_offset_basis must be absolute or within_day")
    absolute = hour_offset_basis == "absolute"
    if absolute:
        return timedelta(hours=hours)
    if day_offset is None:
        raise ValueError("Visit has no calculable day offset")
    return timedelta(days=int(day_offset), hours=hours)


def _visit_elapsed_seconds(visit: ExtractedVisit) -> float:
    """Comparable elapsed time for chronological ordering of dated visits."""
    try:
        return canonical_elapsed_time(
            visit.day_offset, visit.hour_offset, visit.hour_offset_basis,
        ).total_seconds()
    except (TypeError, ValueError):
        # Invalid arithmetic is retained for human review; deterministic day
        # order is still preferable to dropping the visit.
        if visit.day_offset is None:
            return math.inf
        return timedelta(days=visit.day_offset).total_seconds()

def _fill_template(template: str, cycle: int) -> str:
    """Substitute the cycle number into a member name template.

    Uses explicit replacement rather than str.format so a stray brace in a
    model-authored template can never raise.
    """
    out = template
    for token in ("{cycle}", "{c}", "{n}", "{CYCLE}"):
        out = out.replace(token, str(cycle))
    if str(cycle) not in out:
        # Template forgot the placeholder — disambiguate so cycles don't collide.
        out = f"{out} (Cycle {cycle})"
    return out


def _expand_blocks(schedule: ExtractedSchedule,
                   assumptions: List[str],
                   warnings: List[str]) -> List[ExtractedVisit]:
    """Turn each RepeatingBlock into concrete per-cycle visits."""
    out: List[ExtractedVisit] = []
    for block in schedule.repeating_blocks:
        if block.cycle_length_days <= 0:
            warnings.append(
                f"Ignored a repeating block starting at cycle {block.from_cycle}: "
                f"cycle length {block.cycle_length_days} is not a positive number of days.")
            continue
        if not block.members:
            warnings.append(
                f"Ignored a repeating block starting at cycle {block.from_cycle}: "
                "it listed no visits.")
            continue

        to_cycle = block.to_cycle
        if to_cycle is None:
            to_cycle = block.from_cycle + OPEN_ENDED_CYCLE_CAP - 1
            if schedule.total_cycles and schedule.total_cycles >= block.from_cycle:
                to_cycle = schedule.total_cycles
                assumptions.append(
                    f"Cycles {block.from_cycle}-{to_cycle} were expanded using the "
                    f"protocol's stated maximum of {schedule.total_cycles} cycles.")
            else:
                assumptions.append(
                    f"The protocol leaves the schedule open-ended from cycle "
                    f"{block.from_cycle}; expanded {OPEN_ENDED_CYCLE_CAP} cycles "
                    f"(to cycle {to_cycle}). Confirm the real number before saving.")
        if to_cycle < block.from_cycle:
            warnings.append(
                f"Ignored a repeating block: last cycle ({to_cycle}) is before the "
                f"first ({block.from_cycle}).")
            continue

        for cycle in range(block.from_cycle, to_cycle + 1):
            cycle_start = (block.first_cycle_start_day
                           + (cycle - block.from_cycle) * block.cycle_length_days)
            for member in block.members:
                acts = list(member.activities)
                for cond in member.conditional_activities:
                    if cycle in (cond.cycles or []):
                        acts.append(cond.name)
                out.append(ExtractedVisit(
                    name=_fill_template(member.name_template, cycle),
                    source_day_label=(
                        _fill_template(member.source_day_label_template, cycle)
                        if member.source_day_label_template else None
                    ),
                    visit_type=member.visit_type,
                    day_offset=cycle_start + member.day_within_cycle,
                    day_end=(
                        cycle_start + member.day_end_within_cycle
                        if member.day_end_within_cycle is not None else None
                    ),
                    hour_offset=member.hour_offset,
                    hour_offset_basis=member.hour_offset_basis,
                    hour_end=member.hour_end,
                    window_days=member.window_days,
                    window_before=member.window_before,
                    window_after=member.window_after,
                    arm=member.arm,
                    period=member.period,
                    activities=acts,
                    field_evidence=member.field_evidence,
                ))
    return out


def _resolve_relative(visits: List[ExtractedVisit], warnings: List[str]) -> None:
    """Resolve visits timed against another visit into absolute day offsets.

    Runs to a fixed point so a chain (A -> B -> C) resolves, and stops rather
    than looping when a cycle is present.
    """
    by_name: dict[str, List[ExtractedVisit]] = {}
    for visit in visits:
        if visit.name:
            by_name.setdefault(visit.name.strip().lower(), []).append(visit)
    pending = [v for v in visits
               if v.day_offset is None and v.relative_to and v.relative_offset_days is not None]
    for _ in range(len(pending) + 1):
        progressed = False
        for v in list(pending):
            matches = by_name.get((v.relative_to or "").strip().lower(), [])
            scoped = [candidate for candidate in matches
                      if candidate.arm == v.arm and candidate.period == v.period]
            target = scoped[0] if len(scoped) == 1 else (
                matches[0] if len(matches) == 1 else None)
            if target is not None and target.day_offset is not None:
                v.day_offset = target.day_offset + int(v.relative_offset_days or 0)
                pending.remove(v)
                progressed = True
        if not pending or not progressed:
            break
    for v in pending:
        matches = by_name.get((v.relative_to or "").strip().lower(), [])
        ambiguity = " is ambiguous" if len(matches) > 1 else " has no resolvable date"
        warnings.append(
            f"'{v.name}' is scheduled relative to '{v.relative_to}', which{ambiguity} "
            "for its arm/period — set its day manually.")


def expand_schedule(schedule: ExtractedSchedule) -> ExtractedSchedule:
    """Expand declared structure into the flat visit list the app consumes.

    Pure and deterministic: same input always yields the same visits. Returns a
    NEW ExtractedSchedule; the input is not mutated.
    """
    assumptions: List[str] = list(schedule.assumptions)
    warnings: List[str] = []
    if schedule.canonical_plan is not None:
        # The AI authors one canonical graph.  Flat rows are a deterministic
        # compatibility projection and any model-authored duplicate rows are ignored.
        projected, projection_warnings = project_canonical_plan(
            schedule.canonical_plan,
            open_ended_preview_count=OPEN_ENDED_CYCLE_CAP,
            anchor_study_day=schedule.anchor_study_day,
            includes_day_zero=schedule.includes_day_zero,
        )
        visits = [ExtractedVisit.model_validate(row) for row in projected]
        warnings.extend(projection_warnings)
        canonical_plan = schedule.canonical_plan
    else:
        # Historical/API callers may still provide flat visits.  Normalize those
        # first, then construct a canonical fallback from the normalized structure.
        visits, timing_warnings = normalize_extracted_timing(schedule)
        warnings.extend(timing_warnings)
        normalized = schedule.model_copy(update={"visits": visits})
        canonical_plan = canonical_from_flat(normalized)
        visits.extend(_expand_blocks(normalized, assumptions, warnings))
        _resolve_relative(visits, warnings)

    # Drop exact duplicates — a model that both enumerated cycle 2 AND described
    # it in a repeating block should not double-book the patient.
    seen: set = set()
    deduped: List[ExtractedVisit] = []
    for v in visits:
        key = (
            v.name.strip().lower(), v.day_offset, v.hour_offset,
            v.hour_offset_basis, v.day_end, v.hour_end,
            (v.arm or "").strip().lower(), (v.period or "").strip().lower(),
            (v.source_day_label or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(v)

    if len(deduped) > MAX_EXPANDED_VISITS:
        warnings.append(
            f"Schedule expanded to {len(deduped)} visits; kept the first "
            f"{MAX_EXPANDED_VISITS}. Check the cycle count before saving.")
        deduped = deduped[:MAX_EXPANDED_VISITS]

    # Chronological, with undated visits (ET / Unscheduled) last but preserved.
    deduped.sort(key=lambda v: (
        _visit_elapsed_seconds(v) == math.inf,
        _visit_elapsed_seconds(v),
    ))

    result = schedule.model_copy(update={
        "visits": deduped,
        "repeating_blocks": [],     # consumed
        "assumptions": assumptions + warnings,
        "verification_status": (
            "needs_review" if warnings else schedule.verification_status
        ),
        "verification_issues": list(schedule.verification_issues) + warnings,
    })
    canonical_issues = validate_canonical_plan(
        canonical_plan,
        ({item.evidence_id for item in result.evidence_facts}
         if result.evidence_facts else None),
    )
    return result.model_copy(update={
        "canonical_plan": canonical_plan,
        "canonical_validation": canonical_issues,
        "verification_status": (
            "needs_review" if warnings or canonical_issues else result.verification_status
        ),
        "verification_issues": list(result.verification_issues) + canonical_issues,
    })


# ──────────────────────────────── prompt ────────────────────────────────

_SYSTEM_PROMPT = """You are a clinical-trial protocol analyst. Read the attached \
document and extract its visit schedule — the Schedule of Assessments / Schedule of \
Activities / Schedule of Events / study flow chart.

You are producing a DRAFT that a sponsor will review before it is saved. Accuracy and \
honesty matter more than completeness: record what the document says, use `assumptions` \
for anything you inferred, and never invent a visit or an assessment.

## THE MOST IMPORTANT RULE: declare repetition, do not enumerate it

Real protocols collapse repeating cycles. A schedule table may print a column headed \
"Cycle 2 & Next Cycles", "each subsequent cycle", "Cycles 3-6", or prose may say "every \
3 weeks for a maximum of 6 cycles". When that happens, emit ONE `repeating_blocks` entry \
describing the cycle — do NOT write out each cycle as its own visit. The server expands \
blocks arithmetically, which is more reliable than you doing it in your head.

Enumerate a cycle in `visits` ONLY when the protocol prints that cycle in full and it \
differs from the repeating pattern (commonly Cycle 1, which often has extra baseline \
assessments).

## The numbers you need are usually NOT in the schedule table

This is the single most common reason extractions are wrong. A Schedule of Assessments \
appendix routinely omits the cycle length, the number of cycles, and the intra-cycle \
spacing — those live in the treatment-plan / dosing / study-design sections, often dozens \
of pages away. SEARCH THE WHOLE DOCUMENT for them before concluding a cycle is \
unspecified. Typical phrasings: "infused every 3 weekly for maximum 6 cycles" (-> \
cycle_length_days 21, total_cycles 6), "q4w", "28-day cycles", "subject will be scheduled \
for the next visit 7 days after this visit" (-> intra-cycle spacing 7). Cite where you \
found them in `source_notes`.

## Day labels and calendar offsets

- Preserve the exact protocol timing text in `source_day_label` (Day 0, Day 1, Week 4, \
Cycle 2 Day 1, etc.). It is display evidence, never the raw offset.
- Identify the protocol convention once: `anchor_study_day` is 0 or 1 for the study-day \
number on the baseline/randomization date, and `includes_day_zero` says whether Day 0 \
exists. Leave either null when the document is ambiguous.
- `day_offset` is the ABSOLUTE calendar displacement from baseline. Derive a simple Day D \
deterministically: anchor Day 0 -> D; anchor Day 1 with Day 0 -> D-1; anchor Day 1 with no \
Day 0 -> D-1 for D>=1 and D for negative D. Day 0 is invalid in the last convention.
- Do NOT blindly convert Week N to N*7. Week 1 may mean the baseline week, seven days \
after baseline, or a range; use explicit nearby dates/cadence or leave it for review.
- Inside a `repeating_blocks` member, use `day_within_cycle` (0-based from the cycle's \
first day) and set the block's `first_cycle_start_day` to the absolute day that cycle \
starts. Do not pre-compute per-cycle absolute days.
- If a visit is timed against ANOTHER visit ("within 3 days after intra-cycle visit 3", \
"28 days after the last dose"), leave `day_offset` null and set `relative_to` (the other \
visit's exact name) plus `relative_offset_days`. The server resolves it.
- If a visit genuinely has NO timing (many Early-Termination, Unscheduled, Withdrawal \
visits), leave `day_offset` null and STILL INCLUDE the visit. Never drop a real visit just \
because its day is unspecified.
- Multi-day visits (e.g. "Day 14-17", a period's Check-in + Day 1 + Check-out treated as \
one visit): set `day_offset` to the start and `day_end` to the end.

## Structural varieties (handle all)

- CYCLIC / oncology: use `repeating_blocks` (see above). If the cadence CHANGES partway \
("every 6th week for Cycles 1-6 and every 8th week thereafter"), emit TWO blocks with \
different `cycle_length_days` and ranges.
- CONDITIONAL assessments: when a recurring visit performs an assessment only in some \
cycles ("imaging after cycles 2, 4 and 6"), put it in that member's \
`conditional_activities` with the cycle numbers — not in `activities`.
- CROSSOVER / multi-period: enumerate visits across all periods and washouts with \
continuous absolute day offsets; set `period` on each ("Period 1", "Washout 1", \
"Period 2") and name them by the protocol's own labels.
- MULTI-ARM: if the arms share ONE schedule (same timing, only the drug differs), emit \
each visit ONCE and leave `arm` null. Only when the protocol prints a genuinely different \
schedule per arm, set `arm` and repeat the visits per arm.
- MULTI-PHASE (Core + Extension, Blinded + Open-label): enumerate every phase in order, \
using `period` to label them.
- INTRA-DAY / PK: if the study's schedule is hour-level ("Hour -4 to Hour 0", "Hour 26"), \
set `hour_offset` (and `hour_end` for a range) as ABSOLUTE elapsed hours from Hour 0, set \
`hour_offset_basis` to 'absolute', and set `schedule_kind` to 'intra_day'. Hour 26 is \
exactly 26 hours total; never add it on top of a one-day offset. If the document has BOTH a \
visit-level schedule and an \
intra-visit hourly sampling table, extract the VISIT-level schedule.
- SURGICAL / admission: screening, admission/procedure day, post-op days, discharge, \
follow-up.

## Visit type and windows

- `visit_type`: use the protocol's own 'Visit Type' row when present (including codes like \
SS = study site, V = virtual, T/C = telephone); otherwise infer the phase.
- Telephonic visits (phone contacts, or a column marked only with a telephone icon) are \
real visits — include them with visit_type 'Telephonic'.
- `window_days`: the +/- window. If asymmetric ("+3 days only"), set `window_days` to the \
larger side AND set `window_before` / `window_after`. If the protocol does not state a \
window, leave all three fields null. Never substitute an application default.
- Every populated visit field must cite evidence IDs in `field_evidence`. Cite the visit \
label under `name`, all day/week/month/hour/relative values under `timing`, windows under \
`window`, procedures under `activities`, and arm/period values under their own fields. \
If no evidence ID supports a value, leave that value null instead of guessing.

## Documents that have no schedule

Return an EMPTY `visits` list with `schedule_kind` 'none' when the document genuinely has \
no visit schedule — a GCP inspection checklist, a consent form, an investigator CV, a \
one-slide study overview, or a plain data-collection list. An empty result is the correct \
and expected answer there. Never manufacture a plausible-looking schedule to fill the gap.

## Scanned documents

Many of these files are scanned images with no text layer. Read them from the page images. \
If a table is too degraded to read reliably, extract what you can and say so in \
`assumptions` rather than guessing at values."""


_DETAILS_PROMPT = """Read the attached clinical-trial protocol and return only the
trial-level metadata requested by the schema. Preserve the official study title,
CTRI registration number, phase, disease/indications, investigational drug,
planned duration, planned sample size/target enrollment, and the number of
distinct protocol visits. Use empty strings/nulls when the document does not
state a value; never invent one. Normalize status to active, completed, or
terminated, defaulting to active when no status is stated."""


@runtime_checkable
class ProtocolExtractor(Protocol):
    async def extract(
        self, pdf_bytes: bytes, *, selected_schedule_option_id: str | None = None,
    ) -> ExtractedSchedule:
        ...

    async def extract_all(
        self, pdf_bytes: bytes,
    ) -> list[tuple[ScheduleOption | None, ExtractedSchedule]]:
        """Extract every independent Schedule of Assessments in the PDF.

        A single-schedule protocol (the overwhelming majority) returns
        exactly ``[(None, schedule)]``. A protocol printing more than one
        independent substudy schedule returns one entry per substudy,
        paired with the ``ScheduleOption`` it was built from."""
        ...


def _classify_api_error(exc: Exception) -> ExtractionError:
    """Map provider failures to the right error class.

    A billing/quota failure is not a parsing failure — the sponsor needs to be
    told the service is unavailable, not that their protocol was unreadable.
    """
    msg = str(exc)
    low = msg.lower()
    if any(s in low for s in ("credit balance", "billing", "quota", "insufficient funds",
                              "payment", "plans & billing")):
        return ExtractionUnavailable(
            "the AI provider account has no available credit")
    if "rate limit" in low or "429" in low:
        return ExtractionUnavailable("the AI provider is rate limiting requests")
    if any(s in low for s in ("overloaded", "529", "503", "502")):
        return ExtractionUnavailable("the AI provider is temporarily overloaded")
    return ExtractionError(f"model request failed: {msg}")


class ClaudeProtocolExtractor:
    """Default backend: Anthropic Claude, native PDF input + structured output."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = (
            model
            or os.getenv("CLAUDE_PROTOCOL_EXTRACTION_MODEL")
            or LEGACY_CLAUDE_MODEL
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured("ANTHROPIC_API_KEY is not set on the server")
        import anthropic  # imported lazily so the app boots without the dep/key
        return anthropic, anthropic.AsyncAnthropic(api_key=self._api_key)

    @staticmethod
    def _document_block(pdf_bytes: bytes) -> dict:
        """A cached document block.

        Sponsors re-run extraction while iterating on a schedule; caching the
        protocol makes every retry after the first ~10x cheaper on input.
        """
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
            },
            "cache_control": {"type": "ephemeral"},
        }

    @staticmethod
    def _json_text(response) -> str:
        """Extract the JSON text from a normal Messages API response."""
        text = ''.join(
            getattr(block, 'text', '') for block in (getattr(response, 'content', None) or [])
            if getattr(block, 'type', '') == 'text'
        ).strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text
            if text.rstrip().endswith('```'):
                text = text.rstrip()[:-3].rstrip()
        # Models occasionally add a one-line preface despite the instruction.
        # Keep the outer JSON object rather than rejecting usable extraction.
        first, last = text.find('{'), text.rfind('}')
        if first >= 0 and last > first:
            text = text[first:last + 1]
        return text

    async def _extract_without_grammar(self, client, pdf_bytes: bytes, repair: bool = False) -> ExtractedSchedule:
        """Fallback for provider grammar-compilation timeouts.

        The structured parser is preferred, but complex Pydantic schemas can
        exceed Anthropic's grammar compiler limit for a full PDF. Asking for
        JSON and validating it locally preserves the same strict application
        schema without discarding a usable protocol extraction.
        """
        response = await client.messages.create(
            model=self._model,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={'type': 'adaptive'},
            system=_SYSTEM_PROMPT + (
                '\n\nReturn ONLY a valid JSON object with these top-level keys: '
                'schedule_kind, anchor_study_day, includes_day_zero, visits, '
                'repeating_blocks, assumptions, source_notes. '
                'Each visit must include name, visit_type, day_offset, day_end, '
                'source_day_label, hour_offset, hour_offset_basis, hour_end, '
                'window_days, window_before, window_after, '
                'relative_to, relative_offset_days, arm, period, activities, '
                'field_evidence. '
                'Use null only for unknown nullable fields and [] for unknown lists. '
                'window_days must be a non-negative integer when stated and null when '
                'the protocol does not state a visit window.'
            ),
            messages=[{
                'role': 'user',
                'content': [
                    self._document_block(pdf_bytes),
                    {'type': 'text', 'text': (
                        'Extract this protocol schedule as the requested JSON. '
                        'Return JSON only—no explanation, markdown, or code fences.'
                        if not repair else
                        'Return a corrected, strictly valid JSON schedule now. JSON only; no markdown or explanation.'
                    )},
                ],
            }],
        )
        raw = self._json_text(response)
        try:
            payload = json.loads(raw)
            # Normalise the few harmless shape variations a free-form JSON
            # response can make before Pydantic applies the strict schema.
            if isinstance(payload, dict):
                if isinstance(payload.get('source_notes'), list):
                    payload['source_notes'] = ' '.join(
                        str(note).strip() for note in payload['source_notes'] if str(note).strip())
                if isinstance(payload.get('assumptions'), str):
                    payload['assumptions'] = [payload['assumptions']]
            return ExtractedSchedule.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            if not repair:
                log.warning('Anthropic schedule JSON failed validation; requesting one corrected response: %s', exc)
                return await self._extract_without_grammar(client, pdf_bytes, repair=True)
            raise ExtractionError('the AI response was not valid schedule JSON') from exc

    async def extract(
        self, pdf_bytes: bytes, *, selected_schedule_option_id: str | None = None,
    ) -> ExtractedSchedule:
        """Read a protocol and return its EXPANDED visit schedule.

        This legacy single-shot path has no classification stage, so it never
        detects multiple independent Schedules of Assessments and
        ``selected_schedule_option_id`` is accepted only to satisfy
        ``ProtocolExtractor`` and is otherwise unused.
        """
        anthropic, client = self._client()
        # Structured-output grammar compilation can time out for the nested
        # clinical schedule schema. Use Anthropic's normal Messages API and
        # enforce the same schema locally with Pydantic.
        try:
            parsed = await self._extract_without_grammar(client, pdf_bytes)
        except anthropic.APIError as e:
            raise _classify_api_error(e) from e
        expanded = expand_schedule(parsed)
        log.info(
            'protocol extraction (JSON): kind=%s raw_visits=%d -> expanded=%d assumptions=%d',
            parsed.schedule_kind, len(parsed.visits), len(expanded.visits),
            len(expanded.assumptions),
        )
        return expanded

        try:
            resp = await client.messages.parse(
                model=self._model,
                max_tokens=MAX_OUTPUT_TOKENS,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        self._document_block(pdf_bytes),
                        {"type": "text",
                         "text": "Extract this protocol's visit schedule. Search the whole "
                                 "document for cycle length and cycle count before "
                                 "concluding a repeating block is unspecified."},
                    ],
                }],
                output_format=ExtractedSchedule,
            )
        except anthropic.APIError as e:
            if 'grammar compilation timed out' in str(e).lower():
                log.warning('Anthropic structured-output grammar timed out; using JSON fallback')
                return expand_schedule(await self._extract_without_grammar(client, pdf_bytes))
            raise _classify_api_error(e) from e

        if resp.stop_reason == "refusal":
            raise ExtractionError("the model declined to process this document")

        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            if resp.stop_reason == "max_tokens":
                raise ExtractionError(
                    "the schedule was too large to return in one response — "
                    "try uploading only the Schedule of Assessments pages")
            raise ExtractionError("model did not return a parseable schedule")

        expanded = expand_schedule(parsed)
        log.info(
            "protocol extraction: kind=%s raw_visits=%d blocks=%d -> expanded=%d assumptions=%d",
            parsed.schedule_kind, len(parsed.visits), len(parsed.repeating_blocks),
            len(expanded.visits), len(expanded.assumptions),
        )
        return expanded

    async def extract_all(
        self, pdf_bytes: bytes,
    ) -> list[tuple[ScheduleOption | None, ExtractedSchedule]]:
        """This single-shot backend never detects multiple independent
        Schedules of Assessments (no classification stage), so there is
        only ever one schedule to return."""
        return [(None, await self.extract(pdf_bytes))]

    async def extract_details(self, pdf_bytes: bytes) -> ExtractedTrialDetails:
        """Extract the trial-level metadata needed by the pre-creation form."""
        anthropic, client = self._client()
        try:
            resp = await client.messages.parse(
                model=self._model,
                max_tokens=4000,
                system=_DETAILS_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        self._document_block(pdf_bytes),
                        {"type": "text", "text": "Extract the protocol's trial details."},
                    ],
                }],
                output_format=ExtractedTrialDetails,
            )
        except anthropic.APIError as e:
            raise _classify_api_error(e) from e
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise ExtractionError("model did not return parseable trial details")
        return parsed


def _structured_failure_detail(response, exc: Exception) -> str:
    """Summarise WHY a structured response failed, without logging its content.

    Protocol page text is confidential, so this reports field locations, error
    types, the provider finish reason and response size — never input values.
    Truncation (finish_reason MAX_TOKENS) and schema violations look identical
    in the logs otherwise, and they need opposite fixes.
    """
    parts: list[str] = []
    finish_reason = None
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
    except Exception:  # diagnostics must never mask the original failure
        finish_reason = None
    if finish_reason is not None:
        parts.append(f"finish_reason={getattr(finish_reason, 'name', finish_reason)}")
    raw = getattr(response, "text", None) or ""
    parts.append(f"response_chars={len(raw)}")
    errors_fn = getattr(exc, "errors", None)
    if callable(errors_fn):
        try:
            summary = [
                f"{'.'.join(str(part) for part in item.get('loc', ()))}:{item.get('type')}"
                for item in errors_fn()[:6]
            ]
            parts.append("fields=" + ", ".join(summary))
        except Exception:
            parts.append(f"error={type(exc).__name__}")
    else:
        parts.append(f"error={str(exc)[:200]}")
    return "; ".join(parts)


class _PdfCacheHandle:
    """A live Gemini context cache holding one protocol PDF's bytes.

    Tracked client-side so a stage call can tell *before* spending a request
    that the cache is past (or close to) its TTL, instead of only finding out
    from a failed response.
    """
    __slots__ = ("name", "created_at", "ttl_seconds")

    def __init__(self, name: str, ttl_seconds: int):
        self.name = name
        self.created_at = time.monotonic()
        self.ttl_seconds = ttl_seconds

    def usable(self, safety_margin: float = 30.0) -> bool:
        return (time.monotonic() - self.created_at) < (self.ttl_seconds - safety_margin)


class GeminiProtocolExtractor:
    """Google Gemini backend with native PDF input and structured output."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._model = (
            model
            or os.getenv("GEMINI_PROTOCOL_EXTRACTION_MODEL")
            or os.getenv("PROTOCOL_EXTRACTION_MODEL")
            or DEFAULT_MODEL
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured("GEMINI_API_KEY is not set on the server")
        from google import genai  # lazy import keeps non-AI routes independent
        from google.genai import errors, types
        return errors, types, genai.Client(api_key=self._api_key)

    async def _generate(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema,
        *,
        system_instruction: str | None,
        max_tokens: int,
        model_override: str | None = None,
        cached_content: str | None = None,
    ):
        if system_instruction is None:
            system_instruction = _SYSTEM_PROMPT
        last_parse_error: Exception | None = None
        retry_detail = ""
        # Verification fields are populated by our audit/finalizer, not Gemini.
        # In particular, verification_scores is a dictionary and produces JSON
        # Schema `additionalProperties`, which the Gemini Developer API rejects.
        provider_schema = CanonicalScheduleResponse if schema is ExtractedSchedule else schema

        # A provider can return HTTP 200 while its structured response is empty,
        # truncated, or fails schema validation. Retry only that one graph stage;
        # restarting the whole protocol workflow would waste completed AI work.
        for attempt in range(2):
            errors, types, client = self._client()
            async_client = client.aio
            retry_instruction = "" if attempt == 0 else (
                "\n\nSTRUCTURED OUTPUT RETRY: The previous response could not be "
                "validated. Return one complete JSON object matching the requested "
                "schema. Do not use markdown, commentary, or omit required fields.\n"
                "Every evidence entry needs a non-empty evidence_id, claim, "
                "source_location and source_quote plus a confidence between 0 and 1. "
                "If you cannot quote a source for a fact, omit that fact instead of "
                "sending a blank field. Prefer fewer, complete entries over a long "
                "list that gets cut off before the closing brace.\n"
                + (f"The previous attempt failed on: {retry_detail}"
                   if retry_detail else "")
            )
            try:
                contents = []
                # A cache already holds the PDF server-side — attaching it again
                # would both re-pay the input tokens the cache exists to avoid
                # and (per the Gemini API) is unnecessary alongside cached_content.
                if pdf_bytes and not cached_content:
                    contents.append(types.Part.from_bytes(
                        data=pdf_bytes,
                        mime_type="application/pdf",
                    ))
                prompt_text = prompt + retry_instruction
                if cached_content:
                    # The Gemini API rejects system_instruction on a
                    # GenerateContent request that also sets cached_content
                    # ("CachedContent can not be used with ... system_instruction").
                    # Every stage uses a different system_instruction, so it
                    # can't be baked into the shared per-extraction cache
                    # either — fold it into this call's own content instead so
                    # the cache still only ever holds the (large) PDF.
                    prompt_text = system_instruction + "\n\n" + prompt_text
                contents.append(prompt_text)
                config_kwargs = dict(
                    max_output_tokens=max_tokens,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=provider_schema,
                    # Without this, a thinking-capable model defaults to an
                    # automatic thinking budget that shares max_output_tokens
                    # with the actual answer — invisible reasoning tokens can
                    # consume most of the budget and truncate the JSON before
                    # it's written (finish_reason=MAX_TOKENS with no field-level
                    # error). This task is schema-conformant extraction, not
                    # open-ended reasoning, so thinking buys nothing here.
                    # thinking_budget=0 (fully disabled) is rejected outright
                    # by newer models (400 INVALID_ARGUMENT) — 1 is the
                    # smallest budget every generation observed so far
                    # accepts, so it keeps this protection everywhere.
                    thinking_config=types.ThinkingConfig(thinking_budget=1),
                )
                if cached_content:
                    config_kwargs["cached_content"] = cached_content
                else:
                    config_kwargs["system_instruction"] = system_instruction
                response = await async_client.models.generate_content(
                    model=model_override or self._model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
            except errors.APIError as exc:
                raise _classify_api_error(exc) from exc
            except httpx.TransportError as exc:
                # A dropped/reset connection while talking to the provider, not an
                # API-level error response, so errors.APIError never sees it. Wrap
                # it so run_stage's existing retry-with-backoff covers it instead
                # of a transient network blip crashing the whole extraction.
                log.warning(
                    "network error calling Gemini (%s): %s",
                    type(exc).__name__, exc)
                raise ExtractionError(
                    f"network error calling Gemini: {exc}") from exc
            except ValueError as exc:
                log.error(
                    "Gemini rejected the %s request schema: %s",
                    provider_schema.__name__, exc)
                raise ExtractionError(
                    f"Gemini rejected the {provider_schema.__name__} request schema") from exc
            finally:
                await async_client.aclose()
                client.close()

            try:
                parsed = getattr(response, "parsed", None)
                if isinstance(parsed, schema):
                    return parsed
                if parsed is not None:
                    if isinstance(parsed, BaseModel):
                        parsed = parsed.model_dump()
                    return schema.model_validate(parsed)
                raw = (getattr(response, "text", None) or "").strip()
                if not raw:
                    raise ValueError("empty structured response")
                return schema.model_validate_json(raw)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_parse_error = exc
                retry_detail = _structured_failure_detail(response, exc)
                log.warning(
                    "Gemini returned invalid %s structured output (attempt %d/2): "
                    "%s [%s]",
                    schema.__name__, attempt + 1, type(exc).__name__, retry_detail)

        raise ExtractionError(
            f"Gemini could not return valid {schema.__name__} structured JSON "
            "after one retry") from last_parse_error

    # A protocol extraction is classify + discover + one evidence_sweep call
    # per document chunk + synthesize + audit, plus up to 2 more repair/audit
    # rounds — for a 100-page protocol (~5 chunks), roughly 9-13 calls. Every
    # one of them used to re-attach the full PDF, so a large protocol paid
    # for its own document tokens 9-13 times over. A Gemini context cache
    # uploads the PDF once per extraction and every stage call references it
    # instead — caching is purely a cost optimization, so any failure to
    # create/use it (too small, quota, expiry mid-pipeline) must fall back to
    # the old per-call attachment, never fail the extraction itself.
    _CACHE_MIN_BYTES = 100_000

    async def _create_pdf_cache(self, pdf_bytes: bytes) -> "_PdfCacheHandle | None":
        if not pdf_bytes or len(pdf_bytes) < self._CACHE_MIN_BYTES:
            return None
        if os.getenv("PROTOCOL_EXTRACTION_PDF_CACHE", "1").strip().lower() in (
                "0", "false", "no"):
            return None
        ttl_seconds = int(os.getenv("PROTOCOL_EXTRACTION_CACHE_TTL_SECONDS", "1800"))
        try:
            errors, types, client = self._client()
            async_client = client.aio
            try:
                cache = await async_client.caches.create(
                    model=self._model,
                    config=types.CreateCachedContentConfig(
                        contents=[types.Part.from_bytes(
                            data=pdf_bytes, mime_type="application/pdf")],
                        ttl=f"{ttl_seconds}s",
                    ),
                )
            finally:
                await async_client.aclose()
                client.close()
        except Exception as exc:  # noqa: BLE001 — never let caching block extraction
            log.info(
                "Gemini PDF context cache unavailable; every stage call will "
                "attach the PDF directly instead: %s", exc)
            return None
        if not cache.name:
            log.info("Gemini PDF context cache created with no name; skipping cache reuse")
            return None
        log.info(
            "created Gemini PDF context cache %s (ttl=%ds) for this extraction",
            cache.name, ttl_seconds)
        return _PdfCacheHandle(cache.name, ttl_seconds)

    async def _delete_pdf_cache(self, name: str) -> None:
        try:
            errors, types, client = self._client()
            async_client = client.aio
            try:
                await async_client.caches.delete(name=name)
            finally:
                await async_client.aclose()
                client.close()
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup only
            log.info("could not delete Gemini PDF context cache %s: %s", name, exc)

    def _cached_generate(self, pdf_bytes: bytes, handle_box: list):
        """Wrap ``self._generate`` so every stage call reuses one PDF cache.

        ``handle_box`` is a 1-element list (a mutable cell) rather than a
        plain variable so a cache-call failure can turn caching off for every
        later stage in *this* extraction without needing nonlocal plumbing.
        Every stage call site passes (pdf_bytes, prompt, schema) positionally
        plus keyword-only options, same as ``self._generate`` itself.
        """
        async def _generate_with_cache(*args, **kwargs):
            handle = handle_box[0]
            if handle is not None and handle.usable():
                try:
                    _pdf_bytes, prompt, schema = args[:3]
                    return await self._generate(
                        b"", prompt, schema,
                        cached_content=handle.name, **kwargs)
                except ExtractionError as exc:
                    # Expired/evicted mid-pipeline, or some other provider
                    # hiccup specific to the cached call. Fall back for this
                    # stage and stop trying the cache for the rest of the
                    # extraction rather than fail it.
                    log.info(
                        "Gemini PDF cache call failed (%s); falling back to "
                        "full PDF attachment for the rest of this "
                        "extraction: %s", handle.name, exc)
                    handle_box[0] = None
            return await self._generate(*args, **kwargs)
        return _generate_with_cache

    async def extract(
        self, pdf_bytes: bytes, *, selected_schedule_option_id: str | None = None,
    ) -> ExtractedSchedule:
        from protocol_agent import run_schedule_extraction_agent

        max_refinements = int(os.getenv(
            "PROTOCOL_EXTRACTION_MAX_REFINEMENTS", "2"))

        handle_box = [await self._create_pdf_cache(pdf_bytes)]
        generate = self._cached_generate(pdf_bytes, handle_box)

        try:
            expanded = await run_schedule_extraction_agent(
                pdf_bytes,
                generate,
                max_refinements=max_refinements,
                selected_schedule_option_id=selected_schedule_option_id,
            )
        finally:
            if handle_box[0] is not None:
                await self._delete_pdf_cache(handle_box[0].name)
        log.info(
            "Gemini agent extraction: kind=%s expanded=%d assumptions=%d "
            "verification=%s confidence=%s refinements=%d",
            expanded.schedule_kind,
            len(expanded.visits),
            len(expanded.assumptions),
            expanded.verification_status,
            expanded.verification_confidence,
            expanded.verification_iterations,
        )
        return expanded

    async def extract_all(
        self, pdf_bytes: bytes,
    ) -> list[tuple[ScheduleOption | None, ExtractedSchedule]]:
        """Extract every independent Schedule of Assessments the PDF prints.

        Mirrors extract()'s setup exactly (one page index, one Gemini PDF
        context cache) but shares both across every substudy's pipeline run
        instead of building them once per call — the direct extension of
        "one cache per extraction" to "one cache per document, reused by
        every substudy extracted from it." A single-schedule protocol costs
        exactly what extract() costs today; a multi-substudy protocol runs
        one full pipeline per substudy, bounded to a few concurrent
        pipelines at once.
        """
        from protocol_agent import (
            _build_page_index,
            run_schedule_extraction_agent_for_all_options,
        )

        max_refinements = int(os.getenv(
            "PROTOCOL_EXTRACTION_MAX_REFINEMENTS", "2"))
        concurrency = int(os.getenv(
            "PROTOCOL_EXTRACTION_VARIANT_CONCURRENCY", "3"))

        page_index = await _build_page_index(pdf_bytes)
        handle_box = [await self._create_pdf_cache(pdf_bytes)]
        generate = self._cached_generate(pdf_bytes, handle_box)

        try:
            results = await run_schedule_extraction_agent_for_all_options(
                pdf_bytes,
                generate,
                max_refinements=max_refinements,
                page_index=page_index,
                concurrency=max(1, concurrency),
            )
        finally:
            if handle_box[0] is not None:
                await self._delete_pdf_cache(handle_box[0].name)
        log.info(
            "Gemini agent extraction (all options): variants=%d",
            len(results))
        return results

    async def extract_bundle(
        self, pdf_bytes: bytes, *, selected_schedule_option_id: str | None = None,
    ) -> tuple[ExtractedTrialDetails, ExtractedSchedule]:
        """Return metadata + schedule without a separate metadata model call."""
        from protocol_agent import run_protocol_extraction_agent

        max_refinements = int(os.getenv(
            "PROTOCOL_EXTRACTION_MAX_REFINEMENTS", "2"))

        handle_box = [await self._create_pdf_cache(pdf_bytes)]
        generate = self._cached_generate(pdf_bytes, handle_box)

        try:
            return await run_protocol_extraction_agent(
                pdf_bytes,
                generate,
                max_refinements=max_refinements,
                selected_schedule_option_id=selected_schedule_option_id,
            )
        finally:
            if handle_box[0] is not None:
                await self._delete_pdf_cache(handle_box[0].name)

    async def extract_bundle_all(
        self, pdf_bytes: bytes,
    ) -> tuple[ExtractedTrialDetails, list[tuple[ScheduleOption | None, ExtractedSchedule]]]:
        """Metadata + every independent Schedule of Assessments the PDF
        prints, sharing one page index and one Gemini PDF context cache
        across every substudy's pipeline run — same setup as extract_all,
        plus the trial-level metadata extract_bundle also returns."""
        from protocol_agent import (
            _build_page_index,
            run_protocol_extraction_agent_for_all_options,
        )

        max_refinements = int(os.getenv(
            "PROTOCOL_EXTRACTION_MAX_REFINEMENTS", "2"))
        concurrency = int(os.getenv(
            "PROTOCOL_EXTRACTION_VARIANT_CONCURRENCY", "3"))

        page_index = await _build_page_index(pdf_bytes)
        handle_box = [await self._create_pdf_cache(pdf_bytes)]
        generate = self._cached_generate(pdf_bytes, handle_box)

        try:
            details, results = await run_protocol_extraction_agent_for_all_options(
                pdf_bytes,
                generate,
                max_refinements=max_refinements,
                page_index=page_index,
                concurrency=max(1, concurrency),
            )
        finally:
            if handle_box[0] is not None:
                await self._delete_pdf_cache(handle_box[0].name)
        log.info(
            "Gemini agent bundle extraction (all options): variants=%d",
            len(results))
        return details, results

    async def extract_details(self, pdf_bytes: bytes) -> ExtractedTrialDetails:
        return await self._generate(
            pdf_bytes,
            "Extract the protocol's trial-level metadata.",
            ExtractedTrialDetails,
            system_instruction=_DETAILS_PROMPT,
            max_tokens=4000,
        )


class OpenRouterProtocolExtractor:
    """OpenRouter backend using its OpenAI-compatible chat/PDF endpoint."""

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 url: str | None = None):
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self._model = (
            model
            or os.getenv("OPENROUTER_PROTOCOL_EXTRACTION_MODEL")
            or DEFAULT_OPENROUTER_MODEL
        )
        self._url = (
            url or os.getenv("OPENROUTER_API_URL") or DEFAULT_OPENROUTER_URL
        ).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _response_text(payload: dict) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExtractionError(
                "OpenRouter did not return a model response") from exc
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        text = str(content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip()
        first, last = text.find("{"), text.rfind("}")
        return text[first:last + 1] if first >= 0 and last > first else text

    def _request(self, pdf_bytes: bytes, prompt: str, schema, max_tokens: int) -> dict:
        if not self._api_key:
            raise ExtractionNotConfigured(
                "OPENROUTER_API_KEY is not set on the server")

        import requests

        data_url = "data:application/pdf;base64," + base64.b64encode(
            pdf_bytes).decode("ascii")
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": _DETAILS_PROMPT if schema is ExtractedTrialDetails else _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "file",
                            "file": {
                                "filename": "protocol.pdf",
                                "file_data": data_url,
                            },
                        },
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        pdf_engine = os.getenv("OPENROUTER_PDF_ENGINE", "").strip()
        if pdf_engine:
            body["plugins"] = [{
                "id": "file-parser",
                "pdf": {"engine": pdf_engine},
            }]

        try:
            response = requests.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "My Trial Board protocol extraction",
                },
                json=body,
                timeout=(30, 20 * 60),
            )
        except requests.RequestException as exc:
            raise _classify_api_error(exc) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ExtractionError(
                f"OpenRouter returned HTTP {response.status_code} without JSON") from exc
        if not response.ok:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message") if isinstance(error, dict) else None
            if response.status_code in (401, 403):
                raise ExtractionNotConfigured(
                    "OpenRouter rejected OPENROUTER_API_KEY")
            raise _classify_api_error(RuntimeError(
                message or f"OpenRouter returned HTTP {response.status_code}"))
        return payload

    async def _generate(self, pdf_bytes: bytes, prompt: str, schema,
                        max_tokens: int):
        payload = await asyncio.to_thread(
            self._request, pdf_bytes, prompt, schema, max_tokens)
        raw = self._response_text(payload)
        try:
            return schema.model_validate_json(raw)
        except ValueError as exc:
            raise ExtractionError(
                "the OpenRouter response was not valid structured JSON") from exc

    async def extract(
        self, pdf_bytes: bytes, *, selected_schedule_option_id: str | None = None,
    ) -> ExtractedSchedule:
        # This legacy single-shot path has no classification stage, so it
        # never detects multiple independent Schedules of Assessments;
        # selected_schedule_option_id is accepted only to satisfy
        # ProtocolExtractor and is otherwise unused.
        parsed = await self._generate(
            pdf_bytes,
            "Extract this protocol's visit schedule. Search the whole document "
            "for cycle length and cycle count before concluding a repeating "
            "block is unspecified.",
            ExtractedSchedule,
            MAX_OUTPUT_TOKENS,
        )
        expanded = expand_schedule(parsed)
        log.info(
            "OpenRouter protocol extraction: model=%s kind=%s raw_visits=%d "
            "blocks=%d expanded=%d assumptions=%d",
            self._model, parsed.schedule_kind, len(parsed.visits),
            len(parsed.repeating_blocks), len(expanded.visits),
            len(expanded.assumptions),
        )
        return expanded

    async def extract_all(
        self, pdf_bytes: bytes,
    ) -> list[tuple[ScheduleOption | None, ExtractedSchedule]]:
        """This single-shot backend never detects multiple independent
        Schedules of Assessments (no classification stage), so there is
        only ever one schedule to return."""
        return [(None, await self.extract(pdf_bytes))]

    async def extract_details(self, pdf_bytes: bytes) -> ExtractedTrialDetails:
        return await self._generate(
            pdf_bytes,
            "Extract the protocol's trial-level metadata.",
            ExtractedTrialDetails,
            4000,
        )


class OllamaProtocolExtractor:
    """Local Qwen-VL backend served by Ollama; intended for offline development.

    PDF pages are rendered to JPEGs on this machine, then passed to the local
    vision model. Nothing is uploaded to a third-party AI provider.
    """

    def __init__(self, model: str | None = None, host: str | None = None):
        self._model = model or os.getenv("OLLAMA_PROTOCOL_EXTRACTION_MODEL") or DEFAULT_OLLAMA_MODEL
        self._host = (host or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")
        self._batch_size = max(1, int(os.getenv(
            "OLLAMA_PDF_BATCH_PAGES", str(OLLAMA_PAGES_PER_BATCH))))
        cache = os.getenv("OLLAMA_EXTRACTION_CACHE_DIR")
        self._cache_dir = Path(cache) if cache else Path(__file__).parent / ".ollama-extraction-cache"

    @property
    def configured(self) -> bool:
        # A local service needs no secret. Connection/model errors are reported
        # when a request is made, with a useful setup message below.
        return True

    @staticmethod
    def _pdf_page_count(pdf_bytes: bytes) -> int:
        try:
            import pypdfium2 as pdfium
            return len(pdfium.PdfDocument(pdf_bytes))
        except Exception as exc:
            raise ExtractionError(f"could not read the PDF locally: {exc}") from exc

    @staticmethod
    def _render_pdf_pages(pdf_bytes: bytes, start: int, end: int) -> list[bytes]:
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(pdf_bytes)
            images: list[bytes] = []
            for page_number in range(start, min(end, len(document))):
                page = document[page_number]
                bitmap = page.render(scale=1.35)
                image = bitmap.to_pil().convert("RGB")
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=80, optimize=True)
                images.append(output.getvalue())
        except Exception as exc:
            raise ExtractionError(f"could not read the PDF locally: {exc}") from exc
        if not images:
            raise ExtractionError("the PDF contains no pages")
        return images

    def _cache_paths(self, pdf_bytes: bytes, kind: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        stem = self._cache_dir / f"{digest}-{kind}"
        return Path(f"{stem}.jsonl"), Path(f"{stem}.result.json")

    @staticmethod
    def _load_evidence(path: Path) -> dict[int, dict]:
        rows: dict[int, dict] = {}
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                rows[int(row["start_page"])] = row
            except (ValueError, KeyError, TypeError):
                # A process can stop during its final append. Earlier complete
                # checkpoints remain usable; only the damaged line is ignored.
                continue
        return rows

    @staticmethod
    def _append_checkpoint(path: Path, row: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def _chat(self, messages: list[dict], *, format=None):
        import ollama
        client = ollama.AsyncClient(host=self._host, timeout=20 * 60)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await client.chat(
                    model=self._model,
                    messages=messages,
                    format=format,
                    stream=False,
                    think=False,
                    options={"temperature": 0, "num_ctx": OLLAMA_CONTEXT_TOKENS},
                )
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        assert last_error is not None
        low = str(last_error).lower()
        if "not found" in low and self._model.lower() in low:
            raise ExtractionNotConfigured(
                f"local model '{self._model}' is not installed; run: "
                f"ollama pull {self._model}") from last_error
        if any(term in low for term in ("connect", "refused", "10061")):
            raise ExtractionNotConfigured(
                f"Ollama is not running at {self._host}") from last_error
        raise ExtractionError(f"local model request failed: {last_error}") from last_error

    @staticmethod
    def _message_text(response) -> str:
        message = getattr(response, "message", None)
        return (getattr(message, "content", None) or
                (message.get("content") if isinstance(message, dict) else "") or "").strip()

    async def _collect_evidence(self, pdf_bytes: bytes, *, kind: str,
                                checkpoint_path: Path) -> str:
        page_count = await asyncio.to_thread(self._pdf_page_count, pdf_bytes)
        completed = self._load_evidence(checkpoint_path)
        instruction = (
            "Extract every fact relevant to the visit schedule: visit names, dates, "
            "days, weeks, windows, cycles, cycle lengths/counts, arms, periods, "
            "activities, footnotes, and cross-references."
            if kind == "schedule" else
            "Extract every trial-level fact: official title, registration number, "
            "phase, indication, drug, duration, enrollment, status, and stated "
            "number of visits."
        )
        rows: list[dict] = []
        for start in range(0, page_count, self._batch_size):
            if start in completed:
                rows.append(completed[start])
                continue
            end = min(start + self._batch_size, page_count)
            images = await asyncio.to_thread(
                self._render_pdf_pages, pdf_bytes, start, end)
            response = await self._chat([{
                "role": "user",
                "content": (
                    f"These are protocol PDF pages {start + 1}-{end}. {instruction} "
                    "Be faithful to tables and footnotes. Include page numbers. "
                    "If there is no relevant evidence, say NONE. Keep the response "
                    "under 900 words."),
                "images": images,
            }])
            row = {
                "start_page": start,
                "end_page": end,
                "evidence": self._message_text(response),
            }
            self._append_checkpoint(checkpoint_path, row)
            completed[start] = row
            rows.append(row)
            log.info("local Qwen-VL %s extraction: pages %d-%d/%d checkpointed",
                     kind, start + 1, end, page_count)

        evidence = "\n\n".join(
            f"[Pages {row['start_page'] + 1}-{row['end_page']}]\n{row['evidence']}"
            for row in sorted(rows, key=lambda item: item["start_page"])
            if row.get("evidence", "").strip().upper() != "NONE")
        return await self._reduce_evidence(evidence, kind=kind)

    async def _reduce_evidence(self, evidence: str, *, kind: str) -> str:
        while len(evidence) > OLLAMA_FINAL_EVIDENCE_CHARS:
            chunks = [evidence[i:i + OLLAMA_FINAL_EVIDENCE_CHARS]
                      for i in range(0, len(evidence), OLLAMA_FINAL_EVIDENCE_CHARS)]
            reduced: list[str] = []
            for chunk in chunks:
                response = await self._chat([{
                    "role": "user",
                    "content": (
                        f"Consolidate this {kind} evidence without dropping any dates, "
                        "visit/cycle rules, activities, trial facts, conflicts, or page "
                        f"citations. Remove only repetition.\n\n{chunk}"),
                }])
                reduced.append(self._message_text(response))
            new_evidence = "\n\n".join(reduced)
            if len(new_evidence) >= len(evidence):
                return new_evidence[:OLLAMA_FINAL_EVIDENCE_CHARS]
            evidence = new_evidence
        return evidence

    async def _generate(self, pdf_bytes: bytes, prompt: str, schema, *,
                        system_instruction: str):
        kind = "details" if schema is ExtractedTrialDetails else "schedule"
        checkpoint_path, result_path = self._cache_paths(pdf_bytes, kind)
        if result_path.exists():
            try:
                return schema.model_validate_json(result_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        evidence = await self._collect_evidence(
            pdf_bytes, kind=kind, checkpoint_path=checkpoint_path)
        try:
            response = await self._chat(
                [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": (
                        f"{prompt}\nReturn only valid JSON matching the supplied schema. "
                        f"Keep conflicts in assumptions/source notes.\n\n"
                        f"PAGE-CITED EVIDENCE:\n{evidence}")},
                ],
                format=schema.model_json_schema(),
            )
        except (ExtractionError, ExtractionNotConfigured):
            raise
        raw = self._message_text(response)
        try:
            parsed = schema.model_validate_json(raw)
            temp_path = Path(f"{result_path}.tmp")
            temp_path.write_text(parsed.model_dump_json(), encoding="utf-8")
            os.replace(temp_path, result_path)
            return parsed
        except ValueError as exc:
            raise ExtractionError("the local model response was not valid structured JSON") from exc

    async def extract(
        self, pdf_bytes: bytes, *, selected_schedule_option_id: str | None = None,
    ) -> ExtractedSchedule:
        # This legacy single-shot path has no classification stage, so it
        # never detects multiple independent Schedules of Assessments;
        # selected_schedule_option_id is accepted only to satisfy
        # ProtocolExtractor and is otherwise unused.
        parsed = await self._generate(
            pdf_bytes,
            "Extract this protocol's visit schedule. Search all provided pages for "
            "cycle length and cycle count before treating a block as open-ended.",
            ExtractedSchedule,
            system_instruction=_SYSTEM_PROMPT,
        )
        return expand_schedule(parsed)

    async def extract_all(
        self, pdf_bytes: bytes,
    ) -> list[tuple[ScheduleOption | None, ExtractedSchedule]]:
        """This single-shot backend never detects multiple independent
        Schedules of Assessments (no classification stage), so there is
        only ever one schedule to return."""
        return [(None, await self.extract(pdf_bytes))]

    async def extract_details(self, pdf_bytes: bytes) -> ExtractedTrialDetails:
        return await self._generate(
            pdf_bytes,
            "Extract the protocol's trial-level metadata.",
            ExtractedTrialDetails,
            system_instruction=_DETAILS_PROMPT,
        )


def get_extractor() -> ProtocolExtractor:
    """Factory — swap the returned implementation to change backends."""
    provider = os.getenv(
        "PROTOCOL_EXTRACTION_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if provider in ("gemini", "google"):
        return GeminiProtocolExtractor()
    if provider in ("claude", "anthropic"):
        return ClaudeProtocolExtractor()
    if provider in ("openrouter", "deepseek"):
        return OpenRouterProtocolExtractor()
    if provider in ("ollama", "qwen", "local"):
        return OllamaProtocolExtractor()
    raise ExtractionNotConfigured(
        "PROTOCOL_EXTRACTION_PROVIDER must be 'gemini', 'claude', "
        "'openrouter', or 'ollama'")


def get_details_extractor():
    """Factory kept separate so focused tests/providers can replace it alone."""
    return get_extractor()


async def extract_protocol_bundle(
    pdf_bytes: bytes,
    *,
    selected_schedule_option_id: str | None = None,
) -> tuple[ExtractedTrialDetails, ExtractedSchedule]:
    """Extract details and schedule together when the provider supports it.

    Gemini reuses its decomposed discovery pass, eliminating the standalone
    metadata request. Other providers retain a compatible fallback.

    ``selected_schedule_option_id`` chooses one schedule when the classifier
    finds a document with more than one independent Schedule of Assessments
    (see ``ExtractedSchedule.requires_schedule_selection``). Leave it unset on
    the first call; if the returned schedule requires a selection, re-call
    with the id of the option the caller chose.
    """
    extractor = get_extractor()
    combined = getattr(extractor, "extract_bundle", None)
    if callable(combined):
        return await combined(
            pdf_bytes, selected_schedule_option_id=selected_schedule_option_id)
    schedule = await extractor.extract(
        pdf_bytes, selected_schedule_option_id=selected_schedule_option_id)
    details_extractor = extractor if hasattr(extractor, "extract_details") else get_details_extractor()
    details = await details_extractor.extract_details(pdf_bytes)
    return details, schedule


async def extract_protocol_bundle_all(
    pdf_bytes: bytes,
) -> tuple[ExtractedTrialDetails, list[tuple[ScheduleOption | None, ExtractedSchedule]]]:
    """Extract details once and every independent Schedule of Assessments
    the protocol prints, together when the provider supports it.

    Gemini reuses its decomposed discovery pass across every substudy's
    pipeline run. Other providers fall back to extract_all() (which is
    itself a no-op wrapper around extract() when there's nothing to fan
    out) plus one standalone metadata request.
    """
    extractor = get_extractor()
    combined = getattr(extractor, "extract_bundle_all", None)
    if callable(combined):
        return await combined(pdf_bytes)
    results = await extractor.extract_all(pdf_bytes)
    details_extractor = extractor if hasattr(extractor, "extract_details") else get_details_extractor()
    details = await details_extractor.extract_details(pdf_bytes)
    return details, results
