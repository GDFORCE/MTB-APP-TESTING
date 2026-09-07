"""An ExtractionProvider backed by a language model.

The division of labour here is the important part, and it is deliberate:

    The MODEL reads the protocol and produces CLAIMS with citations.
    This module ASSEMBLES those claims into the canonical schedule.

Assembly is ordinary code, not another model call. That is not a performance
decision. Source traceability has to survive the pipeline, and a model asked to
emit its own ClaimEvidence entries will sometimes forget one - at which point a
schedule looks complete while a reviewer can no longer check where a rule came
from. Generating the traceability mechanically from each claim's citations makes
that failure impossible rather than unlikely.

Three consequences follow, and each is tested:

  * a claim citing evidence that does not exist is DROPPED with an issue, not
    kept with a dangling reference;
  * a claim that fails model validation becomes an issue naming the claim, not a
    crash and not a coerced approximation;
  * an event with no timing claim gets an UNRESOLVED timing that states why. The
    validator blocks approval on it, which is correct: a visit whose date nobody
    could determine must reach a human, not a patient.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.domain.schedule.condition import ConditionExpression
from app.domain.schedule.models import (
    Activity, Anchor, ClaimEvidence, ConditionalAction, ConditionalDefinition,
    ConfinementDayDefinition, ConfinementEpisodeDefinition, Dependency, Epoch,
    Event, Evidence, GenericDimension, Qualifier, RecurrenceRule, RepeatBlock,
    ScheduleMetadata, StudyDimension, UniversalSchedule,
)
from app.domain.schedule.timing import TimingExpression
from .llm_client import (
    ExtractionModelError, LLMClient, parse_json_list, parse_json_object,
)
from .prompts import CATEGORY_PROMPTS, DOCUMENT_STRUCTURE, EVIDENCE, SYSTEM

log = logging.getLogger(__name__)

#: Maps a pipeline category to the claim_type recorded against the entity it
#: produces. This is what makes traceability mechanical: the assembler never has
#: to be told which claim type to write, it follows from which stage produced it.
CLAIM_TYPES: dict[str, str] = {
    "protocol_metadata": "SCHEDULE_METADATA",
    "epochs": "EPOCH",
    "arms_cohorts_populations": "DIMENSION",
    "anchors": "ANCHOR",
    "events": "EVENT_NAME",
    "event_types": "EVENT_TYPE",
    "timing": "TIMING",
    "conditions": "CONDITION",
    "dependencies": "DEPENDENCY",
    "dependency_modes": "DEPENDENCY_MODE",
    "recurrence": "RECURRENCE",
    "repeat_blocks": "REPEAT_BLOCK",
    "activities": "ACTIVITY",
    "activity_timing": "ACTIVITY_TIMING",
    "qualifiers": "QUALIFIER",
    "conditional_actions": "CONDITIONAL_ACTION",
    "conditional_resolution": "CONDITIONAL_RESOLUTION",
    "confinement": "CONFINEMENT_EPISODE",
}

NAMED_DIMENSIONS = {"ARM": "arms", "COHORT": "cohorts", "POPULATION": "populations"}

#: Validate what the model produced as the discriminated union it claims to be.
#: Round-tripping a whole event instead would let an invalid expression sit in
#: the schedule until serialisation, where it degrades to a warning.
TIMING = TypeAdapter(TimingExpression)
CONDITION = TypeAdapter(ConditionExpression)


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "issue_code": code, "severity": "ERROR", "blocking": True,
        "message": message, "entity_type": "SCHEDULE", "details": details,
    }


class ClaudeExtractionProvider:
    """Implements the ExtractionProvider protocol against an LLM client.

    One model call per semantic category. A category that fails records an issue
    and contributes nothing, so a single bad answer degrades one dimension of
    meaning instead of losing the whole extraction.
    """

    def __init__(self, client: LLMClient, *, schedule_name: str = "Schedule of Assessments"):
        self.client = client
        self.schedule_name = schedule_name
        #: Model-chosen refs ("e1") mapped to the UUIDs the canonical model uses.
        #: The model never sees a UUID, so it cannot invent one that resolves.
        self._evidence_ids: dict[str, UUID] = {}
        self._evidence: list[Evidence] = []
        self._issues: list[dict[str, Any]] = []
        self._assembled: dict[str, Any] | None = None

    # --- pipeline nodes ---------------------------------------------------------

    def document_structure(self, state: dict[str, Any]) -> dict[str, Any]:
        try:
            return parse_json_object(self._ask(DOCUMENT_STRUCTURE, state))
        except ExtractionModelError as error:
            self._issues.append(_issue(
                "UNSUPPORTED_PROTOCOL_CONSTRUCT",
                f"could not map the protocol's structure: {error}",
            ))
            return {"sections": [], "tables": []}

    def discover_schedule_evidence(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            raw = parse_json_list(self._ask(EVIDENCE, state), "evidence")
        except ExtractionModelError as error:
            self._issues.append(_issue(
                "MISSING_EVIDENCE",
                f"could not collect source passages: {error}",
            ))
            return []

        output: list[dict[str, Any]] = []
        for item in raw:
            ref = str(item.get("ref") or "").strip()
            payload = {key: value for key, value in item.items() if key != "ref"}
            payload.setdefault("evidence_type", "SECTION")
            try:
                evidence = Evidence.model_validate(payload)
            except ValidationError as error:
                self._issues.append(_issue(
                    "MISSING_EVIDENCE",
                    f"a cited passage could not be recorded: {error.errors()[:2]}",
                    ref=ref,
                ))
                continue
            if ref:
                self._evidence_ids[ref] = evidence.id
            # The model's own ref is kept so a reviewer can trace a claim back to
            # the answer that produced it, not just to the passage.
            self._evidence_ids.setdefault(str(evidence.id), evidence.id)
            self._evidence.append(evidence)
            output.append(evidence.model_dump(mode="json"))
        return output

    def extract_claims(self, category: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = CATEGORY_PROMPTS.get(category)
        if prompt is None:
            return []
        try:
            raw = parse_json_list(self._ask(self._with_evidence(prompt), state), "claims")
        except ExtractionModelError as error:
            self._issues.append(_issue(
                "UNSUPPORTED_PROTOCOL_CONSTRUCT",
                f"could not extract {category.replace('_', ' ')}: {error}",
                category=category,
            ))
            return []

        claims: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            candidate = item.get("candidate")
            if not isinstance(candidate, dict):
                continue
            resolved = self._resolve_evidence(item.get("evidence_ids"), category)
            if not resolved:
                # Doc-wide rule: an uncited claim cannot be checked against the
                # protocol, so it is reported rather than absorbed.
                self._issues.append(_issue(
                    "MISSING_EVIDENCE",
                    f"a {category.replace('_', ' ')} claim cited no usable source passage",
                    category=category,
                    claim=str(item.get("claim_id") or f"{category}-{index}"),
                ))
                continue
            claims.append({
                "claim_id": str(item.get("claim_id") or f"{category}-{index}"),
                "claim_type": CLAIM_TYPES.get(category, category.upper()),
                "statement": str(item.get("statement") or "")[:2000],
                "evidence_ids": [str(value) for value in resolved],
                "candidate": {**candidate, "_category": category},
                "confidence": _confidence(item.get("confidence")),
            })
        return claims

    def build_relationships(self, state: dict[str, Any]) -> dict[str, Any]:
        """Relationships are derived from the claims themselves, not asked for."""
        return {}

    def completeness_check(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Problems found while READING the protocol - bad citations, bad JSON."""
        issues, self._issues = self._issues, []
        return issues

    def consistency_check(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Problems found while ASSEMBLING - claims that do not cohere.

        Assembly happens here rather than in the assembly node because the graph
        collects issues before it collects the schedule. A claim naming a visit
        that was never extracted, or a visit whose timing never arrived, is
        exactly what a reviewer needs told, and running assembly later would
        compute those findings and then discard them.
        """
        self._assembled = _assemble(
            claims=state.get("claims", []),
            evidence=self._evidence,
            default_name=self.schedule_name,
            issues=self._issues,
        )
        issues, self._issues = self._issues, []
        return issues

    def assemble_schedule(self, state: dict[str, Any]) -> dict[str, Any]:
        """The schedule built during the consistency check."""
        if self._assembled is None:
            self._assembled = _assemble(
                claims=state.get("claims", []), evidence=self._evidence,
                default_name=self.schedule_name, issues=self._issues,
            )
        return self._assembled

    # --- helpers ------------------------------------------------------------------

    def _ask(self, prompt: str, state: dict[str, Any]) -> str:
        return self.client.complete(system=SYSTEM, prompt=prompt)

    def _with_evidence(self, prompt: str) -> str:
        """Append the citable passages, so claims can only cite what exists."""
        if not self._evidence:
            return prompt
        lines = [
            f'- {ref}: p{item.page_number or "?"} "{(item.source_text or "")[:240]}"'
            for ref, evidence_id in self._evidence_ids.items()
            for item in self._evidence
            if item.id == evidence_id and not _is_uuid(ref)
        ]
        return (
            f"{prompt}\n\nCite only these passages, by their ref:\n"
            + "\n".join(lines)
        )

    def _resolve_evidence(self, values: Any, category: str) -> list[UUID]:
        if not isinstance(values, list):
            return []
        resolved: list[UUID] = []
        for value in values:
            found = self._evidence_ids.get(str(value).strip())
            if found is not None and found not in resolved:
                resolved.append(found)
        return resolved


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, number))


# --- deterministic assembly --------------------------------------------------------


class _Assembler:
    """Turns claims into a UniversalSchedule, recording evidence as it goes.

    Every ``_claim`` call both attaches the entity and records its traceability,
    so there is no path that produces an entity without its citation.
    """

    def __init__(self, default_name: str, issues: list[dict[str, Any]]):
        self.default_name = default_name
        self.issues = issues
        self.metadata = ScheduleMetadata(name=default_name)
        self.epochs: list[Epoch] = []
        self.dimensions: dict[str, list[StudyDimension]] = {
            "arms": [], "cohorts": [], "populations": [],
        }
        self.generic: list[GenericDimension] = []
        self.anchors: list[Anchor] = []
        self.events: dict[str, Event] = {}
        self.conditionals: dict[str, ConditionalDefinition] = {}
        self.repeat_blocks: list[RepeatBlock] = []
        self.confinements: list[ConfinementEpisodeDefinition] = []
        self.claim_evidence: list[ClaimEvidence] = []

    # -- traceability ---------------------------------------------------------------

    def cite(
        self, claim: dict[str, Any], entity_type: str, entity_id: UUID,
        *, claim_path: str | None = None, claim_type: str | None = None,
    ) -> None:
        for evidence_id in claim.get("evidence_ids", []):
            try:
                value = UUID(str(evidence_id))
            except ValueError:
                continue
            self.claim_evidence.append(ClaimEvidence(
                evidence_id=value,
                claim_type=claim_type or claim.get("claim_type", "OTHER"),
                claim_entity_type=entity_type, claim_entity_id=entity_id,
                claim_path=claim_path, confidence=claim.get("confidence"),
            ))

    def refs(self, claim: dict[str, Any]) -> list[UUID]:
        output: list[UUID] = []
        for evidence_id in claim.get("evidence_ids", []):
            try:
                output.append(UUID(str(evidence_id)))
            except ValueError:
                continue
        return output

    def reject(self, claim: dict[str, Any], category: str, error: Exception) -> None:
        detail = (
            error.errors()[:2] if isinstance(error, ValidationError) else str(error))
        self.issues.append(_issue(
            "UNSUPPORTED_PROTOCOL_CONSTRUCT",
            f"a {category.replace('_', ' ')} claim did not describe something the "
            f"schedule model can represent",
            category=category, claim=claim.get("claim_id"), detail=detail,
        ))


def _assemble(
    *,
    claims: list[dict[str, Any]],
    evidence: list[Evidence],
    default_name: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    builder = _Assembler(default_name, issues)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        category = str((claim.get("candidate") or {}).get("_category") or "")
        if category:
            grouped.setdefault(category, []).append(claim)

    # Order matters: events must exist before anything can attach to them.
    _apply_metadata(builder, grouped.get("protocol_metadata", []))
    _apply_epochs(builder, grouped.get("epochs", []))
    _apply_dimensions(builder, grouped.get("arms_cohorts_populations", []))
    _apply_anchors(builder, grouped.get("anchors", []))
    _apply_events(builder, grouped.get("events", []))
    _apply_event_types(builder, grouped.get("event_types", []))
    _apply_timing(builder, grouped.get("timing", []))
    _apply_conditions(builder, grouped.get("conditions", []))
    _apply_dependencies(builder, grouped.get("dependencies", []))
    _apply_dependency_modes(builder, grouped.get("dependency_modes", []))
    _apply_recurrence(builder, grouped.get("recurrence", []))
    _apply_repeat_blocks(builder, grouped.get("repeat_blocks", []))
    _apply_activities(builder, grouped.get("activities", []))
    _apply_activity_timing(builder, grouped.get("activity_timing", []))
    _apply_qualifiers(builder, grouped.get("qualifiers", []))
    _apply_conditionals(builder, grouped.get("conditional_actions", []))
    _apply_conditional_resolution(builder, grouped.get("conditional_resolution", []))
    _apply_confinement(builder, grouped.get("confinement", []))
    _fill_missing_timing(builder)

    schedule = UniversalSchedule(
        schedule_metadata=builder.metadata,
        epochs=builder.epochs,
        arms=builder.dimensions["arms"],
        cohorts=builder.dimensions["cohorts"],
        populations=builder.dimensions["populations"],
        dimensions=builder.generic,
        anchors=builder.anchors,
        events=list(builder.events.values()),
        conditional_definitions=list(builder.conditionals.values()),
        repeat_blocks=builder.repeat_blocks,
        confinement_episodes=builder.confinements,
        evidence=list(evidence),
        claim_evidence=builder.claim_evidence,
    )
    return schedule.model_dump(mode="json")


def _candidate(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in (claim.get("candidate") or {}).items()
        if key != "_category"
    }


def _apply_metadata(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims[:1]:
        values = _candidate(claim)
        builder.metadata = ScheduleMetadata(
            name=str(values.get("name") or builder.default_name),
            description=values.get("description"),
            schedule_type=str(values.get("schedule_type") or "PRIMARY"),
        )


def _apply_epochs(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        try:
            epoch = Epoch(**values, evidence_refs=builder.refs(claim))
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "epochs", error)
            continue
        builder.epochs.append(epoch)
        builder.cite(claim, "EPOCH", epoch.id)


def _apply_dimensions(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        dimension_type = str(values.pop("dimension_type", "") or "").upper()
        try:
            if dimension_type in NAMED_DIMENSIONS:
                dimension = StudyDimension(**values)
                builder.dimensions[NAMED_DIMENSIONS[dimension_type]].append(dimension)
            elif dimension_type:
                dimension = GenericDimension(dimension_type=dimension_type, **values)
                builder.generic.append(dimension)
            else:
                raise ValueError("a patient group needs its dimension type")
        except (ValidationError, TypeError, ValueError) as error:
            builder.reject(claim, "arms_cohorts_populations", error)
            continue
        builder.cite(claim, "DIMENSION", dimension.id)


def _apply_anchors(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        try:
            anchor = Anchor(**values, evidence_refs=builder.refs(claim))
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "anchors", error)
            continue
        builder.anchors.append(anchor)
        builder.cite(claim, "ANCHOR", anchor.id)


def _apply_events(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        code = str(values.get("code") or "").strip()
        if not code or code in builder.events:
            continue
        try:
            event = Event(
                code=code,
                protocol_label=str(values.get("protocol_label") or code),
                display_name=str(values.get("display_name") or code),
                event_type=str(values.get("event_type") or "SITE_VISIT"),
                sequence_number=values.get("sequence_number"),
                # Replaced by the timing stage. Left unresolved so an event whose
                # timing never arrives blocks approval rather than defaulting.
                timing=TIMING.validate_python({
                    "type": "UNRESOLVED",
                    "reason": "no timing rule was extracted for this visit",
                }),
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "events", error)
            continue
        builder.events[code] = event
        builder.cite(claim, "EVENT", event.id, claim_path="display_name")


def _event_for(builder: _Assembler, claim: dict[str, Any], category: str) -> Event | None:
    code = str(_candidate(claim).get("event_code") or "").strip()
    event = builder.events.get(code)
    if event is None:
        builder.issues.append(_issue(
            "UNRESOLVED_REFERENCE",
            f"a {category.replace('_', ' ')} claim referenced visit {code!r}, "
            "which was not extracted as a visit",
            category=category, event_code=code,
        ))
    return event


def _apply_event_types(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "event_types")
        if event is None:
            continue
        values = _candidate(claim)
        modes = values.get("allowed_visit_modes") or []
        try:
            event.visit_mode = values.get("visit_mode") or None
            event.allowed_visit_modes = [str(item) for item in modes if item]
            activation = str(values.get("activation") or "SCHEDULED").upper()
            event.activation = "ON_DEMAND" if activation == "ON_DEMAND" else "SCHEDULED"
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "event_types", error)
            continue
        builder.cite(claim, "EVENT", event.id, claim_path="visit_mode")


def _apply_timing(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "timing")
        if event is None:
            continue
        timing = _candidate(claim).get("timing")
        if not isinstance(timing, dict):
            continue
        try:
            event.timing = TIMING.validate_python(timing)
        except (ValidationError, TypeError) as error:
            # A timing we cannot represent stays UNRESOLVED rather than becoming
            # a near-miss the reviewer would have no reason to question.
            builder.reject(claim, "timing", error)
            continue
        builder.cite(claim, "EVENT", event.id, claim_path="timing")


def _apply_conditions(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "conditions")
        if event is None:
            continue
        condition = _candidate(claim).get("condition")
        if not isinstance(condition, dict):
            continue
        try:
            event.conditions = [CONDITION.validate_python(condition)]
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "conditions", error)
            continue
        builder.cite(claim, "EVENT", event.id, claim_path="conditions")


def _apply_dependencies(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "dependencies")
        if event is None:
            continue
        values = _candidate(claim)
        try:
            dependency = Dependency(
                source_event_code=str(values.get("source_event_code") or ""),
                dependency_type=str(values.get("dependency_type") or "TEMPORAL"),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "dependencies", error)
            continue
        event.dependencies.append(dependency)
        builder.cite(claim, "EVENT", event.id, claim_path="dependencies")


def _apply_dependency_modes(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "dependency_modes")
        if event is None:
            continue
        mode = str(_candidate(claim).get("dependency_mode") or "").upper()
        if mode not in {"NOMINAL", "ACTUAL_PREVIOUS_EVENT", "MANUAL", "UNCLEAR"}:
            continue
        event.dependency_mode = mode  # type: ignore[assignment]
        builder.cite(claim, "EVENT", event.id, claim_path="dependency_mode")


def _apply_recurrence(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "recurrence")
        if event is None:
            continue
        rule = _candidate(claim).get("recurrence")
        if not isinstance(rule, dict):
            continue
        try:
            event.recurrence = RecurrenceRule.model_validate(rule)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "recurrence", error)
            continue
        builder.cite(claim, "EVENT", event.id, claim_path="recurrence")


def _apply_repeat_blocks(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        try:
            block = RepeatBlock(**values, evidence_refs=builder.refs(claim))
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "repeat_blocks", error)
            continue
        builder.repeat_blocks.append(block)
        builder.cite(claim, "REPEAT_BLOCK", block.id, claim_type="REPEAT_BLOCK")
        builder.cite(claim, "REPEAT_BLOCK", block.id, claim_type="DEPENDENCY_MODE")


def _apply_activities(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "activities")
        if event is None:
            continue
        values = _candidate(claim)
        values.pop("event_code", None)
        label = str(values.get("protocol_label") or values.get("display_name") or "")
        try:
            activity = Activity(
                code=values.get("code"),
                protocol_label=label,
                display_name=str(values.get("display_name") or label),
                activity_type=str(values.get("activity_type") or "ASSESSMENT"),
                requiredness=str(values.get("requiredness") or "REQUIRED"),
                sequence_number=values.get("sequence_number"),
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "activities", error)
            continue
        event.activities.append(activity)
        builder.cite(claim, "ACTIVITY", activity.id, claim_type="ACTIVITY")


def _apply_activity_timing(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "activity_timing")
        if event is None:
            continue
        values = _candidate(claim)
        code = str(values.get("activity_code") or "")
        activity = next(
            (item for item in event.activities if (item.code or item.display_name) == code),
            None,
        )
        if activity is None:
            builder.issues.append(_issue(
                "UNRESOLVED_REFERENCE",
                f"an intra-day timing claim referenced assessment {code!r}, "
                f"which is not recorded at {event.code}",
                category="activity_timing",
            ))
            continue
        timing = values.get("timing")
        if not isinstance(timing, dict):
            continue
        try:
            activity.timing = TIMING.validate_python(timing)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "activity_timing", error)
            continue
        builder.cite(claim, "ACTIVITY", activity.id, claim_type="ACTIVITY_TIMING")


def _apply_qualifiers(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        targets = [str(item) for item in (values.get("target_codes") or []) if item]
        try:
            qualifier = Qualifier(
                marker=values.get("marker"),
                text=str(values.get("text") or ""),
                scope=str(values.get("scope") or "CELL"),
                category=str(values.get("category") or "UNRESOLVED"),
                target_codes=targets,
                resolved=bool(values.get("resolved")),
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "qualifiers", error)
            continue

        owner = next(
            (builder.events[code] for code in targets if code in builder.events), None)
        if owner is None:
            owner = next(iter(builder.events.values()), None)
        if owner is None:
            builder.issues.append(_issue(
                "ORPHAN_QUALIFIER",
                f"footnote {qualifier.marker or qualifier.text[:40]!r} has no visit "
                "to attach to",
            ))
            continue
        owner.qualifiers.append(qualifier)
        builder.cite(claim, "QUALIFIER", qualifier.id, claim_type="QUALIFIER")


def _apply_conditionals(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        condition = values.get("condition")
        raw_actions = values.get("actions") or []
        if not isinstance(condition, dict) or not raw_actions:
            continue
        code = str(values.get("code") or "")
        actions: list[dict[str, Any]] = []
        for action in raw_actions:
            if not isinstance(action, dict):
                continue
            actions.append({**action, "condition": action.get("condition") or condition})
        try:
            definition = ConditionalDefinition.model_validate({
                "code": code,
                "protocol_label": values.get("protocol_label") or code,
                "display_name": values.get("display_name") or code,
                "condition": condition,
                "actions": actions,
                "evidence_refs": [str(item) for item in builder.refs(claim)],
            })
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "conditional_actions", error)
            continue
        builder.conditionals[code] = definition
        builder.cite(claim, "CONDITION", definition.id, claim_type="CONDITION")
        builder.cite(claim, "CONDITION", definition.id, claim_type="CONDITIONAL_ACTION")


def _apply_conditional_resolution(
    builder: _Assembler, claims: list[dict[str, Any]],
) -> None:
    for claim in claims:
        values = _candidate(claim)
        definition = builder.conditionals.get(str(values.get("code") or ""))
        resolution = values.get("resolution_condition")
        if definition is None or not isinstance(resolution, dict):
            continue
        try:
            definition.resolution_condition = CONDITION.validate_python(resolution)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "conditional_resolution", error)
            continue
        builder.cite(
            claim, "CONDITION", definition.id, claim_type="CONDITIONAL_RESOLUTION")


def _apply_confinement(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        days = []
        for day in values.get("days") or []:
            if not isinstance(day, dict):
                continue
            try:
                days.append(ConfinementDayDefinition.model_validate(day))
            except ValidationError:
                continue
        if not days:
            builder.issues.append(_issue(
                "UNSUPPORTED_PROTOCOL_CONSTRUCT",
                "a confinement claim listed no days, so the stay has no duration",
                claim=claim.get("claim_id"),
            ))
            continue
        try:
            episode = ConfinementEpisodeDefinition(
                code=str(values.get("code") or ""),
                protocol_label=str(values.get("protocol_label") or values.get("code") or ""),
                display_name=str(values.get("display_name") or values.get("code") or ""),
                admission_event_code=str(values.get("admission_event_code") or ""),
                dose_event_codes=[str(item) for item in (values.get("dose_event_codes") or [])],
                discharge_event_code=str(values.get("discharge_event_code") or ""),
                days=days,
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "confinement", error)
            continue
        builder.confinements.append(episode)
        builder.cite(
            claim, "CONFINEMENT_EPISODE", episode.id, claim_type="CONFINEMENT_EPISODE")


def _fill_missing_timing(builder: _Assembler) -> None:
    """Report every visit whose timing never arrived.

    The event already carries an UNRESOLVED timing, which blocks approval on its
    own. This adds the reviewer-facing issue that names the visit, so the fix is
    obvious rather than buried in a validation list.
    """
    for event in builder.events.values():
        if getattr(event.timing, "type", "") != "UNRESOLVED":
            continue
        builder.issues.append(_issue(
            "AMBIGUOUS_TIMING",
            f"no timing rule was extracted for {event.display_name!r}; a reviewer "
            "must supply it before any patient can be scheduled",
            event_code=event.code,
        ))
