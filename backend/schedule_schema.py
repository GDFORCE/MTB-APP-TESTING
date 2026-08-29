"""Canonical, evidence-backed protocol schedule schema (version 2).

The mobile editor still consumes flattened visit rows.  This module preserves
the richer protocol meaning first, so calendar months, event-driven visits,
recurrences, activities, and conflicts are not destroyed during extraction.
"""
from __future__ import annotations

import math
import re
import calendar
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SourceEvidence(BaseModel):
    evidence_id: str
    page_evidence_id: str = ""
    claim: str
    source_location: str
    source_quote: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_id", "claim", "source_location", "source_quote")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence fields cannot be blank")
        return value


class ScheduleOption(BaseModel):
    """One independently generatable schedule inside a multi-substudy protocol.

    A protocol with several arms sharing ONE Schedule of Assessments table is
    still a single schedule (see schedule_archetypes "multi_arm"). This model
    is only for the different case: a document that prints more than one
    genuinely separate Schedule of Assessments/Activities/Events table — for
    example distinct substudies, sub-protocols, or phase-specific appendices
    each with their own visit list, duration, and population — where merging
    every table into one graph would silently combine incompatible timelines.
    """

    id: str = Field(
        description="Stable slug unique within this document, e.g. 'ssa-p2', "
        "'ss3-m'. Derive it from the protocol's own short code when printed.")
    label: str = Field(
        description="Human label using the protocol's own naming, e.g. "
        "'Substudy A – Phase 2 (SSA-P2)' or 'Maintenance (SS3-M)'.")
    description: str = Field(
        default="",
        description="One short sentence distinguishing this schedule: "
        "population, duration, or purpose, e.g. '66-week Phase 2 induction "
        "and extension for treatment-naive subjects'.")
    source_location: str = Field(
        default="",
        description="Where this schedule's own table lives, e.g. 'Table 30, "
        "pages 156-159' or 'Appendix 1, Schedule of Assessments for SS3-M'.")

    @field_validator("id", "label")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("schedule option id/label cannot be blank")
        return value


class DocumentTaskClassification(BaseModel):
    """The AI's explicit decision about what document and schedule it is reading."""

    document_type: Literal[
        "protocol", "amendment", "synopsis", "schedule_only", "reference",
        "mixed", "unrelated",
    ]
    analysis_task: Literal[
        "full_protocol_schedule", "amendment_comparison", "schedule_table_only",
        "no_schedule",
    ]
    schedule_archetypes: list[Literal[
        "linear", "cyclic", "crossover", "factorial", "multi_arm", "multi_phase",
        "event_driven", "intra_day", "long_term_extension", "mixed",
    ]] = Field(default_factory=list)
    complexity: Literal["simple", "moderate", "complex"]
    has_schedule: bool
    has_attached_reference: bool = False
    needs_version_comparison: bool = False
    schedule_options: list[ScheduleOption] = Field(
        default_factory=list,
        description="Populated ONLY when this document prints more than one "
        "independent Schedule of Assessments/Activities/Events (distinct "
        "substudies/sub-protocols, each with their own visit list and "
        "duration) that a reviewer must choose between before extraction. "
        "Leave empty for a single schedule, even a multi-arm/multi-phase one.")
    protocol_id: str = ""
    protocol_version: str = ""
    amendment_identifier: str = ""
    jurisdiction: str = ""
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    reasoning: str = ""


_UNIT_SYNONYMS = {
    "min": "minute", "mins": "minute", "minutes": "minute",
    "hr": "hour", "hrs": "hour", "hours": "hour", "h": "hour",
    "d": "day", "days": "day",
    "wk": "week", "wks": "week", "weeks": "week", "w": "week",
    "mo": "month", "mon": "month", "months": "month",
    "yr": "year", "yrs": "year", "years": "year", "y": "year",
}


class TemporalAmount(BaseModel):
    value: float
    unit: Literal["minute", "hour", "day", "week", "month", "year"]

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value):
        """Accept the plural/abbreviated units a model naturally writes.

        Pure normalisation: "days" and "day" mean the same duration, so this
        changes no meaning and only stops a whole schedule failing over a
        spelling the schema did not enumerate.
        """
        if isinstance(value, str):
            key = value.strip().lower()
            return _UNIT_SYNONYMS.get(key, key)
        return value


class TimingExpression(BaseModel):
    kind: Literal[
        "offset", "calendar_offset", "range", "relative", "event_driven",
        "constraint", "recurrence", "unresolved",
    ]
    anchor_id: str | None = None
    offset: TemporalAmount | None = None
    range_start: TemporalAmount | None = None
    range_end: TemporalAmount | None = None
    relation: Literal["before", "after", "on", "within", "between"] | None = None
    qualifier: Literal[
        "exact", "approximate", "minimum", "maximum", "up_to", "as_needed",
    ] | None = None
    calendar_mode: Literal["elapsed", "calendar"] | None = None
    source_label: str = ""
    alternative_source_labels: list[str] = Field(default_factory=list)
    weekday_rule: str = ""
    notes: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def downgrade_unsupported_shape(cls, data):
        """Keep an under-specified timing as 'unresolved' instead of failing.

        Procedure prose such as "pre-dose", "at each visit" or "as clinically
        indicated" has no numeric offset and no anchor, and a model routinely
        labels it 'offset' or 'relative' anyway. Rejecting it discarded the
        whole schedule over a field that carries no value to lose: the source
        text survives in source_label and the fact stays unresolved for review,
        which is exactly how an unknown is meant to be represented. Nothing is
        invented here — a claim the payload never supported is simply dropped.
        """
        if not isinstance(data, dict):
            return data
        kind = data.get("kind")
        reason = ""
        if kind in ("offset", "calendar_offset", "relative") and data.get("offset") is None:
            reason = f"no offset amount was supplied for '{kind}' timing"
        elif kind == "range" and (
            data.get("range_start") is None or data.get("range_end") is None
        ):
            reason = "a range was supplied without both of its ends"
        elif kind in ("relative", "event_driven") and not data.get("anchor_id"):
            reason = f"no anchor was supplied for '{kind}' timing"
        if not reason:
            return data
        data = dict(data)
        data["kind"] = "unresolved"
        note = f"Timing left unresolved: {reason}."
        existing = str(data.get("notes") or "").strip()
        data["notes"] = f"{existing} {note}".strip() if existing else note
        return data

    @model_validator(mode="after")
    def required_shape(self):
        if self.kind == "calendar_offset":
            self.calendar_mode = "calendar"
        return self


class WindowSpec(BaseModel):
    scope: Literal["visit", "activity"] = "visit"
    window_type: Literal[
        "tolerance", "validity", "lookback", "minimum_gap", "maximum_gap", "other",
    ] = "tolerance"
    state: Literal["stated", "not_stated", "unclear", "conflicting"]
    early: TemporalAmount | None = None
    late: TemporalAmount | None = None
    source_label: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def downgrade_valueless_stated_window(cls, data):
        """A 'stated' window with no amounts becomes 'unclear', not an error.

        The model asserting a window exists while giving no magnitude is the
        window-shaped twin of an offset with no number. Downgrading to
        'unclear' keeps that assertion visible and forces review, whereas
        inventing a default would breach the no-manufactured-window rule and
        rejecting it would discard the entire schedule.
        """
        if not isinstance(data, dict):
            return data
        if data.get("state") != "stated":
            return data
        if data.get("early") is not None or data.get("late") is not None:
            return data
        data = dict(data)
        data["state"] = "unclear"
        if not str(data.get("source_label") or "").strip():
            data["source_label"] = "A window was reported but no magnitude was given"
        return data

    @model_validator(mode="after")
    def stated_window_has_value(self):
        for amount in (self.early, self.late):
            if amount is not None and amount.value < 0:
                raise ValueError("window magnitudes must be non-negative")
        return self


class ScheduleAnchor(BaseModel):
    id: str
    name: str
    anchor_type: Literal[
        "consent", "screening", "randomization", "first_dose", "dose",
        "cycle_start", "period_start", "last_dose", "end_of_treatment",
        "discharge", "progression", "other",
    ]
    source_label: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class SchedulePhase(BaseModel):
    id: str
    name: str
    phase_type: Literal[
        "screening", "run_in", "treatment", "washout", "follow_up",
        "extension", "other",
    ]
    parent_phase_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleBranch(BaseModel):
    id: str
    name: str
    branch_type: str = Field(
        description="Kind of grouping this branch represents. Use the protocol's own "
        "term when it doesn't fit these common cases: 'arm' (a treatment arm/cohort "
        "distinguished by drug or dose), 'period' (a period/phase within a crossover "
        "or multi-phase design), 'sequence' (a randomized treatment order in a "
        "crossover), 'cohort' (a dose-escalation or expansion cohort). Free text is "
        "fine for a structure that doesn't match these, e.g. 'dose_level' or "
        "'sub_study' — never force a genuinely different grouping into one of these "
        "labels.")
    parent_branch_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleCondition(BaseModel):
    id: str
    expression: str
    applies_to_ids: list[str] = Field(default_factory=list)
    occurrence_numbers: list[int] = Field(
        default_factory=list,
        description="Optional recurrence occurrences to which this condition applies, "
        "for example cycles 2, 4, and 6. Empty means every occurrence.",
    )
    applies_to_branch_ids: list[str] = Field(
        default_factory=list,
        description="Optional arm/cohort branch ids this condition is scoped to. Use "
        "this for a FACTORIAL design's factor-specific activity or event — e.g. "
        "'Drug A dispensing' applies only to the arms that include factor A — instead "
        "of duplicating the activity or event once per arm combination. Empty means "
        "every arm.",
    )
    evidence_ids: list[str] = Field(default_factory=list)


class ActivityTemplate(BaseModel):
    id: str
    name: str
    timing: TimingExpression | None = None
    window: WindowSpec | None = None
    conditional_text: str = ""
    operational_constraints: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleEvent(BaseModel):
    id: str
    name: str
    event_type: str = Field(
        default="visit",
        description="The visit's category, using the protocol's own 'Visit Type' row/codes "
        "when present (e.g. 'SS' study-site, 'V' virtual, 'T/C' telephone). Otherwise use "
        "'screening', 'baseline', 'randomization', 'treatment', 'follow_up', "
        "'end_of_treatment', 'end_of_study', 'early_termination', 'unscheduled', or "
        "'telephonic' (a phone/telephone-icon contact) based on the visit's role in the "
        "schedule. Never leave this at the generic default 'visit' when the protocol or "
        "its position in the schedule (first visit, last visit, phone-only contact) makes a "
        "more specific category determinable.")
    phase_id: str | None = None
    arm_id: str | None = None
    period_id: str | None = None
    timing: TimingExpression
    window: WindowSpec = Field(default_factory=lambda: WindowSpec(state="not_stated"))
    activity_ids: list[str] = Field(default_factory=list)
    required: bool = True
    conditional_text: str = ""
    operational_constraints: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RecurrenceRule(BaseModel):
    id: str
    event_ids: list[str]
    frequency: TemporalAmount
    start_occurrence: int = Field(default=1, ge=1)
    end_occurrence: int | None = Field(default=None, ge=1)
    until_event_id: str | None = None
    source_label: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class TransitionRule(BaseModel):
    id: str
    from_event_id: str
    to_event_id: str
    relation: str = Field(
        description="How these two events relate, in the protocol's own terms when "
        "the common cases don't fit: 'before', 'after', 'same_day', 'minimum_gap', "
        "'maximum_gap'. Free text is fine, e.g. 'concurrent with' or 'no sooner "
        "than' — this field is descriptive only, never invent a category that "
        "misstates what the protocol says.")
    amount: TemporalAmount | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ScheduleConflict(BaseModel):
    id: str
    field_path: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    resolution: str = ""
    status: Literal["unresolved", "resolved"] = "unresolved"


class CanonicalSchedulePlan(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    protocol_id: str = ""
    protocol_version: str = ""
    title: str = ""
    anchors: list[ScheduleAnchor] = Field(default_factory=list)
    phases: list[SchedulePhase] = Field(default_factory=list)
    branches: list[ScheduleBranch] = Field(default_factory=list)
    events: list[ScheduleEvent] = Field(default_factory=list)
    activities: list[ActivityTemplate] = Field(default_factory=list)
    recurrences: list[RecurrenceRule] = Field(default_factory=list)
    transitions: list[TransitionRule] = Field(default_factory=list)
    conditions: list[ScheduleCondition] = Field(default_factory=list)
    conflicts: list[ScheduleConflict] = Field(default_factory=list)


def _stable_id(prefix: str, label: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    return f"{prefix}-{slug or index}-{index}"


def canonical_from_flat(schedule) -> CanonicalSchedulePlan:
    """Lossless-enough fallback when a provider omits the canonical graph."""
    anchors = [ScheduleAnchor(
        id="anchor-baseline", name="Baseline / schedule origin",
        anchor_type="first_dose", source_label=(
            f"Day {schedule.anchor_study_day}"
            if schedule.anchor_study_day is not None else "Schedule origin"),
    )]
    events: list[ScheduleEvent] = []
    activities: list[ActivityTemplate] = []
    activity_by_name: dict[str, str] = {}
    branch_specs = []
    branch_ids: dict[tuple[str, str], str] = {}
    for visit in schedule.visits:
        for branch_type, value in (("arm", visit.arm), ("period", visit.period)):
            if value and (branch_type, value) not in branch_ids:
                branch_id = _stable_id(branch_type, value, len(branch_ids) + 1)
                branch_ids[(branch_type, value)] = branch_id
                branch_specs.append(ScheduleBranch(
                    id=branch_id, name=value, branch_type=branch_type))
    indexed_event_ids = [
        _stable_id("event", visit.name, index)
        for index, visit in enumerate(schedule.visits, 1)
    ]
    event_ids_by_name: dict[str, list[str]] = {}
    for visit, event_id in zip(schedule.visits, indexed_event_ids):
        event_ids_by_name.setdefault(visit.name.strip().lower(), []).append(event_id)
    for index, visit in enumerate(schedule.visits, 1):
        event_id = indexed_event_ids[index - 1]
        evidence_ids = sorted({eid for link in visit.field_evidence for eid in link.evidence_ids})
        if visit.relative_to:
            timing = TimingExpression(
                kind="relative", anchor_id=(
                    (event_ids_by_name.get(visit.relative_to.strip().lower()) or [None])[0]
                    or _stable_id("event", visit.relative_to, 0)),
                offset=TemporalAmount(value=visit.relative_offset_days or 0, unit="day"),
                relation="after" if (visit.relative_offset_days or 0) >= 0 else "before",
                source_label=visit.source_day_label or "", evidence_ids=evidence_ids)
        elif visit.hour_offset is not None:
            timing = TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=visit.hour_offset, unit="hour"),
                source_label=visit.source_day_label or "", evidence_ids=evidence_ids)
        elif visit.day_offset is not None:
            timing = TimingExpression(
                kind="offset", anchor_id="anchor-baseline",
                offset=TemporalAmount(value=visit.day_offset, unit="day"),
                source_label=visit.source_day_label or "", evidence_ids=evidence_ids)
        else:
            timing = TimingExpression(
                kind="unresolved", source_label=visit.source_day_label or "-",
                evidence_ids=evidence_ids)
        if visit.window_before is not None or visit.window_after is not None:
            window = WindowSpec(
                state="stated",
                early=TemporalAmount(value=visit.window_before or 0, unit="day"),
                late=TemporalAmount(value=visit.window_after or 0, unit="day"),
                evidence_ids=evidence_ids)
        elif visit.window_days is not None:
            amount = TemporalAmount(value=visit.window_days, unit="day")
            window = WindowSpec(state="stated", early=amount, late=amount,
                                evidence_ids=evidence_ids)
        else:
            window = WindowSpec(state="not_stated")
        ids = []
        for name in visit.activities:
            if name not in activity_by_name:
                aid = _stable_id("activity", name, len(activity_by_name) + 1)
                activity_by_name[name] = aid
                activities.append(ActivityTemplate(id=aid, name=name, evidence_ids=evidence_ids))
            ids.append(activity_by_name[name])
        events.append(ScheduleEvent(
            id=event_id, name=visit.name, event_type=visit.visit_type or "visit",
            period_id=branch_ids.get(("period", visit.period)),
            arm_id=branch_ids.get(("arm", visit.arm)), timing=timing, window=window,
            activity_ids=ids, evidence_ids=evidence_ids))
    recurrences = []
    for index, block in enumerate(schedule.repeating_blocks, 1):
        recurrences.append(RecurrenceRule(
            id=f"recurrence-{index}", event_ids=[],
            frequency=TemporalAmount(value=block.cycle_length_days, unit="day"),
            start_occurrence=block.from_cycle, end_occurrence=block.to_cycle,
            source_label=f"Cycles {block.from_cycle}-"
                         f"{block.to_cycle if block.to_cycle is not None else 'open ended'}"))
    return CanonicalSchedulePlan(
        protocol_id=(schedule.classification.protocol_id if schedule.classification else ""),
        protocol_version=(schedule.classification.protocol_version if schedule.classification else ""),
        anchors=anchors, branches=branch_specs, events=events, activities=activities,
        recurrences=recurrences)


def validate_canonical_plan(
    plan: CanonicalSchedulePlan,
    evidence_ids: set[str] | None = None,
) -> list[str]:
    """Deterministic integrity checks; issues force human review."""
    issues: list[str] = []
    groups = {
        "anchor": [item.id for item in plan.anchors],
        "phase": [item.id for item in plan.phases],
        "branch": [item.id for item in plan.branches],
        "event": [item.id for item in plan.events],
        "activity": [item.id for item in plan.activities],
        "recurrence": [item.id for item in plan.recurrences],
        "transition": [item.id for item in plan.transitions],
        "condition": [item.id for item in plan.conditions],
        "conflict": [item.id for item in plan.conflicts],
    }
    all_ids: list[str] = []
    for kind, ids in groups.items():
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            issues.append(f"Duplicate {kind} IDs: {', '.join(duplicates)}")
        all_ids.extend(ids)
    duplicates = sorted({item for item in all_ids if all_ids.count(item) > 1})
    if duplicates:
        issues.append("IDs must be globally unique: " + ", ".join(duplicates))
    anchors, phases = set(groups["anchor"]), set(groups["phase"])
    branches = set(groups["branch"])
    events, activities = set(groups["event"]), set(groups["activity"])
    for event in plan.events:
        if event.phase_id and event.phase_id not in phases:
            issues.append(f"{event.id} references unknown phase {event.phase_id}")
        for label, branch_id in (("arm", event.arm_id), ("period", event.period_id)):
            if branch_id and branch_id not in branches:
                issues.append(f"{event.id} references unknown {label} branch {branch_id}")
        if event.timing.anchor_id and event.timing.anchor_id not in anchors | events:
            issues.append(f"{event.id} references unknown timing anchor {event.timing.anchor_id}")
        missing = sorted(set(event.activity_ids) - activities)
        if missing:
            issues.append(f"{event.id} references unknown activities: {', '.join(missing)}")
    for rule in plan.recurrences:
        missing = sorted(set(rule.event_ids) - events)
        if missing:
            issues.append(f"{rule.id} references unknown events: {', '.join(missing)}")
        if rule.end_occurrence is not None and rule.end_occurrence < rule.start_occurrence:
            issues.append(f"{rule.id} ends before it starts")
        if rule.end_occurrence is None and not rule.until_event_id:
            issues.append(f"{rule.id} is open-ended and requires reviewer confirmation")
    for rule in plan.transitions:
        if rule.from_event_id not in events or rule.to_event_id not in events:
            issues.append(f"{rule.id} references an unknown transition event")
    target_ids = set(all_ids)
    for condition in plan.conditions:
        missing = sorted(set(condition.applies_to_ids) - target_ids)
        if missing:
            issues.append(f"{condition.id} references unknown targets: {', '.join(missing)}")
        missing_branches = sorted(set(condition.applies_to_branch_ids) - target_ids)
        if missing_branches:
            issues.append(
                f"{condition.id} references unknown branches: "
                + ", ".join(missing_branches))
        invalid_occurrences = sorted({
            item for item in condition.occurrence_numbers if item < 1
        })
        if invalid_occurrences:
            issues.append(
                f"{condition.id} has invalid occurrence numbers: "
                + ", ".join(str(item) for item in invalid_occurrences))
    for conflict in plan.conflicts:
        if conflict.status == "unresolved":
            issues.append(f"Unresolved source conflict at {conflict.field_path}: {conflict.description}")
    if evidence_ids is not None:
        referenced: set[str] = set()
        for collection in (
            plan.anchors, plan.phases, plan.branches, plan.events, plan.activities,
            plan.recurrences, plan.transitions, plan.conditions, plan.conflicts,
        ):
            for item in collection:
                referenced.update(item.evidence_ids)
                timing = getattr(item, "timing", None)
                window = getattr(item, "window", None)
                if timing:
                    referenced.update(timing.evidence_ids)
                if window:
                    referenced.update(window.evidence_ids)
        unknown = sorted(referenced - evidence_ids)
        if unknown:
            issues.append("Canonical graph cites unknown evidence IDs: " + ", ".join(unknown))
    return list(dict.fromkeys(issues))


def apply_temporal_amount(
    anchor: date | datetime,
    amount: TemporalAmount,
    *,
    direction: int = 1,
) -> date | datetime:
    """Apply protocol timing without approximating calendar months or leap years."""
    value = amount.value * direction
    if amount.unit == "minute":
        return anchor + timedelta(minutes=value)
    if amount.unit == "hour":
        return anchor + timedelta(hours=value)
    if amount.unit == "day":
        return anchor + timedelta(days=value)
    if amount.unit == "week":
        return anchor + timedelta(weeks=value)
    if not float(value).is_integer():
        raise ValueError("calendar month/year offsets must be whole numbers")
    months = int(value) * (12 if amount.unit == "year" else 1)
    absolute_month = anchor.year * 12 + (anchor.month - 1) + months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def format_temporal_amount(amount: TemporalAmount | None) -> str:
    if amount is None:
        return ""
    value = int(amount.value) if float(amount.value).is_integer() else amount.value
    unit = amount.unit if abs(amount.value) == 1 else f"{amount.unit}s"
    return f"{value} {unit}"


def format_window(window: WindowSpec | None) -> str:
    if window is None or window.state == "not_stated":
        return ""
    if window.state != "stated":
        return window.source_label or window.state.replace("_", " ")
    if window.source_label:
        return window.source_label
    early, late = window.early, window.late
    if early and late and early == late:
        return f"±{format_temporal_amount(early)}"
    pieces = []
    if early:
        pieces.append(f"-{format_temporal_amount(early)}")
    if late:
        pieces.append(f"+{format_temporal_amount(late)}")
    return "/".join(pieces)


# A qualified or ranged timing statement bounds a visit; it does not fix its
# day. Projecting "within 28 days before randomization" as a plain Day -28 row
# would silently turn a permitted window into an appointment.
_INEXACT_QUALIFIERS = {"approximate", "minimum", "maximum", "up_to", "as_needed"}
_INEXACT_RELATIONS = {"within", "between"}


def _inexact_timing_note(timing: TimingExpression) -> str:
    """Describe a bounded/approximate timing, or '' when the day is exact."""
    if timing.kind == "range":
        bounds = " to ".join(part for part in (
            format_temporal_amount(timing.range_start),
            format_temporal_amount(timing.range_end),
        ) if part)
        return f"Timing is a range, not an exact day: {timing.source_label or bounds}"
    qualified = timing.qualifier in _INEXACT_QUALIFIERS
    bounded = timing.relation in _INEXACT_RELATIONS
    if not qualified and not bounded:
        return ""
    descriptor = timing.source_label.strip() or " ".join(part for part in (
        (timing.relation or "").replace("_", " "),
        format_temporal_amount(timing.offset),
    ) if part)
    qualifier_text = (timing.qualifier or timing.relation or "").replace("_", " ")
    return (
        f"Timing is bounded, not an exact day ({qualifier_text}): {descriptor}"
    ).strip()


# Protocols list assessments, paperwork and site logistics in one column of the
# Schedule of Assessments. The visit editor separates clinical work from
# administrative work, so the split happens here, deterministically.
#
# Matching is deliberately conservative and defaults to CLINICAL: mis-filing a
# real assessment as paperwork is the dangerous direction, and a coordinator
# can move a row in the editor. Patterns are word-anchored so "breakfast" does
# not match "fast" and "reconsent" still matches "consent".
_ADMIN_TASK_PATTERNS: tuple[str, ...] = (
    # Regulatory / documentation
    r"consent", r"\bassent\b", r"\be?crf\b", r"case report form",
    r"source (?:data|document)", r"data entry", r"quer(?:y|ies)",
    r"protocol deviation", r"\bdemographics?\b", r"\bdiary (?:issue|review|collection|dispens\w*)",
    # Enrolment / allocation
    r"randomi[sz]", r"\biwrs\b", r"\bivrs\b", r"\brtsm\b", r"enrol", r"registration",
    r"subject (?:number|id)", r"screening number", r"eligibilit",
    r"inclusion", r"exclusion",
    # Drug handling and compliance
    r"accountabilit", r"dispens", r"drug return", r"\bcompliance\b",
    r"pill count", r"tablet count",
    # Site logistics — real work, but not a clinical assessment
    r"\bhousing\b", r"confine", r"check[- ]?in", r"check[- ]?out",
    r"\badmission\b", r"\badmitted\b", r"\bdischarge\b", r"overnight",
    r"ambulator", r"\bwashout\b", r"\bfast(?:ing)?\b", r"\bmeals?\b",
    r"reimburse", r"\btravel\b", r"appointment", r"schedul",
)

_ADMIN_TASK_RE = re.compile("|".join(_ADMIN_TASK_PATTERNS), re.IGNORECASE)


def classify_visit_activities(activities: list[str]) -> tuple[list[str], list[str]]:
    """Split protocol activities into (clinical_tasks, admin_tasks).

    Order and wording are preserved exactly — this only routes each item to a
    column. Duplicates are dropped case-insensitively because a schedule table
    and its footnotes often name the same procedure twice.
    """
    clinical: list[str] = []
    admin: list[str] = []
    seen: set[str] = set()
    for activity in activities:
        name = str(activity or "").strip()
        if not name:
            continue
        key = " ".join(name.split()).casefold()
        if key in seen:
            continue
        seen.add(key)
        (admin if _ADMIN_TASK_RE.search(name) else clinical).append(name)
    return clinical, admin


def _elapsed_days(amount: TemporalAmount | None) -> float | None:
    if amount is None:
        return None
    factors = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7}
    factor = factors.get(amount.unit)
    return amount.value * factor if factor is not None else None


def _window_side_days(amount: TemporalAmount | None) -> int | None:
    """Whole-day count for one side of a visit window, via the same exact
    minute/hour/day/week conversion as _elapsed_days — a window of "+/- 1
    week" IS 7 days, unlike an ordinal "Week N" visit label, which is never
    assumed to mean 7 days. None when the unit is month/year (a month's
    day-count varies) or the amount does not land on a whole day; the caller
    falls back to a readable operational_constraint note in that case."""
    days = _elapsed_days(amount)
    if days is None or not float(days).is_integer():
        return None
    return int(days)


# A calendar-unit timing ("Month 3") states no day count — the protocol only
# ever gives the unit. Leaving day_offset null is honest but leaves the visit
# undated in every calendar/month-based schedule (seamless long-term-extension
# and cardiopulmonary-outcome designs commonly use nothing but Month N). A
# 30-day month / 365-day year is the standard clinical-scheduling convention
# used elsewhere for visit-window planning, so it is used here too, but only
# to populate the displayable day number — source_day_label keeps the
# protocol's own "Month 3" text, and build_row attaches a note wherever this
# approximation is actually used so a reviewer can tell an estimate from a
# protocol-stated day.
_CALENDAR_APPROX_DAYS = {"month": 30, "year": 365}


def _calendar_elapsed_days(amount: TemporalAmount | None) -> float | None:
    exact = _elapsed_days(amount)
    if exact is not None or amount is None:
        return exact
    factor = _CALENDAR_APPROX_DAYS.get(amount.unit)
    return amount.value * factor if factor is not None else None


# ─────────────────── printed "Day N" label <-> offset conversion ───────────────────
# Deterministic, no network, no API key. Shared by both schedule shapes: the legacy
# flat-visits path (normalize_extracted_timing in protocol_extraction.py) and the
# canonical_plan path (build_row below) both cross-check a resolved day_offset/day_end
# against the exact day number(s) printed in the event's own source_label, and correct
# it when they disagree instead of trusting whatever arithmetic the model did in its
# head. A schedule's own printed day numbers are ground truth; a computed offset is not.

_SIMPLE_DAY_LABEL = re.compile(r"^\s*day\s*([+-]?\d+)\s*$", re.IGNORECASE)
_SIMPLE_DAY_RANGE_LABEL = re.compile(
    r"^\s*days?\s*([+-]?\d+)\s*(?:-|–|—|to)\s*(?:day\s*)?([+-]?\d+)\s*$",
    re.IGNORECASE,
)
# A short list/enumeration of specific days for one activity — "Day 12, 13 and 14",
# "Days 12, 13, 14", "day 12, 13 and day 14" — the shape a multi-day confinement/
# housing block's shared pre-dose or PK-sampling event is commonly labeled with. The
# shape check first confirms the ENTIRE label is built only from digits, day/days,
# and separator words (so free prose is never mistaken for a day list), then the
# individual numbers are pulled out separately with a plain (unsigned) \d+ — using a
# signed pattern here would let a plain range dash ("12-14") get misread as a
# negative number ("-14") on this fallback path, which the dedicated range regex
# above already handles correctly for exactly that shape.
_DAY_LIST_TOKEN = r"(?:\d+|days?|and|&|,|-|–|—|to)"
_SIMPLE_DAY_LIST_SHAPE = re.compile(
    r"^(?:\s*" + _DAY_LIST_TOKEN + r")+\s*$", re.IGNORECASE)
_MIN_DAY_LIST_ITEMS = 2
_MAX_DAY_LIST_ITEMS = 10


def study_day_to_offset(
    study_day: int,
    *,
    anchor_study_day: int,
    includes_day_zero: bool | None,
) -> int:
    """Convert one printed ``Day N`` into the canonical calendar offset.

    Day-numbering conventions skip zero only when the anchor is Day 1 and the
    protocol explicitly has no Day 0. A printed Day 0 is invalid in that
    convention instead of being silently moved to the baseline date.
    """
    if anchor_study_day not in (0, 1):
        raise ValueError("anchor_study_day must be 0 or 1")
    if anchor_study_day == 0:
        if includes_day_zero is False:
            raise ValueError("A Day 0 anchor requires includes_day_zero=true")
        return int(study_day)
    if study_day >= 1:
        return int(study_day) - 1
    if includes_day_zero is None:
        raise ValueError(
            "includes_day_zero is required for Day 0 or negative Day labels")
    if includes_day_zero:
        return int(study_day) - 1
    if study_day == 0:
        raise ValueError("Day 0 is invalid when the protocol excludes Day 0")
    # In a Day-1/no-Day-0 sequence, Day -1 is the prior calendar day while
    # positive labels are one-based (Day 1 is offset zero).
    return int(study_day) - 1 if study_day >= 1 else int(study_day)


def simple_day_label_offset(
    source_day_label: str | None,
    *,
    anchor_study_day: int | None,
    includes_day_zero: bool | None,
) -> int | None:
    """Convert only an exact ``Day N`` label when convention metadata is known.

    Cycle labels, Week 1, ranges, and prose stay untouched because converting
    them requires protocol-specific evidence. ``None`` means "do not infer".
    """
    if anchor_study_day is None:
        return None
    match = _SIMPLE_DAY_LABEL.fullmatch(str(source_day_label or ""))
    if not match:
        return None
    study_day = int(match.group(1))
    if anchor_study_day == 1 and includes_day_zero is None and study_day <= 0:
        return None
    return study_day_to_offset(
        study_day,
        anchor_study_day=anchor_study_day,
        includes_day_zero=includes_day_zero,
    )


def simple_day_label_range_offsets(
    source_day_label: str | None,
    *,
    anchor_study_day: int | None,
    includes_day_zero: bool | None,
) -> tuple[int, int] | None:
    """Convert an exact ``Day A-B``/``Day A to Day B`` or ``Day A, B and C``
    source label into (start, end) offsets spanning its lowest/highest day.

    Only a label built entirely from digits and day/range vocabulary is
    accepted — anything else (prose, an unrelated number) is left alone.
    """
    if anchor_study_day is None:
        return None
    label = str(source_day_label or "")
    match = _SIMPLE_DAY_RANGE_LABEL.fullmatch(label)
    if match:
        numbers = [int(match.group(1)), int(match.group(2))]
    elif _SIMPLE_DAY_LIST_SHAPE.fullmatch(label) and re.search(
            r"days?", label, re.IGNORECASE):
        numbers = [int(value) for value in re.findall(r"\d+", label)]
        if not (_MIN_DAY_LIST_ITEMS <= len(numbers) <= _MAX_DAY_LIST_ITEMS):
            return None
    else:
        return None
    start_day, end_day = min(numbers), max(numbers)
    if (anchor_study_day == 1 and includes_day_zero is None
            and (start_day <= 0 or end_day <= 0)):
        return None
    start = study_day_to_offset(
        start_day, anchor_study_day=anchor_study_day,
        includes_day_zero=includes_day_zero)
    end = study_day_to_offset(
        end_day, anchor_study_day=anchor_study_day,
        includes_day_zero=includes_day_zero)
    if end < start:
        raise ValueError("day range ends before it starts")
    return start, end


def project_canonical_plan(
    plan: CanonicalSchedulePlan,
    *,
    open_ended_preview_count: int = 12,
    anchor_study_day: int | None = None,
    includes_day_zero: bool | None = None,
) -> tuple[list[dict], list[str]]:
    """Compile one canonical graph into the legacy/mobile visit-row contract.

    Calendar months/years and event-driven timing remain undated in the template
    while their exact source timing is retained.  They are resolved only after a
    patient-specific anchor date exists.

    ``anchor_study_day``/``includes_day_zero`` are the protocol's own Day 0/Day 1
    numbering convention (see ``ExtractedSchedule``). When supplied, every
    resolved ``day_offset``/``day_end`` is cross-checked against the day
    number(s) printed in the event's own ``source_label`` and corrected if they
    disagree — the model's own printed day text is ground truth, a computed
    offset is not. Omitted (``None``) only for callers that never had this
    metadata; the cross-check then simply never fires, matching prior behavior.
    """
    warnings: list[str] = []
    anchor_ids = {item.id for item in plan.anchors}
    event_by_id = {item.id: item for item in plan.events}
    activity_by_id = {item.id: item for item in plan.activities}
    branch_by_id = {item.id: item for item in plan.branches}
    preferred = next((item for item in plan.anchors if item.anchor_type in (
        "randomization", "first_dose", "cycle_start", "period_start")), None)
    baseline_anchor_id = preferred.id if preferred else (
        plan.anchors[0].id if plan.anchors else None)
    anchor_by_id = {item.id: item for item in plan.anchors}
    conditions_by_target: dict[str, list[ScheduleCondition]] = {}
    for condition in plan.conditions:
        for target_id in condition.applies_to_ids:
            conditions_by_target.setdefault(target_id, []).append(condition)

    def event_branch_ids(event: ScheduleEvent) -> frozenset[str]:
        """This event's own arm/period plus the period's sequence, if nested.

        Used to test a FACTORIAL condition's ``applies_to_branch_ids`` against
        the event without requiring the model to duplicate the activity or
        event once per arm combination.
        """
        ids = {branch_id for branch_id in (event.arm_id, event.period_id) if branch_id}
        period_branch = branch_by_id.get(event.period_id) if event.period_id else None
        if period_branch is not None and period_branch.parent_branch_id:
            ids.add(period_branch.parent_branch_id)
        return frozenset(ids)

    def condition_applies(
        target_id: str, occurrence: int | None,
        branch_ids: frozenset[str] = frozenset(),
    ) -> bool:
        """Apply an occurrence and/or arm/branch filter, only when supplied.

        Each filter is independent and only constrains when at least one
        condition on this target actually states it — a target with no
        occurrence-scoped condition is unconstrained by occurrence, and
        likewise for branch scoping, so a plain (non-factorial, non-cyclic)
        condition keeps working exactly as before either extension existed.
        """
        relevant = conditions_by_target.get(target_id, [])
        occurrence_scoped = [c for c in relevant if c.occurrence_numbers]
        if occurrence_scoped and not (
            occurrence is not None
            and any(occurrence in c.occurrence_numbers for c in occurrence_scoped)
        ):
            return False
        branch_scoped = [c for c in relevant if c.applies_to_branch_ids]
        if branch_scoped and not any(
            set(c.applies_to_branch_ids) & branch_ids for c in branch_scoped
        ):
            return False
        return True

    def occurrence_label(
        label: str,
        recurrence: RecurrenceRule | None,
        occurrence: int | None,
    ) -> str:
        """Render a recurrence label without leaking internal occurrence jargon.

        Models should author ``{cycle}``/``{occurrence}`` templates.  The
        cycle-specific fallback is intentionally narrow: it only normalizes a
        label whose recurrence evidence explicitly says it is a cycle.  Other
        recurrence types retain the historical ``(Occurrence N)`` fallback so
        their existing behavior is unchanged and reviewable.
        """
        if occurrence is None:
            return label
        rendered = label.replace("{occurrence}", str(occurrence)).replace(
            "{cycle}", str(occurrence))
        if rendered != label:
            return rendered
        recurrence_text = " ".join((
            recurrence.source_label if recurrence else "",
            label,
        ))
        if re.search(r"\bcycles?\b", recurrence_text, re.IGNORECASE):
            collapsed = re.compile(
                r"\bCycle\s*\d+\s*(?:&|and)\s*"
                r"(?:Next|Subsequent|Further)\s*Cycles?\b",
                re.IGNORECASE,
            )
            if collapsed.search(label):
                return collapsed.sub(f"Cycle {occurrence}", label, count=1)
            numbered = re.compile(r"\bCycle\s*\d+\b", re.IGNORECASE)
            if numbered.search(label):
                return numbered.sub(f"Cycle {occurrence}", label, count=1)
            return f"Cycle {occurrence} {label}".strip()
        if occurrence > 1:
            return f"{label} (Occurrence {occurrence})"
        return label

    # A protocol declares several anchors (first dose, Period II dose, last
    # dose). Only the baseline sits at day zero; the others must be derived or
    # every event hanging off them stays undated and renders as a bare dash.
    #
    # Events inside a period/arm routinely omit the anchor on all but one row
    # ("Day 0 of each period" is printed once per period). Treating that
    # omission as the baseline silently stacks every period onto the first, so
    # a branch instead inherits the anchor its own sibling events declare.
    branch_anchor: dict[str, str] = {}
    for event in plan.events:
        branch_id = event.period_id or event.arm_id
        if branch_id and event.timing.anchor_id in anchor_ids:
            branch_anchor.setdefault(branch_id, event.timing.anchor_id)

    def effective_anchor_id(event: ScheduleEvent) -> str | None:
        if event.timing.anchor_id:
            return event.timing.anchor_id
        branch_id = event.period_id or event.arm_id
        if branch_id and branch_id in branch_anchor:
            return branch_anchor[branch_id]
        return baseline_anchor_id

    anchor_days: dict[str, float] = {}
    if baseline_anchor_id is not None:
        anchor_days[baseline_anchor_id] = 0.0

    def resolve_event_day(
        event_id: str, seen: frozenset[str] = frozenset(),
    ) -> float | None:
        if event_id in seen:
            return None
        event = event_by_id.get(event_id)
        if not event:
            return None
        timing = event.timing
        # A visit that IS a period/cycle/dosing anchor is often authored with
        # no numeric offset at all — "Day 1" needs no delta from itself. That
        # timing downgrades to "unresolved" (no offset, no range) rather than
        # inventing a fake zero amount. Back it to the anchor's own day only
        # when it points straight at a day-numbering-origin anchor — never a
        # patient-specific trigger like discharge/progression, which keeps
        # its own "event_driven" kind and is untouched here — and only when
        # no partial range was actually stated for it.
        if (
            timing.kind == "unresolved"
            and timing.offset is None
            and timing.range_start is None
            and timing.range_end is None
            and timing.anchor_id in anchor_ids
        ):
            anchor = anchor_by_id.get(timing.anchor_id)
            if anchor is not None and anchor.anchor_type in (
                "randomization", "first_dose", "cycle_start", "period_start",
            ):
                return anchor_days.get(timing.anchor_id)
        if timing.kind not in ("offset", "relative", "calendar_offset"):
            return None
        delta = _calendar_elapsed_days(timing.offset)
        if delta is None:
            return None
        if timing.relation == "before" and delta > 0:
            delta = -delta
        if timing.anchor_id in event_by_id:
            parent = resolve_event_day(timing.anchor_id, seen | {event_id})
            return None if parent is None else parent + delta
        anchor_id = effective_anchor_id(event)
        base = anchor_days.get(anchor_id) if anchor_id else None
        return None if base is None else base + delta

    # Derive the remaining anchors from transitions that state a real gap:
    # "Period II Day 0 follows Period I Day 3 by 7 days" dates the Period II
    # anchor and therefore every visit in that period. A transition with no
    # stated amount (an unquantified washout) derives nothing, and that period
    # stays undated rather than being invented onto the baseline.
    for _ in range(len(plan.anchors) + 1):
        progressed = False
        for transition in plan.transitions:
            gap = _elapsed_days(transition.amount)
            if gap is None:
                continue
            source = event_by_id.get(transition.from_event_id)
            target = event_by_id.get(transition.to_event_id)
            if source is None or target is None:
                continue
            target_anchor = effective_anchor_id(target)
            if target_anchor is None or target_anchor in anchor_days:
                continue
            source_day = resolve_event_day(source.id)
            if source_day is None:
                continue
            target_offset = _elapsed_days(target.timing.offset) or 0.0
            anchor_days[target_anchor] = source_day + abs(gap) - target_offset
            progressed = True
        if not progressed:
            break

    # A protocol names several anchors inside one period — "Day 0 of each
    # period" for check-in and "Day 1 of Period I" for dosing. The graph never
    # states the gap between them, so every event hanging off the dosing anchor
    # was undated even though the protocol prints its study day plainly.
    #
    # The day is read from the anchor's own source label and applied only
    # relative to another anchor used by the SAME period, so "Day 0 of each
    # period" cannot leak from Period I into Period II. Nothing is invented:
    # an anchor whose label states no day stays unresolved.
    branch_anchor_ids: dict[str, set[str]] = {}
    for event in plan.events:
        branch_id = event.period_id or event.arm_id
        anchor_id = effective_anchor_id(event)
        if branch_id and anchor_id:
            branch_anchor_ids.setdefault(branch_id, set()).add(anchor_id)

    def label_study_day(anchor_id: str) -> float | None:
        anchor = anchor_by_id.get(anchor_id)
        if anchor is None:
            return None
        match = re.search(r"\bday\s*([+-]?\d+)\b", anchor.source_label or "", re.IGNORECASE)
        return float(match.group(1)) if match else None

    for _ in range(len(plan.anchors) + 1):
        progressed = False
        for anchor_ids_in_branch in branch_anchor_ids.values():
            known: list[tuple[str, float]] = []
            for anchor_id in sorted(anchor_ids_in_branch):
                if anchor_id not in anchor_days:
                    continue
                labelled = label_study_day(anchor_id)
                if labelled is not None:
                    known.append((anchor_id, labelled))
            if not known:
                continue
            reference_id, reference_label = known[0]
            for anchor_id in anchor_ids_in_branch:
                if anchor_id in anchor_days:
                    continue
                own_label = label_study_day(anchor_id)
                if own_label is None:
                    continue
                anchor_days[anchor_id] = (
                    anchor_days[reference_id] + own_label - reference_label)
                progressed = True
        if not progressed:
            break

    def unresolved_anchor_note(event: ScheduleEvent) -> str:
        """Say what an undated row is waiting on instead of showing a bare dash."""
        anchor_id = effective_anchor_id(event)
        if anchor_id is None or anchor_id in anchor_days:
            return ""
        anchor = anchor_by_id.get(anchor_id)
        return (
            f"Scheduled from '{anchor.name if anchor else anchor_id}', whose "
            "interval from the baseline is not stated in the protocol. This date "
            "is set once that event happens."
        )

    def evidence_links(event: ScheduleEvent, activities: list[ActivityTemplate]) -> list[dict]:
        name_ids = list(dict.fromkeys(event.evidence_ids))
        timing_ids = list(dict.fromkeys(event.timing.evidence_ids))
        window_ids = list(dict.fromkeys(event.window.evidence_ids))
        activity_ids = list(dict.fromkeys(
            evidence_id for activity in activities for evidence_id in activity.evidence_ids))
        return [
            {"field": field, "evidence_ids": ids}
            for field, ids in (("name", name_ids), ("timing", timing_ids),
                               ("window", window_ids), ("activities", activity_ids))
            if ids
        ]

    def build_row(event: ScheduleEvent, *, occurrence: int | None = None,
                  recurrence_delta: float | None = None,
                  recurrence: RecurrenceRule | None = None) -> dict:
        timing = event.timing
        source_label = timing.source_label.strip()
        if occurrence is not None:
            source_label = source_label.replace(
                "{occurrence}", str(occurrence)).replace("{cycle}", str(occurrence))
        if not source_label:
            source_label = format_temporal_amount(timing.offset)
        day = resolve_event_day(event.id)
        if day is not None and recurrence_delta is not None:
            day += recurrence_delta

        # day_offset above is a 30-day/365-day approximation for a Month/Year
        # label. Once a real patient anchor date exists, exact calendar math
        # (real month lengths, leap years) beats that approximation — but only
        # when the offset is measured straight off the baseline: an event
        # chained onto another event's timing has no single real date to
        # apply_temporal_amount against at the per-patient stage.
        calendar_offset_value = None
        calendar_offset_unit = None
        if (
            timing.kind == "calendar_offset"
            and timing.offset is not None
            and timing.offset.unit in ("month", "year")
            and recurrence_delta is None
            and timing.anchor_id not in event_by_id
            and effective_anchor_id(event) == baseline_anchor_id
        ):
            calendar_offset_value = timing.offset.value
            if timing.relation == "before" and calendar_offset_value > 0:
                calendar_offset_value = -calendar_offset_value
            calendar_offset_unit = timing.offset.unit
        hour_offset = None
        hour_offset_basis = None
        if timing.offset and timing.offset.unit in ("minute", "hour") \
                and timing.kind == "offset":
            raw_hours = timing.offset.value / 60 if timing.offset.unit == "minute" \
                else timing.offset.value
            if day is not None:
                # `day` already has this offset's fractional contribution
                # folded in via resolve_event_day (an hour/minute offset is a
                # fraction of a day). Split it into a whole calendar day plus
                # the intra-day remainder instead of keeping the raw hour
                # value alone: a PK timepoint chained onto a non-baseline
                # anchor — a later crossover period's own dosing day, any
                # mid-study anchor — needs its anchor's day position folded
                # into the comparable elapsed time. Discarding it here (the
                # historical behavior) made every period's "Hour 4" collide
                # with every other period's "Hour 4" when sorted/compared,
                # since only the bare hour count survived.
                whole_day = math.floor(day)
                hour_offset = round((day - whole_day) * 24, 6)
                day = whole_day
                hour_offset_basis = "within_day"
            else:
                # No resolvable day at all (the anchor itself never dated) —
                # keep the legacy behavior of a bare, self-contained elapsed
                # hour count so the timepoint is still orderable/displayable.
                hour_offset = raw_hours
                hour_offset_basis = "absolute"
        day_offset = int(day) if day is not None and float(day).is_integer() else None
        day_end = None
        if timing.kind == "range":
            start, end = _elapsed_days(timing.range_start), _elapsed_days(timing.range_end)
            if start is not None and end is not None and baseline_anchor_id == timing.anchor_id:
                day_offset = int(start) if float(start).is_integer() else None
                day_end = int(end) if float(end).is_integer() else None

        # Cross-check the resolved day_offset/day_end against the day number(s)
        # actually printed in this event's own source_label — the same
        # protection normalize_extracted_timing gives the legacy flat-visits
        # path, applied here so the canonical_plan path (the one every real AI
        # extraction now uses) gets it too. Restricted to events ultimately
        # dated straight off the study baseline: a protocol that restarts "Day
        # 1" locally within each period/cycle would make the global
        # anchor_study_day/includes_day_zero convention meaningless for a
        # non-baseline-anchored event, so this only fires where it is safe to
        # trust the label at face value.
        if anchor_study_day is not None and effective_anchor_id(event) == baseline_anchor_id:
            try:
                derived_range = simple_day_label_range_offsets(
                    source_label, anchor_study_day=anchor_study_day,
                    includes_day_zero=includes_day_zero)
                derived_single = None if derived_range is not None else simple_day_label_offset(
                    source_label, anchor_study_day=anchor_study_day,
                    includes_day_zero=includes_day_zero)
            except ValueError as exc:
                warnings.append(
                    f"'{event.name}' has invalid day numbering in '{source_label}': "
                    f"{exc}.")
                day_offset = day_end = None
            else:
                if derived_range is not None:
                    derived_start, derived_end = derived_range
                    if (day_offset, day_end) != (derived_start, derived_end):
                        warnings.append(
                            f"'{event.name}' timing was corrected deterministically "
                            f"from '{source_label}' and flagged for review.")
                    day_offset, day_end = derived_start, derived_end
                elif derived_single is not None and day_offset != derived_single:
                    if day_offset is not None:
                        warnings.append(
                            f"'{event.name}' timing was corrected deterministically "
                            f"from '{source_label}' and flagged for review.")
                    day_offset = derived_single

        early = event.window.early
        late = event.window.late
        early_days = _window_side_days(early)
        late_days = _window_side_days(late)
        visit_window_is_days = event.window.scope == "visit" \
            and event.window.window_type == "tolerance" and event.window.state == "stated" \
            and (early is None or early_days is not None) \
            and (late is None or late_days is not None)
        window_before = early_days if visit_window_is_days else None
        window_after = late_days if visit_window_is_days else None
        window_days = None
        if window_before is not None and window_after is not None \
                and window_before == window_after:
            window_days = window_before
            window_before = window_after = None

        branch_ids = event_branch_ids(event)
        activities = [
            activity_by_id[item] for item in event.activity_ids
            if item in activity_by_id and condition_applies(item, occurrence, branch_ids)
        ]
        procedures = []
        operational_constraints: list[str] = list(event.operational_constraints)
        if event.conditional_text.strip():
            operational_constraints.append(event.conditional_text.strip())
        operational_constraints.extend(
            condition.expression for target_id in (event.id, *(item.id for item in activities))
            for condition in conditions_by_target.get(target_id, [])
            if condition_applies(target_id, occurrence, branch_ids) and condition.expression.strip()
        )
        operational_constraints.extend(
            item for item in (
                timing.weekday_rule.strip(), timing.notes.strip(),
                *(label.strip() for label in timing.alternative_source_labels),
            ) if item)
        if event.window.state != "not_stated" and not visit_window_is_days:
            # A unit the legacy row cannot hold, or an unclear/conflicting
            # window, must stay readable instead of vanishing with the field.
            operational_constraints.append(
                f"Visit constraint: {format_window(event.window)}")
        inexact_note = _inexact_timing_note(timing)
        if inexact_note:
            operational_constraints.append(inexact_note)
        if timing.kind == "calendar_offset" and day_offset is not None:
            unit = timing.offset.unit if timing.offset else "month"
            operational_constraints.append(
                f"Day {day_offset} is approximated from the protocol's stated "
                f"'{source_label or format_temporal_amount(timing.offset)}' "
                f"({'30 days/month' if unit == 'month' else '365 days/year'}), "
                "not an exact protocol-given day.")
        anchor_note = unresolved_anchor_note(event)
        if anchor_note:
            operational_constraints.append(anchor_note)
        for activity in activities:
            timing_text = activity.timing.source_label if activity.timing else ""
            window_text = format_window(activity.window)
            # A procedure whose timing could not be structured still has to tell
            # the reviewer why, or the gap looks like the protocol was silent.
            activity_notes = [
                note for note in (
                    activity.timing.notes.strip() if activity.timing else "",
                ) if note
            ]
            procedures.append({
                "id": activity.id,
                "name": activity.name,
                "timing": timing_text,
                "window": window_text,
                "condition": activity.conditional_text,
                "evidence_ids": activity.evidence_ids,
                "constraints": list(activity.operational_constraints) + activity_notes,
            })
            detail = "; ".join(part for part in (
                timing_text and f"timing: {timing_text}",
                window_text and f"window: {window_text}",
                activity.conditional_text.strip(),
            ) if part)
            if detail:
                operational_constraints.append(f"{activity.name} — {detail}")
            operational_constraints.extend(
                f"{activity.name} — {constraint}"
                for constraint in activity.operational_constraints if constraint.strip())
        for transition in plan.transitions:
            if event.id not in (transition.from_event_id, transition.to_event_id):
                continue
            other_id = transition.from_event_id if transition.to_event_id == event.id \
                else transition.to_event_id
            other = event_by_id.get(other_id)
            amount = format_temporal_amount(transition.amount)
            operational_constraints.append(
                " ".join(part for part in (
                    transition.relation.replace("_", " "), amount,
                    other.name if other else other_id,
                ) if part))
        name = occurrence_label(event.name, recurrence, occurrence)
        arm = branch_by_id.get(event.arm_id).name if event.arm_id in branch_by_id else None
        period_branch = branch_by_id.get(event.period_id)
        period = period_branch.name if period_branch is not None else None
        # A period nested under a sequence branch (a crossover design's
        # randomized treatment order, e.g. "Sequence AB" gets Period 1 =
        # Treatment A vs "Sequence BA" gets Period 1 = Treatment B) has no
        # dedicated row field of its own — the flat visit contract only
        # carries arm/period. Fold the sequence name into `arm` so two
        # periods sharing the same label ("Period 1") under different
        # sequences stay distinguishable in the visit list instead of
        # reading identically; an explicit arm is extended, not overwritten,
        # so a genuine dose-arm x sequence combination keeps both.
        sequence_branch = branch_by_id.get(period_branch.parent_branch_id) \
            if period_branch is not None and period_branch.parent_branch_id else None
        if sequence_branch is not None:
            arm = f"{arm} / {sequence_branch.name}" if arm else sequence_branch.name
        unresolved = day_offset is None and hour_offset is None
        # A resolved range keeps both of its ends, so it is represented
        # faithfully. A qualified single day ("within 28 days before", "at least
        # 21 days after") is a permitted boundary that the legacy row can only
        # show as one number, so it must never pass as a confirmed appointment.
        bounded_single_day = bool(inexact_note) and timing.kind != "range"
        review = (
            unresolved
            or bounded_single_day
            or event.window.state in ("unclear", "conflicting")
        )
        return {
            # Keep the canonical template ID stable for API/backward compatibility;
            # each persisted compatibility row receives its own ordinary visit ID.
            "canonical_event_id": event.id,
            "name": name,
            "visit_type": event.event_type,
            "day_offset": day_offset,
            "day_end": day_end,
            "calendar_offset_value": calendar_offset_value,
            "calendar_offset_unit": calendar_offset_unit,
            "source_day_label": source_label or "-",
            "hour_offset": hour_offset,
            "hour_offset_basis": hour_offset_basis,
            "hour_end": None,
            "window_days": window_days,
            "window_before": window_before,
            "window_after": window_after,
            "relative_to": occurrence_label(
                event_by_id[timing.anchor_id].name, recurrence, occurrence)
                if timing.kind == "relative" and timing.anchor_id in event_by_id else None,
            "relative_offset_days": (
                -int(abs(_elapsed_days(timing.offset)))
                if timing.relation == "before" else int(_elapsed_days(timing.offset))
            )
                if timing.kind == "relative" and _elapsed_days(timing.offset) is not None
                and float(_elapsed_days(timing.offset)).is_integer() else None,
            "arm": arm,
            "period": period,
            "activities": [item.name for item in activities],
            "procedures": procedures,
            "operational_constraints": list(dict.fromkeys(
                item for item in operational_constraints if item.strip())),
            "field_evidence": evidence_links(event, activities),
            "extraction_warning": review,
            "review_status": "pending" if review else "ok",
        }

    recurrence_by_event: dict[str, list[RecurrenceRule]] = {}
    for recurrence in plan.recurrences:
        for event_id in recurrence.event_ids:
            recurrence_by_event.setdefault(event_id, []).append(recurrence)
    rows: list[dict] = []
    # A recurrence rule can list several events (e.g. Dosing + IC-1/2/3 sharing one
    # "Cycle {cycle}" pattern). Expanding those occurrence-by-occurrence rather than
    # fully finishing one event before starting the next keeps same-day ties across
    # cycle boundaries (cycle N's last visit vs. cycle N+1's first) in the raw row
    # order the later stable sort falls back on — otherwise cycle N+1's rows for an
    # earlier-listed event silently jump ahead of cycle N's rows for a later-listed
    # one on any tie.
    processed_recurrence_ids: set[str] = set()
    for event in plan.events:
        recurrences = recurrence_by_event.get(event.id) or []
        if not recurrences:
            rows.append(build_row(event))
            continue
        for recurrence in recurrences:
            if recurrence.id in processed_recurrence_ids:
                continue
            processed_recurrence_ids.add(recurrence.id)
            end = recurrence.end_occurrence
            if end is None:
                end = recurrence.start_occurrence + max(1, open_ended_preview_count) - 1
                warnings.append(
                    f"'{recurrence.source_label or recurrence.id}' is open-ended; "
                    f"showing {open_ended_preview_count} occurrences for review only.")
            frequency_days = _elapsed_days(recurrence.frequency)
            group_events = [e for e in plan.events if e.id in recurrence.event_ids]
            for occurrence in range(recurrence.start_occurrence, end + 1):
                for group_event in group_events:
                    if not condition_applies(
                            group_event.id, occurrence, event_branch_ids(group_event)):
                        continue
                    delta = None if frequency_days is None else (
                        occurrence - recurrence.start_occurrence) * frequency_days
                    row = build_row(
                        group_event, occurrence=occurrence, recurrence_delta=delta,
                        recurrence=recurrence)
                    if frequency_days is None and occurrence > recurrence.start_occurrence:
                        # Calendar-month/year recurrence needs a real patient date.
                        # Never duplicate the first occurrence's numeric offset.
                        row["day_offset"] = None
                        row["hour_offset"] = None
                        row["hour_offset_basis"] = None
                        row["extraction_warning"] = True
                        row["review_status"] = "pending"
                        cadence = recurrence.source_label or (
                            "Every " + format_temporal_amount(recurrence.frequency))
                        row["source_day_label"] = "; ".join(
                            item for item in (row["source_day_label"], cadence) if item)
                    rows.append(row)
    return rows, list(dict.fromkeys(warnings))
