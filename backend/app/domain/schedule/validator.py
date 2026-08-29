from __future__ import annotations

from collections import defaultdict, deque
from uuid import UUID

from .models import (
    IssueStatus,
    InterpretationStatus,
    Severity,
    UniversalSchedule,
    ValidationIssue,
)
from .timing import (
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


class ScheduleValidator:
    version = "uctsm-validator.v1"

    def validate(self, schedule: UniversalSchedule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues.extend(self._unique_codes(schedule))
        issues.extend(self._references(schedule))
        issues.extend(self._dependency_graph(schedule))
        issues.extend(self._clinical_completeness(schedule))
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
            for rule in event.applicability:
                allowed = dimensions.get(rule.dimension)
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
            if isinstance(event.timing, UnresolvedTiming):
                issues.append(self._issue(
                    "AMBIGUOUS_TIMING", event.timing.reason,
                    entity_type="EVENT", entity_id=event.id,
                ))
            elif isinstance(event.timing, (ProtocolDefinedTiming, ApproximateTiming)):
                issues.append(self._issue(
                    "UNSUPPORTED_TIMING",
                    "Timing requires a reviewed deterministic policy before execution",
                    entity_type="EVENT", entity_id=event.id,
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
