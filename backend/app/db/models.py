from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


JsonType = JSON().with_variant(JSONB(), "postgresql")


class IdMixin:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=func.gen_random_uuid())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Trial(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_trials"
    organization_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    protocol_id: Mapped[str | None] = mapped_column(Text)
    study_title: Mapped[str | None] = mapped_column(Text)
    indication: Mapped[str | None] = mapped_column(Text)
    drug_name: Mapped[str | None] = mapped_column(Text)
    sponsor_name: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class Protocol(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_protocols"
    __table_args__ = (UniqueConstraint("trial_id", "protocol_number"),)
    trial_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_trials.id"), index=True)
    protocol_number: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("uctsm_protocol_versions.id", use_alter=True, name="fk_uctsm_protocol_current_version")
    )


class ProtocolVersion(IdMixin, Base):
    __tablename__ = "uctsm_protocol_versions"
    __table_args__ = (UniqueConstraint("protocol_id", "version_label"),)
    protocol_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_protocols.id"), index=True)
    version_label: Mapped[str] = mapped_column(Text)
    amendment_number: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    document_name: Mapped[str] = mapped_column(Text)
    document_uri: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(128), index=True)
    uploaded_by: Mapped[UUID | None] = mapped_column(Uuid)
    extraction_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractionRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_extraction_runs"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    protocol_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_protocol_versions.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(32), default="uctsm.v1")
    document_hash: Mapped[str] = mapped_column(String(128))
    configuration: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    trace: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduleDefinition(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_schedule_definitions"
    protocol_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_protocol_versions.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    schedule_type: Mapped[str] = mapped_column(String(64), default="PRIMARY")


class ScheduleVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_schedule_versions"
    __table_args__ = (UniqueConstraint("schedule_definition_id", "version_number"),)
    schedule_definition_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_definitions.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    based_on_schedule_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_schedule_versions.id"))
    extraction_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_extraction_runs.id"))
    approved_by: Mapped[UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(32), default="uctsm.v1")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class ScheduleChildMixin(IdMixin):
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Epoch(ScheduleChildMixin, Base):
    __tablename__ = "uctsm_epochs"
    __table_args__ = (UniqueConstraint("schedule_version_id", "code"),)
    code: Mapped[str] = mapped_column(String(128))
    protocol_label: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    sequence_number: Mapped[int | None] = mapped_column(Integer)
    timing: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    applicability: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    evidence_refs: Mapped[list[Any]] = mapped_column(JsonType, default=list)


class DimensionBase(ScheduleChildMixin):
    __abstract__ = True
    code: Mapped[str] = mapped_column(String(128))
    protocol_label: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    criteria: Mapped[dict[str, Any] | None] = mapped_column(JsonType)


class Arm(DimensionBase, Base):
    __tablename__ = "uctsm_arms"
    __table_args__ = (UniqueConstraint("schedule_version_id", "code"),)


class Cohort(DimensionBase, Base):
    __tablename__ = "uctsm_cohorts"
    __table_args__ = (UniqueConstraint("schedule_version_id", "code"),)


class Population(DimensionBase, Base):
    __tablename__ = "uctsm_populations"
    __table_args__ = (UniqueConstraint("schedule_version_id", "code"),)


class Anchor(ScheduleChildMixin, Base):
    __tablename__ = "uctsm_anchors"
    __table_args__ = (UniqueConstraint("schedule_version_id", "code"),)
    code: Mapped[str] = mapped_column(String(128))
    protocol_label: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    anchor_type: Mapped[str] = mapped_column(String(64))
    derivation_rule: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    source_event_code: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="RESOLVED")
    evidence_refs: Mapped[list[Any]] = mapped_column(JsonType, default=list)


class Event(ScheduleChildMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_events"
    __table_args__ = (UniqueConstraint("schedule_version_id", "code"),)
    code: Mapped[str] = mapped_column(String(128))
    protocol_label: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(64))
    epoch_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_epochs.id"))
    sequence_number: Mapped[int | None] = mapped_column(Integer)
    timing: Mapped[dict[str, Any]] = mapped_column(JsonType)
    conditions: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    interpretation_status: Mapped[str] = mapped_column(String(32), default="EXTRACTED")
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JsonType, default=list)


class EventApplicability(IdMixin, Base):
    __tablename__ = "uctsm_event_applicability"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id", ondelete="CASCADE"), index=True)
    dimension_type: Mapped[str] = mapped_column(String(32))
    dimension_id: Mapped[UUID | None] = mapped_column(Uuid)
    expression: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventDependency(ScheduleChildMixin, Base):
    __tablename__ = "uctsm_event_dependencies"
    __table_args__ = (
        CheckConstraint("source_event_id <> target_event_id"),
        UniqueConstraint("source_event_id", "target_event_id", "dependency_type"),
    )
    source_event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id"))
    target_event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id"))
    dependency_type: Mapped[str] = mapped_column(String(32))
    condition: Mapped[dict[str, Any] | None] = mapped_column(JsonType)


class EventRecurrence(IdMixin, Base):
    __tablename__ = "uctsm_event_recurrence"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id", ondelete="CASCADE"), unique=True)
    rule: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Activity(IdMixin, Base):
    __tablename__ = "uctsm_activities"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id", ondelete="CASCADE"), index=True)
    code: Mapped[str | None] = mapped_column(String(128))
    protocol_label: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    activity_type: Mapped[str] = mapped_column(String(64))
    requiredness: Mapped[str] = mapped_column(String(32), default="REQUIRED")
    timing: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    conditions: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    interpretation_status: Mapped[str] = mapped_column(String(32), default="EXTRACTED")
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_refs: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Evidence(IdMixin, Base):
    __tablename__ = "uctsm_evidence"
    protocol_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_protocol_versions.id"), index=True)
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(64))
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(Text)
    table_title: Mapped[str | None] = mapped_column(Text)
    row_identifier: Mapped[str | None] = mapped_column(Text)
    column_identifier: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    extraction_context: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimEvidence(IdMixin, Base):
    __tablename__ = "uctsm_claim_evidence"
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_evidence.id", ondelete="CASCADE"), index=True)
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id", ondelete="CASCADE"), index=True)
    claim_type: Mapped[str] = mapped_column(String(64))
    claim_entity_type: Mapped[str] = mapped_column(String(64))
    claim_entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    claim_path: Mapped[str | None] = mapped_column(Text)
    interpretation: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationIssue(IdMixin, Base):
    __tablename__ = "uctsm_validation_issues"
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id", ondelete="CASCADE"), index=True)
    validator_version: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[UUID | None] = mapped_column(Uuid)
    issue_code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    resolved_by: Mapped[UUID | None] = mapped_column(Uuid)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewDecision(IdMixin, Base):
    __tablename__ = "uctsm_review_decisions"
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id"), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[UUID | None] = mapped_column(Uuid)
    field_path: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(32))
    previous_value: Mapped[Any | None] = mapped_column(JsonType)
    new_value: Mapped[Any | None] = mapped_column(JsonType)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid)
    reason: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Patient(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_patients"
    __table_args__ = (UniqueConstraint("trial_id", "patient_code"),)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    trial_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_trials.id"), index=True)
    site_id: Mapped[UUID | None] = mapped_column(Uuid)
    patient_code: Mapped[str] = mapped_column(Text)
    current_schedule_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_schedule_versions.id"))
    arm_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_arms.id"))
    cohort_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_cohorts.id"))
    population_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_populations.id"))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")


class PatientAnchor(IdMixin, Base):
    __tablename__ = "uctsm_patient_anchors"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    anchor_definition_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_anchors.id"))
    value_date: Mapped[date | None] = mapped_column(Date)
    value_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_reference: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    recorded_by: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class PatientState(IdMixin, Base):
    __tablename__ = "uctsm_patient_states"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    state_code: Mapped[str] = mapped_column(String(128))
    state_value: Mapped[Any] = mapped_column(JsonType)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_by: Mapped[UUID | None] = mapped_column(Uuid)
    source_reference: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleGenerationRun(IdMixin, Base):
    __tablename__ = "uctsm_schedule_generation_runs"
    __table_args__ = (UniqueConstraint("patient_id", "idempotency_key"),)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id"), index=True)
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    run_type: Mapped[str] = mapped_column(String(32), default="PATIENT_EVALUATION")
    evaluator_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class PatientSchedule(IdMixin, Base):
    __tablename__ = "uctsm_patient_schedules"
    __table_args__ = (UniqueConstraint("patient_id", "schedule_version_id"),)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id"), index=True)
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    current_evaluation_id: Mapped[UUID | None] = mapped_column(Uuid)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    generation_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_schedule_generation_runs.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class ScheduleEvaluation(IdMixin, Base):
    __tablename__ = "uctsm_schedule_evaluations"
    patient_schedule_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patient_schedules.id", ondelete="CASCADE"), index=True)
    generation_run_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_generation_runs.id"), unique=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JsonType)
    evaluator_version: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PatientEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "uctsm_patient_events"
    __table_args__ = (UniqueConstraint("schedule_evaluation_id", "event_definition_id", "occurrence_index"),)
    patient_schedule_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patient_schedules.id", ondelete="CASCADE"), index=True)
    schedule_evaluation_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_evaluations.id", ondelete="CASCADE"), index=True)
    event_definition_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id"))
    occurrence_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    nominal_start_date: Mapped[date | None] = mapped_column(Date)
    nominal_end_date: Mapped[date | None] = mapped_column(Date)
    earliest_date: Mapped[date | None] = mapped_column(Date)
    latest_date: Mapped[date | None] = mapped_column(Date)
    timing_resolution: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    applicability_result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    condition_result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    dependency_result: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    generation_reason: Mapped[dict[str, Any]] = mapped_column(JsonType)


class PatientEventOccurrence(IdMixin, Base):
    __tablename__ = "uctsm_patient_event_occurrences"
    patient_event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patient_events.id", ondelete="CASCADE"), index=True)
    occurrence_type: Mapped[str] = mapped_column(String(32))
    scheduled_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32))
    recorded_by: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class AuditEvent(IdMixin, Base):
    __tablename__ = "uctsm_audit_events"
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    before: Mapped[Any | None] = mapped_column(JsonType)
    after: Mapped[Any | None] = mapped_column(JsonType)
    reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


Index("ix_uctsm_patient_events_dates", PatientEvent.nominal_start_date, PatientEvent.latest_date)
Index("ix_uctsm_patient_states_patient_time", PatientState.patient_id, PatientState.effective_at)
