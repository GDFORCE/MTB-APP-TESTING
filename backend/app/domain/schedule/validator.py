from __future__ import annotations

from collections import defaultdict, deque
from uuid import UUID

from .models import (
    DependencyMode,
    IssueStatus,
    InterpretationStatus,
    Severity,
    UniversalSchedule,
    ValidationIssue,
)
from .timing import (
    ActivityReference,
    ApproximateTiming,
    CycleDayTiming,
    EventReference,
    NominalWindowTiming,
    ProtocolDefinedTiming,
    TriggeredTiming,
    UnresolvedTiming,
)


def timing_references(timing: object) -> list[object]:
    """Return all temporal references nested in a timing expression."""
    references = []
    direct = getattr(timing, "reference", None)
    if direct is not None:
        references.append(direct)
    if isinstance(timing, NominalWindowTiming):
        references.extend(timing_references(timing.nominal))
    if isinstance(timing, TriggeredTiming):
        references.append(timing.trigger)
    return references


#: Doc 10 s2-s3. The event types the projection layer knows how to present -
#: reminder wording, attendance expectation, calendar treatment. A protocol may
#: legitimately name something else, so an unknown type is surfaced for a
#: reviewer rather than blocked: refusing approval over vocabulary would stop
#: real work for no clinical reason, while presenting an unknown type as an
#: ordinary site visit could tell a patient to travel.
CORE_EVENT_TYPES = frozenset({
    "SITE_VISIT", "TELEPHONE_CONTACT", "HOME_VISIT", "ASSESSMENT", "IMAGING",
    "LABORATORY", "INPATIENT_ADMISSION", "INPATIENT_DISCHARGE", "UNSCHEDULED",
    "SAFETY_ASSESSMENT", "TREATMENT", "PROCEDURE", "REMOTE",
    # Generic labels are accepted because they are presentation-SAFE: they
    # promise nothing about how the patient attends, so the mode fields decide
    # that. It is the specific-but-unknown type that needs a reviewer.
    "VISIT", "CONTACT", "OTHER",
})


class ScheduleValidator:
    version = "uctsm-validator.v1"

    def validate(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues.extend(self._unique_codes(schedule))
        issues.extend(self._references(schedule))
        issues.extend(self._dependency_graph(schedule))
        issues.extend(self._clinical_completeness(schedule))
        issues.extend(self._schedule_structures(schedule))
        issues.extend(self._anchor_integrity(schedule))
        issues.extend(self._information_loss(schedule))
        return issues

    @staticmethod
    def _issue(
        code: str,
        message: str,
        *,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        blocking: bool = True,
        severity: Severity = Severity.ERROR,
        details: dict[str, object] | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(
            issue_code=code,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            blocking=blocking,
            severity=severity,
            details=details or {},
        )

    def _unique_codes(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for kind, values in (
            ("EVENT", schedule.events),
            ("ANCHOR", schedule.anchors),
            ("EPOCH", schedule.epochs),
            ("ARM", schedule.arms),
            ("COHORT", schedule.cohorts),
            ("POPULATION", schedule.populations),
            ("DIMENSION", schedule.dimensions),
            ("CONDITION", schedule.conditional_definitions),
            ("REPEAT_BLOCK", schedule.repeat_blocks),
            ("CONFINEMENT_EPISODE", schedule.confinement_episodes),
        ):
            seen: dict[str, UUID] = {}
            for value in values:
                normalized = value.code.casefold()
                if normalized in seen:
                    issues.append(self._issue(
                        f"DUPLICATE_{kind}",
                        f"Duplicate {kind.lower()} code {value.code!r}",
                        entity_type=kind,
                        entity_id=value.id,
                        details={"first_id": str(seen[normalized]), "code": value.code},
                    ))
                else:
                    seen[normalized] = value.id
        return issues

    def _anchor_integrity(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc s4/s29: an anchor the import could not fully resolve stays a
        durable, re-derivable block - not a one-time note the adapter happened
        to attach at import.

        A ``ValidationIssue`` appended only once, at import time, is discarded
        the next time anyone calls validate/submit/approve, because those all
        replace ``validation_issues`` with a fresh run of this validator. If a
        reviewer patches around the SYMPTOM (say, gives one event's timing an
        explicit anchor) without ever resolving which anchor is actually the
        schedule's origin, a validator with no memory of the ambiguity would
        wave the schedule through. Checking ``Anchor.status`` here instead
        means the block is re-derived from the schedule's own persisted state
        every single time, so it cannot be bypassed by fixing something else.
        """
        issues: list[ValidationIssue] = []
        for anchor in schedule.anchors:
            if anchor.status == "AMBIGUOUS":
                issues.append(self._issue(
                    "AMBIGUOUS_BASELINE",
                    f"Anchor {anchor.display_name!r} is one of several equally "
                    "plausible schedule origins; the reviewer must confirm "
                    "which anchor Day 0 is measured from before this schedule "
                    "can be approved.",
                    entity_type="ANCHOR", entity_id=anchor.id,
                ))
            elif anchor.status == "UNRESOLVED":
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    f"Anchor {anchor.display_name!r} could not be resolved from "
                    "the protocol and needs a reviewed value.",
                    entity_type="ANCHOR", entity_id=anchor.id,
                ))
        return issues

    def _information_loss(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Doc s29: re-derive every import-time loss marker on every pass.

        The adapter cannot leave a trace of something that never entered the
        schedule (a missing activity template has no row to point at), so it
        records the fact durably instead: on the OWNING event's own
        ``metadata`` when there is one, or on ``schedule_metadata.extensions``
        when there is not (a recurrence naming an event that does not exist
        anywhere in the plan). This mirrors ``_anchor_integrity`` - the point
        of storing it durably rather than only returning it once from the
        adapter is that validate/submit/approve all replace
        ``validation_issues`` with a fresh run of this validator, which would
        otherwise silently discard a one-time note the moment anyone
        revalidates.
        """
        issues: list[ValidationIssue] = []
        for event in schedule.events:
            dangling_activities = list(event.metadata.get("unresolved_activity_refs") or [])
            if dangling_activities:
                issues.append(self._issue(
                    "INFORMATION_LOSS",
                    f"{event.display_name!r} references {len(dangling_activities)} "
                    "activity/activities this schedule never defined; those "
                    "assessments would be silently missing.",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.CRITICAL,
                    details={"unresolved_activity_refs": dangling_activities},
                ))
            dangling_dependencies = event.metadata.get("unresolved_dependency_refs") or []
            if dangling_dependencies:
                issues.append(self._issue(
                    "INFORMATION_LOSS",
                    f"{event.display_name!r} depends on a visit this schedule "
                    "never defined; that ordering rule would be silently "
                    "dropped.",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.CRITICAL,
                    details={"unresolved_dependency_refs": dangling_dependencies},
                ))
        for extension in schedule.schedule_metadata.extensions:
            if extension.get("type") != "INFORMATION_LOSS":
                continue
            issues.append(self._issue(
                "INFORMATION_LOSS",
                f"A {extension.get('kind', 'schedule')} rule names a visit "
                "this schedule never defined; it would be silently dropped.",
                severity=Severity.CRITICAL, details=extension,
            ))
        return issues

    def _references(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        events = {item.code for item in schedule.events}
        anchors = {item.code for item in schedule.anchors}
        epochs = {item.id for item in schedule.epochs}
        dimensions = {
            "ARM": {item.code for item in schedule.arms},
            "COHORT": {item.code for item in schedule.cohorts},
            "POPULATION": {item.code for item in schedule.populations},
            "EPOCH": {item.code for item in schedule.epochs},
        }
        for item in schedule.dimensions:
            dimensions.setdefault(item.dimension_type, set()).add(item.code)
        issues: list[ValidationIssue] = []

        def check_reference(reference: object, event_id: UUID) -> None:
            if isinstance(reference, EventReference) and reference.event_code not in events:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    f"Unknown event reference {reference.event_code!r}",
                    entity_type="EVENT", entity_id=event_id,
                ))
            code = getattr(reference, "code", None)
            if code is not None and code not in anchors:
                issues.append(self._issue(
                    "UNKNOWN_ANCHOR", f"Unknown anchor {code!r}",
                    entity_type="EVENT", entity_id=event_id,
                ))

        for event in schedule.events:
            if event.epoch_id is not None and event.epoch_id not in epochs:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE", "Event references an unknown epoch",
                    entity_type="EVENT", entity_id=event.id,
                ))
            timing = event.timing
            for reference in timing_references(timing):
                check_reference(reference, event.id)
            for dependency in event.dependencies:
                if dependency.source_event_code not in events:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Unknown dependency event {dependency.source_event_code!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
                if dependency.source_event_code == event.code:
                    issues.append(self._issue(
                        "CIRCULAR_DEPENDENCY", "An event cannot depend on itself",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            if event.dependencies and event.dependency_mode in {None, DependencyMode.UNCLEAR}:
                issues.append(self._issue(
                    "UNCLEAR_DEPENDENCY_MODE",
                    "Event dependencies require an explicit nominal, actual-previous-event, or manual mode",
                    entity_type="EVENT", entity_id=event.id,
                ))
            for rule in event.applicability:
                allowed = dimensions.get(rule.dimension)
                if allowed is None and rule.dimension not in {"PATIENT_ATTRIBUTE", "CUSTOM"}:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Unknown applicability dimension {rule.dimension!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
                if allowed is not None:
                    unknown = sorted(set(rule.values) - allowed)
                    if unknown:
                        issues.append(self._issue(
                            "UNRESOLVED_REFERENCE",
                            f"Unknown {rule.dimension.lower()} applicability values",
                            entity_type="EVENT", entity_id=event.id,
                            details={"values": unknown},
                        ))
                if rule.dimension in {"PATIENT_ATTRIBUTE", "CUSTOM"} and rule.condition is None and not rule.field:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION",
                        f"{rule.dimension} applicability requires a field or typed condition",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            activity_codes = {item.code for item in event.activities if item.code}
            seen_activity_codes: set[str] = set()
            for activity in event.activities:
                if activity.code:
                    normalized = activity.code.casefold()
                    if normalized in seen_activity_codes:
                        issues.append(self._issue(
                            "DUPLICATE_ACTIVITY",
                            f"Duplicate activity code {activity.code!r} inside one visit",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                    seen_activity_codes.add(normalized)
                for reference in timing_references(activity.timing):
                    if not isinstance(reference, ActivityReference):
                        check_reference(reference, event.id)
                        continue
                    if reference.activity_code not in activity_codes:
                        issues.append(self._issue(
                            "UNRESOLVED_REFERENCE",
                            f"Activity timing references unknown activity {reference.activity_code!r}"
                            " in the same visit",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                    elif reference.activity_code == activity.code:
                        issues.append(self._issue(
                            "CIRCULAR_DEPENDENCY",
                            "An activity cannot be timed relative to itself",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                for rule in activity.applicability:
                    allowed = dimensions.get(rule.dimension)
                    if allowed is None and rule.dimension not in {"PATIENT_ATTRIBUTE", "CUSTOM"}:
                        issues.append(self._issue(
                            "UNRESOLVED_REFERENCE",
                            f"Unknown activity applicability dimension {rule.dimension!r}",
                            entity_type="ACTIVITY", entity_id=activity.id,
                        ))
                    if allowed is not None:
                        unknown = sorted(set(rule.values) - allowed)
                        if unknown:
                            issues.append(self._issue(
                                "UNRESOLVED_REFERENCE",
                                f"Unknown {rule.dimension.lower()} activity applicability values",
                                entity_type="ACTIVITY", entity_id=activity.id,
                                details={"values": unknown},
                            ))
            for action in event.conditional_actions:
                if action.action_type in {"ADD_EVENT", "REPEAT_EVENT", "CANCEL_EVENT"} and action.target_code not in events:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Unknown conditional-action event {action.target_code!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            if event.confinement is not None:
                confinement_codes = [
                    event.confinement.admission_event_code,
                    *event.confinement.dose_event_codes,
                    event.confinement.discharge_event_code,
                ]
                missing = sorted(set(confinement_codes) - events)
                if missing:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        "Confinement definition references unknown events",
                        entity_type="EVENT", entity_id=event.id,
                        details={"event_codes": missing},
                    ))
                if len(set(confinement_codes)) != len(confinement_codes):
                    issues.append(self._issue(
                        "INVALID_CONFINEMENT",
                        "Admission, dose, and discharge must remain distinct events",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            if isinstance(timing, CycleDayTiming):
                if timing.cycle_reference not in anchors:
                    issues.append(self._issue(
                        "UNKNOWN_ANCHOR",
                        f"Unknown cycle reference {timing.cycle_reference!r}",
                        entity_type="EVENT", entity_id=event.id,
                    ))
        return issues

    def _dependency_graph(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        edges: dict[str, set[str]] = defaultdict(set)
        indegree = {event.code: 0 for event in schedule.events}
        event_by_code = {event.code: event for event in schedule.events}
        anchors = {anchor.code: anchor for anchor in schedule.anchors}
        for target in schedule.events:
            refs = {dep.source_event_code for dep in target.dependencies}
            for timing_ref in timing_references(target.timing):
                if isinstance(timing_ref, EventReference):
                    refs.add(timing_ref.event_code)
                else:
                    anchor = anchors.get(getattr(timing_ref, "code", ""))
                    if anchor and anchor.source_event_code:
                        refs.add(anchor.source_event_code)
            for source in refs:
                if source in indegree and target.code not in edges[source]:
                    edges[source].add(target.code)
                    indegree[target.code] += 1
        queue = deque(code for code, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            source = queue.popleft()
            visited += 1
            for target in edges[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited == len(indegree):
            return []
        cyclic = sorted(code for code, degree in indegree.items() if degree > 0)
        return [self._issue(
            "CIRCULAR_DEPENDENCY",
            "Event dependency graph contains a cycle",
            entity_type="SCHEDULE",
            entity_id=schedule.schedule_version_id,
            details={"event_codes": cyclic, "event_ids": [str(event_by_code[c].id) for c in cyclic]},
        )]

    def _clinical_completeness(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        evidence_ids = {evidence.id for evidence in schedule.evidence}
        claims = {(claim.claim_entity_id, claim.claim_type) for claim in schedule.claim_evidence}
        for claim in schedule.claim_evidence:
            if claim.evidence_id not in evidence_ids:
                issues.append(self._issue(
                    "MISSING_EVIDENCE",
                    "Claim-level evidence references a source that is not in the schedule",
                    entity_type=claim.claim_entity_type,
                    entity_id=claim.claim_entity_id,
                    details={
                        "claim_type": claim.claim_type,
                        "evidence_id": str(claim.evidence_id),
                    },
                ))
        qualifier_markers: dict[str, list[tuple[str, UUID, str]]] = defaultdict(list)
        for event in schedule.events:
            missing_refs = [str(ref) for ref in event.evidence_refs if ref not in evidence_ids]
            has_name_claim = (event.id, "EVENT_NAME") in claims
            has_timing_claim = (event.id, "TIMING") in claims
            if not event.evidence_refs or not has_name_claim or not has_timing_claim or missing_refs:
                issues.append(self._issue(
                    "MISSING_EVIDENCE",
                    "Event identity and timing require linked protocol evidence",
                    entity_type="EVENT", entity_id=event.id,
                    details={
                        "missing_reference_ids": missing_refs,
                        "event_name_claim": has_name_claim,
                        "timing_claim": has_timing_claim,
                    },
                ))
            for field, present, claim_type in (
                ("conditions", bool(event.conditions), "CONDITION"),
                ("applicability", bool(event.applicability), "APPLICABILITY"),
                ("recurrence", event.recurrence is not None, "RECURRENCE"),
                ("dependencies", bool(event.dependencies), "DEPENDENCY"),
                ("dependency_mode", event.dependency_mode is not None, "DEPENDENCY_MODE"),
                ("conditional_actions", bool(event.conditional_actions), "CONDITIONAL_ACTION"),
                ("confinement", event.confinement is not None, "CONFINEMENT"),
            ):
                if present and (event.id, claim_type) not in claims:
                    issues.append(self._issue(
                        "MISSING_EVIDENCE", f"Event {field} requires claim-level evidence",
                        entity_type="EVENT", entity_id=event.id,
                        details={"claim_type": claim_type, "field": field},
                    ))
            for activity in event.activities:
                missing_activity_refs = [str(ref) for ref in activity.evidence_refs if ref not in evidence_ids]
                if (
                    not activity.evidence_refs
                    or missing_activity_refs
                    or (activity.id, "ACTIVITY") not in claims
                ):
                    issues.append(self._issue(
                        "MISSING_EVIDENCE", "Activity requires claim-level protocol evidence",
                        entity_type="ACTIVITY", entity_id=activity.id,
                        details={"missing_reference_ids": missing_activity_refs},
                    ))
                if activity.timing is not None and (activity.id, "ACTIVITY_TIMING") not in claims:
                    issues.append(self._issue(
                        "MISSING_EVIDENCE",
                        "Intra-day activity timing requires its own evidence claim",
                        entity_type="ACTIVITY", entity_id=activity.id,
                        details={"claim_type": "ACTIVITY_TIMING", "field": "timing"},
                    ))
            for owner_type, owner_id, qualifiers in (
                ("EVENT", event.id, event.qualifiers),
                *(("ACTIVITY", activity.id, activity.qualifiers) for activity in event.activities),
            ):
                for qualifier in qualifiers:
                    if qualifier.marker:
                        qualifier_markers[qualifier.marker.casefold()].append((
                            qualifier.text.strip().casefold(), qualifier.id,
                            qualifier.category.value,
                        ))
                    missing_qualifier_refs = [
                        str(ref) for ref in qualifier.evidence_refs if ref not in evidence_ids]
                    if qualifier.scope.value != "GLOBAL" and not qualifier.target_codes:
                        issues.append(self._issue(
                            "ORPHAN_QUALIFIER",
                            "A non-global table qualifier must identify its target",
                            entity_type=owner_type, entity_id=owner_id,
                        ))
                    if not qualifier.resolved or qualifier.category.value == "UNRESOLVED":
                        issues.append(self._issue(
                            "UNRESOLVED_QUALIFIER",
                            "Table marker or footnote has not been converted into reviewed schedule meaning",
                            entity_type=owner_type, entity_id=owner_id,
                        ))
                    if (
                        not qualifier.evidence_refs
                        or missing_qualifier_refs
                        or (qualifier.id, "QUALIFIER") not in claims
                    ):
                        issues.append(self._issue(
                            "MISSING_EVIDENCE",
                            "Qualifier requires its own linked source-evidence claim",
                            entity_type="QUALIFIER", entity_id=qualifier.id,
                            details={
                                "owner_type": owner_type, "owner_id": str(owner_id),
                                "missing_reference_ids": missing_qualifier_refs,
                                "claim_type": "QUALIFIER",
                            },
                        ))
            if isinstance(event.timing, UnresolvedTiming):
                issues.append(self._issue(
                    "AMBIGUOUS_TIMING", event.timing.reason,
                    entity_type="EVENT", entity_id=event.id,
                ))
            elif isinstance(event.timing, (ProtocolDefinedTiming, ApproximateTiming)):
                # Doc 10 s13-s15: an on-demand visit has no computable date BY
                # DESIGN. That is the requirement, not an extraction failure, so
                # it must not block approval the way an undated scheduled visit
                # does. An ambiguous timing (UnresolvedTiming, above) still does.
                if event.activation != "ON_DEMAND":
                    issues.append(self._issue(
                        "UNSUPPORTED_TIMING",
                        "Timing requires a reviewed deterministic policy before execution",
                        entity_type="EVENT", entity_id=event.id,
                    ))
            elif event.activation == "ON_DEMAND":
                # The inverse mistake: a visit marked on-demand that also carries a
                # calculable protocol date is contradictory, and silently honouring
                # either reading would produce a visit that is due when it should
                # not be, or absent when it should be due.
                issues.append(self._issue(
                    "CONFLICTING_SOURCE",
                    "An unscheduled visit cannot also have a protocol-calculated date",
                    entity_type="EVENT", entity_id=event.id,
                    details={"event_code": event.code,
                             "timing_type": event.timing.type},
                ))
            if event.event_type.strip().upper() not in CORE_EVENT_TYPES:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    "Event type is outside the protocol taxonomy and needs a reviewer "
                    "to confirm how this visit should be presented",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.WARNING, blocking=False,
                    details={
                        "event_code": event.code, "event_type": event.event_type,
                        "known_types": sorted(CORE_EVENT_TYPES),
                    },
                ))
            # Doc 10 s32: a stated mode outside the permitted set means the two
            # readings disagree, and choosing either silently would tell a patient
            # the wrong thing about where to be.
            if (event.visit_mode and event.allowed_visit_modes
                    and event.visit_mode not in event.allowed_visit_modes):
                issues.append(self._issue(
                    "CONFLICTING_SOURCE",
                    "Visit mode is not among the modes the protocol permits",
                    entity_type="EVENT", entity_id=event.id,
                    details={
                        "event_code": event.code, "visit_mode": event.visit_mode,
                        "allowed_visit_modes": list(event.allowed_visit_modes),
                    },
                ))
            # Doc 10 s18 and s31: a hybrid visit needs someone to choose per
            # patient. Surfaced, not blocking - the protocol is legitimate.
            if len(event.allowed_visit_modes) > 1 and not event.visit_mode:
                issues.append(self._issue(
                    "UNRESOLVED_QUALIFIER",
                    "Visit permits more than one mode; the mode must be selected per patient",
                    entity_type="EVENT", entity_id=event.id,
                    severity=Severity.WARNING, blocking=False,
                    details={
                        "event_code": event.code,
                        "allowed_visit_modes": list(event.allowed_visit_modes),
                    },
                ))
            if event.interpretation_status in {
                InterpretationStatus.AMBIGUOUS,
                InterpretationStatus.CONFLICTING,
                InterpretationStatus.UNRESOLVED,
            }:
                issues.append(self._issue(
                    "CONFLICTING_EVIDENCE" if event.interpretation_status == InterpretationStatus.CONFLICTING else "UNRESOLVED_REFERENCE",
                    f"Event interpretation is {event.interpretation_status.value}",
                    entity_type="EVENT", entity_id=event.id,
                ))
        for marker, meanings in qualifier_markers.items():
            normalized_meanings = {(text, category) for text, _, category in meanings}
            if len(normalized_meanings) > 1:
                issues.append(self._issue(
                    "CONFLICTING_EVIDENCE",
                    f"Table marker {marker!r} has conflicting qualifier meanings",
                    entity_type="QUALIFIER", entity_id=meanings[0][1],
                    details={
                        "qualifier_ids": [str(item[1]) for item in meanings],
                        "meanings": sorted({
                            f"{category}: {text}" for text, _, category in meanings
                        }),
                    },
                ))
        return issues

    def _schedule_structures(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        """Validate conditional definitions, repeat blocks, and confinement episodes.

        These are the structures a reviewer cannot easily eyeball, so an impossible
        one is blocked at approval rather than discovered on a patient.
        """
        issues: list[ValidationIssue] = []
        events = {item.code for item in schedule.events}
        blocks = {item.code for item in schedule.repeat_blocks}
        episodes = {item.code for item in schedule.confinement_episodes}
        activity_codes = {
            activity.code
            for event in schedule.events for activity in event.activities
            if activity.code
        }
        evidence_ids = {item.id for item in schedule.evidence}
        claims = {(item.claim_entity_id, item.claim_type) for item in schedule.claim_evidence}

        def require_structure_evidence(
            *, entity_type: str, entity_id: UUID, refs: list[UUID],
            claims_required: list[str], message: str,
        ) -> None:
            missing_refs = [str(ref) for ref in refs if ref not in evidence_ids]
            missing_claims = [
                claim_type for claim_type in claims_required
                if (entity_id, claim_type) not in claims
            ]
            if not refs or missing_refs or missing_claims:
                issues.append(self._issue(
                    "MISSING_EVIDENCE", message,
                    entity_type=entity_type, entity_id=entity_id,
                    details={
                        "missing_reference_ids": missing_refs,
                        "missing_claim_types": missing_claims,
                    },
                ))

        for definition in schedule.conditional_definitions:
            for action in definition.actions:
                known = {
                    "ADD_EVENT": events, "REPEAT_EVENT": events, "CANCEL_EVENT": events,
                    "MANUAL_REVIEW": events, "STOP_BLOCK": blocks, "PAUSE_BLOCK": blocks,
                    "RESUME_BLOCK": blocks, "EXTEND_CONFINEMENT": episodes,
                }[action.action_type]
                if action.target_code not in known:
                    issues.append(self._issue(
                        "UNRESOLVED_REFERENCE",
                        f"Conditional action {action.action_type} targets unknown "
                        f"{action.target_code!r}",
                        entity_type="CONDITION", entity_id=definition.id,
                    ))
                if action.action_type == "REPEAT_EVENT" and "interval" not in action.parameters:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION",
                        "A repeat-until-resolution action requires its repeat interval",
                        entity_type="CONDITION", entity_id=definition.id,
                    ))
                if action.action_type == "EXTEND_CONFINEMENT" and "duration" not in action.parameters:
                    issues.append(self._issue(
                        "AMBIGUOUS_CONDITION",
                        "A confinement extension requires its duration",
                        entity_type="CONDITION", entity_id=definition.id,
                    ))
            repeats = [item for item in definition.actions if item.action_type == "REPEAT_EVENT"]
            if repeats and definition.resolution_condition is None:
                issues.append(self._issue(
                    "AMBIGUOUS_CONDITION",
                    "A repeating conditional action requires an explicit resolution rule",
                    entity_type="CONDITION", entity_id=definition.id,
                ))
            conditional_claims = ["CONDITION", "CONDITIONAL_ACTION"]
            if definition.resolution_condition is not None:
                conditional_claims.append("CONDITIONAL_RESOLUTION")
            require_structure_evidence(
                entity_type="CONDITION", entity_id=definition.id,
                refs=definition.evidence_refs, claims_required=conditional_claims,
                message=(
                    "Conditional trigger, actions, and resolution require claim-level "
                    "protocol evidence"
                ),
            )
            if definition.interpretation_status == InterpretationStatus.UNRESOLVED:
                issues.append(self._issue(
                    "UNRESOLVED_CONDITION",
                    "Conditional requirement needs reviewer confirmation before approval",
                    entity_type="CONDITION", entity_id=definition.id,
                ))

        for block in schedule.repeat_blocks:
            require_structure_evidence(
                entity_type="REPEAT_BLOCK", entity_id=block.id,
                refs=block.evidence_refs,
                claims_required=["REPEAT_BLOCK", "DEPENDENCY_MODE"],
                message=(
                    "Repeating-block membership and dependency mode require "
                    "claim-level protocol evidence"
                ),
            )
            unknown = sorted(set(block.event_codes) - events)
            if unknown:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE", "Repeating block references unknown events",
                    entity_type="REPEAT_BLOCK", entity_id=block.id,
                    details={"event_codes": unknown},
                ))
            if block.dependency_mode == DependencyMode.UNCLEAR:
                issues.append(self._issue(
                    "UNCLEAR_DEPENDENCY_MODE",
                    "A repeating block must state whether later cycles follow the nominal "
                    "schedule or the actual previous cycle",
                    entity_type="REPEAT_BLOCK", entity_id=block.id,
                ))

        for episode in schedule.confinement_episodes:
            require_structure_evidence(
                entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                refs=episode.evidence_refs, claims_required=["CONFINEMENT"],
                message=(
                    "Confinement admission, study-day, dosing, and discharge structure "
                    "requires claim-level protocol evidence"
                ),
            )
            references = [
                episode.admission_event_code, *episode.dose_event_codes,
                episode.discharge_event_code,
            ]
            unknown = sorted(set(references) - events)
            if unknown:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    "Confinement episode references unknown events",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                    details={"event_codes": unknown},
                ))
            # Doc 6 section 10: admission, dosing, and discharge carry different
            # clinical meaning and must not be flattened into one event.
            if len(set(references)) != len(references):
                issues.append(self._issue(
                    "INVALID_CONFINEMENT",
                    "Admission, dose, and discharge must remain distinct events",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                ))
            relative_days = [day.relative_day for day in episode.days]
            if len(set(relative_days)) != len(relative_days):
                issues.append(self._issue(
                    "INVALID_CONFINEMENT",
                    "A confinement episode cannot contain two study days with the same number",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                ))
            if 0 in relative_days:
                issues.append(self._issue(
                    "INVALID_CONFINEMENT",
                    "Clinical day numbering has no Day 0",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                ))
            unknown_activities = sorted({
                code for day in episode.days for code in day.activity_codes
            } - activity_codes)
            if unknown_activities:
                issues.append(self._issue(
                    "UNRESOLVED_REFERENCE",
                    "Confinement study day references unknown activities",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id,
                    details={"activity_codes": unknown_activities},
                ))
        return issues

    @staticmethod
    def blocking(issues: list[ValidationIssue]) -> list[ValidationIssue]:
        return [issue for issue in issues if issue.blocking and issue.status == IssueStatus.OPEN]


def topological_event_codes(schedule: UniversalSchedule) -> list[str]:
    """Stable topological ordering used by the evaluator after validation."""
    original = {event.code: index for index, event in enumerate(schedule.events)}
    indegree = {event.code: 0 for event in schedule.events}
    edges: dict[str, set[str]] = defaultdict(set)
    anchors = {anchor.code: anchor for anchor in schedule.anchors}
    for target in schedule.events:
        refs = {item.source_event_code for item in target.dependencies}
        for timing_ref in timing_references(target.timing):
            if isinstance(timing_ref, EventReference):
                refs.add(timing_ref.event_code)
            else:
                anchor = anchors.get(getattr(timing_ref, "code", ""))
                if anchor and anchor.source_event_code:
                    refs.add(anchor.source_event_code)
        for source in refs:
            if source in indegree and target.code not in edges[source]:
                edges[source].add(target.code)
                indegree[target.code] += 1
    ready = sorted((code for code, degree in indegree.items() if degree == 0), key=original.get)
    result: list[str] = []
    while ready:
        source = ready.pop(0)
        result.append(source)
        for target in sorted(edges[source], key=original.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=original.get)
    if len(result) != len(schedule.events):
        raise ValueError("event graph contains a cycle")
    return result
