from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    ConditionState, EvaluationResult, Event, PatientConditionStatus, PatientContext,
    QualifierCategory, QualifierScope,
    ScheduleStatus, UniversalSchedule, ValidationIssue,
    RollingHorizon,
    UnscheduledOccurrence,
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

    def bulk_confirm_all_fields(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, reason: str,
    ) -> int:
        """Confirm every required field with ONE reviewer action - and say so.

        Doc s14: "the current 'Confirm All' behavior must NOT be treated as
        equivalent to field-level review... do not claim 'every field
        individually reviewed' when the user only pressed one bulk button."

        This still writes the same per-field ``ReviewDecision`` rows
        ``approve()`` checks for (so the field-confirmation gate keeps working
        exactly as before), but their comment says plainly that they came from
        one bulk action, and ONE additional, distinctly-named audit event
        (``SCHEDULE_BULK_CONFIRMED``) records the action itself - so a later
        reviewer of the audit trail sees "bulk-confirmed, N fields, by X, on
        DATE, because REASON" rather than N identical-looking individual
        confirmations that imply N separate looks.
        """
        if not (reason or "").strip():
            raise ValueError("bulk confirmation must record why it was used")
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError("approved schedule versions cannot be reviewed again")

        schedule = self.repository.get(schedule_version_id)
        confirmed = 0
        bulk_comment = (
            "Bulk-confirmed via the 'Confirm all fields' action - this field "
            "was not reviewed individually."
        )
        for event in schedule.events:
            fields = ["display_name", "timing"]
            if event.conditions:
                fields.append("conditions")
            if event.applicability:
                fields.append("applicability")
            if event.recurrence is not None:
                fields.append("recurrence")
            if event.activities:
                fields.append("activities")
            for field_path in fields:
                self.session.add(db.ReviewDecision(
                    schedule_version_id=schedule_version_id, entity_type="EVENT",
                    entity_id=event.id, field_path=field_path, decision="CONFIRM",
                    reviewer_id=reviewer_id, reason=reason, comment=bulk_comment,
                ))
                confirmed += 1

        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="SCHEDULE_BULK_CONFIRMED", entity_type="SCHEDULE_VERSION",
            entity_id=version.id, before=None,
            after={"fields_confirmed": confirmed, "event_count": len(schedule.events)},
            reason=reason,
        ))
        return confirmed

    def resolve_qualifier(
        self,
        schedule_version_id: UUID,
        qualifier_id: UUID,
        *,
        reviewer_id: UUID,
        decision: str,
        reason: str,
        category: str | None = None,
        text: str | None = None,
        scope: str | None = None,
        target_codes: list[str] | None = None,
    ) -> list[ValidationIssue]:
        """Turn one extracted footnote into reviewed schedule meaning (doc s6).

        Extraction deliberately refuses to interpret a footnote: it carries the
        marker, its text and its evidence, and marks it UNRESOLVED, which blocks
        approval. That was only half a workflow - nothing could ever clear the
        block, so any protocol with a footnote produced a draft that could never
        be approved. This is the other half.

        The reviewer states what the footnote MEANS; this method records that
        statement, never guesses at it. ACCEPT keeps the extracted reading, EDIT
        replaces it, NOT_APPLICABLE records that it does not govern this
        schedule, and ESCALATE leaves it unresolved on purpose so it stays
        blocking. Every outcome is a ReviewDecision plus an AuditEvent carrying
        the before/after value, who decided, and why.
        """
        decision = (decision or "").strip().upper()
        allowed = {"ACCEPT", "EDIT", "NOT_APPLICABLE", "ESCALATE"}
        if decision not in allowed:
            raise ValueError(f"decision must be one of {', '.join(sorted(allowed))}")
        if not (reason or "").strip():
            raise ValueError("a qualifier resolution must record why it was made")

        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        if version.status in {ScheduleStatus.APPROVED.value, ScheduleStatus.SUPERSEDED.value}:
            raise ImmutableScheduleError(
                "approved schedule versions cannot be corrected")

        schedule = self.repository.get(schedule_version_id)
        owner_event = None
        owner_type = ""
        target = None
        for event in schedule.events:
            for item in event.qualifiers:
                if item.id == qualifier_id:
                    owner_event, owner_type, target = event, "EVENT", item
            for activity in event.activities:
                for item in activity.qualifiers:
                    if item.id == qualifier_id:
                        owner_event, owner_type, target = event, "ACTIVITY", item
        if owner_event is None or target is None:
            raise KeyError("qualifier not found on this schedule version")

        before = target.model_dump(mode="json")

        if decision == "ESCALATE":
            # Deliberately still unresolved: the reviewer is saying they cannot
            # answer this yet, which must keep blocking approval rather than
            # quietly passing it through.
            target.resolved = False
        elif decision == "NOT_APPLICABLE":
            target.category = QualifierCategory.NOT_APPLICABLE
            target.resolved = True
        else:
            if decision == "EDIT":
                if text is not None and text.strip():
                    target.text = text.strip()
                if scope is not None:
                    target.scope = QualifierScope(scope)
                if target_codes is not None:
                    target.target_codes = list(target_codes)
            if category is not None:
                target.category = QualifierCategory(category)
            if target.category == QualifierCategory.UNRESOLVED:
                raise ValueError(
                    "a resolved qualifier needs a category other than UNRESOLVED; "
                    "state what the footnote means, or escalate it")
            if target.scope != QualifierScope.GLOBAL and not target.target_codes:
                raise ValueError(
                    "a non-global qualifier must name the visit or activity it "
                    "applies to before it can be resolved")
            target.resolved = True

        self.repository.replace_event(schedule_version_id, owner_event)
        self.session.flush()
        issues = ScheduleValidator().validate(self.repository.get(schedule_version_id))
        self.repository.replace_validation_issues(schedule_version_id, issues)
        version.status = ScheduleStatus.VALIDATION_REQUIRED.value

        after = target.model_dump(mode="json")
        self.session.add(db.ReviewDecision(
            schedule_version_id=schedule_version_id, entity_type="QUALIFIER",
            entity_id=qualifier_id, field_path="resolved", decision=decision,
            previous_value=before, new_value=after,
            reviewer_id=reviewer_id, reason=reason,
        ))
        self.session.add(db.AuditEvent(
            organization_id=self._organization_id(version), actor_id=reviewer_id,
            action="QUALIFIER_RESOLVED", entity_type="QUALIFIER",
            entity_id=qualifier_id, before=before, after=after, reason=reason,
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

    def approve(
        self,
        schedule_version_id: UUID,
        *,
        reviewer_id: UUID,
        comment: str | None = None,
        effective_from: date | None = None,
    ) -> None:
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
        # Doc 9 s8: an amendment can be approved before it applies. Recorded
        # separately so nobody reads "approved today" as "in force today".
        version.effective_from = effective_from
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

    @staticmethod
    def result_date(item: object | None) -> date | None:
        if item is None:
            return None
        timing = getattr(item, "timing", None)
        value = getattr(timing, "nominal_start", None) if timing else None
        return value.date() if isinstance(value, datetime) else value

    def logical_key_for_row(self, row: db.PatientEvent) -> str:
        if row.logical_key:
            return row.logical_key
        event = self.session.get(db.Event, row.event_definition_id)
        return f"{event.code if event else row.event_definition_id}:{row.occurrence_index}"

    def current_events(self, patient_id: UUID, schedule_version_id: UUID) -> list[db.PatientEvent]:
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient_id,
            db.PatientSchedule.schedule_version_id == schedule_version_id,
            db.PatientSchedule.current_evaluation_id.is_not(None),
        ).order_by(db.PatientSchedule.generated_at.desc()))
        if patient_schedule is None:
            return []
        return list(self.session.scalars(select(db.PatientEvent).where(
            db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id,
        )))

    def protected_event_ids(self, events: list[db.PatientEvent]) -> set[UUID]:
        protected = {
            item.id for item in events
            if item.status in {"COMPLETED", "MISSED"} or item.protected_history
        }
        ids = [item.id for item in events]
        if ids:
            protected.update(self.session.scalars(select(db.PatientEventOccurrence.patient_event_id).where(
                db.PatientEventOccurrence.patient_event_id.in_(ids),
                db.PatientEventOccurrence.actual_date.is_not(None),
            )))
        return protected

    def evaluate_result(
        self,
        patient: db.Patient,
        schedule_version_id: UUID,
        *,
        horizon: date,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
        rolling: RollingHorizon | None = None,
    ) -> EvaluationResult:
        schedule = self.repository.get(schedule_version_id)
        context = self._context(
            patient, schedule_version_id,
            condition_overrides=condition_overrides,
            anchor_overrides=anchor_overrides,
            state_overrides=state_overrides,
        )
        return ScheduleEvaluator().evaluate(
            schedule, context, horizon=horizon, rolling=rolling)

    def patient_conditions(
        self, patient_id: UUID,
        *,
        overrides: dict[str, PatientConditionStatus] | None = None,
    ) -> dict[str, PatientConditionStatus]:
        """Current condition state per code, newest non-superseded record wins."""
        conditions: dict[str, PatientConditionStatus] = {}
        for row in self.session.scalars(select(db.PatientCondition).where(
            db.PatientCondition.patient_id == patient_id,
            db.PatientCondition.superseded_by_id.is_(None),
        ).order_by(db.PatientCondition.recorded_at)):
            conditions[row.condition_code] = PatientConditionStatus(
                condition_code=row.condition_code, state=ConditionState(row.state),
                occurrence_date=row.occurrence_date, resolution_date=row.resolution_date,
                occurrence_index=row.occurrence_index,
            )
        conditions.update(overrides or {})
        return conditions

    def _context(
        self,
        patient: db.Patient,
        schedule_version_id: UUID,
        *,
        condition_overrides: dict[str, PatientConditionStatus] | None = None,
        anchor_overrides: dict[str, date | datetime] | None = None,
        state_overrides: dict[str, tuple[object, datetime]] | None = None,
    ) -> PatientContext:
        anchors: dict[str, date | datetime] = {}
        rows = self.session.execute(
            select(
                db.Anchor.code, db.Anchor.derivation_rule,
                db.PatientAnchor.value_date, db.PatientAnchor.value_datetime,
            )
            .join(db.PatientAnchor, db.PatientAnchor.anchor_definition_id == db.Anchor.id)
            .where(
                db.PatientAnchor.patient_id == patient.id,
                db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]),
            )
            .order_by(db.PatientAnchor.recorded_at)
        )
        for code, derivation_rule, value_date, value_datetime in rows:
            value = value_datetime or value_date
            selection = str((derivation_rule or {}).get("selection", "LAST")).upper()
            if selection == "FIRST" and code in anchors:
                continue
            anchors[code] = value
        anchors.update(anchor_overrides or {})
        actual_event_values: dict[str, list[date | datetime]] = {}
        actuals = self.session.execute(
            select(db.Event.code, db.PatientEventOccurrence.actual_date)
            .join(db.PatientEvent, db.PatientEvent.id == db.PatientEventOccurrence.patient_event_id)
            .join(db.PatientSchedule, db.PatientSchedule.id == db.PatientEvent.patient_schedule_id)
            .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
            .where(
                db.PatientSchedule.patient_id == patient.id,
                db.PatientEventOccurrence.actual_date.is_not(None),
            )
            .order_by(db.PatientEventOccurrence.actual_date)
        )
        for code, actual_date in actuals:
            actual_event_values.setdefault(code, []).append(actual_date)
        activity_actuals: dict[str, date | datetime] = {}
        activity_statuses: dict[str, str] = {}
        for record in self.session.scalars(select(db.PatientActivityRecord).where(
            db.PatientActivityRecord.patient_id == patient.id,
            db.PatientActivityRecord.superseded_by_id.is_(None),
        ).order_by(db.PatientActivityRecord.recorded_at)):
            activity_statuses[record.logical_key] = record.status
            if record.actual_time is not None:
                activity_actuals[record.logical_key] = record.actual_time
            else:
                activity_actuals.pop(record.logical_key, None)
        state: dict[str, object] = {}
        state_effective_at: dict[str, datetime] = {}
        for row in self.session.scalars(select(db.PatientState).where(
            db.PatientState.patient_id == patient.id,
        ).order_by(db.PatientState.effective_at)):
            state[row.state_code] = row.state_value
            state_effective_at[row.state_code] = row.effective_at
        for code, (value, effective_at) in (state_overrides or {}).items():
            state[code] = value
            state_effective_at[code] = effective_at
        # Doc 10 s14: unscheduled visits the site created are INPUTS to
        # evaluation, so regeneration reproduces them rather than erasing them.
        unscheduled = [
            UnscheduledOccurrence(
                event_code=row.event_code, occurred_on=row.occurred_on,
                reason=row.reason, visit_mode=row.visit_mode,
                created_by=row.created_by, recorded_at=row.recorded_at,
            )
            for row in self.session.scalars(select(db.PatientUnscheduledVisit).where(
                db.PatientUnscheduledVisit.patient_id == patient.id,
            ).order_by(db.PatientUnscheduledVisit.occurred_on))
        ]
        arm_code = self.session.get(db.Arm, patient.arm_id).code if patient.arm_id else None
        cohort_code = self.session.get(db.Cohort, patient.cohort_id).code if patient.cohort_id else None
        population_code = self.session.get(db.Population, patient.population_id).code if patient.population_id else None
        return PatientContext(
            patient_id=patient.id, schedule_version_id=schedule_version_id,
            anchors=anchors, event_values={key: list(values) for key, values in actual_event_values.items()},
            actual_event_values=actual_event_values,
            activity_actuals=activity_actuals, activity_statuses=activity_statuses,
            conditions=self.patient_conditions(patient.id, overrides=condition_overrides),
            unscheduled_occurrences=unscheduled,
            state=state,
            state_effective_at=state_effective_at, arm_code=arm_code,
            cohort_code=cohort_code, population_code=population_code,
            dimension_values=patient.dimension_values or {},
        )

    def _persist_activities(self, row: db.PatientEvent, item: object) -> None:
        """Materialize the day-wise activity schedule for one visit occurrence."""
        activities = getattr(item, "activities", None) or []
        if not activities:
            return
        self.session.flush()

        def as_datetime(value: date | datetime | None) -> datetime | None:
            if value is None or isinstance(value, datetime):
                return value
            return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)

        for activity in activities:
            timing = activity.timing
            self.session.add(db.PatientActivity(
                patient_event_id=row.id,
                activity_definition_id=activity.activity_definition_id,
                activity_code=activity.activity_code,
                logical_key=f"{row.logical_key}#{activity.activity_code or activity.activity_definition_id}",
                status=activity.status.value,
                requiredness=activity.requiredness.value,
                sequence_number=activity.sequence_number,
                planned_time=as_datetime(timing.nominal_start) if timing else None,
                earliest_time=as_datetime(timing.earliest) if timing else None,
                latest_time=as_datetime(timing.latest) if timing else None,
                actual_time=as_datetime(activity.actual_time),
                timing_resolution=timing.model_dump(mode="json") if timing else {},
                generation_reason=activity.explanation,
            ))

    def create_unscheduled_visit(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        event_code: str,
        occurred_on: date,
        reason: str,
        visit_mode: str | None = None,
        actor_id: UUID | None = None,
    ) -> db.PatientUnscheduledVisit:
        """Create one occurrence of a protocol-defined unscheduled visit.

        Doc 10 s13-s15. The definition must already exist in the patient's own
        approved schedule and be marked ON_DEMAND: this creates an OCCURRENCE of
        something the protocol allows, never a new protocol requirement, so the
        master schedule and every other patient are untouched.
        """
        if not reason or not reason.strip():
            raise ValueError(
                "an unscheduled visit needs a reason; without one it cannot be "
                "reviewed against the protocol later"
            )
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient has no active schedule version")
        definition = self.session.scalar(select(db.Event).where(
            db.Event.schedule_version_id == patient.current_schedule_version_id,
            db.Event.code == event_code,
        ))
        if definition is None:
            raise KeyError(f"event {event_code} is not defined in this patient's schedule")
        if (definition.activation or "SCHEDULED") != "ON_DEMAND":
            raise ValueError(
                f"{event_code} is a scheduled protocol visit; only a protocol-defined "
                "unscheduled visit can be created on demand"
            )
        allowed = list(definition.allowed_visit_modes or [])
        if visit_mode is not None and allowed and visit_mode not in allowed:
            raise ValueError(
                f"visit mode {visit_mode!r} is not permitted for {event_code}; "
                f"the protocol allows {', '.join(sorted(allowed))}"
            )
        record = db.PatientUnscheduledVisit(
            patient_id=patient.id, event_definition_id=definition.id,
            event_code=event_code, occurred_on=occurred_on, reason=reason.strip(),
            visit_mode=visit_mode, created_by=actor_id,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def record_activity(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        event_code: str,
        occurrence_index: int,
        activity_code: str,
        status: str,
        actual_time: datetime | None,
        actor_id: UUID | None,
        reason: str | None = None,
    ) -> db.PatientActivityRecord:
        """Record an intra-day actual time or completion decision.

        Corrections supersede rather than overwrite, so the execution history a
        reviewer sees stays append-only and auditable.
        """
        if status not in {"COMPLETED", "NOT_DONE", "PENDING"}:
            raise ValueError("activity status must be COMPLETED, NOT_DONE, or PENDING")
        if status == "COMPLETED" and actual_time is None:
            raise ValueError("a completed activity requires its actual time")
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        logical_key = f"{event_code}#{occurrence_index}#{activity_code}"
        record = db.PatientActivityRecord(
            patient_id=patient.id, event_code=event_code, occurrence_index=occurrence_index,
            activity_code=activity_code, logical_key=logical_key, status=status,
            actual_time=actual_time, recorded_by=actor_id, reason=reason,
        )
        self.session.add(record)
        self.session.flush()
        superseded = [
            item for item in self.session.scalars(select(db.PatientActivityRecord).where(
                db.PatientActivityRecord.patient_id == patient.id,
                db.PatientActivityRecord.logical_key == logical_key,
                db.PatientActivityRecord.superseded_by_id.is_(None),
                db.PatientActivityRecord.id != record.id,
            ))
        ]
        for item in superseded:
            item.superseded_by_id = record.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_ACTIVITY_RECORDED", entity_type="PATIENT", entity_id=patient.id,
            before=({
                "status": superseded[-1].status,
                "actual_time": superseded[-1].actual_time.isoformat() if superseded[-1].actual_time else None,
            } if superseded else None),
            after={
                "logical_key": logical_key, "status": status,
                "actual_time": actual_time.isoformat() if actual_time else None,
            },
            reason=reason,
        ))
        return record

    def evaluate(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        horizon: date,
        idempotency_key: str | None = None,
        prior_events_override: list[db.PatientEvent] | None = None,
        rolling: RollingHorizon | None = None,
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
        prior_events = (
            prior_events_override if prior_events_override is not None
            else self.current_events(patient.id, schedule.schedule_version_id)
        )
        started = utc_now()
        # Doc 3 s7-s8: an open-ended protocol persists a bounded rolling set of
        # upcoming occurrences, not every cycle the date horizon would allow.
        result = self.evaluate_result(
            patient, schedule.schedule_version_id, horizon=horizon, rolling=rolling)
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
        patient_schedule.status = "ACTIVE"
        evaluation = db.ScheduleEvaluation(
            patient_schedule_id=patient_schedule.id, generation_run_id=run.id,
            input_snapshot=result.input_snapshot,
            output_summary={"statuses": [item.status.value for item in result.events], "count": len(result.events)},
            evaluator_version=result.evaluator_version, evaluated_at=result.evaluated_at,
        )
        self.session.add(evaluation)
        self.session.flush()
        persisted = []
        prior_by_key = {self.logical_key_for_row(item): item for item in prior_events}
        protected_ids = self.protected_event_ids(prior_events)
        reconciliation = {key: 0 for key in ("UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
        for item in result.events:
            timing = item.timing
            def as_date(value: date | datetime | None) -> date | None:
                return value.date() if isinstance(value, datetime) else value
            logical_key = f"{item.event_code}:{item.occurrence_index}"
            previous = prior_by_key.pop(logical_key, None)
            protected = previous is not None and previous.id in protected_ids
            status = previous.status if protected else item.status.value
            nominal_start = previous.nominal_start_date if protected else (as_date(timing.nominal_start) if timing else None)
            nominal_end = previous.nominal_end_date if protected else (as_date(timing.nominal_end) if timing else None)
            earliest = previous.earliest_date if protected else (as_date(timing.earliest) if timing else None)
            latest = previous.latest_date if protected else (as_date(timing.latest) if timing else None)
            if previous is None:
                change = "ADDED"
            elif protected:
                change = "PROTECTED"
            elif previous.nominal_start_date != nominal_start:
                change = "MOVED"
            elif previous.status != status:
                change = "STATUS_CHANGED"
            else:
                change = "UNCHANGED"
            reconciliation[change] += 1
            explanation = dict(item.explanation)
            explanation["reconciliation"] = {
                "change": change,
                "supersedes_patient_event_id": str(previous.id) if previous else None,
            }
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=item.event_definition_id, occurrence_index=item.occurrence_index,
                logical_occurrence_id=(previous.logical_occurrence_id if previous and previous.logical_occurrence_id else uuid4()),
                logical_key=logical_key,
                supersedes_patient_event_id=previous.id if previous else None,
                protected_history=protected,
                status=status,
                nominal_start_date=nominal_start, nominal_end_date=nominal_end,
                earliest_date=earliest, latest_date=latest,
                timing_resolution=(previous.timing_resolution if protected and previous else (timing.model_dump(mode="json") if timing else {})),
                applicability_result={"value": item.applicability_result},
                condition_result={"value": item.condition_result},
                dependency_result=item.dependency_result,
                generation_reason=explanation,
                visit_mode=item.visit_mode,
                unscheduled_reason=item.unscheduled_reason,
            )
            self.session.add(row)
            self._persist_activities(row, item)
            persisted.append(row)
        for logical_key, previous in prior_by_key.items():
            protected = previous.id in protected_ids
            change = "PROTECTED" if protected else "CANCELLED"
            reconciliation[change] += 1
            row = db.PatientEvent(
                patient_schedule_id=patient_schedule.id, schedule_evaluation_id=evaluation.id,
                event_definition_id=previous.event_definition_id,
                occurrence_index=previous.occurrence_index,
                logical_occurrence_id=previous.logical_occurrence_id or uuid4(),
                logical_key=logical_key, supersedes_patient_event_id=previous.id,
                protected_history=protected,
                status=previous.status if protected else "CANCELLED",
                nominal_start_date=previous.nominal_start_date,
                nominal_end_date=previous.nominal_end_date,
                earliest_date=previous.earliest_date, latest_date=previous.latest_date,
                timing_resolution=previous.timing_resolution,
                applicability_result=previous.applicability_result,
                condition_result=previous.condition_result,
                dependency_result=previous.dependency_result,
                generation_reason={
                    **(previous.generation_reason or {}),
                    "reconciliation": {
                        "change": change,
                        "supersedes_patient_event_id": str(previous.id),
                    },
                },
            )
            self.session.add(row)
            persisted.append(row)
        self.session.flush()
        evaluation.output_summary = {
            **evaluation.output_summary,
            "persisted_count": len(persisted),
            "reconciliation": reconciliation,
        }
        patient_schedule.current_evaluation_id = evaluation.id
        patient_schedule.generation_run_id = run.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, action="PATIENT_SCHEDULE_EVALUATED",
            entity_type="PATIENT", entity_id=patient.id, before=None,
            after={"evaluation_id": str(evaluation.id), "event_count": len(persisted)},
            metadata_json={"evaluator_version": result.evaluator_version},
        ))
        return evaluation, persisted
