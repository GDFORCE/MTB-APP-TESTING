from datetime import date, datetime, timedelta
import os
from typing import Annotated, Any, Callable, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.base import get_session
from app.db.repositories import ScheduleRepository
from app.domain.schedule.diff import compare_schedule_versions
from app.domain.schedule.exceptions import ImmutableScheduleError, ScheduleNotApprovedError
from app.domain.schedule.projection import project_schedule
from app.domain.schedule.models import (
    ConditionState, Event, PatientConditionStatus, RollingHorizon,
)
from app.services.schedule_service import PatientScheduleService, ScheduleReviewService
from app.domain.schedule.enrolment import enrolment_options
from app.services.anchor_status_service import AnchorStatusService
from app.services.parity_service import ParityService
from app.services.dashboard_service import DashboardService
from app.services.deviation_service import DeviationService
from app.services.impact_service import PatientScheduleImpactService
from app.services.schedule_projection import (
    ProjectedConfinement, ProjectedEvent, project,
)
from app.extraction.runner import execute_run, provider_configured
from app.services.extraction_service import ExtractionService
from app.services.demo_service import DemoService


class BulkConfirmIn(BaseModel):
    """Doc s14: bulk confirmation must record WHY, same as any other reviewed
    decision - it is a real reviewer action, just not a per-field one."""

    reason: str = Field(min_length=1)


class ReviewDecisionIn(BaseModel):
    decision: str
    entity_type: str | None = None
    entity_id: UUID | None = None
    field_path: str | None = None
    previous_value: Any | None = None
    new_value: Any | None = None
    reason: str | None = None
    comment: str | None = None


class QualifierResolutionIn(BaseModel):
    """A reviewer stating what one footnote means (doc s6).

    ``reason`` is mandatory on every outcome, including NOT_APPLICABLE and
    ESCALATE: the audit trail has to answer "why was this decided" for a rule
    that changes what happens to a patient.
    """

    decision: Literal["ACCEPT", "EDIT", "NOT_APPLICABLE", "ESCALATE"]
    reason: str = Field(min_length=1)
    #: Required to resolve a qualifier that arrived UNRESOLVED - saying what
    #: kind of rule the footnote is IS the resolution.
    category: str | None = None
    #: EDIT only: the reviewer's own wording, replacing the extracted text.
    text: str | None = None
    scope: str | None = None
    #: Which visits/activities the footnote governs. A non-global qualifier
    #: cannot be resolved without them.
    target_codes: list[str] | None = None


class ReviewIn(BaseModel):
    decision: str
    comment: str | None = None
    reason: str | None = None
    # Doc 9 s8. When this version starts applying to NEW enrolments. Omit it and
    # the version is in force as soon as it is approved.
    effective_from: date | None = None


class ParityCheckIn(BaseModel):
    """The operational schedule to compare against, supplied by the caller.

    The canonical services speak to Postgres; the operational store is Mongo. The
    caller crosses that boundary and hands the visits over, so nothing here has
    to reach across it.
    """

    legacy_visits: list[dict[str, object]] = Field(default_factory=list)
    horizon: date | None = None


class ReadModeIn(BaseModel):
    mode: Literal["LEGACY", "ENGINE"]
    reason: str = Field(min_length=1)


class UnscheduledVisitIn(BaseModel):
    event_code: str = Field(min_length=1)
    occurred_on: date
    # Required by doc 10 s14: an unscheduled visit is defined by its cause.
    reason: str = Field(min_length=1)
    visit_mode: str | None = None


class EvaluateIn(BaseModel):
    horizon: date
    # Doc 3 s7-s8. Bounds what is written down for an open-ended repeat; it is
    # never a statement about how long the protocol runs, so the response also
    # carries the repeat rules that continue past it.
    upcoming_limit: int | None = Field(default=None, gt=0)
    as_of: date | None = None

    def rolling(self) -> RollingHorizon | None:
        if self.upcoming_limit is None:
            return None
        return RollingHorizon(upcoming_limit=self.upcoming_limit, as_of=self.as_of)


class TrialIn(BaseModel):
    protocol_id: str | None = None
    study_title: str | None = None
    indication: str | None = None
    drug_name: str | None = None
    sponsor_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_trial_id: str | None = None


class ProtocolIn(BaseModel):
    protocol_number: str


class ProtocolVersionIn(BaseModel):
    version_label: str
    amendment_number: str | None = None
    effective_date: date | None = None
    document_name: str
    document_uri: str
    document_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatientIn(BaseModel):
    patient_code: str
    site_id: UUID | None = None
    schedule_definition_id: UUID | None = None
    arm_id: UUID | None = None
    cohort_id: UUID | None = None
    population_id: UUID | None = None
    dimension_values: dict[str, list[str]] = Field(default_factory=dict)
    external_patient_id: str | None = None


class PatientAnchorIn(BaseModel):
    anchor_definition_id: UUID
    value_date: date | None = None
    value_datetime: str | None = None
    status: Literal["PENDING_CONFIRMATION"] = "PENDING_CONFIRMATION"
    source_type: str | None = None
    source_reference: dict[str, Any] | None = None


class PatientStateIn(BaseModel):
    state_code: str
    state_value: Any
    effective_at: str
    source_reference: dict[str, Any] | None = None


class StateImpactPreviewIn(BaseModel):
    state: PatientStateIn
    horizon: date
    reason: str = Field(min_length=1)
    expires_in_minutes: int = Field(default=30, ge=5, le=1440)


class PatientOccurrenceIn(BaseModel):
    occurrence_type: str
    scheduled_date: date | None = None
    actual_date: date | None = None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActivityRecordIn(BaseModel):
    event_code: str = Field(min_length=1)
    occurrence_index: int = Field(default=0, ge=0)
    activity_code: str = Field(min_length=1)
    status: Literal["COMPLETED", "NOT_DONE", "PENDING"]
    actual_time: datetime | None = None
    reason: str | None = None


class ConditionChangeIn(BaseModel):
    """A proposed condition state change, previewed before it is applied."""

    condition_code: str = Field(min_length=1)
    state: Literal[
        "NOT_OCCURRED", "PENDING_CONFIRMATION", "ACTIVE",
        "RESOLVED", "NOT_APPLICABLE", "CANCELLED",
    ]
    occurrence_date: date | None = None
    resolution_date: date | None = None
    occurrence_index: int = Field(default=0, ge=0)


class ConditionPreviewIn(BaseModel):
    condition: ConditionChangeIn
    horizon: date
    reason: str = Field(min_length=1)
    expires_in_minutes: int = Field(default=30, ge=5, le=1440)


class ImpactPreviewIn(BaseModel):
    target_schedule_version_id: UUID
    horizon: date
    reason: str = Field(min_length=1)
    expires_in_minutes: int = Field(default=30, ge=5, le=1440)


class ImpactConfirmIn(BaseModel):
    reason: str | None = None


class AnchorImpactPreviewIn(BaseModel):
    horizon: date
    reason: str = Field(min_length=1)
    expires_in_minutes: int = Field(default=30, ge=5, le=1440)


class AssignmentPreviewIn(BaseModel):
    """A proposed change to which protocol groups a patient belongs to.

    Keys are dimension names the schedule defines - ARM, COHORT, POPULATION, or
    any open dimension such as PART, SEQUENCE or SUBSTUDY (doc 8 s35). A null
    value removes the assignment.
    """

    assignment: dict[str, str | None] = Field(min_length=1)
    horizon: date
    reason: str = Field(min_length=1)
    expires_in_minutes: int = Field(default=30, ge=5, le=1440)


class ImpactCancelIn(BaseModel):
    reason: str = Field(min_length=1)


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _uuid(value: object, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise HTTPException(403, f"Authenticated user has no valid {label}") from error


def _identity(user: dict[str, Any]) -> tuple[UUID, UUID]:
    actor = _uuid(user.get("id"), "identity")
    organization = user.get("organization_id") or user.get("org_id")
    return actor, _uuid(organization, "organization context")


def _assert_schedule_tenant(session: Session, schedule_version_id: UUID, organization_id: UUID) -> None:
    actual = session.execute(
        select(db.Trial.organization_id)
        .join(db.Protocol, db.Protocol.trial_id == db.Trial.id)
        .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
        .join(db.ScheduleDefinition, db.ScheduleDefinition.protocol_version_id == db.ProtocolVersion.id)
        .join(db.ScheduleVersion, db.ScheduleVersion.schedule_definition_id == db.ScheduleDefinition.id)
        .where(db.ScheduleVersion.id == schedule_version_id)
    ).scalar_one_or_none()
    if actual is None or actual != organization_id:
        raise HTTPException(404, "Schedule version not found")


async def _run_extraction_task(run_id: UUID) -> None:
    """Execute one queued extraction on its own session.

    The request's session is closed once the 202 is returned, so a background
    task that borrowed it would fail on its first query.
    """
    from app.db.base import get_session

    for session in get_session():
        try:
            await execute_run(session, run_id)
        finally:
            session.close()
        break


def create_uctsm_router(current_user_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api/uctsm", tags=["universal-schedule"])
    User = Annotated[dict[str, Any], Depends(current_user_dependency)]
    Db = Annotated[Session, Depends(get_session)]

    @router.post("/demo/seed", status_code=201)
    def seed_demo(user: User, session: Db):
        if os.getenv("UCTSM_DEMO_MODE", "").strip().lower() not in {"1", "true", "yes"}:
            raise HTTPException(404, "Not found")
        actor_id, organization_id = _identity(user)
        try:
            result = DemoService(session).seed(
                organization_id=organization_id, actor_id=actor_id,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise

    @router.post("/trials", status_code=201)
    def create_trial(body: TrialIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        row = db.Trial(
            organization_id=organization_id, protocol_id=body.protocol_id,
            study_title=body.study_title, indication=body.indication,
            drug_name=body.drug_name, sponsor_name=body.sponsor_name,
            metadata_json=body.metadata,
            external_trial_id=body.external_trial_id,
        )
        session.add(row)
        session.flush()
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="TRIAL_CREATED", entity_type="TRIAL", entity_id=row.id,
            before=None, after={"study_title": row.study_title},
        ))
        session.commit()
        return {"id": str(row.id), "study_title": row.study_title}

    @router.post("/trials/{trial_id}/protocols", status_code=201)
    def create_protocol(trial_id: UUID, body: ProtocolIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        trial = session.scalar(select(db.Trial).where(
            db.Trial.id == trial_id, db.Trial.organization_id == organization_id,
        ))
        if trial is None:
            raise HTTPException(404, "Trial not found")
        row = db.Protocol(trial_id=trial.id, protocol_number=body.protocol_number)
        session.add(row)
        session.flush()
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PROTOCOL_CREATED", entity_type="PROTOCOL", entity_id=row.id,
            before=None, after={"protocol_number": row.protocol_number},
        ))
        session.commit()
        return {"id": str(row.id), "protocol_number": row.protocol_number}

    @router.post("/protocols/{protocol_id}/versions", status_code=201)
    def create_protocol_version(protocol_id: UUID, body: ProtocolVersionIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        protocol = session.scalar(
            select(db.Protocol)
            .join(db.Trial, db.Trial.id == db.Protocol.trial_id)
            .where(db.Protocol.id == protocol_id, db.Trial.organization_id == organization_id)
        )
        if protocol is None:
            raise HTTPException(404, "Protocol not found")
        if not body.document_uri.startswith(("private://", "s3://", "gs://", "azure://")):
            raise HTTPException(422, "document_uri must identify private object storage")
        if len(body.document_hash) < 32:
            raise HTTPException(422, "document_hash is invalid")
        row = db.ProtocolVersion(
            protocol_id=protocol.id, version_label=body.version_label,
            amendment_number=body.amendment_number, effective_date=body.effective_date,
            document_name=body.document_name, document_uri=body.document_uri,
            document_hash=body.document_hash.lower(), uploaded_by=actor_id,
            metadata_json=body.metadata,
        )
        session.add(row)
        session.flush()
        protocol.current_version_id = row.id
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PROTOCOL_VERSION_UPLOADED", entity_type="PROTOCOL_VERSION", entity_id=row.id,
            before=None, after={"version_label": row.version_label, "document_hash": row.document_hash},
        ))
        session.commit()
        return {"id": str(row.id), "version_label": row.version_label, "extraction_status": row.extraction_status}

    @router.get("/trials/{trial_id}/approved-schedules")
    def approved_schedules(trial_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        rows = session.execute(
            select(db.ScheduleDefinition, db.ScheduleVersion)
            .join(db.ScheduleVersion, db.ScheduleVersion.schedule_definition_id == db.ScheduleDefinition.id)
            .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .join(db.Trial, db.Trial.id == db.Protocol.trial_id)
            .where(
                db.Trial.id == trial_id, db.Trial.organization_id == organization_id,
                db.ScheduleVersion.status == "APPROVED",
            )
            .order_by(db.ScheduleDefinition.name, db.ScheduleVersion.version_number.desc())
        ).all()
        return [{
            "schedule_definition_id": str(definition.id), "schedule_version_id": str(version.id),
            "name": definition.name, "schedule_type": definition.schedule_type,
            "version_number": version.version_number, "approved_at": version.approved_at,
            "effective_from": version.effective_from,
            # Doc 9 s8: a version approved but not yet in force must not look
            # available for enrolment just because it appears in this list.
            "in_force": (
                version.effective_from is None
                or version.effective_from <= date.today()
            ),
        } for definition, version in rows]

    @router.post("/trials/{trial_id}/patients", status_code=201)
    def create_patient(trial_id: UUID, body: PatientIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        trial = session.scalar(select(db.Trial).where(
            db.Trial.id == trial_id, db.Trial.organization_id == organization_id,
        ))
        if trial is None:
            raise HTTPException(404, "Trial not found")
        query = (
            select(db.ScheduleVersion)
            .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
            .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .where(
                db.Protocol.trial_id == trial_id,
                db.Protocol.current_version_id == db.ProtocolVersion.id,
                db.ScheduleVersion.status == "APPROVED",
            )
        )
        if body.schedule_definition_id:
            query = query.where(db.ScheduleDefinition.id == body.schedule_definition_id)
        else:
            query = query.where(db.ScheduleDefinition.schedule_type == "PRIMARY")
        approved = list(session.scalars(query.order_by(db.ScheduleVersion.version_number.desc())))
        # Doc 9 s8: a version approved but not yet effective must not take new
        # enrolments. Falling back to the newest IN-FORCE version is what a site
        # expects; silently enrolling onto a future amendment is not.
        today = date.today()
        schedules = [
            item for item in approved
            if item.effective_from is None or item.effective_from <= today
        ]
        if not schedules:
            if approved:
                soonest = min(
                    item.effective_from for item in approved
                    if item.effective_from is not None
                )
                raise HTTPException(
                    409,
                    f"The approved schedule does not take effect until "
                    f"{soonest.isoformat()}; no version is in force today",
                )
            raise HTTPException(409, "No applicable approved schedule is available")
        definitions = {item.schedule_definition_id for item in schedules}
        if body.schedule_definition_id is None and len(definitions) > 1:
            raise HTTPException(409, "Multiple approved primary schedules apply; select a schedule definition")
        selected = schedules[0]
        for model, identifier, label in (
            (db.Arm, body.arm_id, "arm"), (db.Cohort, body.cohort_id, "cohort"),
            (db.Population, body.population_id, "population"),
        ):
            if identifier:
                dimension = session.get(model, identifier)
                if dimension is None or dimension.schedule_version_id != selected.id:
                    raise HTTPException(422, f"Selected {label} does not belong to the approved schedule")
        if body.dimension_values:
            schedule = ScheduleRepository(session).get(selected.id)
            allowed: dict[str, set[str]] = {}
            for dimension in schedule.dimensions:
                allowed.setdefault(dimension.dimension_type, set()).add(dimension.code)
            for dimension_type, values in body.dimension_values.items():
                unknown = sorted(set(values) - allowed.get(dimension_type, set()))
                if dimension_type not in allowed or unknown:
                    raise HTTPException(
                        422,
                        f"Invalid {dimension_type} assignment values: {', '.join(unknown or values)}",
                    )
        row = db.Patient(
            organization_id=organization_id, trial_id=trial.id, site_id=body.site_id,
            patient_code=body.patient_code, current_schedule_version_id=selected.id,
            arm_id=body.arm_id, cohort_id=body.cohort_id, population_id=body.population_id,
            dimension_values=body.dimension_values,
            external_patient_id=body.external_patient_id,
        )
        session.add(row)
        session.flush()
        # Doc 9 s12: the version a patient was enrolled under is pinned here, not
        # inferred later from the trial's current version. Without this row an
        # amendment would silently re-date an already-enrolled patient.
        session.add(db.PatientScheduleAssignment(
            patient_id=row.id, schedule_version_id=selected.id,
            assignment_type="ENROLMENT", reason="Schedule assigned at enrolment",
            assigned_by=actor_id,
        ))
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_CREATED_WITH_SCHEDULE", entity_type="PATIENT", entity_id=row.id,
            before=None, after={"schedule_version_id": str(selected.id)},
        ))
        session.commit()
        return {
            "id": str(row.id), "patient_code": row.patient_code,
            "schedule_version_id": str(selected.id),
        }

    @router.post("/protocols/{protocol_id}/versions/{protocol_version_id}/extract-schedule", status_code=202)
    def queue_extraction(
        protocol_id: UUID, protocol_version_id: UUID, user: User, session: Db,
        background: BackgroundTasks,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        _, organization_id = _identity(user)
        protocol_version = session.scalar(
            select(db.ProtocolVersion)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .join(db.Trial, db.Trial.id == db.Protocol.trial_id)
            .where(
                db.ProtocolVersion.id == protocol_version_id,
                db.Protocol.id == protocol_id,
                db.Trial.organization_id == organization_id,
            )
        )
        if protocol_version is None:
            raise HTTPException(404, "Protocol version not found")
        try:
            run = ExtractionService(session).queue(
                organization_id=organization_id, protocol_version=protocol_version,
                idempotency_key=idempotency_key,
                provider=os.getenv("UCTSM_EXTRACTION_PROVIDER"),
                model_name=os.getenv("UCTSM_EXTRACTION_MODEL"),
                model_version=os.getenv("UCTSM_EXTRACTION_MODEL_VERSION"),
                prompt_version=os.getenv("UCTSM_PROMPT_VERSION", "uctsm-extractor.v1"),
            )
            session.commit()
        except ValueError as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error

        # Reading a full protocol takes minutes, so the run executes in the
        # background and the caller polls GET /extraction-runs/{id}. A run that
        # cannot start says so now rather than sitting QUEUED forever.
        configured = provider_configured()
        if configured:
            background.add_task(_run_extraction_task, run.id)
        else:
            run.status = "FAILED"
            run.error_details = {
                "type": "ExtractionNotConfigured",
                "message": "ANTHROPIC_API_KEY is not set on the server",
            }
            session.commit()
        return {
            "extraction_run_id": str(run.id), "status": run.status,
            "provider_configured": configured,
        }

    @router.get("/extraction-runs/{run_id}")
    def get_extraction_run(run_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        run = session.scalar(select(db.ExtractionRun).where(
            db.ExtractionRun.id == run_id, db.ExtractionRun.organization_id == organization_id,
        ))
        if run is None:
            raise HTTPException(404, "Extraction run not found")
        return {
            "id": str(run.id), "status": run.status, "protocol_version_id": str(run.protocol_version_id),
            "schema_version": run.schema_version, "prompt_version": run.prompt_version,
            "model": {"provider": run.provider, "name": run.model_name, "version": run.model_version},
            "trace": run.trace, "error_details": run.error_details,
        }

    @router.get("/schedule-versions/{schedule_version_id}")
    def get_schedule(schedule_version_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        return ScheduleRepository(session).get(schedule_version_id).model_dump(mode="json")

    @router.post("/schedule-versions/{schedule_version_id}/validate")
    def validate_schedule(schedule_version_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        try:
            issues = ScheduleReviewService(session).validate(schedule_version_id)
            session.commit()
        except (ValueError, ImmutableScheduleError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        blocking = [item for item in issues if item.blocking]
        return {"status": "VALIDATION_FAILED" if blocking else "VALIDATED", "blocking_issues": len(blocking), "warnings": len(issues) - len(blocking), "issues": [item.model_dump(mode="json") for item in issues]}

    @router.post("/schedule-versions/{schedule_version_id}/review-decisions", status_code=201)
    def record_decision(schedule_version_id: UUID, body: ReviewDecisionIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        try:
            row = ScheduleReviewService(session).record_decision(
                schedule_version_id, reviewer_id=actor_id, **body.model_dump(),
            )
            session.commit()
        except (ValueError, ImmutableScheduleError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {"id": str(row.id), "decision": row.decision}

    @router.post("/schedule-versions/{schedule_version_id}/bulk-confirm", status_code=201)
    def bulk_confirm(schedule_version_id: UUID, body: BulkConfirmIn, user: User, session: Db):
        """Confirm every required field with one action, and record it as one
        action (doc s14) - never presented as N individual field reviews."""
        actor_id, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        try:
            confirmed = ScheduleReviewService(session).bulk_confirm_all_fields(
                schedule_version_id, reviewer_id=actor_id, reason=body.reason,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ImmutableScheduleError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {"fields_confirmed": confirmed, "action": "SCHEDULE_BULK_CONFIRMED"}

    @router.put("/schedule-versions/{schedule_version_id}/events/{event_id}")
    def correct_event(
        schedule_version_id: UUID, event_id: UUID, event: Event,
        user: User, session: Db, reason: Annotated[str, Header(alias="X-Review-Reason")],
    ):
        actor_id, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        if event.id != event_id:
            raise HTTPException(422, "Event body ID must match the route")
        try:
            issues = ScheduleReviewService(session).correct_event(
                schedule_version_id, event, reviewer_id=actor_id, reason=reason,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ImmutableScheduleError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "status": "VALIDATION_REQUIRED",
            "blocking_issues": len([item for item in issues if item.blocking]),
        }

    @router.post(
        "/schedule-versions/{schedule_version_id}/qualifiers/{qualifier_id}/resolve")
    def resolve_qualifier(
        schedule_version_id: UUID, qualifier_id: UUID, body: QualifierResolutionIn,
        user: User, session: Db,
    ):
        """Record what a footnote MEANS, so it can stop blocking approval (doc s6).

        Extraction never interprets a table marker; it carries the marker, its
        text and its evidence and leaves it UNRESOLVED, which blocks approval by
        design. Without this endpoint nothing could ever clear that block, so a
        protocol with a single footnote produced a draft that could not be
        approved at all.
        """
        actor_id, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        try:
            issues = ScheduleReviewService(session).resolve_qualifier(
                schedule_version_id, qualifier_id, reviewer_id=actor_id,
                decision=body.decision, reason=body.reason, category=body.category,
                text=body.text, scope=body.scope, target_codes=body.target_codes,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ImmutableScheduleError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        blocking = [item for item in issues if item.blocking]
        return {
            "qualifier_id": str(qualifier_id),
            "decision": body.decision.strip().upper(),
            "status": "VALIDATION_REQUIRED",
            "blocking_issues": len(blocking),
            "unresolved_qualifiers": len([
                item for item in blocking if item.issue_code == "UNRESOLVED_QUALIFIER"
            ]),
        }

    @router.get("/schedule-versions/{schedule_version_id}/qualifiers")
    def list_qualifiers(schedule_version_id: UUID, user: User, session: Db):
        """Every footnote on this version, with what a reviewer needs to judge it.

        Doc s6 and s17: the marker as printed, the source text, what it is
        attached to, and the evidence behind it - so the reviewer can answer
        "why does MTB think this applies here?" without leaving the screen.
        """
        _, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        schedule = ScheduleRepository(session).get(schedule_version_id)
        evidence_by_id = {item.id: item for item in schedule.evidence}

        rows = []
        for event in schedule.events:
            owners = [("EVENT", event.code, event.display_name, event.qualifiers)]
            owners += [
                ("ACTIVITY", activity.code or activity.display_name,
                 activity.display_name, activity.qualifiers)
                for activity in event.activities
            ]
            for owner_type, owner_code, owner_name, qualifiers in owners:
                for item in qualifiers:
                    rows.append({
                        "id": str(item.id),
                        "marker": item.marker,
                        "text": item.text,
                        "scope": item.scope.value,
                        "category": item.category.value,
                        "resolved": item.resolved,
                        "target_codes": list(item.target_codes),
                        "owner_type": owner_type,
                        "owner_code": owner_code,
                        "owner_name": owner_name,
                        "event_code": event.code,
                        "evidence": [
                            {
                                "id": str(ref),
                                "page_number": getattr(evidence_by_id.get(ref), "page_number", None),
                                "section_title": getattr(evidence_by_id.get(ref), "section_title", None),
                                "source_text": getattr(evidence_by_id.get(ref), "source_text", None),
                            }
                            for ref in item.evidence_refs if ref in evidence_by_id
                        ],
                    })
        return {
            "schedule_version_id": str(schedule_version_id),
            "qualifiers": rows,
            "unresolved": len([item for item in rows if not item["resolved"]]),
        }

    @router.post("/schedule-versions/{schedule_version_id}/submit-review")
    def submit_review(schedule_version_id: UUID, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        try:
            ScheduleReviewService(session).submit_for_review(schedule_version_id, actor_id=actor_id)
            session.commit()
        except ValueError as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {"status": "IN_REVIEW"}

    @router.post("/schedule-versions/{schedule_version_id}/review")
    def review_schedule(schedule_version_id: UUID, body: ReviewIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        service = ScheduleReviewService(session)
        try:
            if body.decision == "APPROVE":
                service.approve(
                    schedule_version_id, reviewer_id=actor_id, comment=body.comment,
                    effective_from=body.effective_from,
                )
                response_status = "APPROVED"
            elif body.decision == "REJECT":
                service.reject(schedule_version_id, reviewer_id=actor_id, reason=body.reason or body.comment or "Rejected")
                response_status = "REJECTED"
            else:
                raise ValueError("decision must be APPROVE or REJECT")
            session.commit()
        except (ValueError, ImmutableScheduleError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {"status": response_status}

    @router.get("/schedule-versions/{schedule_version_id}/projection")
    def get_projection(schedule_version_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        _assert_schedule_tenant(session, schedule_version_id, organization_id)
        return [item.model_dump(mode="json") for item in project_schedule(ScheduleRepository(session).get(schedule_version_id))]

    @router.get("/schedule-versions/{schedule_version_id}/enrolment-options")
    def get_enrolment_options(
        schedule_version_id: UUID, user: User, session: Db,
        arm: str | None = None, cohort: str | None = None,
        population: str | None = None, part: str | None = None,
        substudy: str | None = None, sequence: str | None = None,
    ):
        """Which groups a patient can still be assigned to (doc 8 s15-s18).

        Selections narrow what follows: once a Part is chosen only that Part's
        cohorts are offered. A dimension whose values all nest inside an unchosen
        parent comes back blocked, rather than listing values that cannot apply.
        """
        _, _organization_id = _identity(user)
        try:
            schedule = ScheduleRepository(session).get(schedule_version_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        selected = {
            "ARM": arm, "COHORT": cohort, "POPULATION": population,
            "PART": part, "SUBSTUDY": substudy, "SEQUENCE": sequence,
        }
        return {
            "schedule_version_id": str(schedule_version_id),
            "dimensions": [
                item.model_dump(mode="json")
                for item in enrolment_options(schedule, selected)
            ],
        }

    @router.get("/schedule-versions/{left_id}/diff/{right_id}")
    def diff_schedules(left_id: UUID, right_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        _assert_schedule_tenant(session, left_id, organization_id)
        _assert_schedule_tenant(session, right_id, organization_id)
        repository = ScheduleRepository(session)
        diff = compare_schedule_versions(repository.get(left_id), repository.get(right_id))
        payload = diff.model_dump(mode="json")
        # Doc 9 s10: a reviewer needs to know which differences can affect a
        # patient already on study, not just that bytes differ.
        payload["clinically_significant_count"] = len(diff.clinically_significant())
        return payload

    @router.post("/patients/{patient_id}/schedule/evaluate")
    def evaluate_patient(
        patient_id: UUID, body: EvaluateIn, user: User, session: Db,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        _, organization_id = _identity(user)
        try:
            evaluation, events = PatientScheduleService(session).evaluate(
                patient_id, organization_id=organization_id,
                horizon=body.horizon, idempotency_key=idempotency_key,
                rolling=body.rolling(),
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ScheduleNotApprovedError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "evaluation_id": str(evaluation.id), "evaluator_version": evaluation.evaluator_version,
            "events": [{
                "id": str(item.id), "event_definition_id": str(item.event_definition_id),
                "occurrence_index": item.occurrence_index, "status": item.status,
                "logical_occurrence_id": str(item.logical_occurrence_id),
                "logical_key": item.logical_key,
                "nominal_start_date": item.nominal_start_date,
                "earliest_date": item.earliest_date, "latest_date": item.latest_date,
                "explanation": item.generation_reason,
            } for item in events],
        }

    @router.get("/patients/{patient_id}/schedule")
    def get_patient_schedule(patient_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        patient_schedule = session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient_id,
            db.PatientSchedule.status == "ACTIVE",
        ).order_by(db.PatientSchedule.generated_at.desc()))
        if patient_schedule is None or patient_schedule.current_evaluation_id is None:
            return {"patient_id": str(patient_id), "status": "NOT_EVALUATED", "events": []}
        events = list(session.scalars(select(db.PatientEvent).where(
            db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id,
        ).order_by(db.PatientEvent.nominal_start_date, db.PatientEvent.occurrence_index)))
        actual_by_logical: dict[UUID, date] = {}
        logical_ids = [item.logical_occurrence_id for item in events]
        if logical_ids:
            actual_rows = session.execute(
                select(db.PatientEvent.logical_occurrence_id, db.PatientEventOccurrence.actual_date)
                .join(db.PatientEventOccurrence, db.PatientEventOccurrence.patient_event_id == db.PatientEvent.id)
                .where(
                    db.PatientEvent.logical_occurrence_id.in_(logical_ids),
                    db.PatientEventOccurrence.actual_date.is_not(None),
                )
                .order_by(db.PatientEventOccurrence.recorded_at)
            )
            for logical_id, actual_date in actual_rows:
                actual_by_logical[logical_id] = actual_date
        activities_by_event: dict[UUID, list[dict[str, object]]] = {}
        if events:
            for activity in session.scalars(select(db.PatientActivity).where(
                db.PatientActivity.patient_event_id.in_([item.id for item in events]),
            ).order_by(db.PatientActivity.sequence_number, db.PatientActivity.planned_time)):
                activities_by_event.setdefault(activity.patient_event_id, []).append({
                    "id": str(activity.id),
                    "activity_definition_id": str(activity.activity_definition_id),
                    "activity_code": activity.activity_code,
                    "status": activity.status,
                    "requiredness": activity.requiredness,
                    "sequence_number": activity.sequence_number,
                    "planned_time": activity.planned_time,
                    "earliest_time": activity.earliest_time,
                    "latest_time": activity.latest_time,
                    "actual_time": activity.actual_time,
                    "explanation": activity.generation_reason,
                })
        # Doc 3 s4 and s36: the materialized rows are not the whole protocol. Any
        # repeat that continues past what was written down is reported alongside
        # them, recomputed rather than stored so a corrected anchor is reflected.
        repeat_rules: list[dict[str, object]] = []
        if patient.current_schedule_version_id is not None:
            service = PatientScheduleService(session)
            summaries = service.evaluate_result(
                patient, patient.current_schedule_version_id,
                horizon=date.today() + timedelta(days=365),
                rolling=RollingHorizon(upcoming_limit=len(events) or 1),
            ).repeat_summaries
            repeat_rules = [item.model_dump(mode="json") for item in summaries]
        return {
            "patient_id": str(patient_id), "status": patient_schedule.status,
            "schedule_version_id": str(patient_schedule.schedule_version_id),
            "evaluation_id": str(patient_schedule.current_evaluation_id),
            "repeat_rules": repeat_rules,
            "events": [{
                "id": str(item.id), "event_definition_id": str(item.event_definition_id),
                "occurrence_index": item.occurrence_index, "status": item.status,
                "logical_occurrence_id": str(item.logical_occurrence_id),
                "logical_key": item.logical_key,
                "protected_history": item.protected_history,
                "nominal_start_date": item.nominal_start_date,
                "nominal_end_date": item.nominal_end_date,
                "earliest_date": item.earliest_date, "latest_date": item.latest_date,
                "actual_date": actual_by_logical.get(item.logical_occurrence_id),
                "timing_resolution": item.timing_resolution,
                "activities": activities_by_event.get(item.id, []),
                "explanation": item.generation_reason,
            } for item in events],
        }

    @router.get("/patients/{patient_id}/schedule/projection")
    def get_patient_schedule_projection(
        patient_id: UUID, user: User, session: Db, today: date | None = None,
    ):
        """Reminders and calendar entries implied by the confirmed patient plan.

        Undated, conditional, paused, cancelled, and unscheduled items are reported
        under `suppressed` with a reason rather than silently dropped, so an
        operator can see why a patient is not being reminded about something.
        """
        _, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        patient_schedule = session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient_id,
            db.PatientSchedule.status == "ACTIVE",
        ).order_by(db.PatientSchedule.generated_at.desc()))
        if patient_schedule is None or patient_schedule.current_evaluation_id is None:
            return {"patient_id": str(patient_id), "reminders": [], "calendar": [], "suppressed": []}

        rows = session.execute(
            select(db.PatientEvent, db.Event.code, db.Event.event_type, db.Event.display_name)
            .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
            .where(db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id)
        ).all()
        projected = [
            ProjectedEvent(
                logical_key=row.PatientEvent.logical_key, event_code=row.code,
                event_type=row.event_type, display_name=row.display_name,
                status=row.PatientEvent.status,
                nominal_date=row.PatientEvent.nominal_start_date,
                earliest_date=row.PatientEvent.earliest_date,
                latest_date=row.PatientEvent.latest_date,
                is_unscheduled=str(row.event_type).upper() == "UNSCHEDULED",
            )
            for row in rows
        ]
        # Confinement episodes are recomputed rather than stored, so the calendar
        # always reflects the current admission and discharge state.
        service = PatientScheduleService(session)
        confinements: list[ProjectedConfinement] = []
        episodes = []
        if patient.current_schedule_version_id is not None:
            result = service.evaluate_result(
                patient, patient.current_schedule_version_id,
                horizon=(today or date.today()) + timedelta(days=365),
            )
            episodes = result.confinements
            for episode in result.confinements:
                definition = next(
                    (item for item in service.repository.get(
                        patient.current_schedule_version_id).confinement_episodes
                     if item.code == episode.episode_code), None)
                members = ([
                    definition.admission_event_code, *definition.dose_event_codes,
                    definition.discharge_event_code,
                ] if definition else [])
                confinements.append(ProjectedConfinement(
                    episode_code=episode.episode_code, display_name=episode.display_name,
                    status=episode.status.value,
                    start_date=_as_date(episode.actual_admission or episode.planned_admission),
                    end_date=_as_date(episode.actual_discharge or episode.planned_discharge),
                    member_event_codes=members,
                ))
        projection = project(
            projected, today=today or date.today(), confinements=confinements)
        return {
            "patient_id": str(patient_id),
            "reminders": [item.model_dump(mode="json") for item in projection.reminders],
            "calendar": [item.model_dump(mode="json") for item in projection.calendar],
            "suppressed": [item.model_dump(mode="json") for item in projection.suppressed],
            # Doc 6 s17/s31: the UI shows ONE stay with its study days nested, so
            # it needs the day breakdown, not just the calendar span.
            "confinements": [item.model_dump(mode="json") for item in episodes],
        }

    @router.get("/dashboard/actions")
    def get_dashboard_actions(
        user: User, session: Db, trial_id: UUID | None = None, today: date | None = None,
    ):
        """What currently needs a PI or CRC decision - actions, not a schedule.

        Deliberately bounded: an open-ended protocol must not put every future cycle
        on the board, and a conditional the patient never triggered is absent
        entirely (doc 2 s13, doc 3 s28).
        """
        _, organization_id = _identity(user)
        result = DashboardService(session).actions(
            organization_id=organization_id, trial_id=trial_id, today=today)
        payload = result.model_dump(mode="json")
        counts: dict[str, int] = {}
        for action in result.actions:
            counts[action.category.value] = counts.get(action.category.value, 0) + 1
        payload["summary"] = {"total": len(result.actions), "by_category": counts}
        return payload

    @router.get("/patients/{patient_id}/deviations")
    def get_patient_deviations(
        patient_id: UUID, user: User, session: Db, today: date | None = None,
    ):
        """Protocol deviations derived from the current plan and recorded actuals.

        Nothing is stored: the protocol-expected date moves when an anchor is
        corrected, so a stored deviation would go stale. Items that were never an
        active requirement for this patient come back as NOT_ASSESSABLE with the
        reason, rather than being silently omitted.
        """
        _, organization_id = _identity(user)
        try:
            report = DeviationService(session).report(
                patient_id, organization_id=organization_id, today=today)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        payload = report.model_dump(mode="json")
        payload["summary"] = {
            "deviations": len(report.deviations()),
            "visits_assessed": sum(
                1 for item in report.visits
                if item.deviation_type.value != "NOT_ASSESSABLE"),
            "activity_deviations": sum(
                1 for item in report.activities
                if item.deviation_type.value not in {"NONE", "NOT_ASSESSABLE"}),
        }
        return payload

    @router.post("/patients/{patient_id}/activities", status_code=201)
    def record_patient_activity(
        patient_id: UUID, body: ActivityRecordIn, user: User, session: Db,
    ):
        """Record an intra-day actual time; dependent activity times follow it."""
        actor_id, organization_id = _identity(user)
        try:
            record = PatientScheduleService(session).record_activity(
                patient_id, organization_id=organization_id,
                event_code=body.event_code, occurrence_index=body.occurrence_index,
                activity_code=body.activity_code, status=body.status,
                actual_time=body.actual_time, actor_id=actor_id, reason=body.reason,
            )
            session.commit()
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {
            "id": str(record.id), "logical_key": record.logical_key,
            "status": record.status, "actual_time": record.actual_time,
        }

    @router.post("/patients/{patient_id}/parity-check", status_code=201)
    def run_parity_check(
        patient_id: UUID, body: ParityCheckIn, user: User, session: Db,
    ):
        """Compare this patient's engine schedule against the operational one.

        Records the result, whatever it is. A cutover is a decision someone made
        on evidence, and the evidence has to still exist afterwards.
        """
        actor_id, organization_id = _identity(user)
        try:
            report, run = ParityService(session).compare_patient(
                patient_id, organization_id=organization_id,
                legacy_visits=body.legacy_visits, horizon=body.horizon,
                actor_id=actor_id,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ScheduleNotApprovedError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        payload = report.model_dump(mode="json")
        payload["parity_run_id"] = str(run.id) if run else None
        payload["passed"] = report.passed
        return payload

    @router.get("/trials/{trial_id}/read-mode")
    def get_read_mode(trial_id: UUID, user: User, session: Db):
        """Where this trial's visit dates are read from, and whether it can move."""
        _, organization_id = _identity(user)
        try:
            return ParityService(session).readiness(
                trial_id, organization_id=organization_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.post("/trials/{trial_id}/read-mode")
    def set_read_mode(trial_id: UUID, body: ReadModeIn, user: User, session: Db):
        """Move a trial's visit reads between the operational store and the engine.

        Moving TO the engine requires a matching parity run for every patient
        against the version they are pinned to. Moving back needs no gate:
        reversing a cutover must never be harder than making one.
        """
        actor_id, organization_id = _identity(user)
        try:
            result = ParityService(session).set_read_mode(
                trial_id, organization_id=organization_id, mode=body.mode,
                actor_id=actor_id, reason=body.reason,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return result

    @router.get("/patients/{patient_id}/anchors")
    def get_patient_anchor_statuses(patient_id: UUID, user: User, session: Db):
        """What is known about each anchor for this patient (doc 1 s22).

        The vocabulary matters: PLANNED is an expected date and never becomes
        ACTUAL on its own, so a follow-up calculated from an expected surgery date
        is never presented as if surgery had happened. AWAITING_EVENT names the
        event it is waiting on, which is what makes an undated visit explicable
        rather than broken.
        """
        _, organization_id = _identity(user)
        try:
            statuses = AnchorStatusService(session).statuses(
                patient_id, organization_id=organization_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        return {
            "patient_id": str(patient_id),
            "anchors": [item.model_dump(mode="json") for item in statuses],
            "outstanding": [
                item.anchor_code for item in statuses
                if item.status.value in {"AWAITING_EVENT", "PLANNED", "ACTUAL"}
            ],
        }

    @router.post("/patients/{patient_id}/unscheduled-visits", status_code=201)
    def create_unscheduled_visit(
        patient_id: UUID, body: UnscheduledVisitIn, user: User, session: Db,
    ):
        """Create an occurrence of a protocol-defined unscheduled visit.

        Doc 10 s13-s15. Until a site does this the definition is visible but not
        due, so it never generates a reminder and never counts as missed. Creating
        one affects this patient only; the master schedule is untouched.
        """
        actor_id, organization_id = _identity(user)
        try:
            record = PatientScheduleService(session).create_unscheduled_visit(
                patient_id, organization_id=organization_id,
                event_code=body.event_code, occurred_on=body.occurred_on,
                reason=body.reason, visit_mode=body.visit_mode, actor_id=actor_id,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(422, str(error)) from error
        return {
            "id": str(record.id), "event_code": record.event_code,
            "occurred_on": record.occurred_on, "reason": record.reason,
            "visit_mode": record.visit_mode,
            "note": "regenerate the schedule to see this visit in the patient timeline",
        }

    @router.get("/patients/{patient_id}/conditions")
    def list_patient_conditions(patient_id: UUID, user: User, session: Db):
        """Protocol conditions relevant to this patient, with their current state.

        Conditions that have never occurred are listed too, so the Patient Profile
        can show the full checklist without those items entering the dated schedule.
        """
        _, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        if patient.current_schedule_version_id is None:
            return {"patient_id": str(patient_id), "conditions": []}
        service = PatientScheduleService(session)
        schedule = service.repository.get(patient.current_schedule_version_id)
        states = service.patient_conditions(patient.id)
        return {
            "patient_id": str(patient_id),
            "schedule_version_id": str(patient.current_schedule_version_id),
            "conditions": [{
                "condition_code": definition.code,
                "display_name": definition.display_name,
                "protocol_label": definition.protocol_label,
                "state": (
                    states[definition.code].state.value if definition.code in states
                    else "NOT_OCCURRED"
                ),
                "occurrence_date": (
                    states[definition.code].occurrence_date if definition.code in states else None),
                "resolution_date": (
                    states[definition.code].resolution_date if definition.code in states else None),
                "requires_review": definition.requires_review,
                "interpretation_status": definition.interpretation_status.value,
                "actions": [{
                    "action_type": action.action_type, "target_code": action.target_code,
                } for action in definition.actions],
                "evidence_refs": [str(item) for item in definition.evidence_refs],
            } for definition in schedule.conditional_definitions],
        }

    @router.post("/patients/{patient_id}/conditions/impact-preview", status_code=201)
    def preview_condition_impact(
        patient_id: UUID, body: ConditionPreviewIn, user: User, session: Db,
    ):
        """Show what activating or resolving a condition would do, without applying it."""
        actor_id, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        if patient.current_schedule_version_id is None:
            raise HTTPException(422, "Patient is not pinned to an approved schedule")
        try:
            change = PatientConditionStatus(
                condition_code=body.condition.condition_code,
                state=ConditionState(body.condition.state),
                occurrence_date=body.condition.occurrence_date,
                resolution_date=body.condition.resolution_date,
                occurrence_index=body.condition.occurrence_index,
            )
            proposal = PatientScheduleImpactService(session).preview(
                patient_id, organization_id=organization_id,
                target_schedule_version_id=patient.current_schedule_version_id,
                horizon=body.horizon, actor_id=actor_id, reason=body.reason,
                expires_in_minutes=body.expires_in_minutes, condition_change=change,
            )
            session.commit()
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, PydanticValidationError) as error:
            raise HTTPException(422, str(error)) from error
        return {
            "id": str(proposal.id), "status": proposal.status,
            "expires_at": proposal.expires_at, "impact": proposal.impact,
        }

    @router.post("/patients/{patient_id}/assignment/impact-preview", status_code=201)
    def preview_assignment_impact(
        patient_id: UUID, body: AssignmentPreviewIn, user: User, session: Db,
    ):
        """Show what moving a patient between protocol groups would do (doc 8 s22).

        Reassignment changes WHICH visits the patient has, so it never applies
        directly. Confirming the returned proposal is what commits it, and the
        proposal goes stale if the patient's inputs move in the meantime.
        """
        actor_id, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        if patient.current_schedule_version_id is None:
            raise HTTPException(422, "Patient is not pinned to an approved schedule")
        try:
            proposal = PatientScheduleImpactService(session).preview(
                patient_id, organization_id=organization_id,
                target_schedule_version_id=patient.current_schedule_version_id,
                horizon=body.horizon, actor_id=actor_id, reason=body.reason,
                expires_in_minutes=body.expires_in_minutes,
                assignment_change=body.assignment,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, PydanticValidationError) as error:
            session.rollback()
            raise HTTPException(422, str(error)) from error
        return {
            "id": str(proposal.id), "status": proposal.status,
            "expires_at": proposal.expires_at, "impact": proposal.impact,
        }

    @router.post("/patients/{patient_id}/schedule/impact-preview", status_code=201)
    def preview_patient_schedule_impact(
        patient_id: UUID, body: ImpactPreviewIn, user: User, session: Db,
    ):
        actor_id, organization_id = _identity(user)
        try:
            proposal = PatientScheduleImpactService(session).preview(
                patient_id, organization_id=organization_id,
                target_schedule_version_id=body.target_schedule_version_id,
                horizon=body.horizon, actor_id=actor_id, reason=body.reason,
                expires_in_minutes=body.expires_in_minutes,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ScheduleNotApprovedError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "proposal_id": str(proposal.id), "status": proposal.status,
            "from_schedule_version_id": str(proposal.from_schedule_version_id),
            "to_schedule_version_id": str(proposal.to_schedule_version_id),
            "horizon": proposal.horizon, "expires_at": proposal.expires_at,
            "input_hash": proposal.input_hash, "impact": proposal.impact,
        }

    @router.post("/schedule-impact-proposals/{proposal_id}/confirm")
    def confirm_patient_schedule_impact(
        proposal_id: UUID, body: ImpactConfirmIn, user: User, session: Db,
    ):
        actor_id, organization_id = _identity(user)
        try:
            proposal, evaluation, events = PatientScheduleImpactService(session).confirm(
                proposal_id, organization_id=organization_id,
                actor_id=actor_id, reason=body.reason,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except (ValueError, ScheduleNotApprovedError) as error:
            if "stale" in str(error).lower() or "expired" in str(error).lower():
                session.commit()
            else:
                session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "proposal_id": str(proposal.id), "status": proposal.status,
            "schedule_version_id": str(proposal.to_schedule_version_id),
            "evaluation_id": str(evaluation.id), "event_count": len(events),
        }

    @router.post("/schedule-impact-proposals/{proposal_id}/cancel")
    def cancel_patient_schedule_impact(
        proposal_id: UUID, body: ImpactCancelIn, user: User, session: Db,
    ):
        actor_id, organization_id = _identity(user)
        try:
            proposal = PatientScheduleImpactService(session).cancel(
                proposal_id, organization_id=organization_id,
                actor_id=actor_id, reason=body.reason,
            )
            session.commit()
        except KeyError as error:
            session.rollback()
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {"proposal_id": str(proposal.id), "status": proposal.status}

    @router.get("/patient-events/{patient_event_id}")
    def get_patient_event(patient_event_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        row = session.scalar(
            select(db.PatientEvent)
            .join(db.PatientSchedule, db.PatientSchedule.id == db.PatientEvent.patient_schedule_id)
            .join(db.Patient, db.Patient.id == db.PatientSchedule.patient_id)
            .where(db.PatientEvent.id == patient_event_id, db.Patient.organization_id == organization_id)
        )
        if row is None:
            raise HTTPException(404, "Patient event not found")
        return {
            "id": str(row.id), "status": row.status,
            "timing_resolution": row.timing_resolution,
            "applicability_result": row.applicability_result,
            "condition_result": row.condition_result,
            "dependency_result": row.dependency_result,
            "explanation": row.generation_reason,
        }

    @router.post("/patient-events/{patient_event_id}/occurrences", status_code=201)
    def record_occurrence(patient_event_id: UUID, body: PatientOccurrenceIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        event = session.scalar(
            select(db.PatientEvent)
            .join(db.PatientSchedule, db.PatientSchedule.id == db.PatientEvent.patient_schedule_id)
            .join(db.Patient, db.Patient.id == db.PatientSchedule.patient_id)
            .where(db.PatientEvent.id == patient_event_id, db.Patient.organization_id == organization_id)
        )
        if event is None:
            raise HTTPException(404, "Patient event not found")
        row = db.PatientEventOccurrence(
            patient_event_id=event.id, occurrence_type=body.occurrence_type,
            scheduled_date=body.scheduled_date, actual_date=body.actual_date,
            status=body.status, recorded_by=actor_id,
            metadata_json=body.metadata,
        )
        session.add(row)
        session.flush()
        derived_anchors: list[dict[str, str]] = []
        patient_schedule = session.get(db.PatientSchedule, event.patient_schedule_id)
        event_definition = session.get(db.Event, event.event_definition_id)
        if body.actual_date and patient_schedule and event_definition:
            anchors = list(session.scalars(select(db.Anchor).where(
                db.Anchor.schedule_version_id == patient_schedule.schedule_version_id,
                db.Anchor.source_event_code == event_definition.code,
            )))
            for anchor in anchors:
                candidate = db.PatientAnchor(
                    patient_id=patient_schedule.patient_id,
                    anchor_definition_id=anchor.id,
                    value_date=body.actual_date,
                    status="PENDING_CONFIRMATION",
                    source_type="PATIENT_EVENT_OCCURRENCE",
                    source_reference={
                        "patient_event_id": str(event.id),
                        "occurrence_id": str(row.id),
                        "event_code": event_definition.code,
                    },
                    recorded_by=actor_id,
                )
                session.add(candidate)
                session.flush()
                derived_anchors.append({
                    "anchor_code": anchor.code,
                    "candidate_id": str(candidate.id),
                    "status": candidate.status,
                })
                session.add(db.AuditEvent(
                    organization_id=organization_id, actor_id=actor_id,
                    action="PATIENT_ANCHOR_CANDIDATE_DERIVED_FROM_ACTUAL",
                    entity_type="PATIENT_ANCHOR", entity_id=candidate.id,
                    before=None,
                    after={
                        "anchor_code": anchor.code, "actual_date": str(body.actual_date),
                        "source_patient_event_id": str(event.id),
                    },
                ))
        if body.status == "COMPLETED":
            event.status = "COMPLETED"
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_EVENT_OCCURRENCE_RECORDED", entity_type="PATIENT_EVENT",
            entity_id=event.id, before=None,
            after={"actual_date": str(body.actual_date) if body.actual_date else None, "status": body.status},
        ))
        session.commit()
        return {
            "id": str(row.id), "patient_event_id": str(event.id), "status": row.status,
            "derived_anchors": [item["anchor_code"] for item in derived_anchors],
            "derived_anchor_candidates": derived_anchors,
            "impact_preview_required": bool(derived_anchors),
        }

    @router.post("/patients/{patient_id}/anchors", status_code=201)
    def record_patient_anchor(patient_id: UUID, body: PatientAnchorIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        anchor = session.get(db.Anchor, body.anchor_definition_id)
        if patient is None or anchor is None or anchor.schedule_version_id != patient.current_schedule_version_id:
            raise HTTPException(404, "Patient or applicable anchor not found")
        if body.value_date is None and body.value_datetime is None:
            raise HTTPException(422, "An anchor date or datetime is required")
        from datetime import datetime
        row = db.PatientAnchor(
            patient_id=patient_id, anchor_definition_id=anchor.id,
            value_date=body.value_date,
            value_datetime=datetime.fromisoformat(body.value_datetime) if body.value_datetime else None,
            status="PENDING_CONFIRMATION", source_type=body.source_type,
            source_reference=body.source_reference, recorded_by=actor_id,
        )
        session.add(row)
        session.flush()
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_ANCHOR_CANDIDATE_RECORDED",
            entity_type="PATIENT_ANCHOR", entity_id=row.id,
            before=None, after={"anchor_code": anchor.code, "status": row.status},
        ))
        session.commit()
        return {
            "id": str(row.id), "anchor_code": anchor.code,
            "status": row.status,
            "impact_preview_required": True,
            "impact_preview_schedule_version_id": str(patient.current_schedule_version_id),
        }

    @router.post(
        "/patients/{patient_id}/anchors/{candidate_id}/impact-preview",
        status_code=201,
    )
    def preview_anchor_impact(
        patient_id: UUID, candidate_id: UUID, body: AnchorImpactPreviewIn,
        user: User, session: Db,
    ):
        actor_id, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id,
            db.Patient.organization_id == organization_id,
        ))
        candidate = session.get(db.PatientAnchor, candidate_id)
        if patient is None or candidate is None or candidate.patient_id != patient.id:
            raise HTTPException(404, "Patient or anchor candidate not found")
        try:
            proposal = PatientScheduleImpactService(session).preview(
                patient.id, organization_id=organization_id,
                target_schedule_version_id=patient.current_schedule_version_id,
                horizon=body.horizon, actor_id=actor_id, reason=body.reason,
                expires_in_minutes=body.expires_in_minutes,
                anchor_candidate=candidate,
            )
            session.commit()
        except (KeyError, ValueError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "id": str(proposal.id), "status": proposal.status,
            "expires_at": proposal.expires_at,
            "impact": proposal.impact,
        }

    @router.post("/patients/{patient_id}/states", status_code=201)
    def record_patient_state(patient_id: UUID, body: PatientStateIn, user: User, session: Db):
        _identity(user)
        raise HTTPException(
            409,
            "Direct schedule-input changes are disabled; use the state impact-preview "
            "endpoint and confirm its proposal.",
        )

    @router.post("/patients/{patient_id}/states/impact-preview", status_code=201)
    def preview_patient_state_impact(
        patient_id: UUID, body: StateImpactPreviewIn, user: User, session: Db,
    ):
        actor_id, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id,
            db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        state_change = body.state.model_dump(mode="json")
        try:
            proposal = PatientScheduleImpactService(session).preview(
                patient.id, organization_id=organization_id,
                target_schedule_version_id=patient.current_schedule_version_id,
                horizon=body.horizon, actor_id=actor_id, reason=body.reason,
                expires_in_minutes=body.expires_in_minutes,
                state_change=state_change,
            )
            session.commit()
        except (KeyError, ValueError) as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "id": str(proposal.id), "status": proposal.status,
            "expires_at": proposal.expires_at, "impact": proposal.impact,
        }

    return router
