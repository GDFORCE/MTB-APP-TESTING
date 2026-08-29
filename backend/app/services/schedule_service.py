from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Event, PatientContext, ScheduleStatus, UniversalSchedule, ValidationIssue,
)
from app.domain.schedule.validator import ScheduleValidator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleReviewService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)

    def validate(self, schedule_version_id: UUID) -> list[ValidationIssue]:
        schedule = self.repository.get(schedule_version_id)
        issues = ScheduleValidator().validate(schedule)
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        assert version is not None
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be revalidated or edited")
        self.repository.replace_validation_issues(schedule_version_id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=None,
            action="SCHEDULE_VALIDATED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=None,
            after={"blocking": len(ScheduleValidator.blocking(issues)), "issues": len(issues)},
        ))
        return issues

    def correct_event(self, schedule_version_id: UUID, event: Event, *, reviewer_id: UUID, reason: str) -> list[ValidationIssue]:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be corrected")
        before_schedule = self.repository.get(schedule_version_id)
        before = next((item for item in before_schedule.events if item.id == event.id), None)
        if before is None:
            raise KeyError("event definition not found")
        self.repository.replace_event(schedule_version_id, event)
        self.session.flush()
        corrected = self.repository.get(schedule_version_id)
        issues = ScheduleValidator().validate(corrected)
        self.repository.replace_validation_issues(schedule_version_id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value
        self.session.add(db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type="EVENT", entity_id=event.id,
            decision="CORRECT", previous_value=before.model_dump(mode="json"),
            new_value=event.model_dump(mode="json"), reviewer_id=reviewer_id, reason=reason,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_EVENT_CORRECTED", entity_type="EVENT", entity_id=event.id,
            before=before.model_dump(mode="json"), after=event.model_dump(mode="json"), reason=reason,
        ))
        return issues

    def record_decision(
        self,
        schedule_version_id: UUID,
        *,
        reviewer_id: UUID,
        decision: str,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        field_path: str | None = None,
        previous_value: object | None = None,
        new_value: object | None = None,
        reason: str | None = None,
        comment: str | None = None,
    ) -> db.ReviewDecision:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be reviewed again")
        row = db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type=entity_type,
            entity_id=entity_id, field_path=field_path, decision=decision,
            previous_value=previous_value, new_value=new_value,
            reviewer_id=reviewer_id, reason=reason, comment=comment,
        )
        self.session.add(row)
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="REVIEW_DECISION_RECORDED", entity_type=entity_type or "SCHEDULE_VERSION",
            entity_id=entity_id or version.id, before=previous_value, after=new_value,
            reason=reason, metadata_json={"decision": decision, "field_path": field_path},
        ))
        return row

    def submit_for_review(self, schedule_version_id: UUID, *, actor_id: UUID) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status not in {ScheduleStatus.VALIDATION_REQUIRED.value, ScheduleStatus.EXTRACTED.value}:
            raise ValueError(f"cannot submit schedule in {version.status} state")
        version.status = ScheduleStatus.IN_REVIEW.value
        self._audit(version, actor_id, "SCHEDULE_SUBMITTED_FOR_REVIEW")

    def approve(self, schedule_version_id: UUID, *, reviewer_id: UUID, comment: str | None = None) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id, with_for_update=True)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status != ScheduleStatus.IN_REVIEW.value:
            raise ValueError("only an in-review schedule can be approved")
        schedule = self.repository.get(schedule_version_id)
        issues = ScheduleValidator().validate(schedule)
        self.repository.replace_validation_issues(schedule_version_id, issues)
        blocking = ScheduleValidator.blocking(issues)
        if blocking:
            raise ValueError(f"approval blocked by {len(blocking)} validation issue(s)")
        decisions = list(self.session.scalars(select(db.ReviewDecision).where(
            db.ReviewDecision.schedule_version_id == schedule_version_id,
            db.ReviewDecision.reviewer_id == reviewer_id,
            db.ReviewDecision.decision.in_(["APPROVE", "CONFIRM", "CORRECT"]),
        )))
        confirmed = {(item.entity_id, item.field_path) for item in decisions}
        required = {(event.id, path) for event in schedule.events for path in ("display_name", "timing")}
        for event in schedule.events:
            if event.activities:
                required.add((event.id, "activities"))
            if event.conditions:
                required.add((event.id, "conditions"))
            if event.applicability:
                required.add((event.id, "applicability"))
            if event.recurrence:
                required.add((event.id, "recurrence"))
        missing = required - confirmed
        if missing:
            raise ValueError(f"approval requires {len(missing)} outstanding field review(s)")
        before = {"status": version.status}
        version.status = ScheduleStatus.APPROVED.value
        version.approved_by = reviewer_id
        version.approved_at = utc_now()
        self.session.add(db.ReviewDecision(
            schedule_version_id=version.id, decision="APPROVE",
            reviewer_id=reviewer_id, comment=comment,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_APPROVED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=before,
            after={"status": version.status, "approved_at": version.approved_at.isoformat()},
            reason=comment,
        ))

    def reject(self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str) -> None:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None or version.status != ScheduleStatus.IN_REVIEW.value:
            raise ValueError("only an in-review schedule can be rejected")
        version.status = ScheduleStatus.REJECTED.value
        version.rejection_reason = reason
        self.session.add(db.ReviewDecision(
            schedule_version_id=version.id, decision="REJECT",
            reviewer_id=reviewer_id, reason=reason,
        ))
        self._audit(version, reviewer_id, "SCHEDULE_REJECTED", reason)

    def _organization_id(self, version: db.ScheduleVersion) -> UUID:
        return self.session.execute(
            select(db.Trial.organization_id)
            .join(db.Protocol, db.Protocol.trial_id == db.Trial.id)
            .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
            .join(db.ScheduleDefinition, db.ScheduleDefinition.protocol_version_id == db.ProtocolVersion.id)
            .where(db.ScheduleDefinition.id == version.schedule_definition_id)
        ).scalar_one()

    def _audit(self, version: db.ScheduleVersion, actor_id: UUID, action: str, reason: str | None = None) -> None:
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=actor_id,
            action=action, entity_type="SCHEDULE_VERSION", entity_id=version.id,
            before=None, after={"status": version.status}, reason=reason,
        ))


class PatientScheduleService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)

    def evaluate(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        horizon: date,
        idempotency_key: str | None = None,
    ) -> tuple[db.ScheduleEvaluation, list[db.PatientEvent]]:
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient is not pinned to an approved schedule")
        if idempotency_key:
            existing = self.session.scalar(select(db.ScheduleGenerationRun).where(
                db.ScheduleGenerationRun.patient_id == patient_id,
                db.ScheduleGenerationRun.idempotency_key == idempotency_key,
            ))
            if existing:
                evaluation = self.session.scalar(select(db.ScheduleEvaluation).where(
                    db.ScheduleEvaluation.generation_run_id == existing.id,
                ))
                assert evaluation is not None
                events = list(self.session.scalars(select(db.PatientEvent).where(
                    db.PatientEvent.schedule_evaluation_id == evaluation.id,
                )))
                return evaluation, events
        schedule = self.repository.get(patient.current_schedule_version_id)
        anchors = {}
        rows = self.session.execute(
            select(db.Anchor.code, db.PatientAnchor.value_date, db.PatientAnchor.value_datetime)
            .join(db.PatientAnchor, db.PatientAnchor.anchor_definition_id == db.Anchor.id)
            .where(db.PatientAnchor.patient_id == patient.id, db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]))
            .order_by(db.PatientAnchor.recorded_at)
        )
        for code, value_date, value_datetime in rows:
            anchors[code] = value_datetime or value_date
        state = {}
        state_effective_at = {}
        for row in self.session.scalars(select(db.PatientState).where(
            db.PatientState.patient_id == patient.id,
        ).order_by(db.PatientState.effective_at)):
            state[row.state_code] = row.state_value
            state_effective_at[row.state_code] = row.effective_at
        arm_code = self.session.get(db.Arm, patient.arm_id).code if patient.arm_id else None
        cohort_code = self.session.get(db.Cohort, patient.cohort_id).code if patient.cohort_id else None
        population_code = self.session.get(db.Population, patient.population_id).code if patient.population_id else None
        context = PatientContext(
            patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            anchors=anchors, state=state, state_effective_at=state_effective_at, arm_code=arm_code,
            cohort_code=cohort_code, population_code=population_code,
        )
        started = utc_now()
        result = ScheduleEvaluator().evaluate(schedule, context, horizon=horizon)
        run = db.ScheduleGenerationRun(
            patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            idempotency_key=idempotency_key, evaluator_version=result.evaluator_version,
            status="COMPLETED", started_at=started, completed_at=utc_now(),
        )
        self.session.add(run)
        self.session.flush()
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient.id,
            db.PatientSchedule.schedule_version_id == schedule.schedule_version_id,
        ))
        if patient_schedule is None:
            patient_schedule = db.PatientSchedule(
                patient_id=patient.id, schedule_version_id=schedule.schedule_version_id,
            )
            self.session.add(patient_schedule)
            self.session.flush()
        evaluation = db.ScheduleEvaluation(
            patient_schedule_id=patient_schedule.id, generation_run_id=run.id,
            input_snapshot=result.input_snapshot,
            output_summary={"statuses": [item.status.value for item in result.events], "count": len(result.events)},
            evaluator_version=result.evaluator_version, evaluated_at=result.evaluated_at,
        )
        self.session.add(evaluation)
        self.session.flush()
        persisted = []
        for item in result.events:
            timing = item.timing
            def as_date(value: date | datetime | None) -> date | None:
                return value.date() if isinstance(value, datetime) else value
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=item.event_definition_id, occurrence_index=item.occurrence_index,
                status=item.status.value,
                nominal_start_date=as_date(timing.nominal_start) if timing else None,
                nominal_end_date=as_date(timing.nominal_end) if timing else None,
                earliest_date=as_date(timing.earliest) if timing else None,
                latest_date=as_date(timing.latest) if timing else None,
                timing_resolution=timing.model_dump(mode="json") if timing else {},
                applicability_result={"value": item.applicability_result},
                condition_result={"value": item.condition_result},
                dependency_result=item.dependency_result,
                generation_reason=item.explanation,
            )
            self.session.add(row)
            persisted.append(row)
        self.session.flush()
        patient_schedule.current_evaluation_id = evaluation.id
        patient_schedule.generation_run_id = run.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, action="PATIENT_SCHEDULE_EVALUATED",
            entity_type="PATIENT", entity_id=patient.id, before=None,
            after={"evaluation_id": str(evaluation.id), "event_count": len(persisted)},
            metadata_json={"evaluator_version": result.evaluator_version},
        ))
        return evaluation, persisted
