"""Bounded evaluator/optimizer agent for protocol schedule extraction."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections import Counter
from collections.abc import MutableMapping
from typing import Any, Awaitable, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from protocol_document_index import (
    PdfIndexingError,
    ProtocolDocumentIndex,
    ProtocolDocumentIndexCache,
    ProtocolPageChunk,
    RetrievalTask,
    build_protocol_document_index,
    chunk_protocol_pages,
    render_page_chunk,
    render_page_selection,
    retrieve_protocol_pages,
)
from protocol_extraction import (
    MAX_OUTPUT_TOKENS,
    ExtractionError,
    ExtractionNotConfigured,
    ExtractedSchedule,
    ExtractedTrialDetails,
    expand_schedule,
)
from schedule_schema import (
    DocumentTaskClassification,
    ScheduleOption,
    SourceEvidence,
    study_day_to_offset,
)

log = logging.getLogger(__name__)

# Bar for the AI audit's per-dimension accuracy and for evidence-confidence
# gating. Was a hardcoded 0.95 — strict enough that most real (imperfect,
# scanned, OCR'd) protocols never reached "verified" and instead looped
# through refine_node, burning extra Gemini calls and raising the odds of an
# eventual hard failure. Self-reported by the same model doing the extraction,
# so treat this as a review-priority knob, not a truth signal: lower it to see
# more full drafts marked verified, raise it to flag more for manual review.
MIN_ACCEPT_CONFIDENCE = float(os.getenv("PROTOCOL_EXTRACTION_MIN_CONFIDENCE", "0.75"))

# Full-document evidence sweep: every page is assigned to exactly one chunk's
# CORE range (deterministic partition, not a keyword guess), with a small
# overlap on each side so a table or governing rule that straddles a chunk
# boundary is still legible to whichever chunk needs it for context.
_EVIDENCE_SWEEP_CORE_PAGES = int(os.getenv("PROTOCOL_EVIDENCE_CHUNK_CORE_PAGES", "22"))
_EVIDENCE_SWEEP_OVERLAP_PAGES = int(os.getenv("PROTOCOL_EVIDENCE_CHUNK_OVERLAP_PAGES", "4"))
_EVIDENCE_SWEEP_MAX_TOKENS = 8000


class ScheduleAuditIssue(BaseModel):
    """One evidence-backed problem found by the verification pass."""

    severity: Literal["critical", "major", "minor"]
    category: Literal[
        "missing_visit",
        "extra_visit",
        "timing",
        "window",
        "cycle_structure",
        "activity",
        "visit_type",
        "arm_or_period",
        "source_conflict",
        "overall_schedule",
        "other",
    ]
    finding: str = Field(
        description="Precisely what is wrong or missing in the candidate schedule.")
    evidence: str = Field(
        description="Protocol page/section/table evidence supporting the finding.")
    repair_instruction: str = Field(
        description="The smallest evidence-supported change needed to fix the issue.")


class ScheduleAccuracyDimension(BaseModel):
    """One independently judged dimension of schedule fidelity."""

    applicable: bool = Field(
        description="False only when the protocol contains nothing to score in this dimension.")
    accuracy: float | None = Field(
        ge=0,
        le=1,
        description="Estimated correctness for this dimension, or null when not applicable.")
    passed: bool = Field(
        description="True only when this dimension is sufficiently accurate for a review draft.")
    checked_items: list[str] = Field(
        default_factory=list,
        description="Page-cited facts actually compared with the candidate.")
    summary: str = ""

    @property
    def accepted(self) -> bool:
        if not self.applicable:
            return True
        return self.passed and self.accuracy is not None and self.accuracy >= MIN_ACCEPT_CONFIDENCE


class ScheduleAudit(BaseModel):
    """Independent semantic audit of an extracted schedule against its PDF."""

    approved: bool = Field(
        description="True only when no critical or major evidence-backed issue remains.")
    confidence: float = Field(
        ge=0, le=1,
        description="Confidence in the audit evidence, not schedule accuracy.")
    visit_coverage: ScheduleAccuracyDimension
    timing: ScheduleAccuracyDimension
    windows: ScheduleAccuracyDimension
    visit_types: ScheduleAccuracyDimension
    procedure_mapping: ScheduleAccuracyDimension
    overall_schedule: ScheduleAccuracyDimension
    verified_items: list[str] = Field(
        default_factory=list,
        description="Important schedule facts explicitly checked against the PDF.")
    issues: list[ScheduleAuditIssue] = Field(default_factory=list)
    summary: str = ""

    @field_validator("approved")
    @classmethod
    def approval_is_boolean(cls, value):
        return bool(value)

    @property
    def accepted(self) -> bool:
        dimensions = (
            self.visit_coverage,
            self.timing,
            self.windows,
            self.visit_types,
            self.procedure_mapping,
            self.overall_schedule,
        )
        return (
            self.approved
            and all(dimension.accepted for dimension in dimensions)
            and not any(issue.severity in ("critical", "major") for issue in self.issues)
        )

    def accuracy_scores(self) -> dict[str, float | None]:
        return {
            "visit_coverage": self.visit_coverage.accuracy,
            "timing": self.timing.accuracy,
            "windows": self.windows.accuracy,
            "visit_types": self.visit_types.accuracy,
            "procedure_mapping": self.procedure_mapping.accuracy,
            "overall_schedule": self.overall_schedule.accuracy,
        }


def _unavailable_audit(message: str) -> ScheduleAudit:
    """Represent an unavailable AI audit without approving an unchecked draft."""
    unchecked = ScheduleAccuracyDimension(
        applicable=True,
        accuracy=None,
        passed=False,
        checked_items=[],
        summary=message,
    )
    return ScheduleAudit(
        approved=False,
        confidence=0,
        visit_coverage=unchecked,
        timing=unchecked,
        windows=unchecked,
        visit_types=unchecked,
        procedure_mapping=unchecked,
        overall_schedule=unchecked,
        verified_items=[],
        issues=[],
        summary=message,
    )


class ScheduleDocumentMap(BaseModel):
    """Protocol metadata, locations, and structure found before schedule synthesis."""

    has_schedule: bool = Field(
        description="Whether the document contains a real visit schedule.")
    schedule_kind: str = Field(
        description="Likely structure: linear, cyclic, crossover, factorial, multi_arm, "
                    "multi_phase, intra_day, or none.")
    schedule_locations: list[str] = Field(
        default_factory=list,
        description="Page-cited schedule tables, flow charts, and appendices.")
    supporting_locations: list[str] = Field(
        default_factory=list,
        description="Page-cited dosing/design sections that define schedule rules.")
    arms_and_periods: list[str] = Field(
        default_factory=list,
        description="Distinct arms, cohorts, periods, washouts, and extensions.")
    baseline_anchor: str = Field(
        default="",
        description="The event treated as calendar day_offset zero. Preserve whether "
                    "the protocol labels that event Day 0 or Day 1.")
    ctri_number: str = ""
    official_title: str = ""
    phase: str = ""
    indications: list[str] = Field(default_factory=list)
    investigational_drug: str = ""
    planned_duration: str = ""
    target_enrollment: int | None = None
    stated_total_visits: int | None = None
    study_status: str = "active"
    notes: list[str] = Field(default_factory=list)


class EvidenceFact(BaseModel):
    """One atomic, traceable fact read from the protocol."""

    evidence_id: str = Field(
        description="Unique stable ID within this extraction, such as timing-p12-01.")
    claim: str = Field(description="The normalized fact supported by the source.")
    source_location: str = Field(
        description="Page plus table, section, footnote, row, or column location.")
    source_quote: str = Field(
        description="Short exact text or table-cell content supporting the claim.")
    page_evidence_id: str = Field(
        default="",
        description="Optional ID of the retrieved PDF page/table evidence record.")
    confidence: float = Field(
        ge=0, le=1,
        description="Reading confidence. Low-confidence facts must remain reviewable.")

    @field_validator("evidence_id", "claim", "source_location", "source_quote")
    @classmethod
    def evidence_text_is_not_blank(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("evidence fields cannot be blank")
        return value


class ScheduleTimingEvidence(BaseModel):
    """Page-cited timing facts collected independently of visit construction."""

    visit_timing: list[EvidenceFact] = Field(default_factory=list)
    visit_windows: list[EvidenceFact] = Field(default_factory=list)
    cycle_rules: list[EvidenceFact] = Field(default_factory=list)
    relative_timing: list[EvidenceFact] = Field(default_factory=list)
    open_ended_rules: list[EvidenceFact] = Field(default_factory=list)
    conflicts_or_unknowns: list[EvidenceFact] = Field(default_factory=list)


class ScheduleVisitEvidence(BaseModel):
    """Page-cited visit-column, activity, and footnote facts."""

    visit_columns: list[EvidenceFact] = Field(default_factory=list)
    special_visits: list[EvidenceFact] = Field(default_factory=list)
    activity_assignments: list[EvidenceFact] = Field(default_factory=list)
    table_footnotes: list[EvidenceFact] = Field(default_factory=list)
    arm_period_differences: list[EvidenceFact] = Field(default_factory=list)
    conflicts_or_unknowns: list[EvidenceFact] = Field(default_factory=list)


class ScheduleChunkEvidence(BaseModel):
    """Timing + visit/activity facts found on ONE chunk's CORE pages only.

    Union of ScheduleTimingEvidence's and ScheduleVisitEvidence's fields, so
    one model call per document chunk can do both jobs at once instead of
    two. Every field here is populated the same way its ScheduleTimingEvidence
    / ScheduleVisitEvidence counterpart is; the merged pool from every chunk
    becomes the final ScheduleTimingEvidence and ScheduleVisitEvidence the
    rest of the pipeline already consumes unchanged.
    """

    visit_timing: list[EvidenceFact] = Field(default_factory=list)
    visit_windows: list[EvidenceFact] = Field(default_factory=list)
    cycle_rules: list[EvidenceFact] = Field(default_factory=list)
    relative_timing: list[EvidenceFact] = Field(default_factory=list)
    open_ended_rules: list[EvidenceFact] = Field(default_factory=list)
    visit_columns: list[EvidenceFact] = Field(default_factory=list)
    special_visits: list[EvidenceFact] = Field(default_factory=list)
    activity_assignments: list[EvidenceFact] = Field(default_factory=list)
    table_footnotes: list[EvidenceFact] = Field(default_factory=list)
    arm_period_differences: list[EvidenceFact] = Field(default_factory=list)
    conflicts_or_unknowns: list[EvidenceFact] = Field(default_factory=list)


def _prefix_evidence_ids(facts: list[EvidenceFact], prefix: str) -> list[EvidenceFact]:
    """Namespace one chunk's evidence_ids so independently-minted IDs from N
    parallel chunk calls can never collide once merged into one pool."""
    renamed = []
    for fact in facts:
        renamed.append(fact.model_copy(update={"evidence_id": f"{prefix}-{fact.evidence_id}"}))
    return renamed


def _merge_chunk_evidence(
    chunks: list[ScheduleChunkEvidence],
) -> tuple[ScheduleTimingEvidence, ScheduleVisitEvidence]:
    """Pool every chunk's facts into the same two evidence objects the rest
    of the pipeline (synthesis/audit/repair prompts) already consumes, so
    nothing downstream needs to know evidence-gathering was chunked at
    all."""
    timing = ScheduleTimingEvidence()
    visits = ScheduleVisitEvidence()
    for index, chunk in enumerate(chunks):
        prefix = f"chunk{index}"
        timing.visit_timing.extend(_prefix_evidence_ids(chunk.visit_timing, prefix))
        timing.visit_windows.extend(_prefix_evidence_ids(chunk.visit_windows, prefix))
        timing.cycle_rules.extend(_prefix_evidence_ids(chunk.cycle_rules, prefix))
        timing.relative_timing.extend(_prefix_evidence_ids(chunk.relative_timing, prefix))
        timing.open_ended_rules.extend(_prefix_evidence_ids(chunk.open_ended_rules, prefix))
        timing.conflicts_or_unknowns.extend(
            _prefix_evidence_ids(chunk.conflicts_or_unknowns, prefix))
        visits.visit_columns.extend(_prefix_evidence_ids(chunk.visit_columns, prefix))
        visits.special_visits.extend(_prefix_evidence_ids(chunk.special_visits, prefix))
        visits.activity_assignments.extend(
            _prefix_evidence_ids(chunk.activity_assignments, prefix))
        visits.table_footnotes.extend(_prefix_evidence_ids(chunk.table_footnotes, prefix))
        visits.arm_period_differences.extend(
            _prefix_evidence_ids(chunk.arm_period_differences, prefix))
        visits.conflicts_or_unknowns.extend(
            _prefix_evidence_ids(chunk.conflicts_or_unknowns, prefix))
    return timing, visits


_CLASSIFICATION_PROMPT = """You are the classification stage of a clinical-protocol
analysis pipeline. Read the whole attached PDF before extraction. Classify the document
as a protocol, amendment, synopsis, schedule-only document, reference, mixed bundle, or
unrelated document. Select the actual task and every applicable schedule archetype:
linear, cyclic, crossover, factorial, multi-arm, multi-phase, event-driven, intra-day,
long-term extension, or mixed. A crossover has subjects rotate through PERIODS in a
randomized SEQUENCE (2-way, 3-way, or more); a factorial has subjects randomized
independently on two or more separate factors at once (e.g. a 2x2 crossing drug A
present/absent with drug B present/absent) with everyone sharing one visit timeline — do
not tag a factorial design as crossover, they require different structures. Detect
appended/reference protocols and whether version comparison
is required. Capture protocol ID, version, amendment ID, and jurisdiction only when
stated. Cite page/section evidence for the classification. Never infer that a document
is simple merely because its first schedule table is simple.

MULTIPLE INDEPENDENT SCHEDULES: some protocols (commonly seamless Phase 2/3 designs)
print more than one genuinely separate Schedule of Assessments/Activities/Events table —
distinct substudies or sub-protocols, each with its own visit list, duration, and
population (e.g. "Substudy A – Phase 2", "Substudy 2 – Induction", "Substudy 3 –
Maintenance", each with its own Schedule of Assessments table). This is DIFFERENT from a
single schedule shared by multiple arms/cohorts/periods (that stays multi_arm/multi_phase
with schedule_options left empty) — the test is whether the protocol prints separate
tables that a reviewer must choose between, not merely separate columns of one table.
When you find more than one such independent schedule, populate schedule_options with one
entry per schedule using the protocol's own short name/code as the label and id, a
one-sentence description (population/duration/purpose), and the table's page/section
location. Leave schedule_options empty for every other document, including one with
multiple arms, cohorts, periods, or phases sharing a single table. Return only the
requested classification schema."""


_DISCOVERY_PROMPT = """You are the discovery stage of a clinical-protocol schedule
pipeline. Do not build a visit schedule yet. First capture the official trial metadata:
study title, CTRI/registration number, phase, indications, investigational drug, planned
duration, target enrollment, stated total visit count, and study status. Use empty values
when the PDF does not state them; never invent metadata. Then locate every Schedule of Assessments,
Activities, Events, flow chart, and relevant appendix in the PDF. Then locate the study
design, treatment, dosing, and follow-up sections that define cadence. Map arms, cohorts,
periods, washouts, extensions, and the baseline/randomization anchor. Cite a page, section,
table, or nearby heading for every location. If this document has no visit schedule, say
so explicitly. Return only the requested discovery schema."""

# _TIMING_PROMPT and _VISIT_EVIDENCE_PROMPT (single-shot specialists working from a
# keyword-scored ~24-page excerpt of the whole document) were replaced by
# _CHUNK_EVIDENCE_PROMPT + evidence_sweep_node, which partitions the ENTIRE document
# into full-coverage page chunks instead of guessing which pages matter.

_CHUNK_EVIDENCE_PROMPT = """You are one worker in a full-document evidence sweep over a
clinical-protocol PDF. The document has been split into chunks of consecutive pages, and
you are responsible for exactly one chunk. Every page below is marked either CORE (this
chunk's responsibility) or CONTEXT ONLY (shown so a table or rule that straddles the
boundary into a neighbouring chunk is still legible to you). Extract facts ONLY from CORE
pages. Never emit a fact whose evidence is a CONTEXT ONLY page — a different worker owns
that page and will emit it from its own chunk; emitting it here would duplicate it in the
merged pool. It is correct and expected for a chunk of mostly boilerplate (consent forms,
investigator signatures, adverse-event definitions) to return entirely empty lists.

Do not calculate final absolute offsets and do not construct the final schedule; that
happens in a later stage from the pooled evidence of every chunk. Within your CORE pages,
collect every explicit visit day, week, month, hour, window, cycle length/count, repetition
range, relative-time rule, open-ended cadence, visit column (including unlabeled numeric
columns), special/telephonic/unscheduled visit, activity/procedure assignment, table
footnote, and arm/period difference. Search dosing and treatment prose on your CORE pages,
not only tables — a governing rule (cycle length, a dose-modification/toxicity-triggered
visit, a conditional repeat) is exactly as real when it is stated only in a paragraph as
when it is in a table cell, and must be captured as a fact either way. Preserve every exact
Day/Week/Cycle/Hour label; Week 1 is ambiguous unless the protocol defines its relationship
to the anchor, so do not blindly convert it to seven days. Preserve conflicts and unknowns
instead of guessing. A visit whose timing is conditional on a clinical event or lab value
(e.g. "repeat if ANC < 1000", "extend by 1 week if toxicity persists") is still a real fact:
capture it in special_visits or open_ended_rules with the trigger condition preserved
verbatim, even though its date cannot be resolved to a fixed offset.

POPULATION-GATED ACTIVITIES: an assessment restricted to a named subgroup rather than
every subject — by country/region (e.g. "EQ-5D only for subjects in the USA, Germany,
France..."), age (e.g. "frailty characteristics collected only for subjects 65 years old
and above"), sex or reproductive status (e.g. "pregnancy test only for women of
childbearing potential"), or any other stated eligibility/demographic subset — is a
distinct fact from an arm/period difference and must not be flattened into a generic
activity note that loses the gating condition. Capture it in activity_assignments with the
exact gating condition (the named countries, the age threshold, the population
description) preserved verbatim in the claim, not paraphrased into "some subjects" or
dropped.

WIDE TABLES: a Schedule of Assessments/Activities often prints one column per visit under a
shared header row, wrapping across pages with the header repeated on each page. Every column
on a CORE page is a distinct real visit, even with no name beyond its number (emit it
anyway, e.g. "Week 8 Visit"), even adjacent to near-identical columns, even when its interval
to the previous column later changes. Do not compress a long run of such columns down to a
handful of "representative" milestones — dropping unlabeled numeric columns in between is
the single most common failure in this task.

MULTI-DAY CONFINEMENT/HOUSING PERIODS: an inpatient PK/BA-BE visit is often not one day but
a block of several consecutive calendar days under one Visit/Period label — e.g. a Day 11
check-in, Day 12/13/14 pre-dose-only housing days, a Day 15 dosing-and-intensive-PK day, and
a Day 16 check-out day, all inside "Visit 2 (Treatment Period 1)". Whenever the source
states a distinct activity for an individual day inside such a block, capture EACH such day
as its own visit_columns fact with its own exact printed day number and its own activities
(check-in/admission, pre-dose sample only, dosing plus PK draws, discharge/dispensing) —
do not collapse evidence of distinct per-day activities into one fact describing only the
block's start and end day. If the source instead states only a plain span with no per-day
differentiation (e.g. "subjects remain confined Days 11-16"), capture that as a single fact
covering the span — do not invent day-by-day differentiation that is not in the source
either.

Every fact must carry a page/section/table/footnote/nearby-label citation from a CORE page.
Return each fact atomically with a unique evidence_id, a short exact source_quote, its
precise source_location, and reading confidence. Do not combine unrelated facts under one
ID. Return only the requested schema."""

_SYNTHESIS_PROMPT = """You are the synthesis stage of a decomposed clinical-protocol
schedule pipeline. Build the complete schedule from the attached PDF and the three
page-cited evidence packets in the user message. The evidence guides you but the PDF is
authoritative.

The supplied CLASSIFICATION is authoritative about the analysis task unless the PDF
contains direct contradictory evidence, which must be recorded as a conflict. Populate
canonical_plan as the only AI-authored source-of-truth schedule graph. Leave visits and
repeating_blocks empty; the server deterministically generates the backward-compatible
mobile-editor projection after validation. Never author a second flat copy of the
schedule. Use stable unique IDs. Preserve calendar month/year offsets with
calendar_mode=calendar; never turn them
into 30/365-day approximations. Represent discharge, last dose, progression, consent,
randomization, and similar triggers as anchors. Keep open-ended schedules as recurrence
rules rather than pretending that an arbitrary number of cycles is permanent. Separate
visit windows from activity/procedure windows. Record conflicting source statements in
canonical_plan.conflicts instead of choosing one silently. Define arms, cohorts, periods,
and treatment sequences as branches and express "if/when/only for" applicability as
conditions rather than deleting conditional visits.

Preserve each exact timing string in source_day_label. Set anchor_study_day to 0 or 1 and
includes_day_zero only when supported by the protocol. Derive simple Day D offsets using:
anchor Day 0 -> D; anchor Day 1 with Day 0 -> D-1; anchor Day 1 without Day 0 -> D-1 for
D>=1 and D for negative D. Day 0 is invalid in the no-Day-0 convention. Do not blindly
convert Week 1 to seven days. Preserve
real visits whose timing is unknown with a null day offset. Use relative_to and
relative_offset_days for timing against another visit. Use hour offsets only for genuine
intra-day schedules and set hour_offset_basis to absolute; Hour 26 is exactly 26 elapsed
hours and must not also carry another 24-hour addition. Preserve asymmetric windows.
An unstated window is null, never +/-3 or any other default.

For every canonical anchor, event, activity, timing, window, recurrence, transition,
condition, and conflict, attach supporting evidence IDs in its evidence_ids field. Only
use IDs present in the supplied evidence packets. If a value has no supporting evidence
ID, leave it unresolved or empty rather than guessing.

For collapsed cycles, emit recurrence rules in canonical_plan; do not populate legacy
repeating_blocks and do not enumerate repeated cycles manually. Use separate recurrence
rules when cadence changes. Put cycle-specific procedures in conditions. Duplicate events
by arm only when timing genuinely differs, and label crossover periods, washouts, and
extensions. Include early termination, unscheduled, telephone, and safety follow-up
events when present.

Every visit_columns fact in the supplied evidence packet must become an event — never
dropped and never left uncovered. A wide, plainly-numbered column (e.g. "Week 8", "Week
12") with no distinct name is still a real, mandatory event; do not compress a long run of
such columns into only the screening/baseline/early/final visits. Before returning, count
the distinct visit_columns facts you were given and confirm every one is represented.

RECURRENCE VS INDIVIDUAL EVENTS: use a recurrence rule ONLY when the protocol itself
collapses the repetition — it prints one column/phrase covering many instances ("Cycle 2 &
Next Cycles", "every 3 weeks for a maximum of 6 cycles") without ever printing each
instance's own number. When the table instead prints a SEPARATE, distinctly-numbered
column for every instance (Week 4, 8, 12, 16, 20...), create a SEPARATE event per column
with that exact printed number as its name — do not fold them into a recurrence rule. The
server's recurrence expansion can only substitute a bare 1, 2, 3... occurrence index into a
name; it cannot reproduce arbitrary printed numbers like 8, 12, 44, 108, so recurrence-
covering a printed, individually-numbered column always renders wrong (e.g. a stray "Week
4 (Occurrence 2)" duplicate instead of "Week 8"). If unsure whether a run of columns is
collapsed or individually printed, prefer individual events — that is always representable
exactly.

COLLAPSED CYCLE BLOCKS: when one source column means "Cycle 2 & Next Cycles", keep one
shared recurrence rule whose event_ids contain every member of that cycle block. Give each
member an occurrence-aware name containing the literal placeholder {cycle}, for example
"Cycle {cycle} Dosing Visit" and "Cycle {cycle} Intra-Cycle Visit IC-1". Set
start_occurrence to the first represented cycle. Give the first occurrence of every member
an evidence-backed, resolvable timing; later occurrences are shifted by the recurrence
frequency. Same-cycle relative events must anchor to another event in the shared block.
Leave end_occurrence null when treatment continues until progression/toxicity, and record
that stopping event instead of borrowing a cycle maximum from historical-study prose. For
activities limited to named cycles (for example imaging at cycles 2, 4, and 6), attach a
ScheduleCondition to the activity with occurrence_numbers [2, 4, 6].

CROSSOVER / BIOEQUIVALENCE (BA/BE) DESIGNS, ANY NUMBER OF PERIODS: when subjects are
randomized to a treatment SEQUENCE (e.g. a 2-way "Sequence AB" doses Test in Period 1 then
Reference in Period 2, "Sequence BA" is the reverse; a 3-way Williams design has sequences
ABC, BCA, CAB across three periods; N periods and N (or more) sequences work identically —
nothing about this structure is specific to exactly two), create one branch of branch_type
"sequence" per randomized sequence and one branch of branch_type "period" per period WITHIN
each sequence, and set each period branch's parent_branch_id to its sequence branch. This
is what keeps "Period 1" under Sequence AB distinguishable from "Period 1" under Sequence
BA even though both print the identical printed label — the same holds for every period,
however many there are. Attach each period's events (dosing, PK draws, safety checks) to
that period's own branch via period_id, never to one shared unscoped branch.

Give every dosing event its own timing resolvable to a real day: an offset from baseline
for Period 1, a relative/minimum-gap offset from the IMMEDIATELY PRIOR period's dosing for
every period after it (Period 3 anchors to Period 2's dosing, Period 4 to Period 3's, and
so on — never straight back to Period 1 or to baseline) — this is also how washout is
expressed, as a TransitionRule with relation "minimum_gap" between each consecutive pair of
period dosing events, never as a fabricated "Washout" visit. Anchor EVERY intra-day PK
timepoint in a period to THAT period's OWN dosing event via anchor_id — never to the study
baseline and never to another period's dosing event. A dense PK table repeats the identical
hour set (Hour 0, 0.5, 1, 2, 4, 8, 12, 24...) once per period; each period's own "Hour 4"
event must point its anchor_id at that period's own dosing event so it is dated against
that period's day, not another period's. Reusing one shared "Hour 4" event across periods,
or anchoring every period's PK draws to the baseline instead of that period's own dosing,
makes every period's PK samples collapse onto the same elapsed time and is always wrong.

MULTI-DAY CONFINEMENT/HOUSING PERIODS: let the evidence decide the shape here exactly as
everywhere else — do not default to either extreme. When a period or visit is a block of
several consecutive named days (a housing/confinement stay) and the evidence gives each day
its own distinct activity (e.g. Day 11 check-in, Day 12/13/14 pre-dose-only housing, Day 15
dosing plus intensive PK, Day 16 check-out), author ONE event per distinct day, each with
its own resolvable offset and its own activities, instead of one event spanning the whole
block with a range/day_end offset — collapsing evidence-backed distinct days into one range
loses exactly the information a coordinator needs to run the stay. But when the evidence
states only a plain span with no per-day differentiation (e.g. "subjects remain confined
Days 11-16" with no distinct daily activities, or one activity repeated identically across a
range), keep the single range/day_end event as-is — do not invent day-by-day structure the
source never stated just to look more granular; a range/day_end timing is the correct,
faithful representation of a source span that genuinely never breaks the days apart. When
you do split days out, name each one so it reads as one step in the stay rather than a
duplicate of its neighbors, combining the visit/period label with the day's own role, e.g.
"Treatment Period 1 — Day 11 Check-in", "Treatment Period 1 — Day 13 Housing (Pre-dose
Sample)", "Treatment Period 1 — Day 15 Dosing", "Treatment Period 1 — Day 16 Check-out".

FACTORIAL DESIGNS: when subjects are randomized independently on TWO OR MORE separate
factors at once (e.g. a 2x2 factorial crossing Drug A present/absent with Drug B
present/absent gives 4 combination arms: A+B+, A+B-, A-B+, A-B-), this is NOT a crossover —
there are no periods or sequences, just one shared visit-day timeline that every arm
follows identically. Create one branch of branch_type "arm" per factor COMBINATION (4
arms for a 2x2, 8 for a 2x2x2, and so on) as flat siblings, not nested under each other.
Do NOT duplicate the whole event graph once per arm combination just to vary which drug is
given — author the shared visit schedule ONCE, and for any activity or event that only
applies to some combinations (e.g. "Drug A dispensing" only in the A+ arms), attach a
ScheduleCondition to it with applies_to_branch_ids set to exactly the arm branch ids that
include that factor. An activity with no such condition applies to every arm, same as
today. This keeps one visit list shared across all combinations instead of the same visit
timeline repeated verbatim per arm with only the drug names changed.

Classify every event's event_type using the protocol's own visit-type codes when present
(e.g. 'SS' study-site, 'V' virtual, 'T/C' telephone), otherwise using its role in the
schedule: 'screening', 'baseline', 'randomization', 'treatment', 'follow_up',
'end_of_treatment', 'end_of_study', 'early_termination', 'unscheduled', or 'telephonic'
(a phone/telephone-icon contact). Do not leave every event at the generic default
'visit' — the first screening visit, the dosing/randomization visit, telephone-only
contacts, and the final visit are almost always determinable from the protocol text or
their position in the schedule. Never invent missing facts: record
uncertainties or evidence conflicts in assumptions and cite source locations in
source_notes. If discovery shows no schedule and the PDF confirms it, return schedule_kind
none with no visits. Return only the requested schedule schema.

TIMING SHAPE RULE (applies to every timing object, including activity and procedure timing): choose the kind from what the source actually supplies. Use offset/calendar_offset only with a numeric offset amount. Use range only with both range_start and range_end. Use relative or event_driven only with an anchor_id naming a real anchor or event. Procedure prose with no number and no anchor -- "pre-dose", "at each visit", "as clinically indicated", "prior to discharge" -- must use kind unresolved with the exact wording in source_label. Never label such a value offset or relative and leave its companion field empty."""

_AUDIT_PROMPT = f"""You are the adjudicating quality-control reviewer for a clinical-trial
visit schedule. You are the only check this schedule gets before a clinician relies on it —
re-read the attached protocol PDF yourself and verify the builder schedule against it
directly; do not assume the builder read anything correctly. Do not merely critique
formatting. Every issue you report must be resolved from cited protocol evidence.

You are also given a DETERMINISTIC CHECKS list: mechanically-computed findings (unsupported
or missing evidence citations, duplicate visits, an event count short of the inventoried
visit columns, malformed recurrence) that need no judgment to detect. Treat every entry
there as a confirmed, real defect — verify it against the PDF only to describe it
accurately, never to argue it away, and always resolve it as a critical or major issue.

Then read the PDF's own Schedule of Assessments/Activities section end to end and go
column by column, row by row: for every visit or event column it prints, confirm the
builder schedule has a matching entry with the right day/week/hour, the right window, and
the right procedures — do not sample a few and extrapolate. This is the primary way you
catch a real error, not a skim for anything that looks obviously wrong.

Score these dimensions INDEPENDENTLY:
1. VISIT COVERAGE — is every Schedule of Assessments/Activities/Events column represented,
   including screening, baseline, early termination, unscheduled and safety follow-up?
2. TIMING — are all days, weeks, months, hours, relative offsets, cycle lengths/counts,
   repeating blocks, arms, periods and crossover/washout timing correct? For a crossover/
   BA-BE design specifically: does each period's own "sequence" branch keep it
   distinguishable from the same-numbered period in another sequence, and does every
   intra-day PK timepoint anchor to ITS OWN period's dosing event rather than the baseline
   or another period's dosing (a shared/misanchored hour value silently collapses that
   period's whole PK profile onto another period's)? Also check for a multi-day
   confinement/housing block (check-in, pre-dose-only housing days, a dosing/intensive-PK
   day, check-out) wrongly compressed into one event with a range/day_end span instead of
   one dated event per distinct day — this is a major issue whenever the source prints each
   day's own activities. A DETERMINISTIC CHECKS entry naming an activity that spans two or
   more specific days (e.g. "PK Sampling Day 30 & 31") but whose day list only partly overlaps
   the built schedule is a confirmed defect: re-read that exact source location and confirm
   EVERY named day carries the activity, not just the first. This is the single most common
   way a multi-day block loses information — the activity gets attached to the block's
   anchor/dosing day and silently dropped from the trailing day(s) the source names just as
   explicitly (a PK-sampling profile whose last timepoints land on the following calendar day
   is a frequent real example: the source table lists it as spanning both days for exactly
   that reason).
3. WINDOWS — is every symmetric or asymmetric +/- visit window preserved correctly?
4. VISIT TYPE — is each site, virtual, telephone, home, unscheduled, and other visit type
   classified correctly from the protocol?
5. PROCEDURE MAPPING — is each assessment/procedure attached to the correct visit column,
   including conditional-cycle activities and table footnotes? For a factorial design, is
   each factor-specific activity (e.g. a drug given only to arms containing that factor)
   scoped via ScheduleCondition.applies_to_branch_ids rather than either missing from arms
   that need it or leaking into arms that should not have it?
6. OVERALL SCHEDULE — is the entire generated schedule structurally correct end to end,
   with no invented, omitted, or duplicated visits, activities, or timing values?

For each applicable dimension, list what was checked, assign its own accuracy, and pass it
only at {MIN_ACCEPT_CONFIDENCE:.2f} or higher. Mark a dimension not applicable only after
confirming the protocol contains no such information. Overall schedule accuracy is a
separate end-to-end judgment, NOT an average. For example, high procedure-mapping accuracy
does not imply equally high overall schedule accuracy. A strong dimension must never
compensate for a weak one.

Also check treatment-plan prose elsewhere in the PDF that defines cadence or maximum
cycles. `confidence` measures confidence in your evidence review; it is not an accuracy
score. Set `approved` true only if every applicable dimension passes and no critical or
major evidence-backed issue remains.

Report an issue only when the PDF provides evidence. Cite a page, section, table heading,
footnote, or exact nearby label in every issue. Minor uncertainty belongs in an issue
rather than being silently treated as fact."""

_REPAIR_PROMPT = """You are the schedule correction specialist. Re-read the attached PDF,
especially every location cited by the audit, and return a COMPLETE replacement schedule
matching the requested schema.

Apply every evidence-backed audit repair. Preserve candidate facts that the audit did not
challenge. Search nearby footnotes and cross-referenced treatment-plan sections for each
missing fact. Never accept an audit claim blindly: if it conflicts with the PDF, retain
the PDF-supported value and explain the conflict in assumptions. Never invent a value.
Use canonical recurrence rules for collapsed cycles; do not manually enumerate them.
Preserve or add valid evidence_ids for every populated canonical object, use not_stated
for unstated windows, and make the smallest possible evidence-supported change. Classify
every event's event_type ('screening', 'baseline', 'randomization', 'treatment',
'follow_up', 'end_of_treatment', 'end_of_study', 'early_termination', 'unscheduled',
'telephonic', or the protocol's own visit-type code) instead of leaving it at the
generic default 'visit'. Populate
canonical_plan only and leave visits and repeating_blocks empty; deterministic server
code rebuilds the compatibility rows. Do not discard calendar units, event triggers,
recurrence, activity windows, conditions, transitions, branches, or unresolved source
conflicts.

If the audit reports missing visit columns, treat the supplied VISIT/ACTIVITY EVIDENCE
packet as the checklist: every visit_columns fact there must end up represented as its own
event. A wide, plainly-numbered column with no distinct name is still a real, mandatory
event — do not leave it out because it looks like a duplicate of its neighbors. Count the
distinct visit_columns facts and confirm every one is covered before returning.

If the audit reports a garbled or duplicated name like "X (Occurrence 2)", first determine
which source shape applies. For separately printed columns (Week 4, 8, 12...), remove the
recurrence and create one event per printed column. For a genuinely collapsed source block
("Cycle 2 & Next Cycles"), KEEP recurrence: put all block event IDs in one shared rule,
change every member name to an occurrence-aware {cycle} template, and ensure the first
occurrence of each member has resolvable timing. Use occurrence_numbers on a condition for
activities limited to particular cycles. Never replace a collapsed, open-ended cycle block
with invented individually sourced columns or a fixed final cycle.

If the audit reports a multi-day confinement/housing visit collapsed into one event with a
range/day_end span (e.g. "Day 11 to Day 16" as a single event), split it into one event per
distinct day named and dated from the PDF and the evidence packet — a check-in day, each
pre-dose-only housing day, the dosing/intensive-PK day, and the check-out day are each their
own event, not a single ranged event.

If the audit or a DETERMINISTIC CHECKS entry reports an activity named for several specific
days (e.g. "PK Sampling Day 30 & 31") that the schedule only attaches to some of them, attach
the SAME real activity — by its actual protocol name, never a placeholder like "Activity" or
"Procedure" — to an event covering every day the source names it for. Confirm from the PDF
whether the missing day already has its own event (add the activity to it) or needs one (e.g.
a PK-sampling profile whose final timepoints land on the calendar day after dosing still
belongs to that same activity, dated on the day it actually occurs).

TIMING SHAPE RULE (applies to every timing object, including activity and procedure timing): choose the kind from what the source actually supplies. Use offset/calendar_offset only with a numeric offset amount. Use range only with both range_start and range_end. Use relative or event_driven only with an anchor_id naming a real anchor or event. Procedure prose with no number and no anchor -- "pre-dose", "at each visit", "as clinically indicated", "prior to discharge" -- must use kind unresolved with the exact wording in source_label. Never label such a value offset or relative and leave its companion field empty."""


_TIMING_FIELDS = {
    "day_offset", "day_end", "source_day_label", "hour_offset", "hour_end",
    "hour_offset_basis", "relative_to", "relative_offset_days",
}


# ─────────────────── deterministic PDF page retrieval ───────────────────
# classify/discover still receive a keyword-scored text selection — their job
# is locating sections/metadata, a lower-stakes task where a scored hint is a
# reasonable aid. synthesize/audit/repair do NOT: their evidence now comes
# from the chunked full-document sweep (evidence_sweep_node), which reads
# every page of the document by construction, not by keyword score. A scored
# excerpt on top of that pool would be pure redundant cost with no coverage
# benefit, since nothing in it can be a fact the sweep missed.

_StageRetrieval = tuple[RetrievalTask, int, int]

_STAGE_RETRIEVAL: dict[str, _StageRetrieval] = {
    "classify": ("classification", 14, 45_000),
    "discover": ("schedule_discovery", 24, 80_000),
}

_STAGES_WITHOUT_RETRIEVAL = {"synthesize", "audit", "repair"}


def _page_context_block(state: "ExtractionAgentState", stage_key: str) -> str:
    """Render the retrieved page packet for one stage, or '' when unavailable."""
    if stage_key in _STAGES_WITHOUT_RETRIEVAL:
        return ""
    index = state.get("page_index")
    if index is None:
        return ""
    task, max_pages, max_characters = _STAGE_RETRIEVAL.get(
        stage_key, ("review", 24, 80_000))
    cache = state.get("page_context_cache")
    cache_key = f"{task}:{max_pages}:{max_characters}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        selection = retrieve_protocol_pages(
            index, task=task, max_pages=max_pages, neighbour_radius=1)
        rendered = render_page_selection(selection, max_characters=max_characters)
    except (ValueError, PdfIndexingError) as exc:  # never fail a stage on retrieval
        log.warning("page retrieval for stage %s unavailable: %s", stage_key, exc)
        return ""
    notes = [
        f"document_sha256={index.document_sha256}",
        f"selected PDF pages: {', '.join(map(str, selection.page_numbers)) or 'none'}",
        f"{selection.omitted_page_count} page(s) of this PDF are not reproduced below",
        *selection.retrieval_warnings,
    ]
    block = (
        "RETRIEVED SOURCE PAGES (deterministic selection from the attached PDF; "
        "page numbers are physical one-based PDF pages):\n"
        + rendered
        + "\n\nRETRIEVAL NOTES:\n- "
        + "\n- ".join(notes)
        + "\n\nThese pages are a focus aid, not a boundary. The attached PDF remains "
        "authoritative: read any other page when a fact is missing here, and never "
        "treat a page absent from this selection as evidence that a fact is unstated. "
        "When a fact comes from a page reproduced above, copy that page's evidence_id "
        "into page_evidence_id and keep its PDF page number in source_location."
    )
    if cache is not None:
        cache[cache_key] = block
    return block


def _stage_prompt(state: "ExtractionAgentState", stage_key: str, body: str) -> str:
    """Prefix a stage instruction with its retrieved page packet and guidance."""
    sections = [_page_context_block(state, stage_key)]
    if stage_key not in ("classify", "discover"):
        sections.append(_classification_guidance(
            state.get("classification"), state.get("selected_schedule_option_id")))
    sections.append(body)
    return "\n\n".join(section for section in sections if section)


# ────────────────── classification-driven extraction routing ──────────────────

_DOCUMENT_GUIDANCE: dict[str, str] = {
    "amendment": (
        "This is an amendment. Extract the amended schedule, state which protocol "
        "version each changed fact belongs to, and record any pre-amendment value "
        "that survives elsewhere in the bundle as a conflict rather than deleting it."
    ),
    "mixed": (
        "This bundle contains more than one document. Keep each document's schedule "
        "facts separate, identify which document a fact came from in its "
        "source_location, and record cross-document contradictions as conflicts."
    ),
    "synopsis": (
        "This is a synopsis, so its schedule is usually incomplete. Extract only what "
        "is printed and record the missing detail in assumptions; do not fill gaps "
        "from a typical protocol."
    ),
    "schedule_only": (
        "This is a schedule table/flow-chart document with no surrounding prose. The "
        "cycle length, cycle count, and window definitions are usually in the table "
        "header rows and footnotes — read every footnote marker before concluding a "
        "value is unstated, and leave genuinely absent values unresolved."
    ),
    "reference": (
        "This is a reference document appended to a protocol. Extract a schedule only "
        "if it prints one; otherwise return schedule_kind none."
    ),
    "unrelated": (
        "This document is not a protocol. Do not manufacture a schedule; return "
        "schedule_kind none with an empty visit list."
    ),
}

_TASK_GUIDANCE: dict[str, str] = {
    "amendment_comparison": (
        "Task: version comparison. Locate both the amended and the appended/referenced "
        "base version, compare them field by field, and record every difference. When "
        "the two versions disagree and the governing version is not stated, record an "
        "unresolved conflict instead of silently preferring one."
    ),
    "schedule_table_only": (
        "Task: table-focused extraction. Work from the schedule table, its header rows, "
        "its column labels, and its footnotes. Do not infer cadence from prose that is "
        "not present in this document."
    ),
    "no_schedule": (
        "Task: no schedule. Confirm the absence and return schedule_kind none."
    ),
}

_ARCHETYPE_GUIDANCE: dict[str, str] = {
    "cyclic": (
        "Cyclic/oncology: build cycle templates with recurrence rules. Emit a separate "
        "recurrence whenever the cadence changes, capture the maximum cycle count and "
        "the end condition (progression, toxicity, withdrawal), and keep a genuinely "
        "open-ended tail as an open recurrence rather than an invented final cycle."
    ),
    "crossover": (
        "Crossover: one 'sequence' branch per randomized treatment order, one 'period' "
        "branch per period nested under it via parent_branch_id (so same-numbered periods "
        "in different sequences stay distinguishable), events attached via period_id, and "
        "washout expressed as a minimum_gap transition between each consecutive pair of "
        "dosing events, never as a fabricated visit. Works identically for 2-way, 3-way, "
        "or more periods/sequences — nothing here is specific to exactly two."
    ),
    "factorial": (
        "Factorial: one flat 'arm' branch per factor COMBINATION (4 for a 2x2, 8 for a "
        "2x2x2), all siblings — not nested periods, not a crossover. Author the shared "
        "visit timeline once; scope any factor-specific activity or event to the arms "
        "that include that factor via ScheduleCondition.applies_to_branch_ids instead of "
        "duplicating the event graph once per combination."
    ),
    "multi_arm": (
        "Multi-arm: define arms as branches. Duplicate an event per arm only when the "
        "arms genuinely differ in timing, windows, or activities; otherwise emit the "
        "shared event once with no arm."
    ),
    "multi_phase": (
        "Multi-phase: define each phase (screening, run-in, treatment, follow-up, "
        "extension) and attach every event to its phase, keeping the day numbering "
        "convention of each phase explicit."
    ),
    "intra_day": (
        "Intra-day: preserve hour and minute timepoints exactly. Hour N means N elapsed "
        "hours from the anchor and must not gain an extra day. If the same hour set "
        "repeats once per crossover period, anchor each period's timepoints to THAT "
        "period's own dosing event, never a shared or baseline anchor. Keep "
        "procedure-level tolerances on the activity, never on the visit window."
    ),
    "event_driven": (
        "Event-driven: represent last dose, discharge, disease progression, surgery, "
        "end of treatment, and similar triggers as anchors, and leave the calculated "
        "day unresolved when the trigger date is patient-specific."
    ),
    "long_term_extension": (
        "Long-term extension: keep the extension as its own phase with its own anchor "
        "and cadence; do not continue the core-study day numbering unless the protocol "
        "explicitly does."
    ),
    "linear": (
        "Linear: enumerate every printed visit column in order, including screening, "
        "baseline, early termination, unscheduled, telephone, and safety follow-up."
    ),
    "mixed": (
        "Mixed structure: more than one archetype applies. Model each part with the "
        "structure the protocol actually prints instead of forcing one shape."
    ),
}


def _classification_guidance(
    classification: DocumentTaskClassification | None,
    selected_schedule_option_id: str | None = None,
) -> str:
    """Turn the classification decision into stage-specific extraction rules.

    Structural authoring rules (cyclic, crossover, factorial, ...) are included
    UNCONDITIONALLY, not gated by classification.schedule_archetypes. Classify
    runs first with the least evidence of any stage in the pipeline; if it
    guesses one shape and later evidence shows another (or a blend — a cyclic
    regimen inside a multi-arm design, a crossover with an intra-day PK block),
    a gate here would silently withhold the very rules needed to model it
    correctly. So every pattern is always in play, and each later stage picks
    whichever ones its own evidence actually supports. classify's archetype
    guess still ships in the header below, but only as a hint to cross-check
    against, never as a constraint on what the model is allowed to build.
    """
    if classification is None:
        return ""
    lines: list[str] = []
    document_rule = _DOCUMENT_GUIDANCE.get(classification.document_type)
    if document_rule:
        lines.append(document_rule)
    task_rule = _TASK_GUIDANCE.get(classification.analysis_task)
    if task_rule:
        lines.append(task_rule)
    lines.append(
        "SCHEDULE STRUCTURE PATTERNS — all of the following are always in "
        "play, regardless of classification's archetype guess below. Apply "
        "whichever ones THIS protocol's own evidence actually supports, more "
        "than one at once when the design blends patterns, and ignore the "
        "rest. Do not force the protocol into a single preset shape:\n    - "
        + "\n    - ".join(_ARCHETYPE_GUIDANCE.values())
    )
    if len(classification.schedule_options) > 1:
        selected = next(
            (option for option in classification.schedule_options
             if option.id == selected_schedule_option_id), None)
        if selected is not None:
            lines.append(
                "This protocol prints more than one independent Schedule of "
                f"Assessments. The reviewer selected: '{selected.label}'"
                + (f" — {selected.description}" if selected.description else "")
                + (f" (found at {selected.source_location})"
                   if selected.source_location else "")
                + ". Build the schedule ONLY for this selected schedule: use "
                "only ITS visit table, timing, windows, and activities. Do not "
                "pull in visits, timing, or activities that belong exclusively "
                "to another listed schedule/substudy, even if they share a "
                "screening period or background therapy with the selected one.")
        else:
            options = "; ".join(
                f"{option.id} ({option.label})"
                for option in classification.schedule_options)
            lines.append(
                "This protocol prints more than one independent Schedule of "
                f"Assessments: {options}. No selection has been made yet, so "
                "record the schedule_options for review rather than merging "
                "every schedule into one.")
    if classification.has_attached_reference:
        lines.append(
            "An appended/referenced protocol was detected. Read it, and mark any fact "
            "that exists only in the appended reference with its own source_location.")
    if classification.needs_version_comparison:
        lines.append(
            "Version comparison is required. Every schedule fact must state which "
            "protocol version it belongs to.")
    if classification.complexity == "complex":
        lines.append(
            "This document was classified complex. Prefer leaving a fact unresolved "
            "over resolving it from a simpler reading than the protocol supports.")
    if not classification.has_schedule:
        lines.append(
            "Classification found no visit schedule. Do not synthesize one; if the PDF "
            "contradicts that classification, record the contradiction as a conflict.")
    if not lines:
        return ""
    header = (
        "EXTRACTION GUIDANCE (document_type=" + classification.document_type
        + ", analysis_task=" + classification.analysis_task
        + ", classification's first-pass archetype guess="
        + (', '.join(classification.schedule_archetypes) or 'none')
        + " — a hint from the stage with the least evidence, not a constraint; "
        "trust what you read yourself over this guess):"
    )
    return header + "\n- " + "\n- ".join(lines)


def _evidence_catalog(
    timing: ScheduleTimingEvidence,
    visits: ScheduleVisitEvidence,
) -> tuple[dict[str, EvidenceFact], set[str], set[str], set[str]]:
    timing_facts = [
        *timing.visit_timing, *timing.cycle_rules, *timing.relative_timing,
        *timing.open_ended_rules,
    ]
    window_facts = list(timing.visit_windows)
    visit_facts = [
        *visits.visit_columns, *visits.special_visits,
        *visits.activity_assignments, *visits.table_footnotes,
        *visits.arm_period_differences,
    ]
    all_facts = [
        *timing_facts, *window_facts, *timing.conflicts_or_unknowns,
        *visit_facts, *visits.conflicts_or_unknowns,
    ]
    catalog = {fact.evidence_id: fact for fact in all_facts}
    return (
        catalog,
        {fact.evidence_id for fact in timing_facts},
        {fact.evidence_id for fact in window_facts},
        {fact.evidence_id for fact in visit_facts},
    )


def _validate_evidence_links(
    schedule: ExtractedSchedule,
    timing: ScheduleTimingEvidence,
    visits: ScheduleVisitEvidence,
) -> list[str]:
    """Reject unsupported values instead of trusting plausible model output."""
    catalog, timing_ids, window_ids, visit_ids = _evidence_catalog(timing, visits)
    issues: list[str] = []
    evidence_ids = [
        fact.evidence_id
        for packet in (timing, visits)
        for value in packet.__dict__.values()
        if isinstance(value, list)
        for fact in value
        if isinstance(fact, EvidenceFact)
    ]
    duplicates = sorted(
        evidence_id for evidence_id, count in Counter(evidence_ids).items() if count > 1)
    if duplicates:
        issues.append("Evidence IDs are not unique: " + ", ".join(duplicates))
    expanded = expand_schedule(schedule)
    for index, visit in enumerate(expanded.visits, 1):
        label = visit.name or f"visit {index}"
        links = {
            item.field.strip().lower(): set(item.evidence_ids)
            for item in visit.field_evidence
        }
        referenced = set().union(*links.values()) if links else set()
        unknown = sorted(referenced - set(catalog))
        if unknown:
            issues.append(f"'{label}' cites unknown evidence IDs: {', '.join(unknown)}")

        def require(field: str, allowed: set[str]) -> None:
            refs = links.get(field, set())
            if not refs:
                issues.append(f"'{label}' has no evidence for {field}")
                return
            if not refs.intersection(allowed):
                issues.append(f"'{label}' cites the wrong evidence category for {field}")
            weak = [ref for ref in refs if ref in catalog and catalog[ref].confidence < MIN_ACCEPT_CONFIDENCE]
            if weak:
                issues.append(
                    f"'{label}' uses below-threshold confidence evidence for {field}: "
                    + ", ".join(sorted(weak)))

        require("name", visit_ids)
        if any(getattr(visit, field, None) is not None for field in _TIMING_FIELDS):
            require("timing", timing_ids)
        if any(value is not None for value in (
            visit.window_days, visit.window_before, visit.window_after,
        )):
            require("window", window_ids)
        if visit.activities:
            require("activities", visit_ids)
        if visit.arm:
            require("arm", visit_ids)
        if visit.period:
            require("period", visit_ids)

    # Flat rows are only a deterministic projection. Validate the richer graph
    # directly so a procedure window, recurrence, condition, or transition cannot
    # bypass evidence checks merely because the mobile table does not show it.
    plan = schedule.canonical_plan
    if plan is not None:
        known_ids = set(catalog)

        def require_canonical(
            path: str,
            refs: list[str],
            allowed: set[str] | None = None,
        ) -> None:
            refs = list(dict.fromkeys(refs))
            if not refs:
                issues.append(f"Canonical {path} has no evidence")
                return
            unknown = sorted(set(refs) - known_ids)
            if unknown:
                issues.append(
                    f"Canonical {path} cites unknown evidence IDs: "
                    + ", ".join(unknown))
            if allowed is not None and not set(refs).intersection(allowed):
                issues.append(f"Canonical {path} cites the wrong evidence category")
            weak = sorted(
                ref for ref in refs
                if ref in catalog and catalog[ref].confidence < MIN_ACCEPT_CONFIDENCE)
            if weak:
                issues.append(
                    f"Canonical {path} uses below-threshold confidence evidence: "
                    + ", ".join(weak))

        for item in plan.anchors:
            require_canonical(f"anchor {item.id}", item.evidence_ids, timing_ids)
        for item in plan.phases:
            require_canonical(f"phase {item.id}", item.evidence_ids)
        for item in plan.branches:
            require_canonical(f"branch {item.id}", item.evidence_ids)
        for item in plan.activities:
            require_canonical(f"activity {item.id}", item.evidence_ids, visit_ids)
            if item.timing is not None:
                require_canonical(
                    f"activity {item.id} timing", item.timing.evidence_ids)
            if item.window is not None and item.window.state != "not_stated":
                require_canonical(
                    f"activity {item.id} window", item.window.evidence_ids)
        for item in plan.events:
            require_canonical(f"event {item.id}", item.evidence_ids, visit_ids)
            require_canonical(
                f"event {item.id} timing", item.timing.evidence_ids, timing_ids)
            if item.window.state != "not_stated":
                require_canonical(
                    f"event {item.id} window", item.window.evidence_ids, window_ids)
        for collection_name, collection in (
            ("recurrence", plan.recurrences),
            ("transition", plan.transitions),
            ("condition", plan.conditions),
            ("conflict", plan.conflicts),
        ):
            for item in collection:
                require_canonical(
                    f"{collection_name} {item.id}", item.evidence_ids)
    return issues


def _visit_signature(visit) -> tuple:
    clean = lambda value: str(value or "").strip().lower()
    return (
        clean(visit.name), clean(visit.visit_type), visit.day_offset, visit.day_end,
        visit.hour_offset, clean(visit.hour_offset_basis), visit.hour_end,
        visit.window_days, visit.window_before, visit.window_after,
        clean(visit.relative_to), visit.relative_offset_days, clean(visit.arm),
        clean(visit.period), tuple(sorted(clean(item) for item in visit.activities)),
    )


def _visit_coverage_issues(
    candidate: ExtractedSchedule,
    visit_evidence: ScheduleVisitEvidence,
) -> list[str]:
    """Flag a schedule that built fewer visits than columns were inventoried.

    The audit LLM cannot be relied on to catch this alone: it is scored from a
    separately-retrieved page selection and is never shown the visit_columns
    evidence packet, so a collapsed wide table (the common failure mode on
    plainly-numbered columns) can pass audit unnoticed. This check needs no
    model call: it compares the schedule directly against the evidence
    catalog gathered by the deterministic full-document sweep, which is
    unaffected by whatever the synthesis/repair model chose to do.
    """
    clean = lambda value: " ".join(str(value or "").split()).casefold()
    columns = {}
    for fact in visit_evidence.visit_columns:
        key = clean(fact.claim) or clean(fact.source_quote)
        if key:
            columns.setdefault(key, fact)
    if not columns:
        return []
    visit_count = len(expand_schedule(candidate).visits)
    # Allow slack of one: a schedule may legitimately consolidate an
    # arm/period duplicate of the same column into a single visit.
    if visit_count >= len(columns) - 1:
        return []
    sample = [fact.claim or fact.source_quote for fact in list(columns.values())[:8]]
    return [
        f"Visit evidence lists {len(columns)} distinct visit column(s), but the "
        f"schedule produced only {visit_count} visit(s). Check for dropped "
        "columns, for example: " + "; ".join(sample)
    ]


_NAMED_DAY_LIST = re.compile(
    r"days?\s*\d+(?:\s*(?:,|&|and)\s*(?:days?\s*)?\d+)+",
    re.IGNORECASE,
)


def _named_day_lists(text: str) -> list[list[int]]:
    """Every 'Day 30 & 31' / 'day 27, 28, 29'-shaped span naming 2+ distinct
    days for one activity inside a free-text evidence claim or quote."""
    found = []
    for match in _NAMED_DAY_LIST.finditer(text or ""):
        days = sorted({int(value) for value in re.findall(r"\d+", match.group(0))})
        if len(days) >= 2:
            found.append(days)
    return found


def _activity_day_gap_issues(
    candidate: ExtractedSchedule,
    visit_evidence: ScheduleVisitEvidence,
) -> list[str]:
    """Flag a day the source names for an activity that has NO visit at all.

    A multi-day confinement/housing block's shared activity (a pre-dose
    sample, PK sampling) is commonly stated for a short list of specific
    days, e.g. "PK Sampling Day 30 & 31". This check catches the day being
    dropped from the schedule entirely — some named day in that list has no
    visit whose day_offset/day_end span covers it at all, while at least one
    other named day from the SAME fact does (so the fact is clearly a real,
    resolvable activity, not stale/unrelated evidence).

    This is deliberately narrower than "does the covering visit actually
    carry this activity's name" — matching free text in an evidence claim
    against a schedule's activities list is too failure-prone to gate
    verification on. A day that already has some OTHER visit (e.g. a
    check-out visit that is simply missing this one activity) is treated as
    covered and is NOT flagged here; that fuzzier, more common failure mode
    is instead the _AUDIT_PROMPT's job, since the audit LLM re-reads the PDF
    and can judge whether the right activity was actually attached.

    Also requires resolvable anchor metadata (anchor_study_day not None):
    with no stated Day 0/Day 1 convention there is no reliable way to
    convert a printed day number into the schedule's own day_offset, so the
    check stays silent rather than guessing.
    """
    anchor_study_day = candidate.anchor_study_day
    includes_day_zero = candidate.includes_day_zero
    if anchor_study_day is None:
        return []

    def expected_offset(day: int) -> int | None:
        if anchor_study_day == 1 and includes_day_zero is None and day <= 0:
            return None
        try:
            return study_day_to_offset(
                day, anchor_study_day=anchor_study_day,
                includes_day_zero=includes_day_zero)
        except ValueError:
            return None

    spans = [
        (visit.day_offset, visit.day_end if visit.day_end is not None else visit.day_offset)
        for visit in expand_schedule(candidate).visits
        if visit.day_offset is not None
    ]
    if not spans:
        return []

    def covered(day: int) -> bool:
        offset = expected_offset(day)
        if offset is None:
            return True  # convention unresolvable for this day; do not guess a gap
        return any(start <= offset <= end for start, end in spans)

    issues: list[str] = []
    seen: set[tuple[int, ...]] = set()
    for fact in (*visit_evidence.activity_assignments, *visit_evidence.visit_columns):
        text = fact.claim or fact.source_quote
        for days in _named_day_lists(text):
            key = tuple(days)
            if key in seen:
                continue
            covered_days = [day for day in days if covered(day)]
            missing_days = [day for day in days if day not in covered_days]
            if covered_days and missing_days:
                seen.add(key)
                issues.append(
                    f"Evidence '{text}' names day(s) "
                    f"{', '.join(str(d) for d in days)} for one activity, but "
                    f"day(s) {', '.join(str(d) for d in missing_days)} have no "
                    "visit at all in the schedule (day(s) "
                    f"{', '.join(str(d) for d in covered_days)} do) — check "
                    "whether that day was dropped entirely.")
    return issues


def _structural_issues(schedule: ExtractedSchedule) -> list[str]:
    expanded = expand_schedule(schedule)
    issues = list(expanded.verification_issues)
    if expanded.schedule_kind == "none" and expanded.visits:
        issues.append("schedule_kind is none but visits were produced")
    if expanded.schedule_kind not in (None, "none") and not expanded.visits:
        issues.append("a schedule kind was identified but no visits were produced")
    signatures = Counter(_visit_signature(visit) for visit in expanded.visits)
    duplicates = [row for row, count in signatures.items() if count > 1]
    for row in duplicates[:5]:
        issues.append(f"duplicate compiled visit: {row[0] or '<unnamed>'}")
    dated = [visit.day_offset for visit in expanded.visits if visit.day_offset is not None]
    if len(dated) >= 3 and len(set(dated)) == 1:
        issues.append(f"all {len(dated)} dated visits collapse onto day {dated[0]}")
    plan = schedule.canonical_plan
    if plan is not None:
        for recurrence in plan.recurrences:
            if recurrence.frequency.unit not in ("day", "week"):
                continue
            for event_id in recurrence.event_ids:
                projected = [
                    visit for visit in expanded.visits
                    if (visit.canonical_event_id or "").split("@", 1)[0] == event_id
                ]
                if projected and all(
                    visit.day_offset is None and visit.hour_offset is None
                    for visit in projected
                ):
                    event = next(
                        (item for item in plan.events if item.id == event_id), None)
                    issues.append(
                        f"recurring event '{event.name if event else event_id}' has a "
                        f"numeric {recurrence.frequency.value:g}-"
                        f"{recurrence.frequency.unit} cadence but no resolvable first "
                        "occurrence timing — anchor the first occurrence so later cycle "
                        "dates can be projected")
    # A recurrence rule wrongly used to cover individually-printed, distinctly-numbered
    # columns (Week 4, 8, 12...) can only substitute a bare 1, 2, 3... index into the
    # name, which the projection then disambiguates as "<name> (Occurrence N)" — always
    # wrong here, since it can never reproduce the protocol's actual printed number.
    garbled = sorted({
        visit.name for visit in expanded.visits
        if " (Occurrence " in visit.name and visit.name.endswith(")")
    })
    for name in garbled[:5]:
        issues.append(
            f"visit name looks recurrence-generated, not protocol-printed: '{name}' "
            "— replace the recurrence rule with individual events per printed column")
    # event_type defaults to the literal string "visit" when the model never classifies
    # it. The prompt explicitly forbids leaving it there, but that instruction is not
    # self-enforcing — catch it deterministically instead of trusting compliance.
    unclassified = sorted({
        visit.name for visit in expanded.visits
        if (visit.visit_type or "").strip().lower() == "visit"
    })
    for name in unclassified[:5]:
        issues.append(
            f"'{name}' was left at the generic default visit_type 'visit' — classify it "
            "as screening/baseline/treatment/follow_up/etc. per its role in the schedule")
    return list(dict.fromkeys(issues))


Generate = Callable[..., Awaitable[BaseModel]]
StageCheckpoint = MutableMapping[str, Any]

_CHECKPOINT_FORMAT = "canonical-schedule-agent-v1"
_CHECKPOINT_PDF_KEY = "__pdf_sha256__"
_CHECKPOINT_FORMAT_KEY = "__format__"


class ExtractionAgentState(TypedDict, total=False):
    pdf_bytes: bytes
    page_index: ProtocolDocumentIndex | None
    page_context_cache: MutableMapping[str, str]
    selected_schedule_option_id: str | None
    classification: DocumentTaskClassification
    document_map: ScheduleDocumentMap
    timing_evidence: ScheduleTimingEvidence
    visit_evidence: ScheduleVisitEvidence
    candidate: ExtractedSchedule
    deterministic_issues: list[str]
    audit: ScheduleAudit
    refinement_count: int
    max_refinements: int
    result: ExtractedSchedule
    stage_warnings: list[str]
    stop_after_stage_error: bool
    stage_checkpoint: StageCheckpoint


def build_schedule_extraction_graph(
    generate: Generate,
    *,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
):
    """Compile discovery -> evidence -> synthesis -> audit -> repair."""
    stage_max_attempts = max(1, min(int(stage_max_attempts), 5))
    retry_base_delay_seconds = max(0.0, float(retry_base_delay_seconds))

    async def run_stage(
        state: ExtractionAgentState,
        stage_key: str,
        prompt: str,
        schema,
        *,
        system_instruction: str,
        max_tokens: int,
    ):
        """Reuse a completed stage or retry only the failing provider call.

        The checkpoint is plain JSON-compatible data, so an API worker may persist
        it between attempts. A malformed cached value is ignored and regenerated;
        completed upstream stages are never paid for again during a resumed run.
        """
        checkpoint = state["stage_checkpoint"]
        cached = checkpoint.get(stage_key)
        if cached is not None:
            try:
                value = cached.model_dump() if isinstance(cached, BaseModel) else cached
                restored = schema.model_validate(value)
                log.info("schedule extraction stage %s restored from checkpoint", stage_key)
                return restored
            except (TypeError, ValueError):
                log.warning("ignoring invalid schedule extraction checkpoint %s", stage_key)
                checkpoint.pop(stage_key, None)

        last_error: BaseException | None = None
        for attempt in range(1, stage_max_attempts + 1):
            try:
                result = await generate(
                    state["pdf_bytes"], prompt, schema,
                    system_instruction=system_instruction,
                    max_tokens=max_tokens,
                )
                if not isinstance(result, schema):
                    result = schema.model_validate(
                        result.model_dump() if isinstance(result, BaseModel) else result)
                checkpoint[stage_key] = result.model_dump(mode="json")
                return result
            except ExtractionNotConfigured:
                # Missing credentials cannot recover through backoff.
                raise
            except (ExtractionError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt >= stage_max_attempts:
                    break
                delay = retry_base_delay_seconds * (2 ** (attempt - 1))
                log.warning(
                    "schedule extraction stage %s failed (attempt %d/%d); "
                    "retrying in %.2fs: %s",
                    stage_key, attempt, stage_max_attempts, delay, exc,
                )
                if delay:
                    await asyncio.sleep(delay)
        assert last_error is not None
        if isinstance(last_error, ExtractionError):
            raise last_error
        raise ExtractionError(
            f"schedule extraction stage {stage_key} failed after "
            f"{stage_max_attempts} attempt(s)") from last_error

    async def classify_node(state: ExtractionAgentState):
        # A multi-option batch (run_schedule_extraction_agent_for_all_options)
        # classifies once and reuses that result for every option's run —
        # skip the LLM call entirely when it's already been supplied.
        seeded = state.get("classification")
        if seeded is not None:
            state["stage_checkpoint"]["classify"] = seeded.model_dump(mode="json")
            return {"classification": seeded}
        classification = await run_stage(
            state, "classify",
            _stage_prompt(
                state, "classify",
                "Classify this document and the schedule-analysis task before extraction."),
            DocumentTaskClassification,
            system_instruction=_CLASSIFICATION_PROMPT,
            max_tokens=2500,
        )
        return {"classification": classification}

    async def discover_node(state: ExtractionAgentState):
        document_map = await run_stage(
            state, "discover",
            _stage_prompt(
                state, "discover",
                "CLASSIFICATION:\n" + state["classification"].model_dump_json()
                + "\n\n" + _classification_guidance(
                    state["classification"], state.get("selected_schedule_option_id"))
                + "\n\nMap the protocol sections needed to reconstruct its visit schedule."),
            ScheduleDocumentMap,
            system_instruction=_DISCOVERY_PROMPT,
            max_tokens=6000,
        )
        return {"document_map": document_map, "refinement_count": 0}

    async def evidence_sweep_node(state: ExtractionAgentState):
        """Read every page of the document, guaranteed — not a keyword guess.

        Replaces timing_node + visit_evidence_node, which both worked from a
        keyword-scored ~24-page excerpt of the whole document and could
        silently miss a fact on a page that never matched the scorer's
        vocabulary (a dose-modification/toxicity section, a schedule stated
        only in prose, a rule mentioned once outside any table). This instead
        partitions the document into fixed page chunks with a small overlap
        so nothing at a chunk boundary is missed, and runs one call per
        chunk in parallel. Every chunk's CORE pages are read by construction,
        so full coverage is a structural guarantee, not a hope — and the
        merged result is stored under the same timing_evidence/visit_evidence
        keys synthesize/audit/repair already consume, so nothing
        downstream needs to know evidence-gathering was chunked at all.
        """
        index = state.get("page_index")
        classification_block = (
            "CLASSIFICATION:\n" + state["classification"].model_dump_json()
            + "\n\nDOCUMENT MAP:\n" + state["document_map"].model_dump_json())
        guidance = _classification_guidance(
            state["classification"], state.get("selected_schedule_option_id"))

        if index is None:
            # No page index (e.g. an unparseable/scanned PDF): one call reads
            # the whole attached PDF directly, same as any other stage falls
            # back to when retrieval/chunking metadata is unavailable.
            chunk_evidence = await run_stage(
                state, "evidence_sweep:0",
                "\n\n".join(filter(None, [
                    classification_block, guidance,
                    "No page index is available for this document. Read the "
                    "entire attached PDF directly and extract every "
                    "schedule-relevant fact from it; there is no CORE/CONTEXT "
                    "page split to observe."])),
                ScheduleChunkEvidence,
                system_instruction=_CHUNK_EVIDENCE_PROMPT,
                max_tokens=_EVIDENCE_SWEEP_MAX_TOKENS,
            )
            timing, visits = _merge_chunk_evidence([chunk_evidence])
            return {"timing_evidence": timing, "visit_evidence": visits}

        chunks = chunk_protocol_pages(
            index,
            core_pages=_EVIDENCE_SWEEP_CORE_PAGES,
            overlap_pages=_EVIDENCE_SWEEP_OVERLAP_PAGES,
        )

        async def run_chunk(chunk: ProtocolPageChunk) -> ScheduleChunkEvidence:
            prompt = "\n\n".join(filter(None, [
                f"CHUNK {chunk.chunk_index + 1} of {len(chunks)} — you are "
                f"responsible for CORE pages {chunk.core_page_numbers[0]}-"
                f"{chunk.core_page_numbers[-1]} of this {index.page_count}-"
                "page document.",
                "DOCUMENT PAGES FOR THIS CHUNK (core + boundary context):\n"
                + render_page_chunk(chunk),
                classification_block,
                guidance,
            ]))
            return await run_stage(
                state, f"evidence_sweep:{chunk.chunk_index}", prompt,
                ScheduleChunkEvidence,
                system_instruction=_CHUNK_EVIDENCE_PROMPT,
                max_tokens=_EVIDENCE_SWEEP_MAX_TOKENS,
            )

        chunk_results = await asyncio.gather(*(run_chunk(chunk) for chunk in chunks))
        timing, visits = _merge_chunk_evidence(list(chunk_results))
        return {"timing_evidence": timing, "visit_evidence": visits}

    async def needs_selection_node(state: ExtractionAgentState):
        """Stop right after classification when the reviewer must pick a schedule.

        Reached only when classification found more than one independent
        Schedule of Assessments and the caller has not yet supplied
        selected_schedule_option_id. Discovery/timing/synthesis/audit would
        otherwise be spent building a schedule that merges incompatible
        substudies, so they are skipped entirely until a choice is made.
        """
        classification = state["classification"]
        options = classification.schedule_options
        note = (
            "This document contains more than one independent Schedule of "
            "Assessments ("
            + "; ".join(f"{option.id}: {option.label}" for option in options)
            + "). Extraction stopped after classification; re-run with the "
            "chosen schedule_options[].id to build that schedule."
        )
        log.info(
            "schedule extraction stopped: %d schedule options found, awaiting "
            "selection (document_type=%s)",
            len(options), classification.document_type)
        candidate = ExtractedSchedule(
            schedule_kind=None,
            visits=[],
            assumptions=[note],
            requires_schedule_selection=True,
            source_notes="; ".join(
                option.source_location for option in options
                if option.source_location) or None,
        )
        not_applicable = ScheduleAccuracyDimension(
            applicable=False, accuracy=None, passed=False, summary=note)
        audit = ScheduleAudit(
            approved=True,
            confidence=classification.confidence,
            visit_coverage=not_applicable,
            timing=not_applicable,
            windows=not_applicable,
            visit_types=not_applicable,
            procedure_mapping=not_applicable,
            overall_schedule=not_applicable,
            verified_items=list(classification.evidence),
            issues=[],
            summary=note,
        )
        return {
            "candidate": candidate,
            "deterministic_issues": [],
            "timing_evidence": ScheduleTimingEvidence(),
            "visit_evidence": ScheduleVisitEvidence(),
            "audit": audit,
        }

    async def no_schedule_node(state: ExtractionAgentState):
        """Stop schedule synthesis cleanly when the document has no schedule.

        Reached only when the classifier and the discovery map agree. An empty
        result is the correct answer here, so the later stages are skipped
        instead of being asked to audit a schedule that should not exist.
        """
        classification = state["classification"]
        note = (
            "Classification and discovery agree that this document contains no visit "
            "schedule, so schedule extraction stopped after metadata discovery.")
        log.info(
            "schedule extraction stopped: no schedule (document_type=%s task=%s)",
            classification.document_type, classification.analysis_task)
        candidate = ExtractedSchedule(
            schedule_kind="none",
            visits=[],
            assumptions=[note],
            source_notes="; ".join(
                state["document_map"].schedule_locations
                or classification.evidence) or None,
        )
        not_applicable = ScheduleAccuracyDimension(
            applicable=False, accuracy=None, passed=False, summary=note)
        audit = ScheduleAudit(
            approved=True,
            confidence=min(
                classification.confidence,
                1.0 if state["document_map"].has_schedule is False else 0.5),
            visit_coverage=not_applicable,
            timing=not_applicable,
            windows=not_applicable,
            visit_types=not_applicable,
            procedure_mapping=not_applicable,
            overall_schedule=not_applicable,
            verified_items=list(classification.evidence),
            issues=[],
            summary=note,
        )
        return {
            "candidate": candidate,
            "deterministic_issues": [],
            "timing_evidence": ScheduleTimingEvidence(),
            "visit_evidence": ScheduleVisitEvidence(),
            "audit": audit,
        }

    async def synthesize_node(state: ExtractionAgentState):
        evidence = _stage_prompt(
            state, "synthesize",
            "CLASSIFICATION:\n" + state["classification"].model_dump_json()
            + "\n\nDOCUMENT MAP:\n" + state["document_map"].model_dump_json()
            + "\n\nTIMING EVIDENCE:\n" + state["timing_evidence"].model_dump_json()
            + "\n\nVISIT/ACTIVITY EVIDENCE:\n" + state["visit_evidence"].model_dump_json()
        )
        candidate = await run_stage(
            state, "synthesize", evidence,
            ExtractedSchedule,
            system_instruction=_SYNTHESIS_PROMPT,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        return {"candidate": candidate}

    async def audit_node(state: ExtractionAgentState):
        candidate = state["candidate"]
        candidate_json = expand_schedule(candidate).model_dump_json(
            exclude={
                "verification_status",
                "verification_confidence",
                "verification_iterations",
                "verification_issues",
                "verification_scores",
            })
        # Objective, code-only checks that need no model call: unsupported or
        # missing evidence citations, malformed graph structure, and visit
        # coverage against the full evidence sweep's own column inventory.
        # These are real defects (not the noisy "two independently generated
        # schedules phrased things differently" signal a second full
        # generation used to produce), so the audit is told to treat every
        # one as confirmed and route_after_audit/finalize below still gate
        # on them directly rather than deferring entirely to the audit's
        # judgment call.
        deterministic_issues = list(dict.fromkeys([
            *_validate_evidence_links(
                candidate, state["timing_evidence"], state["visit_evidence"]),
            *_structural_issues(candidate),
            *_visit_coverage_issues(candidate, state["visit_evidence"]),
            *_activity_day_gap_issues(candidate, state["visit_evidence"]),
        ]))
        try:
            refinement = state.get("refinement_count", 0)
            audit = await run_stage(
                state, f"audit:{refinement}",
                _stage_prompt(
                    state, "audit",
                    "BUILDER SCHEDULE:\n" + candidate_json
                    + "\n\nDETERMINISTIC CHECKS (mechanically computed; treat each "
                    "as a confirmed real defect, not a hypothesis):\n"
                    + ("\n".join(deterministic_issues) or "none")
                    + "\n\nVISIT/ACTIVITY EVIDENCE (the full column inventory this "
                    "schedule was built from; use it to check visit coverage "
                    "directly instead of relying only on the schedule above):\n"
                    + state["visit_evidence"].model_dump_json()),
                ScheduleAudit,
                system_instruction=_AUDIT_PROMPT,
                max_tokens=6000,
            )
        except ExtractionError as exc:
            warning = (
                "Automated schedule verification could not be completed. "
                "Review every visit against the protocol before saving."
            )
            log.warning("schedule audit unavailable; returning review draft: %s", exc)
            return {
                "audit": state.get("audit") or _unavailable_audit(warning),
                "deterministic_issues": deterministic_issues,
                "stage_warnings": list(state.get("stage_warnings", [])) + [warning],
                "stop_after_stage_error": True,
            }
        log.info(
            "schedule agent audit: refinement=%d accepted=%s confidence=%.2f "
            "issues=%d deterministic_issues=%d",
            state.get("refinement_count", 0), audit.accepted,
            audit.confidence, len(audit.issues), len(deterministic_issues),
        )
        return {"audit": audit, "deterministic_issues": deterministic_issues}

    async def refine_node(state: ExtractionAgentState):
        candidate = state["candidate"].model_dump_json(
            exclude={
                "verification_status",
                "verification_confidence",
                "verification_iterations",
                "verification_issues",
                "verification_scores",
            })
        audit = state["audit"].model_dump_json()
        try:
            refinement = state.get("refinement_count", 0)
            repaired = await run_stage(
                state, f"repair:{refinement}",
                _stage_prompt(
                    state, "repair",
                    "CANDIDATE SCHEDULE:\n" + candidate
                    + "\n\nDETERMINISTIC CHECKS:\n"
                    + "\n".join(state.get("deterministic_issues", []))
                    + "\n\nAUDIT:\n" + audit
                    + "\n\nVISIT/ACTIVITY EVIDENCE:\n"
                    + state["visit_evidence"].model_dump_json()),
                ExtractedSchedule,
                system_instruction=_REPAIR_PROMPT,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        except ExtractionError as exc:
            warning = (
                "The automated correction pass could not be completed. The last "
                "valid schedule draft was retained for manual review."
            )
            log.warning("schedule repair unavailable; retaining candidate: %s", exc)
            return {
                "stage_warnings": list(state.get("stage_warnings", [])) + [warning],
                "stop_after_stage_error": True,
            }
        return {
            "candidate": repaired,
            "refinement_count": state.get("refinement_count", 0) + 1,
        }

    async def finalize_node(state: ExtractionAgentState):
        audit = state["audit"]
        unresolved = [
            f"Verification {issue.severity}: {issue.finding} Evidence: {issue.evidence}"
            for issue in audit.issues
            if issue.severity in ("critical", "major")
        ]
        if not audit.accepted and not unresolved:
            unresolved.append(
                "Verification did not approve this schedule, but returned no "
                "specific major finding. Review the protocol manually before saving.")
        evidence_facts = []
        for packet in (state["timing_evidence"], state["visit_evidence"]):
            for value in packet.__dict__.values():
                if isinstance(value, list):
                    evidence_facts.extend(
                        SourceEvidence.model_validate(item.model_dump())
                        for item in value if isinstance(item, EvidenceFact))
        unique_evidence = {
            item.evidence_id: item for item in evidence_facts
        }
        candidate = state["candidate"].model_copy(update={
            "classification": state["classification"],
            "evidence_facts": list(unique_evidence.values()),
        })
        stage_warnings = list(state.get("stage_warnings", []))
        unresolved.extend(stage_warnings)
        if unresolved:
            candidate = candidate.model_copy(update={
                "assumptions": list(candidate.assumptions) + unresolved,
            })
        expanded = expand_schedule(candidate)
        projection_issues = list(expanded.verification_issues)
        deterministic_issues = list(state.get("deterministic_issues", []))
        scores = audit.accuracy_scores()
        scores["deterministic_checks"] = 0.0 if deterministic_issues else 1.0
        result = expanded.model_copy(update={
            "verification_status": (
                "verified" if audit.accepted
                and not deterministic_issues
                and expanded.verification_status != "needs_review"
                else "needs_review"
            ),
            "verification_confidence": audit.confidence,
            "verification_iterations": state.get("refinement_count", 0),
            "verification_issues": (
                projection_issues
                + [issue.finding for issue in audit.issues]
                + deterministic_issues
                + stage_warnings
            ),
            "verification_scores": scores,
        })
        return {"result": result}

    def route_after_classification(state: ExtractionAgentState):
        """Stop before spending discovery/timing/synthesis on the wrong merge.

        Trusted on the classifier alone (no second-stage confirmation, unlike
        route_after_discovery): schedule_options is itself a deliberate,
        evidence-cited structured decision, and the failure mode of trusting
        it is only the same "everything merged into one schedule" outcome the
        pipeline already produced before this check existed. A single
        schedule_options entry is not treated as multiple — that just records
        the one schedule's identity and needs no reviewer choice.
        """
        classification = state["classification"]
        if (
            len(classification.schedule_options) > 1
            and not state.get("selected_schedule_option_id")
        ):
            return "needs_selection"
        return "discover"

    def route_after_discovery(state: ExtractionAgentState):
        """Let the classification change the workflow, not only the prompts.

        Schedule synthesis is skipped only when the AI classifier and the
        independent discovery map BOTH report no schedule. One of them alone is
        not enough: a classifier can misread a schedule-only appendix, and a
        discovery pass can miss a table the classifier saw.
        """
        classification = state["classification"]
        document_map = state["document_map"]
        classifier_says_none = (
            classification.analysis_task == "no_schedule"
            or not classification.has_schedule
        )
        if classifier_says_none and not document_map.has_schedule:
            return "no_schedule"
        return "evidence_sweep"

    def route_after_audit(state: ExtractionAgentState):
        if state.get("stop_after_stage_error"):
            return "finalize"
        if state["audit"].accepted and not state.get("deterministic_issues"):
            return "finalize"
        if state.get("refinement_count", 0) >= state["max_refinements"]:
            return "finalize"
        return "refine"

    def route_after_refine(state: ExtractionAgentState):
        return "finalize" if state.get("stop_after_stage_error") else "audit"

    graph = StateGraph(ExtractionAgentState)
    graph.add_node("classify", classify_node)
    graph.add_node("needs_selection", needs_selection_node)
    graph.add_node("discover", discover_node)
    graph.add_node("evidence_sweep", evidence_sweep_node)
    graph.add_node("no_schedule", no_schedule_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("audit", audit_node)
    graph.add_node("refine", refine_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify", route_after_classification,
        {"discover": "discover", "needs_selection": "needs_selection"},
    )
    graph.add_edge("needs_selection", "finalize")
    graph.add_conditional_edges(
        "discover", route_after_discovery,
        {"evidence_sweep": "evidence_sweep", "no_schedule": "no_schedule"},
    )
    graph.add_edge("no_schedule", "finalize")
    graph.add_edge("evidence_sweep", "synthesize")
    graph.add_edge("synthesize", "audit")
    graph.add_conditional_edges(
        "audit", route_after_audit,
        {"refine": "refine", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "refine", route_after_refine,
        {"audit": "audit", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()


async def run_schedule_extraction_agent(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int = 2,
    stage_checkpoint: StageCheckpoint | None = None,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    page_index: ProtocolDocumentIndex | None = None,
    selected_schedule_option_id: str | None = None,
    classification: DocumentTaskClassification | None = None,
) -> ExtractedSchedule:
    final_state = await _run_schedule_extraction_graph(
        pdf_bytes, generate, max_refinements=max_refinements,
        stage_checkpoint=stage_checkpoint,
        stage_max_attempts=stage_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        page_index=page_index,
        selected_schedule_option_id=selected_schedule_option_id,
        classification=classification)
    return final_state["result"]


async def _fan_out_schedule_options(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int = 2,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    page_index: ProtocolDocumentIndex | None = None,
    concurrency: int = 3,
) -> list[tuple[ScheduleOption | None, ExtractionAgentState]]:
    """Run the schedule-extraction graph once per independent Schedule of
    Assessments a protocol prints, returning each option's full final
    state (not just its schedule) so callers can also read byproducts
    like ``document_map`` (trial-level metadata).

    A document with a single schedule (the overwhelming majority of
    uploads) returns exactly ``[(None, final_state)]`` — identical in
    shape and cost to a single ungated run, since only the classify+
    discover stages that already detect "no selection needed" run once.
    A document with multiple independent substudy schedules
    (schedule_options) instead extracts every one of them, each with its
    own full discover/evidence_sweep/synthesize/audit
    pass, bounded to ``concurrency`` pipelines in flight at once so one
    upload doesn't fan out into an unbounded burst of concurrent
    provider calls.
    """
    if page_index is None:
        page_index = await _build_page_index(pdf_bytes)
    base_state = await _run_schedule_extraction_graph(
        pdf_bytes, generate,
        max_refinements=max_refinements,
        stage_checkpoint={},
        stage_max_attempts=stage_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        page_index=page_index,
    )
    base: ExtractedSchedule = base_state["result"]
    if not base.requires_schedule_selection:
        return [(None, base_state)]

    classification = base.classification
    options = list(classification.schedule_options) if classification else []
    if not options:
        # Classification changed its mind between the two reads of its own
        # output (should not happen, but the selection flag is the only
        # thing route_after_classification trusts) — fall back to the
        # single "please pick one" result rather than returning nothing.
        return [(None, base_state)]

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(option: ScheduleOption) -> tuple[ScheduleOption, ExtractionAgentState]:
        async with semaphore:
            try:
                state = await _run_schedule_extraction_graph(
                    pdf_bytes, generate,
                    max_refinements=max_refinements,
                                # A fresh checkpoint per option: stage keys such as
                    # "discover"/"synthesize" are not namespaced by
                    # schedule_option_id, so reusing one dict across two
                    # options would restore one option's discovery/
                    # synthesis output while building a different one.
                    stage_checkpoint={},
                    stage_max_attempts=stage_max_attempts,
                    retry_base_delay_seconds=retry_base_delay_seconds,
                    page_index=page_index,
                    classification=classification,
                    selected_schedule_option_id=option.id,
                )
            except Exception as exc:  # noqa: BLE001 — one bad substudy must not sink the batch
                log.warning(
                    "schedule extraction failed for option %s (%s): %s",
                    option.id, option.label, exc)
                state = {
                    "result": ExtractedSchedule(
                        schedule_kind=None,
                        visits=[],
                        assumptions=[f"Extraction failed for {option.label}: {exc}"],
                        verification_status="needs_review",
                        classification=classification,
                    ),
                }
            return option, state

    results = await asyncio.gather(*(run_one(option) for option in options))
    return list(results)


async def run_schedule_extraction_agent_for_all_options(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int = 2,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    page_index: ProtocolDocumentIndex | None = None,
    concurrency: int = 3,
) -> list[tuple[ScheduleOption | None, ExtractedSchedule]]:
    """Extract every independent Schedule of Assessments a protocol prints.

    See ``_fan_out_schedule_options`` for the fan-out/concurrency
    behavior this wraps.
    """
    results = await _fan_out_schedule_options(
        pdf_bytes, generate,
        max_refinements=max_refinements,
        stage_max_attempts=stage_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        page_index=page_index,
        concurrency=concurrency,
    )
    return [(option, state["result"]) for option, state in results]


async def run_protocol_extraction_agent(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int = 2,
    stage_checkpoint: StageCheckpoint | None = None,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    page_index: ProtocolDocumentIndex | None = None,
    selected_schedule_option_id: str | None = None,
) -> tuple[ExtractedTrialDetails, ExtractedSchedule]:
    """Extract metadata and schedule from one shared decomposed model workflow."""
    final_state = await _run_schedule_extraction_graph(
        pdf_bytes, generate, max_refinements=max_refinements,
        stage_checkpoint=stage_checkpoint,
        stage_max_attempts=stage_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        page_index=page_index,
        selected_schedule_option_id=selected_schedule_option_id)
    schedule = final_state["result"]
    # document_map does not exist when extraction stopped early at
    # needs_selection — the schedule requires a reviewer choice before any
    # metadata discovery ran, so trial details are returned empty.
    document_map = final_state.get("document_map")
    if document_map is None:
        return ExtractedTrialDetails(), schedule
    return _details_from_document_map(document_map, schedule), schedule


def _details_from_document_map(
    document_map: "ScheduleDocumentMap", schedule: ExtractedSchedule,
) -> ExtractedTrialDetails:
    status = document_map.study_status.lower().strip()
    if status not in ("active", "completed", "terminated"):
        status = "active"
    return ExtractedTrialDetails(
        ctri_number=document_map.ctri_number,
        title=document_map.official_title,
        phase=document_map.phase,
        indications=document_map.indications,
        drug=document_map.investigational_drug,
        duration=document_map.planned_duration,
        target_enrollment=document_map.target_enrollment,
        total_visits=document_map.stated_total_visits or len(schedule.visits),
        status=status,
    )


async def run_protocol_extraction_agent_for_all_options(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int = 2,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    page_index: ProtocolDocumentIndex | None = None,
    concurrency: int = 3,
) -> tuple[ExtractedTrialDetails, list[tuple[ScheduleOption | None, ExtractedSchedule]]]:
    """Extract trial metadata once and every independent Schedule of
    Assessments a protocol prints, from one shared decomposed workflow.

    Mirrors ``run_schedule_extraction_agent_for_all_options``, but also
    surfaces the trial-level metadata (title/phase/drug/...) the discover
    stage produces as a byproduct of the first option's run — those facts
    describe the protocol as a whole and don't vary by which substudy
    schedule is being extracted, so one option's document_map is enough.
    """
    results = await _fan_out_schedule_options(
        pdf_bytes, generate,
        max_refinements=max_refinements,
        stage_max_attempts=stage_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
        page_index=page_index,
        concurrency=concurrency,
    )
    schedules = [(option, state["result"]) for option, state in results]
    document_map = results[0][1].get("document_map")
    if document_map is None:
        details = ExtractedTrialDetails()
    else:
        details = _details_from_document_map(document_map, schedules[0][1])
    return details, schedules


def _page_index_cache() -> ProtocolDocumentIndexCache | None:
    """Content-addressed index cache, when the deployment configured one.

    Page text is protocol content, so the directory must be private and follow
    the same retention policy as the source PDF. Without the setting the index
    is simply rebuilt per request.
    """
    directory = os.getenv("PROTOCOL_PAGE_INDEX_CACHE_DIR", "").strip()
    return ProtocolDocumentIndexCache(directory) if directory else None


async def _build_page_index(pdf_bytes: bytes) -> ProtocolDocumentIndex | None:
    """Index the PDF text once for the whole graph, or continue without it.

    Retrieval only narrows what each stage reads. A PDF that cannot be indexed
    (scanned, encrypted, malformed text layer) must still be extractable from
    the attached document, so an indexing failure is logged, never raised.
    """
    cache = _page_index_cache()
    try:
        if cache is not None:
            return await asyncio.to_thread(cache.get_or_build, pdf_bytes)
        return await asyncio.to_thread(build_protocol_document_index, pdf_bytes)
    except PdfIndexingError as exc:
        log.warning("protocol page index unavailable; using the PDF alone: %s", exc)
    except OSError as exc:
        # An unwritable cache must not cost the caller its extraction.
        log.warning("protocol page index cache unusable: %s", exc)
        try:
            return await asyncio.to_thread(build_protocol_document_index, pdf_bytes)
        except Exception as inner:
            log.warning("protocol page index failed: %s", inner)
    except Exception as exc:  # a retrieval aid must never block extraction
        log.warning("protocol page index failed unexpectedly: %s", exc, exc_info=True)
    return None


async def _run_schedule_extraction_graph(
    pdf_bytes: bytes,
    generate: Generate,
    *,
    max_refinements: int,
    stage_checkpoint: StageCheckpoint | None = None,
    stage_max_attempts: int = 3,
    retry_base_delay_seconds: float = 0.25,
    page_index: ProtocolDocumentIndex | None = None,
    selected_schedule_option_id: str | None = None,
    classification: DocumentTaskClassification | None = None,
) -> ExtractionAgentState:
    checkpoint = stage_checkpoint if stage_checkpoint is not None else {}
    pdf_digest = hashlib.sha256(pdf_bytes).hexdigest()
    checkpoint_digest = checkpoint.get(_CHECKPOINT_PDF_KEY)
    if checkpoint_digest is not None and checkpoint_digest != pdf_digest:
        raise ValueError("stage checkpoint belongs to a different protocol PDF")
    checkpoint_format = checkpoint.get(_CHECKPOINT_FORMAT_KEY)
    if checkpoint_format not in (None, _CHECKPOINT_FORMAT):
        # Code/schema changes invalidate stage values, but the caller's mapping
        # remains the checkpoint container for the new run.
        for key in list(checkpoint):
            if not str(key).startswith("__"):
                checkpoint.pop(key, None)
    checkpoint[_CHECKPOINT_PDF_KEY] = pdf_digest
    checkpoint[_CHECKPOINT_FORMAT_KEY] = _CHECKPOINT_FORMAT
    graph = build_schedule_extraction_graph(
        generate,
        stage_max_attempts=stage_max_attempts,
        retry_base_delay_seconds=retry_base_delay_seconds,
    )
    if page_index is None:
        page_index = await _build_page_index(pdf_bytes)
    elif page_index.document_sha256 != pdf_digest:
        raise ValueError("the supplied page index belongs to a different protocol PDF")
    initial_state: ExtractionAgentState = {
        "pdf_bytes": pdf_bytes,
        "page_index": page_index,
        "page_context_cache": {},
        "max_refinements": max(0, min(max_refinements, 3)),
        "stage_checkpoint": checkpoint,
        "selected_schedule_option_id": (selected_schedule_option_id or "").strip() or None,
    }
    if classification is not None:
        initial_state["classification"] = classification
    return await graph.ainvoke(initial_state)
