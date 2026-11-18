from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from pydantic_core import to_jsonable_python

from app.domain.schedule import models as domain

from . import models as db


def _json(value: object | None) -> object | None:
    if value is None:
        return None
    return to_jsonable_python(value, by_alias=True)


class ScheduleRepository:
    def __init__(self, session: Session):
        self.session = session

    def persist_draft(
        self,
        schedule: domain.UniversalSchedule,
        *,
        schedule_definition_id: UUID,
        extraction_run_id: UUID | None = None,
    ) -> None:
        if schedule.schedule_metadata.protocol_version_id is None:
            raise ValueError("protocol_version_id is required to persist schedule evidence")
        if self.session.get(db.ScheduleVersion, schedule.schedule_version_id):
            raise ValueError("schedule version already exists")
        version = db.ScheduleVersion(
            id=schedule.schedule_version_id,
            schedule_definition_id=schedule_definition_id,
            version_number=schedule.schedule_metadata.version_number,
            status=schedule.schedule_metadata.status.value,
            extraction_run_id=extraction_run_id,
            schema_version=schedule.schema_version,
            metadata_json=schedule.schedule_metadata.model_dump(mode="json", exclude={"status", "version_number", "protocol_version_id"}),
            created_at=schedule.created_at,
        )
        self.session.add(version)
        for item in schedule.epochs:
            self.session.add(db.Epoch(
                id=item.id, schedule_version_id=version.id, code=item.code,
                protocol_label=item.protocol_label, display_name=item.display_name,
                description=item.description, sequence_number=item.sequence_number,
                timing=_json(item.timing), applicability=_json(item.applicability),
                evidence_refs=_json(item.evidence_refs),
            ))
        for cls, values in ((db.Arm, schedule.arms), (db.Cohort, schedule.cohorts), (db.Population, schedule.populations)):
            for item in values:
                self.session.add(cls(
                    id=item.id, schedule_version_id=version.id, code=item.code,
                    protocol_label=item.protocol_label, display_name=item.display_name,
                    description=item.description, criteria=_json(item.criteria),
                ))
        for item in schedule.anchors:
            self.session.add(db.Anchor(
                id=item.id, schedule_version_id=version.id, code=item.code,
                protocol_label=item.protocol_label, display_name=item.display_name,
                anchor_type=item.anchor_type, derivation_rule=item.derivation_rule,
                source_event_code=item.source_event_code, status=item.status,
                evidence_refs=_json(item.evidence_refs),
            ))
        event_ids = {item.code: item.id for item in schedule.events}
        for item in schedule.events:
            self.session.add(db.Event(
                id=item.id, schedule_version_id=version.id, code=item.code,
                protocol_label=item.protocol_label, display_name=item.display_name,
                normalized_name=item.normalized_name, event_type=item.event_type,
                epoch_id=item.epoch_id, sequence_number=item.sequence_number,
                timing=_json(item.timing), conditions=_json(item.conditions),
                metadata_json=item.metadata,
                interpretation_status=item.interpretation_status.value,
                requires_review=item.requires_review, evidence_refs=_json(item.evidence_refs),
            ))
            for rule in item.applicability:
                self.session.add(db.EventApplicability(
                    event_id=item.id, dimension_type=rule.dimension,
                    expression=_json(rule),
                ))
            for activity in item.activities:
                self.session.add(db.Activity(
                    id=activity.id, event_id=item.id, code=activity.code,
                    protocol_label=activity.protocol_label, display_name=activity.display_name,
                    activity_type=activity.activity_type, requiredness=activity.requiredness.value,
                    timing=_json(activity.timing), conditions=_json(activity.conditions),
                    metadata_json=activity.metadata,
                    interpretation_status=activity.interpretation_status.value,
                    requires_review=activity.requires_review,
                    evidence_refs=_json(activity.evidence_refs),
                ))
            if item.recurrence:
                self.session.add(db.EventRecurrence(event_id=item.id, rule=_json(item.recurrence)))
        self.session.flush()
        for target in schedule.events:
            for dependency in target.dependencies:
                source_id = event_ids.get(dependency.source_event_code)
                if source_id is None:
                    continue
                self.session.add(db.EventDependency(
                    schedule_version_id=version.id, source_event_id=source_id,
                    target_event_id=target.id, dependency_type=dependency.dependency_type,
                    condition=_json(dependency.condition),
                ))
        for item in schedule.evidence:
            self.session.add(db.Evidence(
                id=item.id, protocol_version_id=schedule.schedule_metadata.protocol_version_id,
                schedule_version_id=version.id, evidence_type=item.evidence_type,
                page_number=item.page_number, section_title=item.section_title,
                table_title=item.table_title, row_identifier=item.row_identifier,
                column_identifier=item.column_identifier, source_text=item.source_text,
                source_locator=item.source_locator, extraction_context=item.extraction_context,
            ))
        self.session.flush()
        for item in schedule.claim_evidence:
            self.session.add(db.ClaimEvidence(
                schedule_version_id=version.id, evidence_id=item.evidence_id,
                claim_type=item.claim_type, claim_entity_type=item.claim_entity_type,
                claim_entity_id=item.claim_entity_id, claim_path=item.claim_path,
                interpretation=item.interpretation,
                confidence=Decimal(str(item.confidence)) if item.confidence is not None else None,
            ))
        self.replace_validation_issues(version.id, schedule.validation_issues)

    def replace_validation_issues(self, schedule_version_id: UUID, issues: list[domain.ValidationIssue]) -> None:
        self.session.execute(delete(db.ValidationIssue).where(
            db.ValidationIssue.schedule_version_id == schedule_version_id,
            db.ValidationIssue.validator_version == "uctsm-validator.v1",
            db.ValidationIssue.status == "OPEN",
        ))
        for item in issues:
            self.session.add(db.ValidationIssue(
                id=item.id, schedule_version_id=schedule_version_id,
                validator_version="uctsm-validator.v1", entity_type=item.entity_type,
                entity_id=item.entity_id, issue_code=item.issue_code,
                severity=item.severity.value, message=item.message, details=item.details,
                blocking=item.blocking, status=item.status.value,
            ))

    def replace_event(self, schedule_version_id: UUID, event: domain.Event) -> None:
        row = self.session.scalar(select(db.Event).where(
            db.Event.id == event.id, db.Event.schedule_version_id == schedule_version_id,
        ))
        if row is None:
            raise KeyError("event definition not found")
        row.code = event.code
        row.protocol_label = event.protocol_label
        row.display_name = event.display_name
        row.normalized_name = event.normalized_name
        row.event_type = event.event_type
        row.epoch_id = event.epoch_id
        row.sequence_number = event.sequence_number
        row.timing = _json(event.timing)
        row.conditions = _json(event.conditions)
        row.metadata_json = event.metadata
        row.interpretation_status = event.interpretation_status.value
        row.requires_review = event.requires_review
        row.evidence_refs = _json(event.evidence_refs)
        self.session.execute(delete(db.EventApplicability).where(db.EventApplicability.event_id == event.id))
        self.session.execute(delete(db.EventDependency).where(db.EventDependency.target_event_id == event.id))
        self.session.execute(delete(db.EventRecurrence).where(db.EventRecurrence.event_id == event.id))
        self.session.execute(delete(db.Activity).where(db.Activity.event_id == event.id))
        event_ids = dict(self.session.execute(select(db.Event.code, db.Event.id).where(
            db.Event.schedule_version_id == schedule_version_id,
        )).all())
        for rule in event.applicability:
            self.session.add(db.EventApplicability(
                event_id=event.id, dimension_type=rule.dimension, expression=_json(rule),
            ))
        for dependency in event.dependencies:
            source_id = event_ids.get(dependency.source_event_code)
            if source_id is None:
                continue
            self.session.add(db.EventDependency(
                schedule_version_id=schedule_version_id, source_event_id=source_id,
                target_event_id=event.id, dependency_type=dependency.dependency_type,
                condition=_json(dependency.condition),
            ))
        if event.recurrence:
            self.session.add(db.EventRecurrence(event_id=event.id, rule=_json(event.recurrence)))
        for activity in event.activities:
            self.session.add(db.Activity(
                id=activity.id, event_id=event.id, code=activity.code,
                protocol_label=activity.protocol_label, display_name=activity.display_name,
                activity_type=activity.activity_type, requiredness=activity.requiredness.value,
                timing=_json(activity.timing), conditions=_json(activity.conditions),
                metadata_json=activity.metadata,
                interpretation_status=activity.interpretation_status.value,
                requires_review=activity.requires_review,
                evidence_refs=_json(activity.evidence_refs),
            ))

    def get(self, schedule_version_id: UUID) -> domain.UniversalSchedule:
        version = self.session.get(db.ScheduleVersion, schedule_version_id)
        if version is None:
            raise KeyError("schedule version not found")
        definition = self.session.get(db.ScheduleDefinition, version.schedule_definition_id)
        assert definition is not None
        metadata = dict(version.metadata_json or {})
        metadata.update({
            "name": metadata.get("name", definition.name),
            "description": metadata.get("description", definition.description),
            "schedule_type": metadata.get("schedule_type", definition.schedule_type),
            "protocol_version_id": definition.protocol_version_id,
            "version_number": version.version_number,
            "status": version.status,
        })
        epochs = [domain.Epoch.model_validate({
            "id": row.id, "code": row.code, "protocol_label": row.protocol_label,
            "display_name": row.display_name, "description": row.description,
            "sequence_number": row.sequence_number, "timing": row.timing,
            "applicability": row.applicability, "evidence_refs": row.evidence_refs,
        }) for row in self.session.scalars(select(db.Epoch).where(db.Epoch.schedule_version_id == version.id))]

        def dimensions(cls: type[db.DimensionBase]) -> list[domain.StudyDimension]:
            return [domain.StudyDimension.model_validate({
                "id": row.id, "code": row.code, "protocol_label": row.protocol_label,
                "display_name": row.display_name, "description": row.description,
                "criteria": row.criteria,
            }) for row in self.session.scalars(select(cls).where(cls.schedule_version_id == version.id))]

        anchors = [domain.Anchor.model_validate({
            "id": row.id, "code": row.code, "protocol_label": row.protocol_label,
            "display_name": row.display_name, "anchor_type": row.anchor_type,
            "derivation_rule": row.derivation_rule, "source_event_code": row.source_event_code,
            "status": row.status, "evidence_refs": row.evidence_refs,
        }) for row in self.session.scalars(select(db.Anchor).where(db.Anchor.schedule_version_id == version.id))]
        event_rows = list(self.session.scalars(select(db.Event).where(db.Event.schedule_version_id == version.id).order_by(db.Event.sequence_number, db.Event.code)))
        event_ids = [row.id for row in event_rows]
        applicability = {event_id: [] for event_id in event_ids}
        activities = {event_id: [] for event_id in event_ids}
        recurrence: dict[UUID, object] = {}
        dependencies = {event_id: [] for event_id in event_ids}
        if event_ids:
            for row in self.session.scalars(select(db.EventApplicability).where(db.EventApplicability.event_id.in_(event_ids))):
                applicability[row.event_id].append(row.expression)
            for row in self.session.scalars(select(db.Activity).where(db.Activity.event_id.in_(event_ids))):
                activities[row.event_id].append({
                    "id": row.id, "code": row.code, "protocol_label": row.protocol_label,
                    "display_name": row.display_name, "activity_type": row.activity_type,
                    "requiredness": row.requiredness, "timing": row.timing,
                    "conditions": row.conditions, "metadata": row.metadata_json,
                    "interpretation_status": row.interpretation_status,
                    "requires_review": row.requires_review, "evidence_refs": row.evidence_refs,
                })
            for row in self.session.scalars(select(db.EventRecurrence).where(db.EventRecurrence.event_id.in_(event_ids))):
                recurrence[row.event_id] = row.rule
            code_by_id = {row.id: row.code for row in event_rows}
            for row in self.session.scalars(select(db.EventDependency).where(db.EventDependency.target_event_id.in_(event_ids))):
                dependencies[row.target_event_id].append({
                    "source_event_code": code_by_id[row.source_event_id],
                    "dependency_type": row.dependency_type, "condition": row.condition,
                })
        events = [domain.Event.model_validate({
            "id": row.id, "code": row.code, "protocol_label": row.protocol_label,
            "display_name": row.display_name, "normalized_name": row.normalized_name,
            "event_type": row.event_type, "epoch_id": row.epoch_id,
            "sequence_number": row.sequence_number, "timing": row.timing,
            "conditions": row.conditions, "applicability": applicability[row.id],
            "dependencies": dependencies[row.id], "recurrence": recurrence.get(row.id),
            "activities": activities[row.id], "evidence_refs": row.evidence_refs,
            "interpretation_status": row.interpretation_status,
            "requires_review": row.requires_review, "metadata": row.metadata_json,
        }) for row in event_rows]
        evidence = [domain.Evidence.model_validate({
            "id": row.id, "evidence_type": row.evidence_type,
            "page_number": row.page_number, "section_title": row.section_title,
            "table_title": row.table_title, "row_identifier": row.row_identifier,
            "column_identifier": row.column_identifier, "source_text": row.source_text,
            "source_locator": row.source_locator, "extraction_context": row.extraction_context,
        }) for row in self.session.scalars(select(db.Evidence).where(db.Evidence.schedule_version_id == version.id))]
        claims = [domain.ClaimEvidence.model_validate({
            "evidence_id": row.evidence_id, "claim_type": row.claim_type,
            "claim_entity_type": row.claim_entity_type, "claim_entity_id": row.claim_entity_id,
            "claim_path": row.claim_path, "interpretation": row.interpretation,
            "confidence": float(row.confidence) if row.confidence is not None else None,
        }) for row in self.session.scalars(select(db.ClaimEvidence).where(db.ClaimEvidence.schedule_version_id == version.id))]
        issues = [domain.ValidationIssue.model_validate({
            "id": row.id, "entity_type": row.entity_type, "entity_id": row.entity_id,
            "issue_code": row.issue_code, "severity": row.severity,
            "message": row.message, "details": row.details,
            "blocking": row.blocking, "status": row.status,
        }) for row in self.session.scalars(select(db.ValidationIssue).where(db.ValidationIssue.schedule_version_id == version.id))]
        return domain.UniversalSchedule(
            schedule_version_id=version.id,
            schedule_metadata=domain.ScheduleMetadata.model_validate(metadata),
            epochs=epochs, arms=dimensions(db.Arm), cohorts=dimensions(db.Cohort),
            populations=dimensions(db.Population), anchors=anchors, events=events,
            evidence=evidence, claim_evidence=claims, validation_issues=issues,
            created_at=version.created_at,
        )


def unsupported_extension_handler_frequency(session: Session) -> list[dict[str, object]]:
    """Rank every still-open, blocking UNSUPPORTED_TIMING issue by the
    PROTOCOL_DEFINED `handler` name it names, across every schedule version in
    the database -- the concrete "what to build next" signal for the
    extension-handler process in `extension_handlers.py`/`builtin_extensions.py`.

    A handler name appearing here has been seen in real/attempted protocol
    extraction but has no deterministic implementation registered yet. Once a
    handler for that name is installed and the affected schedules are
    revalidated, it naturally disappears from this ranking -- there is
    nothing else to maintain. Rows are sorted by occurrence_count descending,
    so the most-needed missing handler is always first.
    """
    rows = session.scalars(select(db.ValidationIssue).where(
        db.ValidationIssue.issue_code == "UNSUPPORTED_TIMING",
        db.ValidationIssue.status == "OPEN",
        db.ValidationIssue.blocking.is_(True),
    )).all()
    buckets: dict[str | None, dict[str, object]] = {}
    for row in rows:
        handler = row.details.get("handler") if isinstance(row.details, dict) else None
        bucket = buckets.setdefault(handler, {
            "handler": handler, "occurrence_count": 0,
            "schedule_version_ids": set(), "sample_message": row.message,
        })
        bucket["occurrence_count"] = int(bucket["occurrence_count"]) + 1
        bucket["schedule_version_ids"].add(row.schedule_version_id)  # type: ignore[union-attr]
    ranked = [
        {
            "handler": bucket["handler"],
            "occurrence_count": bucket["occurrence_count"],
            "distinct_schedule_versions": len(bucket["schedule_version_ids"]),  # type: ignore[arg-type]
            "sample_message": bucket["sample_message"],
        }
        for bucket in buckets.values()
    ]
    ranked.sort(key=lambda item: item["occurrence_count"], reverse=True)  # type: ignore[arg-type,return-value]
    return ranked


def unsupported_pattern_alert_payloads(
    session: Session, *, already_alerted: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """Build admin-facing, ready-to-insert `system_alerts` payloads (Mongo
    `db.system_alerts` schema: type/description/affected/severity) for every
    still-unregistered handler NOT already in `already_alerted` -- the actual
    "direct the admin what to do" ticket, generated from real occurrence data
    rather than raw technical logs.

    Callers own de-duplication: pass the set of handler names that already
    have an open alert so this never creates a second one for the same gap.
    Severity escalates with real impact (more blocked protocols = higher
    priority), matching the existing alert severities already used elsewhere
    in `admin_routes.py`.
    """
    payloads = []
    for row in unsupported_extension_handler_frequency(session):
        handler = row["handler"]
        if handler is None or handler in already_alerted:
            continue
        occurrence_count = row["occurrence_count"]
        distinct = row["distinct_schedule_versions"]
        severity = "high" if occurrence_count >= 5 else "medium" if occurrence_count >= 2 else "low"
        payloads.append({
            "type": "unsupported_protocol_pattern",
            "description": (
                f"Protocol pattern {handler!r} has no installed deterministic handler. "
                f"{row['sample_message']}"
            ),
            "affected": f"{occurrence_count} issue(s) across {distinct} protocol schedule(s)",
            "severity": severity,
            "status": "open",
            "handler": handler,
        })
    return payloads


def unsupported_handler_evidence(
    session: Session, handler: str, *, limit: int = 10,
) -> list[dict[str, object]]:
    """The actual protocol text behind a named gap -- real quotes, their page
    numbers, and which schedule version each came from -- so a developer can
    design the correct fix without manually opening every affected protocol's
    review screen one by one.

    Joins each still-open, blocking UNSUPPORTED_TIMING issue (for `handler`)
    to its flagged Event, then to that event's own cited Evidence rows.
    Deduplicates identical quotes (the same wording often repeats verbatim
    across amendments/protocols) and caps the result -- this is reconnaissance
    for a developer, not a replacement for reading the real protocol if the
    pattern turns out to be more subtle than the quotes alone suggest.
    """
    rows = session.scalars(select(db.ValidationIssue).where(
        db.ValidationIssue.issue_code == "UNSUPPORTED_TIMING",
        db.ValidationIssue.status == "OPEN",
        db.ValidationIssue.blocking.is_(True),
    )).all()
    event_ids: list[tuple[UUID, UUID]] = []  # (schedule_version_id, event_id)
    for row in rows:
        if (
            isinstance(row.details, dict) and row.details.get("handler") == handler
            and row.entity_type == "EVENT" and row.entity_id is not None
        ):
            event_ids.append((row.schedule_version_id, row.entity_id))
    seen_quotes: set[str] = set()
    quotes: list[dict[str, object]] = []
    for schedule_version_id, event_id in event_ids:
        event = session.get(db.Event, event_id)
        if event is None or not event.evidence_refs:
            continue
        evidence_rows = session.scalars(select(db.Evidence).where(
            db.Evidence.id.in_([UUID(str(ref)) for ref in event.evidence_refs]),
        )).all()
        for evidence in evidence_rows:
            text = (evidence.source_text or "").strip()
            if not text or text in seen_quotes:
                continue
            seen_quotes.add(text)
            quotes.append({
                "source_text": text, "page_number": evidence.page_number,
                "schedule_version_id": schedule_version_id,
            })
            if len(quotes) >= limit:
                return quotes
    return quotes
