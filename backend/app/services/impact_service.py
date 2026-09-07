from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID

from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.enrolment import validate_assignment
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    ConditionState, EvaluationResult, PatientConditionStatus,
)
from app.services.schedule_service import PatientScheduleService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


class PatientScheduleImpactService:
    """Human-confirmed, stale-safe schedule amendment assignment workflow."""

    terminal_statuses = {"COMPLETED", "MISSED", "CANCELLED"}

    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)
        self.patient_schedules = PatientScheduleService(session)

    def preview(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        target_schedule_version_id: UUID,
        horizon: date,
        actor_id: UUID,
        reason: str,
        expires_in_minutes: int = 30,
        condition_change: PatientConditionStatus | None = None,
        anchor_candidate: db.PatientAnchor | None = None,
        state_change: dict[str, object] | None = None,
        assignment_change: dict[str, str | None] | None = None,
    ) -> db.ScheduleImpactProposal:
        patient = self._patient(patient_id, organization_id, lock=False)
        source_id = patient.current_schedule_version_id
        if source_id is None:
            raise ValueError("patient is not pinned to an approved schedule")
        self._assert_target(patient, target_schedule_version_id)
        overrides = (
            {condition_change.condition_code: condition_change} if condition_change else None)
        anchor_change = self._anchor_change(patient, anchor_candidate)
        state_override = self._state_override(state_change)
        assignment = self._assignment_change(patient, assignment_change)
        # The proposed assignment is applied to a COPY for the preview, so a
        # preview that is never confirmed leaves the patient untouched.
        preview_patient = self._with_assignment(patient, assignment)
        result = self.patient_schedules.evaluate_result(
            preview_patient, target_schedule_version_id, horizon=horizon,
            condition_overrides=overrides,
            anchor_overrides=(
                {anchor_change["anchor_code"]: anchor_change["value"]}
                if anchor_change and anchor_change["effective"] else None
            ),
            state_overrides=state_override,
        )
        prior = self.patient_schedules.current_events(patient.id, source_id)
        impact = self._impact(prior, result)
        if condition_change is not None:
            impact["condition_change"] = condition_change.model_dump(mode="json")
        if anchor_change is not None:
            impact["anchor_change"] = to_jsonable_python(anchor_change)
        if state_change is not None:
            impact["state_change"] = to_jsonable_python(state_change)
        if assignment is not None:
            impact["assignment_change"] = to_jsonable_python(assignment)
        input_hash = self._fingerprint(
            patient, result, prior, horizon, condition_change, anchor_change,
            state_change, assignment,
        )
        proposal = db.ScheduleImpactProposal(
            patient_id=patient.id,
            from_schedule_version_id=source_id,
            to_schedule_version_id=target_schedule_version_id,
            horizon=horizon,
            input_hash=input_hash,
            impact=impact,
            status="PENDING",
            reason=reason,
            created_by=actor_id,
            expires_at=utc_now() + timedelta(minutes=expires_in_minutes),
        )
        self.session.add(proposal)
        self.session.flush()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_SCHEDULE_IMPACT_PREVIEWED",
            entity_type="SCHEDULE_IMPACT_PROPOSAL", entity_id=proposal.id,
            before={"schedule_version_id": str(source_id)},
            after={
                "schedule_version_id": str(target_schedule_version_id),
                "summary": impact["summary"], "input_hash": input_hash,
            },
            reason=reason,
        ))
        return proposal

    def confirm(
        self,
        proposal_id: UUID,
        *,
        organization_id: UUID,
        actor_id: UUID,
        reason: str | None = None,
    ) -> tuple[db.ScheduleImpactProposal, db.ScheduleEvaluation, list[db.PatientEvent]]:
        proposal = self.session.get(db.ScheduleImpactProposal, proposal_id, with_for_update=True)
        if proposal is None:
            raise KeyError("impact proposal not found")
        patient = self._patient(proposal.patient_id, organization_id, lock=True)
        if proposal.status != "PENDING":
            raise ValueError(f"impact proposal is {proposal.status.lower()}")
        expires_at = proposal.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utc_now():
            proposal.status = "EXPIRED"
            raise ValueError("impact proposal has expired")
        if patient.current_schedule_version_id != proposal.from_schedule_version_id:
            proposal.status = "STALE"
            raise ValueError("patient schedule assignment changed; impact proposal is stale")
        self._assert_target(patient, proposal.to_schedule_version_id)
        condition_change = self._proposed_condition(proposal)
        anchor_change = self._proposed_anchor(proposal, patient)
        state_change = (proposal.impact or {}).get("state_change")
        state_override = self._state_override(state_change)
        assignment = (proposal.impact or {}).get("assignment_change")
        overrides = (
            {condition_change.condition_code: condition_change} if condition_change else None)
        result = self.patient_schedules.evaluate_result(
            self._with_assignment(patient, assignment),
            proposal.to_schedule_version_id, horizon=proposal.horizon,
            condition_overrides=overrides,
            anchor_overrides=(
                {anchor_change["anchor_code"]: anchor_change["value"]}
                if anchor_change and anchor_change["effective"] else None
            ),
            state_overrides=state_override,
        )
        prior = self.patient_schedules.current_events(
            patient.id, proposal.from_schedule_version_id,
        )
        current_hash = self._fingerprint(
            patient, result, prior, proposal.horizon, condition_change, anchor_change,
            state_change, assignment,
        )
        if current_hash != proposal.input_hash:
            proposal.status = "STALE"
            self.session.add(db.AuditEvent(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_SCHEDULE_IMPACT_STALE",
                entity_type="SCHEDULE_IMPACT_PROPOSAL", entity_id=proposal.id,
                before={"input_hash": proposal.input_hash}, after={"input_hash": current_hash},
            ))
            raise ValueError("patient inputs changed; generate a new impact preview")

        if assignment:
            self._apply_assignment(
                patient, assignment, proposal_id=proposal.id,
                organization_id=organization_id, actor_id=actor_id,
                reason=reason or proposal.reason,
            )
        if condition_change is not None:
            self._persist_condition(
                patient, condition_change, proposal_id=proposal.id,
                organization_id=organization_id, actor_id=actor_id,
                reason=reason or proposal.reason,
            )
        if anchor_change is not None:
            candidate = self.session.get(
                db.PatientAnchor, UUID(str(anchor_change["candidate_id"])),
                with_for_update=True,
            )
            if candidate is None or candidate.status != "PENDING_CONFIRMATION":
                proposal.status = "STALE"
                raise ValueError("anchor candidate changed; generate a new impact preview")
            candidate.status = "CONFIRMED"
            candidate.metadata_json = {
                **(candidate.metadata_json or {}),
                "impact_proposal_id": str(proposal.id),
                "confirmed_by": str(actor_id),
                "confirmed_at": utc_now().isoformat(),
            }
            previous_value = anchor_change.get("previous_value")
            self.session.add(db.AuditEvent(
                organization_id=organization_id, actor_id=actor_id,
                # A correction and a first recording are different events, and a
                # reviewer scanning the trail needs to tell them apart.
                action=(
                    "PATIENT_ANCHOR_CHANGED" if previous_value is not None
                    else "PATIENT_ANCHOR_CONFIRMED"
                ),
                entity_type="PATIENT_ANCHOR", entity_id=candidate.id,
                before={
                    "status": "PENDING_CONFIRMATION",
                    "anchor_code": anchor_change["anchor_code"],
                    "value": str(previous_value) if previous_value is not None else None,
                },
                after={
                    "status": "CONFIRMED", "anchor_code": anchor_change["anchor_code"],
                    "value": str(anchor_change["value"]),
                    "previous_value": (
                        str(previous_value) if previous_value is not None else None),
                    "proposal_id": str(proposal.id),
                },
                reason=reason or proposal.reason,
            ))
        if state_change is not None:
            effective_at = datetime.fromisoformat(str(state_change["effective_at"]))
            state_row = db.PatientState(
                patient_id=patient.id,
                state_code=str(state_change["state_code"]),
                state_value=state_change.get("state_value"),
                effective_at=effective_at,
                recorded_by=actor_id,
                source_reference={
                    **(state_change.get("source_reference") or {}),
                    "impact_proposal_id": str(proposal.id),
                },
            )
            self.session.add(state_row)
            self.session.add(db.AuditEvent(
                organization_id=organization_id, actor_id=actor_id,
                action="PATIENT_STATE_CHANGED",
                entity_type="PATIENT", entity_id=patient.id,
                before=None,
                after={
                    "state_code": state_change["state_code"],
                    "state_value": state_change.get("state_value"),
                    "effective_at": str(state_change["effective_at"]),
                    "proposal_id": str(proposal.id),
                },
                reason=reason or proposal.reason,
            ))
        previous_id = patient.current_schedule_version_id
        if previous_id != proposal.to_schedule_version_id:
            for schedule in self.session.scalars(select(db.PatientSchedule).where(
                db.PatientSchedule.patient_id == patient.id,
                db.PatientSchedule.status == "ACTIVE",
            )):
                schedule.status = "SUPERSEDED"
        patient.current_schedule_version_id = proposal.to_schedule_version_id
        self.session.add(db.PatientScheduleAssignment(
            patient_id=patient.id,
            schedule_version_id=proposal.to_schedule_version_id,
            previous_schedule_version_id=previous_id,
            impact_proposal_id=proposal.id,
            assignment_type=("AMENDMENT" if previous_id != proposal.to_schedule_version_id else "INPUT_CHANGE"),
            reason=reason or proposal.reason,
            assigned_by=actor_id,
        ))
        evaluation, events = self.patient_schedules.evaluate(
            patient.id,
            organization_id=organization_id,
            horizon=proposal.horizon,
            idempotency_key=f"impact:{proposal.id}",
            prior_events_override=prior,
        )
        proposal.status = "CONFIRMED"
        proposal.confirmed_by = actor_id
        proposal.confirmed_at = utc_now()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_SCHEDULE_VERSION_ASSIGNED",
            entity_type="PATIENT", entity_id=patient.id,
            before={"schedule_version_id": str(previous_id)},
            after={
                "schedule_version_id": str(proposal.to_schedule_version_id),
                "proposal_id": str(proposal.id), "evaluation_id": str(evaluation.id),
            },
            reason=reason or proposal.reason,
        ))
        return proposal, evaluation, events

    def cancel(
        self, proposal_id: UUID, *, organization_id: UUID, actor_id: UUID, reason: str,
    ) -> db.ScheduleImpactProposal:
        proposal = self.session.get(db.ScheduleImpactProposal, proposal_id, with_for_update=True)
        if proposal is None:
            raise KeyError("impact proposal not found")
        self._patient(proposal.patient_id, organization_id, lock=False)
        if proposal.status != "PENDING":
            raise ValueError(f"impact proposal is {proposal.status.lower()}")
        proposal.status = "CANCELLED"
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_SCHEDULE_IMPACT_CANCELLED",
            entity_type="SCHEDULE_IMPACT_PROPOSAL", entity_id=proposal.id,
            before={"status": "PENDING"}, after={"status": proposal.status}, reason=reason,
        ))
        return proposal

    def _patient(self, patient_id: UUID, organization_id: UUID, *, lock: bool) -> db.Patient:
        query = select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        )
        if lock:
            query = query.with_for_update()
        patient = self.session.scalar(query)
        if patient is None:
            raise KeyError("patient not found")
        return patient

    def _assert_target(self, patient: db.Patient, schedule_version_id: UUID) -> None:
        target = self.session.execute(
            select(db.ScheduleVersion.id, db.Protocol.trial_id)
            .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
            .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .where(db.ScheduleVersion.id == schedule_version_id, db.ScheduleVersion.status == "APPROVED")
        ).one_or_none()
        if target is None or target.trial_id != patient.trial_id:
            raise ValueError("target must be an approved schedule version for the patient's trial")

    def _impact(self, prior: list[db.PatientEvent], result: EvaluationResult) -> dict[str, object]:
        prior_by_key = {self.patient_schedules.logical_key_for_row(row): row for row in prior}
        proposed = {f"{item.event_code}:{item.occurrence_index}": item for item in result.events}
        protected_ids = self.patient_schedules.protected_event_ids(prior)
        items: list[dict[str, object]] = []
        counts = {key: 0 for key in ("UNCHANGED", "ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED", "PROTECTED")}
        for key in sorted(prior_by_key.keys() | proposed.keys()):
            before = prior_by_key.get(key)
            after = proposed.get(key)
            before_date = before.nominal_start_date if before else None
            after_date = self.patient_schedules.result_date(after) if after else None
            before_status = before.status if before else None
            after_status = after.status.value if after else "CANCELLED"
            if before is None:
                change = "ADDED"
            elif before.id in protected_ids:
                change = "PROTECTED"
                after_date = before_date
                after_status = before_status
            elif after is None:
                change = "CANCELLED"
            elif before_date != after_date:
                change = "MOVED"
            elif before_status != after_status:
                change = "STATUS_CHANGED"
            else:
                change = "UNCHANGED"
            counts[change] += 1
            items.append({
                "logical_key": key,
                "change": change,
                "before": {"date": before_date, "status": before_status} if before else None,
                "after": {"date": after_date, "status": after_status} if after else None,
                "protected_history": change == "PROTECTED",
            })
        return to_jsonable_python({"summary": counts, "events": items})

    def _proposed_condition(
        self, proposal: db.ScheduleImpactProposal,
    ) -> PatientConditionStatus | None:
        payload = (proposal.impact or {}).get("condition_change")
        return PatientConditionStatus.model_validate(payload) if payload else None

    def _anchor_change(
        self, patient: db.Patient, candidate: db.PatientAnchor | None,
    ) -> dict[str, object] | None:
        if candidate is None:
            return None
        anchor = self.session.get(db.Anchor, candidate.anchor_definition_id)
        if (
            anchor is None
            or candidate.patient_id != patient.id
            or anchor.schedule_version_id != patient.current_schedule_version_id
        ):
            raise ValueError("anchor candidate does not belong to the patient's schedule")
        if candidate.status != "PENDING_CONFIRMATION":
            raise ValueError("anchor candidate is not pending confirmation")
        value = candidate.value_datetime or candidate.value_date
        if value is None:
            raise ValueError("anchor candidate has no date")
        selection = str((anchor.derivation_rule or {}).get("selection", "LAST")).upper()
        # Doc 1 s21: an anchor change has to be auditable as a CHANGE, which means
        # recording what it replaced. "The anchor is 03-Nov" does not tell a
        # reviewer that every dependent visit moved by four days.
        previous = self.session.scalar(select(db.PatientAnchor).where(
            db.PatientAnchor.patient_id == patient.id,
            db.PatientAnchor.anchor_definition_id == anchor.id,
            db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]),
        ).order_by(db.PatientAnchor.recorded_at.desc()))
        previous_value = (
            previous.value_datetime or previous.value_date if previous else None)
        return {
            "candidate_id": str(candidate.id), "anchor_code": anchor.code,
            "value": value, "selection": selection,
            "candidate_status": candidate.status,
            "previous_value": previous_value,
            "effective": not (selection == "FIRST" and previous is not None),
        }

    def _proposed_anchor(
        self, proposal: db.ScheduleImpactProposal, patient: db.Patient,
    ) -> dict[str, object] | None:
        payload = (proposal.impact or {}).get("anchor_change")
        if not payload:
            return None
        candidate = self.session.get(
            db.PatientAnchor, UUID(str(payload["candidate_id"])))
        return self._anchor_change(patient, candidate)

    @staticmethod
    def _state_override(
        state_change: dict[str, object] | None,
    ) -> dict[str, tuple[object, datetime]] | None:
        if not state_change:
            return None
        code = str(state_change.get("state_code") or "").strip()
        if not code:
            raise ValueError("state change requires state_code")
        try:
            effective_at = datetime.fromisoformat(str(state_change["effective_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("state change requires an ISO 8601 effective_at") from exc
        return {code: (state_change.get("state_value"), effective_at)}

    def _persist_condition(
        self,
        patient: db.Patient,
        change: PatientConditionStatus,
        *,
        proposal_id: UUID,
        organization_id: UUID,
        actor_id: UUID,
        reason: str | None,
    ) -> db.PatientCondition:
        """Write the confirmed condition transition, superseding the prior record.

        History stays append-only: reversing a wrongly activated condition adds a
        CANCELLED row rather than deleting the activation.
        """
        previous = list(self.session.scalars(select(db.PatientCondition).where(
            db.PatientCondition.patient_id == patient.id,
            db.PatientCondition.condition_code == change.condition_code,
            db.PatientCondition.superseded_by_id.is_(None),
        )))
        row = db.PatientCondition(
            patient_id=patient.id, condition_code=change.condition_code,
            state=change.state.value, occurrence_index=change.occurrence_index,
            occurrence_date=_as_date(change.occurrence_date),
            resolution_date=_as_date(change.resolution_date),
            impact_proposal_id=proposal_id, recorded_by=actor_id, reason=reason,
        )
        self.session.add(row)
        self.session.flush()
        for item in previous:
            item.superseded_by_id = row.id
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_CONDITION_STATE_CHANGED",
            entity_type="PATIENT", entity_id=patient.id,
            before=(
                {"state": previous[-1].state} if previous
                else {"state": ConditionState.NOT_OCCURRED.value}
            ),
            after={
                "condition_code": change.condition_code,
                "state": change.state.value,
                "occurrence_date": (
                    change.occurrence_date.isoformat() if change.occurrence_date else None),
                "resolution_date": (
                    change.resolution_date.isoformat() if change.resolution_date else None),
                "proposal_id": str(proposal_id),
            },
            reason=reason,
        ))
        return row

    #: The three assignments that have their own Patient columns. Anything else
    #: (PART, SEQUENCE, SUBSTUDY, COUNTRY...) lives in dimension_values, which is
    #: how doc 8 s35's open dimension set is supported without a migration per
    #: protocol.
    NAMED_DIMENSIONS = {
        "ARM": ("arm_id", db.Arm),
        "COHORT": ("cohort_id", db.Cohort),
        "POPULATION": ("population_id", db.Population),
    }

    def _assignment_change(
        self, patient: db.Patient, requested: dict[str, str | None] | None,
    ) -> dict[str, object] | None:
        """Validate a proposed reassignment against the patient's own schedule.

        A value the schedule does not define is rejected rather than stored: doc 8
        s30 is explicit that an unknown group must not silently become "no group",
        because that quietly turns a restricted visit into one everybody gets.
        """
        if not requested:
            return None
        version_id = patient.current_schedule_version_id
        if version_id is None:
            raise ValueError("patient is not pinned to an approved schedule")
        schedule = self.patient_schedules.repository.get(version_id)
        changes: dict[str, object] = {
            key.strip().upper(): value for key, value in requested.items()
        }
        # The hierarchy is checked against the WHOLE resulting assignment, not
        # one field at a time: "Cohort 1" is only wrong once you know which Part
        # the patient is in (doc 8 s10).
        resulting = {
            **_current_assignment_codes(self.session, patient, schedule),
            **changes,
        }
        problems = validate_assignment(schedule, resulting)
        if problems:
            raise ValueError("; ".join(problems))
        return changes or None

    def _with_assignment(
        self, patient: db.Patient, assignment: dict[str, object] | None,
    ) -> db.Patient:
        """A detached copy of the patient carrying the proposed assignment.

        Never the live row: a preview must be able to answer "what would happen"
        without the answer itself changing the patient.
        """
        if not assignment:
            return patient
        preview = db.Patient(
            id=patient.id, organization_id=patient.organization_id,
            trial_id=patient.trial_id, site_id=patient.site_id,
            patient_code=patient.patient_code,
            current_schedule_version_id=patient.current_schedule_version_id,
            arm_id=patient.arm_id, cohort_id=patient.cohort_id,
            population_id=patient.population_id,
            dimension_values=dict(patient.dimension_values or {}),
            status=patient.status,
        )
        self._set_assignment(preview, assignment)
        return preview

    def _set_assignment(
        self, patient: db.Patient, assignment: dict[str, object],
    ) -> None:
        values = dict(patient.dimension_values or {})
        for dimension, code in assignment.items():
            named = self.NAMED_DIMENSIONS.get(dimension)
            if named is not None:
                column, model = named
                row = None
                if code is not None:
                    row = self.session.scalar(select(model).where(
                        model.schedule_version_id == patient.current_schedule_version_id,
                        model.code == code,
                    ))
                setattr(patient, column, row.id if row is not None else None)
            if code is None:
                values.pop(dimension, None)
            else:
                values[dimension] = [str(code)]
        patient.dimension_values = values

    def _apply_assignment(
        self,
        patient: db.Patient,
        assignment: dict[str, object],
        *,
        proposal_id: UUID,
        organization_id: UUID,
        actor_id: UUID,
        reason: str | None,
    ) -> None:
        before = {
            "arm_id": str(patient.arm_id) if patient.arm_id else None,
            "cohort_id": str(patient.cohort_id) if patient.cohort_id else None,
            "population_id": str(patient.population_id) if patient.population_id else None,
            "dimension_values": to_jsonable_python(patient.dimension_values or {}),
        }
        self._set_assignment(patient, assignment)
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="PATIENT_ASSIGNMENT_CHANGED",
            entity_type="PATIENT", entity_id=patient.id,
            before=before,
            after={
                "arm_id": str(patient.arm_id) if patient.arm_id else None,
                "cohort_id": str(patient.cohort_id) if patient.cohort_id else None,
                "population_id": (
                    str(patient.population_id) if patient.population_id else None),
                "dimension_values": to_jsonable_python(patient.dimension_values or {}),
                "impact_proposal_id": str(proposal_id),
                "reason": reason,
            },
        ))

    def _fingerprint(
        self,
        patient: db.Patient,
        result: EvaluationResult,
        prior: list[db.PatientEvent],
        horizon: date,
        condition_change: PatientConditionStatus | None = None,
        anchor_change: dict[str, object] | None = None,
        state_change: dict[str, object] | None = None,
        assignment_change: dict[str, object] | None = None,
    ) -> str:
        protected_ids = self.patient_schedules.protected_event_ids(prior)
        payload = {
            "patient_id": str(patient.id),
            "from_schedule_version_id": str(patient.current_schedule_version_id),
            "to_schedule_version_id": str(result.schedule_version_id),
            "horizon": horizon.isoformat(),
            "condition_change": (
                condition_change.model_dump(mode="json") if condition_change else None),
            "anchor_change": to_jsonable_python(anchor_change),
            "state_change": to_jsonable_python(state_change),
            "assignment_change": to_jsonable_python(assignment_change),
            # The assignment the patient is on TODAY is part of the fingerprint:
            # if someone else reassigns them between preview and confirm, this
            # proposal is describing a change from a state that no longer exists.
            "current_assignment": {
                "arm_id": str(patient.arm_id) if patient.arm_id else None,
                "cohort_id": str(patient.cohort_id) if patient.cohort_id else None,
                "population_id": (
                    str(patient.population_id) if patient.population_id else None),
                "dimension_values": to_jsonable_python(patient.dimension_values or {}),
            },
            "input": result.input_snapshot,
            "prior": [{
                "id": str(item.id),
                "logical_key": self.patient_schedules.logical_key_for_row(item),
                "date": item.nominal_start_date.isoformat() if item.nominal_start_date else None,
                "status": item.status,
                "protected": item.id in protected_ids,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            } for item in prior],
        }
        encoded = json.dumps(to_jsonable_python(payload), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _current_assignment_codes(
    session, patient: db.Patient, schedule,
) -> dict[str, str]:
    """The codes the patient is assigned to right now, keyed by dimension type."""
    codes: dict[str, str] = {}
    for dimension_type, column, model in (
        ("ARM", patient.arm_id, db.Arm),
        ("COHORT", patient.cohort_id, db.Cohort),
        ("POPULATION", patient.population_id, db.Population),
    ):
        if column is None:
            continue
        row = session.get(model, column)
        if row is not None:
            codes[dimension_type] = row.code
    for dimension_type, values in (patient.dimension_values or {}).items():
        if values:
            codes.setdefault(dimension_type.upper(), str(values[0]))
    return codes
