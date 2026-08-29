from datetime import date
import os
from typing import Annotated, Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.base import get_session
from app.db.repositories import ScheduleRepository
from app.domain.schedule.diff import compare_schedule_versions
from app.domain.schedule.exceptions import ImmutableScheduleError, ScheduleNotApprovedError
from app.domain.schedule.projection import project_schedule
from app.domain.schedule.models import Event
from app.services.schedule_service import PatientScheduleService, ScheduleReviewService
from app.services.extraction_service import ExtractionService
from app.services.demo_service import DemoService


class ReviewDecisionIn(BaseModel):
    decision: str
    entity_type: str | None = None
    entity_id: UUID | None = None
    field_path: str | None = None
    previous_value: Any | None = None
    new_value: Any | None = None
    reason: str | None = None
    comment: str | None = None


class ReviewIn(BaseModel):
    decision: str
    comment: str | None = None
    reason: str | None = None


class EvaluateIn(BaseModel):
    horizon: date


class TrialIn(BaseModel):
    protocol_id: str | None = None
    study_title: str | None = None
    indication: str | None = None
    drug_name: str | None = None
    sponsor_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class PatientAnchorIn(BaseModel):
    anchor_definition_id: UUID
    value_date: date | None = None
    value_datetime: str | None = None
    status: str = "CONFIRMED"
    source_type: str | None = None
    source_reference: dict[str, Any] | None = None


class PatientStateIn(BaseModel):
    state_code: str
    state_value: Any
    effective_at: str
    source_reference: dict[str, Any] | None = None


class PatientOccurrenceIn(BaseModel):
    occurrence_type: str
    scheduled_date: date | None = None
    actual_date: date | None = None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


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
        schedules = list(session.scalars(query.order_by(db.ScheduleVersion.version_number.desc())))
        if not schedules:
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
        row = db.Patient(
            organization_id=organization_id, trial_id=trial.id, site_id=body.site_id,
            patient_code=body.patient_code, current_schedule_version_id=selected.id,
            arm_id=body.arm_id, cohort_id=body.cohort_id, population_id=body.population_id,
        )
        session.add(row)
        session.flush()
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
        return {"extraction_run_id": str(run.id), "status": run.status}

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
                service.approve(schedule_version_id, reviewer_id=actor_id, comment=body.comment)
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

    @router.get("/schedule-versions/{left_id}/diff/{right_id}")
    def diff_schedules(left_id: UUID, right_id: UUID, user: User, session: Db):
        _, organization_id = _identity(user)
        _assert_schedule_tenant(session, left_id, organization_id)
        _assert_schedule_tenant(session, right_id, organization_id)
        repository = ScheduleRepository(session)
        return compare_schedule_versions(repository.get(left_id), repository.get(right_id)).model_dump(mode="json")

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
        return {
            "patient_id": str(patient_id), "status": patient_schedule.status,
            "schedule_version_id": str(patient_schedule.schedule_version_id),
            "evaluation_id": str(patient_schedule.current_evaluation_id),
            "events": [{
                "id": str(item.id), "event_definition_id": str(item.event_definition_id),
                "occurrence_index": item.occurrence_index, "status": item.status,
                "nominal_start_date": item.nominal_start_date,
                "nominal_end_date": item.nominal_end_date,
                "earliest_date": item.earliest_date, "latest_date": item.latest_date,
                "timing_resolution": item.timing_resolution,
                "explanation": item.generation_reason,
            } for item in events],
        }

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
        if body.status == "COMPLETED":
            event.status = "COMPLETED"
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_EVENT_OCCURRENCE_RECORDED", entity_type="PATIENT_EVENT",
            entity_id=event.id, before=None,
            after={"actual_date": str(body.actual_date) if body.actual_date else None, "status": body.status},
        ))
        session.commit()
        return {"id": str(row.id), "patient_event_id": str(event.id), "status": row.status}

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
            status=body.status, source_type=body.source_type,
            source_reference=body.source_reference, recorded_by=actor_id,
        )
        session.add(row)
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_ANCHOR_RECORDED", entity_type="PATIENT", entity_id=patient_id,
            before=None, after={"anchor_code": anchor.code, "status": body.status},
        ))
        session.commit()
        return {"id": str(row.id), "anchor_code": anchor.code}

    @router.post("/patients/{patient_id}/states", status_code=201)
    def record_patient_state(patient_id: UUID, body: PatientStateIn, user: User, session: Db):
        actor_id, organization_id = _identity(user)
        patient = session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise HTTPException(404, "Patient not found")
        from datetime import datetime
        row = db.PatientState(
            patient_id=patient_id, state_code=body.state_code,
            state_value=body.state_value, effective_at=datetime.fromisoformat(body.effective_at),
            recorded_by=actor_id, source_reference=body.source_reference,
        )
        session.add(row)
        session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_STATE_RECORDED", entity_type="PATIENT", entity_id=patient_id,
            before=None, after={"state_code": body.state_code},
        ))
        session.commit()
        return {"id": str(row.id), "state_code": row.state_code}

    return router
