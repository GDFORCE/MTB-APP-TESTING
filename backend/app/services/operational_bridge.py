from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import schedule_origin_anchor
from app.services.schedule_service import PatientScheduleService


@dataclass(frozen=True)
class BridgeActivity:
    """One activity within one patient visit occurrence (doc s12).

    Carries what the patient/CRC visit-detail view needs to show a real
    ``Activity | Timing Rule | Planned Time | Actual Time | Status`` row,
    read from the per-occurrence ``uctsm_patient_activities`` record the
    evaluator already persists - not the event's static activity list, which
    knows nothing about which cycle this occurrence actually is.
    """

    code: str | None
    display_name: str
    status: str
    requiredness: str
    planned_time: datetime | None
    earliest_time: datetime | None
    latest_time: datetime | None
    actual_time: datetime | None


@dataclass(frozen=True)
class BridgeEvent:
    patient_event_id: UUID
    logical_occurrence_id: UUID
    logical_key: str
    event_definition_id: UUID
    event_code: str
    name: str
    event_type: str
    status: str
    nominal_date: date | None
    earliest_date: date | None
    latest_date: date | None
    #: Doc s10/s12: names only, and only the activities APPLICABLE to this
    #: occurrence - never every activity the event's definition could ever
    #: have, which would put "MRI" back on a cycle it does not apply to.
    activities: tuple[str, ...]
    #: The richer per-activity data a "Level 3" expanded view needs. Additive:
    #: existing consumers of ``activities`` (plain names) are untouched, and
    #: this defaults to empty so a caller/fixture that only ever dealt with
    #: names keeps working unchanged.
    activity_details: tuple[BridgeActivity, ...] = ()


@dataclass(frozen=True)
class BridgeEnrollment:
    patient_id: UUID
    schedule_version_id: UUID
    evaluation_id: UUID
    events: tuple[BridgeEvent, ...]


class OperationalBridgeService:
    """Idempotent boundary between Mongo operational IDs and canonical UCTSM IDs."""

    def __init__(self, session: Session):
        self.session = session
        self.repository = ScheduleRepository(session)

    def validate_trial_link(
        self,
        *,
        organization_id: UUID,
        external_trial_id: str,
        external_schedule_definition_id: str | None,
        trial_id: UUID,
        schedule_definition_id: UUID,
    ) -> dict[str, str]:
        trial = self.session.scalar(select(db.Trial).where(
            db.Trial.id == trial_id, db.Trial.organization_id == organization_id,
        ))
        if trial is None:
            raise KeyError("canonical trial not found")
        definition = self.session.scalar(
            select(db.ScheduleDefinition)
            .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .where(
                db.ScheduleDefinition.id == schedule_definition_id,
                db.Protocol.trial_id == trial.id,
            )
        )
        if definition is None:
            raise KeyError("canonical schedule definition does not belong to the trial")
        approved = self._approved_version(trial.id, definition.id)
        trial.external_trial_id = external_trial_id
        definition.external_schedule_definition_id = (
            external_schedule_definition_id or external_trial_id
        )
        self.session.add(db.AuditEvent(
            organization_id=organization_id,
            action="OPERATIONAL_TRIAL_LINKED",
            entity_type="TRIAL", entity_id=trial.id,
            before=None,
            after={
                "external_trial_id": external_trial_id,
                "schedule_definition_id": str(definition.id),
                "approved_schedule_version_id": str(approved.id),
            },
        ))
        return {
            "trial_id": str(trial.id),
            "schedule_definition_id": str(definition.id),
            "schedule_version_id": str(approved.id),
        }

    def enroll_patient(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        canonical_trial_id: UUID,
        schedule_definition_id: UUID,
        external_patient_id: str,
        patient_code: str,
        baseline_date: date,
        horizon: date,
        arm_label: str | None = None,
        cohort_label: str | None = None,
        dimension_values: dict[str, list[str]] | None = None,
    ) -> BridgeEnrollment:
        trial = self.session.scalar(select(db.Trial).where(
            db.Trial.id == canonical_trial_id,
            db.Trial.organization_id == organization_id,
        ))
        if trial is None:
            raise KeyError("canonical trial not found")
        schedule_version = self._approved_version(trial.id, schedule_definition_id)
        schedule = self.repository.get(schedule_version.id)
        existing = self.session.scalar(select(db.Patient).where(
            db.Patient.trial_id == trial.id,
            db.Patient.external_patient_id == external_patient_id,
        ))
        if existing is not None:
            if existing.current_schedule_version_id != schedule_version.id:
                raise ValueError(
                    "existing linked patient is pinned to another schedule; use impact preview/confirm")
            patient = existing
        else:
            patient = db.Patient(
                organization_id=organization_id, trial_id=trial.id,
                patient_code=patient_code, external_patient_id=external_patient_id,
                current_schedule_version_id=schedule_version.id,
                arm_id=self._dimension_id(schedule.arms, arm_label),
                cohort_id=self._dimension_id(schedule.cohorts, cohort_label),
                dimension_values=dimension_values or {},
            )
            self.session.add(patient)
            self.session.flush()
            self.session.add(db.PatientScheduleAssignment(
                patient_id=patient.id, schedule_version_id=schedule_version.id,
                assignment_type="ENROLMENT", reason="Operational patient enrolment bridge",
                assigned_by=actor_id,
            ))
            self.session.add(db.AuditEvent(
                organization_id=organization_id, actor_id=actor_id,
                action="OPERATIONAL_PATIENT_LINKED",
                entity_type="PATIENT", entity_id=patient.id,
                before=None,
                after={
                    "external_patient_id": external_patient_id,
                    "schedule_version_id": str(schedule_version.id),
                },
            ))
        # Doc s4. The origin is whichever anchor the schedule was BUILT against,
        # recorded on the anchor itself by the shared resolver. Matching on the
        # name "Baseline" used to strand every protocol whose origin is printed
        # as "First Dose" or "Randomisation": enrolment failed, and the canonical
        # path refuses to fall back to legacy, so the patient got no schedule.
        baseline_anchor = schedule_origin_anchor(schedule)
        if baseline_anchor is None:
            raise ValueError(
                "the approved schedule does not state which anchor its Day 0 is "
                "measured from, so a patient cannot be dated against it")
        current_anchor = self.session.scalar(select(db.PatientAnchor).where(
            db.PatientAnchor.patient_id == patient.id,
            db.PatientAnchor.anchor_definition_id == baseline_anchor.id,
            db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]),
        ).order_by(db.PatientAnchor.recorded_at.desc()))
        if current_anchor is None:
            self.session.add(db.PatientAnchor(
                patient_id=patient.id, anchor_definition_id=baseline_anchor.id,
                value_date=baseline_date, status="CONFIRMED",
                source_type="OPERATIONAL_ENROLMENT",
                source_reference={"external_patient_id": external_patient_id},
                recorded_by=actor_id,
            ))
            self.session.flush()
        elif current_anchor.value_date != baseline_date:
            raise ValueError(
                "linked patient's baseline differs; use anchor impact preview/confirm")
        evaluation, rows = PatientScheduleService(self.session).evaluate(
            patient.id, organization_id=organization_id, horizon=horizon,
            idempotency_key=f"operational-enrolment:{external_patient_id}:{schedule_version.id}",
        )
        definitions = {item.id: item for item in schedule.events}
        # Doc s10/s12: per-OCCURRENCE activity results, not the event's static
        # activity list. The static list has no idea which cycle this is, so it
        # would put every activity - including one gated to "cycles 2, 4, 6" -
        # on every occurrence regardless of what the evaluator actually decided.
        activity_defs = {
            activity.id: activity
            for event_def in schedule.events for activity in event_def.activities
        }
        patient_activities = self.session.scalars(select(db.PatientActivity).where(
            db.PatientActivity.patient_event_id.in_([row.id for row in rows]),
        )).all() if rows else []
        activities_by_event: dict[UUID, list[db.PatientActivity]] = {}
        for row in patient_activities:
            activities_by_event.setdefault(row.patient_event_id, []).append(row)

        def bridge_activities(event_id: UUID) -> tuple[BridgeActivity, ...]:
            items = sorted(
                activities_by_event.get(event_id, []),
                key=lambda item: (item.sequence_number is None, item.sequence_number or 0),
            )
            return tuple(
                BridgeActivity(
                    code=item.activity_code,
                    display_name=(
                        activity_defs[item.activity_definition_id].display_name
                        if item.activity_definition_id in activity_defs
                        else (item.activity_code or "Activity")
                    ),
                    status=item.status, requiredness=item.requiredness,
                    planned_time=item.planned_time, earliest_time=item.earliest_time,
                    latest_time=item.latest_time, actual_time=item.actual_time,
                )
                for item in items
            )

        return BridgeEnrollment(
            patient_id=patient.id,
            schedule_version_id=schedule_version.id,
            evaluation_id=evaluation.id,
            events=tuple(
                BridgeEvent(
                    patient_event_id=row.id,
                    logical_occurrence_id=row.logical_occurrence_id,
                    logical_key=row.logical_key,
                    event_definition_id=row.event_definition_id,
                    event_code=(definitions[row.event_definition_id].code if row.event_definition_id in definitions else row.logical_key.rsplit(":", 1)[0]),
                    name=(definitions[row.event_definition_id].display_name if row.event_definition_id in definitions else row.logical_key),
                    event_type=(definitions[row.event_definition_id].event_type if row.event_definition_id in definitions else "VISIT"),
                    status=row.status,
                    nominal_date=row.nominal_start_date,
                    earliest_date=row.earliest_date,
                    latest_date=row.latest_date,
                    activities=tuple(
                        item.display_name for item in bridge_activities(row.id)
                        if item.status not in ("NOT_APPLICABLE", "CANCELLED")
                    ),
                    activity_details=bridge_activities(row.id),
                )
                for row in rows
            ),
        )

    def _approved_version(
        self, trial_id: UUID, schedule_definition_id: UUID,
    ) -> db.ScheduleVersion:
        approved = list(self.session.scalars(
            select(db.ScheduleVersion)
            .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
            .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
            .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
            .where(
                db.Protocol.trial_id == trial_id,
                db.ScheduleDefinition.id == schedule_definition_id,
                db.ScheduleVersion.status == "APPROVED",
            )
            .order_by(db.ScheduleVersion.version_number.desc())
        ))
        # Doc 9 s8: an amendment can be approved before it applies. Enrolling
        # onto a version that is not in force yet would schedule a patient by a
        # protocol that does not govern them today - the same rule the canonical
        # enrolment endpoint already applies, enforced here too so the live Add
        # Patient flow cannot bypass it.
        today = date.today()
        in_force = [
            item for item in approved
            if item.effective_from is None or item.effective_from <= today
        ]
        if in_force:
            return in_force[0]
        if approved:
            soonest = min(
                item.effective_from for item in approved
                if item.effective_from is not None
            )
            raise ValueError(
                "no approved canonical schedule is in force today; the approved "
                f"version takes effect on {soonest.isoformat()}"
            )
        raise ValueError("no approved canonical schedule is available for the linked trial")

    @staticmethod
    def _dimension_id(values: list[object], label: str | None) -> UUID | None:
        if not label:
            return None
        normalized = label.strip().casefold()
        matches = [item for item in values if normalized in {
            item.code.casefold(), item.protocol_label.casefold(), item.display_name.casefold(),
        }]
        if len(matches) != 1:
            raise ValueError(f"dimension assignment {label!r} is missing or ambiguous")
        return matches[0].id
