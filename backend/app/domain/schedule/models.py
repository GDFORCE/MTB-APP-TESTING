from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .condition import ConditionExpression
from .timing import (
    AnchorReference,
    PositiveTemporalAmount,
    StrictModel,
    TemporalReference,
    TimingExpression,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleStatus(StrEnum):
    DRAFT = "DRAFT"
    EXTRACTED = "EXTRACTED"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class InterpretationStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    CONFIRMED = "CONFIRMED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"


class Requiredness(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    CONDITIONAL = "CONDITIONAL"
    RECOMMENDED = "RECOMMENDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


class DependencyMode(StrEnum):
    NOMINAL = "NOMINAL"
    ACTUAL_PREVIOUS_EVENT = "ACTUAL_PREVIOUS_EVENT"
    MANUAL = "MANUAL"
    UNCLEAR = "UNCLEAR"


class QualifierScope(StrEnum):
    CELL = "CELL"
    ROW = "ROW"
    COLUMN = "COLUMN"
    VISIT = "VISIT"
    ACTIVITY = "ACTIVITY"
    BLOCK = "BLOCK"
    ARM = "ARM"
    COHORT = "COHORT"
    SUBSTUDY = "SUBSTUDY"
    PERIOD = "PERIOD"
    # Doc s5: a footnote can restrict a population rather than an arm.
    POPULATION = "POPULATION"
    GLOBAL = "GLOBAL"


class QualifierCategory(StrEnum):
    TIMING = "TIMING"
    WINDOW = "WINDOW"
    CONDITION = "CONDITION"
    APPLICABILITY = "APPLICABILITY"
    OPTIONALITY = "OPTIONALITY"
    EXCEPTION = "EXCEPTION"
    REPEAT = "REPEAT"
    STOP = "STOP"
    SUBSTITUTION = "SUBSTITUTION"
    SEQUENCE = "SEQUENCE"
    DETAIL = "DETAIL"
    POPULATION_RESTRICTION = "POPULATION_RESTRICTION"
    # A reviewer determined the marker does not govern THIS schedule. It is
    # a decision that was made, which is why it is not the same as UNRESOLVED.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OTHER = "OTHER"
    UNRESOLVED = "UNRESOLVED"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    ACCEPTED = "ACCEPTED"
    DISMISSED = "DISMISSED"


class ConditionState(StrEnum):
    """Lifecycle of one protocol condition for one patient (requirement doc 2 s14).

    A condition is never a boolean checkbox: it carries a state, an occurrence date
    that can anchor resulting visits, and a resolution date that stops repeats.
    """

    NOT_OCCURRED = "NOT_OCCURRED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    ACTIVE = "ACTIVE"
    # A condition that resolved and has come back. It ACTS exactly like ACTIVE -
    # the same visits are generated - but stays distinguishable, because "the
    # toxicity returned" is a different clinical picture from "the patient had a
    # toxicity", and a reviewer deciding on dose reduction needs to see which.
    RECURRED = "RECURRED"
    RESOLVED = "RESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANCELLED = "CANCELLED"


class BlockState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class ConfinementStatus(StrEnum):
    """State of one continuous inpatient stay (requirement doc 6 section 36).

    The distinction that matters clinically is: not yet arrived, currently confined,
    actually discharged. A planned discharge date passing is not a discharge.
    """

    UPCOMING = "UPCOMING"
    IN_CONFINEMENT = "IN_CONFINEMENT"
    EXTENDED = "EXTENDED"
    DISCHARGED = "DISCHARGED"
    CANCELLED = "CANCELLED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"


class PatientActivityStatus(StrEnum):
    RESOLVED = "RESOLVED"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"
    WAITING_FOR_CONDITION = "WAITING_FOR_CONDITION"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"
    COMPLETED = "COMPLETED"
    NOT_DONE = "NOT_DONE"


class PatientEventStatus(StrEnum):
    RESOLVED = "RESOLVED"
    # A protocol-defined unscheduled visit that exists but is not due. Distinct
    # from NOT_APPLICABLE, which means the patient will never have it, and from
    # WAITING_FOR_CONDITION, which means the protocol is still waiting on a
    # clinical trigger (requirement doc 10 s13-s15).
    AVAILABLE_ON_DEMAND = "AVAILABLE_ON_DEMAND"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"
    WAITING_FOR_CONDITION = "WAITING_FOR_CONDITION"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    UNRESOLVED = "UNRESOLVED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"


class UnscheduledOccurrence(StrictModel):
    """One unscheduled visit a site actually created (requirement doc 10 s14).

    A reason is required: an unscheduled visit with no stated cause is not
    reviewable later, and the protocol defines these by their cause.
    """

    event_code: str = Field(min_length=1)
    occurred_on: date | datetime
    reason: str = Field(min_length=1)
    visit_mode: str | None = None
    created_by: UUID | None = None
    recorded_at: datetime = Field(default_factory=utc_now)


class DayNumbering(StrictModel):
    day_1_anchor: AnchorReference
    counting_convention: Literal["CLINICAL_DAY", "ELAPSED_DURATION"] = "CLINICAL_DAY"


class ScheduleMetadata(StrictModel):
    name: str = Field(min_length=1)
    description: str | None = None
    schedule_type: str = "PRIMARY"
    protocol_version_id: UUID | None = None
    version_number: int = Field(default=1, gt=0)
    status: ScheduleStatus = ScheduleStatus.DRAFT
    # Doc 9 s8. When this version starts applying to NEW enrolments, which is not
    # the same moment as approval. None means "in force as soon as approved".
    effective_from: date | None = None
    day_numbering: DayNumbering | None = None
    extensions: list[dict[str, object]] = Field(default_factory=list)


class Epoch(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str | None = None
    sequence_number: int | None = None
    timing: TimingExpression | None = None
    applicability: list[ConditionExpression] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)


class StudyDimension(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str | None = None
    criteria: ConditionExpression | None = None
    # Doc 8 s10: protocols nest their groups - "Part A, Cohort 1". Recording the
    # parent is what lets enrolment offer only the cohorts that exist inside the
    # part already chosen, instead of every cohort in the protocol.
    parent_dimension_type: str | None = None
    parent_code: str | None = None

    @model_validator(mode="after")
    def parent_is_complete(self) -> "StudyDimension":
        if (self.parent_dimension_type is None) != (self.parent_code is None):
            raise ValueError(
                "a parent dimension needs both its type and its code; a half-stated "
                "hierarchy would silently behave as if there were none"
            )
        return self


class GenericDimension(StudyDimension):
    dimension_type: str = Field(min_length=1)


class Anchor(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str | None = None
    display_name: str = Field(min_length=1)
    anchor_type: str = Field(min_length=1)
    derivation_rule: dict[str, object] | None = None
    source_event_code: str | None = None
    source_condition_code: str | None = None
    status: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS"] = "RESOLVED"
    evidence_refs: list[UUID] = Field(default_factory=list)


#: Marks the anchor a schedule counts Day 0 from. Recorded on the anchor's own
#: ``derivation_rule`` by whatever produced the schedule, so the origin is a
#: stated fact carried WITH its semantic anchor type rather than something each
#: consumer re-derives (doc s4).
SCHEDULE_ORIGIN_ROLE = "SCHEDULE_ORIGIN"


def schedule_origin_anchor(schedule: "UniversalSchedule"):
    """The anchor this schedule's Day 0 is measured from, or None.

    Never matches on an anchor's printed NAME. A protocol's origin may be called
    Baseline, Day 1, Randomisation or First Dose, and those are different
    clinical facts; the origin is whichever anchor the schedule was built
    against, which the builder records explicitly.
    """
    for anchor in schedule.anchors:
        if (anchor.derivation_rule or {}).get("role") == SCHEDULE_ORIGIN_ROLE:
            return anchor
    # Schedules persisted before the origin was recorded explicitly, plus the
    # demo/native-extraction paths that mint an anchor typed BASELINE outright.
    for anchor in schedule.anchors:
        if anchor.anchor_type.upper() == "BASELINE" or anchor.code == "BASELINE":
            return anchor
    return None


class ApplicabilityRule(StrictModel):
    dimension: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    operator: Literal["IN", "NOT_IN"] = "IN"
    values: list[str] = Field(min_length=1)
    field: str | None = None
    condition: ConditionExpression | None = None


class Dependency(StrictModel):
    source_event_code: str = Field(min_length=1)
    dependency_type: Literal["TEMPORAL", "TRIGGER", "PRECONDITION", "SEQUENCE", "ANCHOR"]
    condition: ConditionExpression | None = None


class Qualifier(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    marker: str | None = None
    text: str = Field(min_length=1)
    scope: QualifierScope
    category: QualifierCategory = QualifierCategory.UNRESOLVED
    target_codes: list[str] = Field(default_factory=list)
    resolved: bool = False
    evidence_refs: list[UUID] = Field(default_factory=list)
    #: Structured facts a qualifier's PROSE would otherwise be the only home for
    #: - a window's real type and bounds, a repeat rule's cycle list, and so on.
    #: Doc s7/s27: "preserve window type, lower bound, upper bound, units,
    #: reference event" means those numbers must survive as data a reviewer's
    #: decision can act on, not only as a sentence a human has to re-parse.
    details: dict[str, object] = Field(default_factory=dict)


class ConditionalAction(StrictModel):
    action_type: Literal[
        "ADD_EVENT", "REPEAT_EVENT", "STOP_BLOCK", "PAUSE_BLOCK",
        "RESUME_BLOCK", "EXTEND_CONFINEMENT", "CANCEL_EVENT", "MANUAL_REVIEW",
    ]
    target_code: str = Field(min_length=1)
    condition: ConditionExpression
    parameters: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConfinementDefinition(StrictModel):
    admission_event_code: str = Field(min_length=1)
    dose_event_codes: list[str] = Field(min_length=1)
    discharge_event_code: str = Field(min_length=1)
    extension_actions_allowed: bool = False
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConditionalDefinition(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    condition: ConditionExpression
    trigger_event_code: str | None = None
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    actions: list[ConditionalAction] = Field(min_length=1)
    resolution_condition: ConditionExpression | None = None
    recurrence: RecurrenceRule | None = None
    evidence_refs: list[UUID] = Field(default_factory=list)
    interpretation_status: InterpretationStatus = InterpretationStatus.EXTRACTED
    requires_review: bool = False


class RepeatBlock(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    event_codes: list[str] = Field(min_length=1)
    dependency_mode: DependencyMode
    resume_mode: Literal["NOMINAL", "ACTUAL_RESUME", "MANUAL", "UNCLEAR"] = "UNCLEAR"
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConfinementDayDefinition(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    day_label: str = Field(min_length=1)
    relative_day: int
    activity_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)


class ConfinementEpisodeDefinition(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    admission_event_code: str = Field(min_length=1)
    dose_event_codes: list[str] = Field(min_length=1)
    discharge_event_code: str = Field(min_length=1)
    days: list[ConfinementDayDefinition] = Field(min_length=1)
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    conditions: list[ConditionExpression] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)


class RecurrenceTermination(StrictModel):
    type: Literal["COUNT", "DATE", "EVENT", "CONDITION", "HORIZON"]
    count: int | None = Field(default=None, gt=0)
    termination_date: date | None = Field(default=None, alias="date")
    event_code: str | None = None
    condition: ConditionExpression | None = None

    @model_validator(mode="after")
    def matching_value(self) -> "RecurrenceTermination":
        values = {
            "COUNT": self.count,
            "DATE": self.termination_date,
            "EVENT": self.event_code,
            "CONDITION": self.condition,
            "HORIZON": True,
        }
        if values[self.type] is None:
            raise ValueError(f"{self.type} termination requires its matching value")
        return self


class RecurrenceRule(StrictModel):
    type: Literal["INTERVAL"] = "INTERVAL"
    interval: PositiveTemporalAmount
    start_reference: TemporalReference
    termination: RecurrenceTermination
    include_start: bool = True


class Activity(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str | None = None
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    activity_type: str = Field(min_length=1)
    requiredness: Requiredness = Requiredness.REQUIRED
    sequence_number: int | None = None
    timing: TimingExpression | None = None
    conditions: list[ConditionExpression] = Field(default_factory=list)
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    qualifiers: list[Qualifier] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    interpretation_status: InterpretationStatus = InterpretationStatus.EXTRACTED
    requires_review: bool = False
    evidence_refs: list[UUID] = Field(default_factory=list)


class Event(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    normalized_name: str | None = None
    event_type: str = Field(min_length=1)
    epoch_id: UUID | None = None
    sequence_number: int | None = None
    timing: TimingExpression
    applicability: list[ApplicabilityRule] = Field(default_factory=list)
    conditions: list[ConditionExpression] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    dependency_mode: DependencyMode | None = None
    # Doc 10 s2-s3 and s31-s32. ``visit_mode`` is the single mode when the
    # protocol states one; ``allowed_visit_modes`` holds every mode a hybrid
    # visit permits. Neither is defaulted - an unstated mode stays unstated so
    # no patient is told to travel on a guess (doc 10 s18, s33).
    visit_mode: str | None = None
    allowed_visit_modes: list[str] = Field(default_factory=list)
    activation: Literal["SCHEDULED", "ON_DEMAND"] = "SCHEDULED"
    recurrence: RecurrenceRule | None = None
    conditional_actions: list[ConditionalAction] = Field(default_factory=list)
    qualifiers: list[Qualifier] = Field(default_factory=list)
    confinement: ConfinementDefinition | None = None
    activities: list[Activity] = Field(default_factory=list)
    evidence_refs: list[UUID] = Field(default_factory=list)
    interpretation_status: InterpretationStatus = InterpretationStatus.EXTRACTED
    requires_review: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)


class Evidence(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    evidence_type: str = Field(min_length=1)
    page_number: int | None = Field(default=None, gt=0)
    section_title: str | None = None
    table_title: str | None = None
    row_identifier: str | None = None
    column_identifier: str | None = None
    source_text: str | None = None
    source_locator: dict[str, object] = Field(default_factory=dict)
    extraction_context: dict[str, object] = Field(default_factory=dict)


class ClaimEvidence(StrictModel):
    evidence_id: UUID
    claim_type: str = Field(min_length=1)
    claim_entity_type: str = Field(min_length=1)
    claim_entity_id: UUID
    claim_path: str | None = None
    interpretation: dict[str, object] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ValidationIssue(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    entity_type: str | None = None
    entity_id: UUID | None = None
    issue_code: str = Field(min_length=1)
    severity: Severity
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)
    blocking: bool = False
    status: IssueStatus = IssueStatus.OPEN


class UniversalSchedule(StrictModel):
    schema_version: Literal["uctsm.v1"] = "uctsm.v1"
    schedule_version_id: UUID = Field(default_factory=uuid4)
    schedule_metadata: ScheduleMetadata
    epochs: list[Epoch] = Field(default_factory=list)
    arms: list[StudyDimension] = Field(default_factory=list)
    cohorts: list[StudyDimension] = Field(default_factory=list)
    populations: list[StudyDimension] = Field(default_factory=list)
    dimensions: list[GenericDimension] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    conditional_definitions: list[ConditionalDefinition] = Field(default_factory=list)
    repeat_blocks: list[RepeatBlock] = Field(default_factory=list)
    confinement_episodes: list[ConfinementEpisodeDefinition] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PatientContext(StrictModel):
    patient_id: UUID
    schedule_version_id: UUID
    anchors: dict[str, date | datetime] = Field(default_factory=dict)
    event_values: dict[str, list[date | datetime]] = Field(default_factory=dict)
    actual_event_values: dict[str, list[date | datetime]] = Field(default_factory=dict)
    activity_actuals: dict[str, date | datetime] = Field(default_factory=dict)
    activity_statuses: dict[str, str] = Field(default_factory=dict)
    conditions: dict[str, PatientConditionStatus] = Field(default_factory=dict)
    unscheduled_occurrences: list[UnscheduledOccurrence] = Field(default_factory=list)
    visit_modes: dict[str, str] = Field(default_factory=dict)
    state: dict[str, object] = Field(default_factory=dict)
    state_effective_at: dict[str, datetime] = Field(default_factory=dict)
    arm_code: str | None = None
    cohort_code: str | None = None
    population_code: str | None = None
    dimension_values: dict[str, list[str]] = Field(default_factory=dict)


class PatientConditionStatus(StrictModel):
    condition_code: str = Field(min_length=1)
    state: ConditionState = ConditionState.NOT_OCCURRED
    occurrence_date: date | datetime | None = None
    resolution_date: date | datetime | None = None
    occurrence_index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def recurrence_is_numbered(self) -> "PatientConditionStatus":
        """A recurrence is at least the second occurrence, by definition."""
        if self.state == ConditionState.RECURRED and self.occurrence_index < 1:
            raise ValueError(
                "a RECURRED condition must carry the occurrence index of the "
                "recurrence; index 0 is the first occurrence, which is ACTIVE"
            )
        return self

    @model_validator(mode="after")
    def dated_when_active(self) -> "PatientConditionStatus":
        dated = {
            ConditionState.ACTIVE, ConditionState.RECURRED, ConditionState.RESOLVED,
        }
        if self.state in dated and self.occurrence_date is None:
            raise ValueError(f"{self.state.value} condition requires its occurrence date")
        return self


class ResolvedTiming(StrictModel):
    nominal_start: date | datetime | None = None
    nominal_end: date | datetime | None = None
    earliest: date | datetime | None = None
    latest: date | datetime | None = None
    precision: Literal["EXACT", "APPROXIMATE", "CONSTRAINT"]
    constraints: list[dict[str, object]] = Field(default_factory=list)


class EvaluatedActivity(StrictModel):
    activity_definition_id: UUID
    activity_code: str | None = None
    display_name: str
    requiredness: Requiredness
    sequence_number: int | None = None
    status: PatientActivityStatus
    timing: ResolvedTiming | None = None
    actual_time: date | datetime | None = None
    applicability_result: str | None = None
    condition_result: str | None = None
    explanation: dict[str, object] = Field(default_factory=dict)


class EvaluatedEvent(StrictModel):
    event_definition_id: UUID
    event_code: str
    occurrence_index: int = Field(default=0, ge=0)
    status: PatientEventStatus
    timing: ResolvedTiming | None = None
    visit_mode: str | None = None
    allowed_visit_modes: list[str] = Field(default_factory=list)
    unscheduled_reason: str | None = None
    applicability_result: str | None = None
    condition_result: str | None = None
    dependency_result: dict[str, object] = Field(default_factory=dict)
    activities: list[EvaluatedActivity] = Field(default_factory=list)
    explanation: dict[str, object] = Field(default_factory=dict)


class EvaluatedConfinementDay(StrictModel):
    day_label: str
    relative_day: int
    scheduled_date: date | datetime | None = None
    activity_codes: list[str] = Field(default_factory=list)


class EvaluatedConfinement(StrictModel):
    """One patient confinement episode: a single parent stay, not one visit per day."""

    episode_definition_id: UUID
    episode_code: str
    display_name: str
    status: ConfinementStatus
    planned_admission: date | datetime | None = None
    actual_admission: date | datetime | None = None
    planned_discharge: date | datetime | None = None
    actual_discharge: date | datetime | None = None
    extended_by: list[str] = Field(default_factory=list)
    days: list[EvaluatedConfinementDay] = Field(default_factory=list)
    explanation: dict[str, object] = Field(default_factory=dict)


class RollingHorizon(StrictModel):
    """How much of an open-ended repeat to materialize (requirement doc 3 s7-s8).

    ``upcoming_limit`` bounds only what is WRITTEN DOWN. It is a display and
    storage policy, never a statement about how long the protocol runs: doc 3 s2
    and s36 are explicit that a technical cap must not be presented as the
    protocol maximum. ``None`` keeps the previous behaviour of materializing
    everything up to the date horizon.
    """

    upcoming_limit: int | None = Field(default=None, gt=0)
    as_of: date | None = None


class RepeatSummary(StrictModel):
    """What a repeating rule does beyond the occurrences that were materialized.

    Carried alongside the events so a UI can show "every 21 days, continues"
    instead of implying the schedule ends at the last row (doc 3 s4, s36).
    """

    event_code: str
    block_code: str | None = None
    cadence: str
    termination: str
    materialized_count: int
    continues: bool
    next_unmaterialized: date | datetime | None = None


class EvaluationResult(StrictModel):
    schedule_version_id: UUID
    patient_id: UUID
    evaluator_version: str
    evaluated_at: datetime = Field(default_factory=utc_now)
    input_snapshot: dict[str, object]
    events: list[EvaluatedEvent]
    confinements: list[EvaluatedConfinement] = Field(default_factory=list)
    repeat_summaries: list[RepeatSummary] = Field(default_factory=list)
