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
    external_trial_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # Where this trial's visit dates are READ from. Defaults to LEGACY, and can
    # only move to ENGINE through a recorded parity run that matched. Writes stay
    # on the operational store either way - this switches reads only, so a
    # disagreement discovered after cutover is reversible without data loss.
    schedule_read_mode: Mapped[str] = mapped_column(
        String(16), default="LEGACY", server_default="LEGACY")


class ParityRun(IdMixin, Base):
    """One recorded comparison of the engine against the operational schedule.

    Kept because a cutover is a decision someone made on evidence, and the
    evidence has to still exist afterwards. A run is tied to the schedule version
    it compared, so a later amendment invalidates it rather than silently
    carrying an old approval forward.
    """

    __tablename__ = "uctsm_parity_runs"
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    trial_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_trials.id", ondelete="CASCADE"), index=True)
    patient_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    schedule_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_schedule_versions.id"))
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    compared: Mapped[int] = mapped_column(Integer, default=0)
    matched: Mapped[int] = mapped_column(Integer, default=0)
    differences: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    ran_by: Mapped[UUID | None] = mapped_column(Uuid)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    external_schedule_definition_id: Mapped[str | None] = mapped_column(String(128), index=True)


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
    # Doc 9 s8. When this version starts applying to NEW enrolments. Approval and
    # effect are different moments: an amendment approved today may not be in
    # force until a stated date, and conflating them enrols patients onto a
    # version that does not yet apply. NULL means "in force once approved".
    effective_from: Mapped[date | None] = mapped_column(Date, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(32), default="uctsm.v1")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    dimensions: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    conditional_definitions: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    repeat_blocks: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    confinement_episodes: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)


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
    # Doc 8 s10: "Part A, Cohort 1". Both columns move together - a half-stated
    # hierarchy behaves as if there were none.
    parent_dimension_type: Mapped[str | None] = mapped_column(String(32))
    parent_code: Mapped[str | None] = mapped_column(String(128))


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
    source_condition_code: Mapped[str | None] = mapped_column(String(128))
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
    # Doc 10 s2-s3, s31-s33. Left null when the protocol does not state a mode:
    # a default here would send a patient travelling on a guess.
    visit_mode: Mapped[str | None] = mapped_column(String(64))
    allowed_visit_modes: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    # Doc 10 s13-s15: ON_DEMAND definitions exist but are never due.
    activation: Mapped[str] = mapped_column(String(16), default="SCHEDULED", server_default="SCHEDULED")
    epoch_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_epochs.id"))
    sequence_number: Mapped[int | None] = mapped_column(Integer)
    timing: Mapped[dict[str, Any]] = mapped_column(JsonType)
    conditions: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    dependency_mode: Mapped[str | None] = mapped_column(String(32))
    conditional_actions: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    qualifiers: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    confinement: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
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
    applicability: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
    qualifiers: Mapped[list[Any] | None] = mapped_column(JsonType, default=list)
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
    dimension_values: Mapped[dict[str, Any] | None] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    external_patient_id: Mapped[str | None] = mapped_column(String(128), index=True)


class PatientScheduleAssignment(IdMixin, Base):
    __tablename__ = "uctsm_patient_schedule_assignments"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id"), index=True)
    previous_schedule_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_schedule_versions.id"))
    impact_proposal_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    assignment_type: Mapped[str] = mapped_column(String(32), default="ENROLMENT")
    reason: Mapped[str | None] = mapped_column(Text)
    assigned_by: Mapped[UUID | None] = mapped_column(Uuid)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ScheduleImpactProposal(IdMixin, Base):
    __tablename__ = "uctsm_schedule_impact_proposals"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    from_schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id"), index=True)
    to_schedule_version_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_schedule_versions.id"), index=True)
    horizon: Mapped[date] = mapped_column(Date)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    impact: Mapped[dict[str, Any]] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    confirmed_by: Mapped[UUID | None] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    logical_occurrence_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4, index=True)
    logical_key: Mapped[str] = mapped_column(String(256), index=True)
    supersedes_patient_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("uctsm_patient_events.id"))
    protected_history: Mapped[bool] = mapped_column(Boolean, default=False)
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
    visit_mode: Mapped[str | None] = mapped_column(String(64))
    unscheduled_reason: Mapped[str | None] = mapped_column(Text)


class PatientUnscheduledVisit(IdMixin, Base):
    """One unscheduled visit a site created for a patient (doc 10 s14).

    Kept separate from the generated schedule: the patient's own actions are
    inputs to evaluation, so regenerating a schedule can never erase them.
    """

    __tablename__ = "uctsm_patient_unscheduled_visits"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    event_definition_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_events.id"))
    event_code: Mapped[str] = mapped_column(String(128))
    occurred_on: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    visit_mode: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class PatientCondition(IdMixin, Base):
    """Append-only lifecycle of one protocol condition for one patient.

    A correction or reversal writes a new row and supersedes the previous one, so
    an incorrectly activated pathway can be undone without erasing the fact that it
    was activated (requirement doc 2 sections 34-36).
    """

    __tablename__ = "uctsm_patient_conditions"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    condition_code: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    occurrence_index: Mapped[int] = mapped_column(Integer, default=0)
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    resolution_date: Mapped[date | None] = mapped_column(Date)
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid)
    impact_proposal_id: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_by: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str | None] = mapped_column(Text)


class PatientActivity(IdMixin, TimestampMixin, Base):
    """One protocol activity inside one patient visit occurrence.

    Planned times are recalculated on every evaluation; ``actual_time`` and a
    NOT_DONE status are execution history and are only written by explicit
    site data entry, never by re-evaluation.
    """

    __tablename__ = "uctsm_patient_activities"
    __table_args__ = (UniqueConstraint("patient_event_id", "activity_definition_id"),)
    patient_event_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patient_events.id", ondelete="CASCADE"), index=True)
    activity_definition_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_activities.id"))
    activity_code: Mapped[str | None] = mapped_column(String(128), index=True)
    logical_key: Mapped[str] = mapped_column(String(320), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    requiredness: Mapped[str] = mapped_column(String(32))
    sequence_number: Mapped[int | None] = mapped_column(Integer)
    planned_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    earliest_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timing_resolution: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    generation_reason: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class PatientActivityRecord(IdMixin, Base):
    """Append-only site record of an intra-day actual time or completion decision."""

    __tablename__ = "uctsm_patient_activity_records"
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("uctsm_patients.id", ondelete="CASCADE"), index=True)
    event_code: Mapped[str] = mapped_column(String(128), index=True)
    occurrence_index: Mapped[int] = mapped_column(Integer, default=0)
    activity_code: Mapped[str] = mapped_column(String(128), index=True)
    logical_key: Mapped[str] = mapped_column(String(320), index=True)
    status: Mapped[str] = mapped_column(String(32))
    actual_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_by: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str | None] = mapped_column(Text)


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
