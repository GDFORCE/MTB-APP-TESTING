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


class PatientEventStatus(StrEnum):
    RESOLVED = "RESOLVED"
    WAITING_FOR_ANCHOR = "WAITING_FOR_ANCHOR"
    WAITING_FOR_CONDITION = "WAITING_FOR_CONDITION"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    UNRESOLVED = "UNRESOLVED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"


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


class Anchor(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    code: str = Field(min_length=1)
    protocol_label: str | None = None
    display_name: str = Field(min_length=1)
    anchor_type: str = Field(min_length=1)
    derivation_rule: dict[str, object] | None = None
    source_event_code: str | None = None
    status: Literal["RESOLVED", "UNRESOLVED", "AMBIGUOUS"] = "RESOLVED"
    evidence_refs: list[UUID] = Field(default_factory=list)


class ApplicabilityRule(StrictModel):
    dimension: Literal["ARM", "COHORT", "POPULATION", "EPOCH", "PATIENT_ATTRIBUTE", "CUSTOM"]
    operator: Literal["IN", "NOT_IN"] = "IN"
    values: list[str] = Field(min_length=1)
    field: str | None = None
    condition: ConditionExpression | None = None


class Dependency(StrictModel):
    source_event_code: str = Field(min_length=1)
    dependency_type: Literal["TEMPORAL", "TRIGGER", "PRECONDITION", "SEQUENCE", "ANCHOR"]
    condition: ConditionExpression | None = None


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
    timing: TimingExpression | None = None
    conditions: list[ConditionExpression] = Field(default_factory=list)
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
    recurrence: RecurrenceRule | None = None
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
    anchors: list[Anchor] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PatientContext(StrictModel):
    patient_id: UUID
    schedule_version_id: UUID
    anchors: dict[str, date | datetime] = Field(default_factory=dict)
    event_values: dict[str, list[date | datetime]] = Field(default_factory=dict)
    state: dict[str, object] = Field(default_factory=dict)
    state_effective_at: dict[str, datetime] = Field(default_factory=dict)
    arm_code: str | None = None
    cohort_code: str | None = None
    population_code: str | None = None


class ResolvedTiming(StrictModel):
    nominal_start: date | datetime | None = None
    nominal_end: date | datetime | None = None
    earliest: date | datetime | None = None
    latest: date | datetime | None = None
    precision: Literal["EXACT", "APPROXIMATE", "CONSTRAINT"]
    constraints: list[dict[str, object]] = Field(default_factory=list)


class EvaluatedEvent(StrictModel):
    event_definition_id: UUID
    event_code: str
    occurrence_index: int = Field(default=0, ge=0)
    status: PatientEventStatus
    timing: ResolvedTiming | None = None
    applicability_result: str | None = None
    condition_result: str | None = None
    dependency_result: dict[str, object] = Field(default_factory=dict)
    explanation: dict[str, object] = Field(default_factory=dict)


class EvaluationResult(StrictModel):
    schedule_version_id: UUID
    patient_id: UUID
    evaluator_version: str
    evaluated_at: datetime = Field(default_factory=utc_now)
    input_snapshot: dict[str, object]
    events: list[EvaluatedEvent]
