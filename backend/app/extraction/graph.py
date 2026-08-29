from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domain.schedule.models import UniversalSchedule, ValidationIssue
from app.domain.schedule.validator import ScheduleValidator


class DocumentPage(BaseModel):
    page_number: int
    text: str
    layout: dict[str, Any] = Field(default_factory=dict)


class ExtractionClaim(BaseModel):
    claim_id: str
    claim_type: str
    statement: str
    evidence_ids: list[str]
    candidate: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ExtractionState(TypedDict, total=False):
    document_hash: str
    pages: list[dict[str, Any]]
    document_map: dict[str, Any]
    candidate_evidence: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    components: dict[str, Any]
    schedule: dict[str, Any]
    issues: list[dict[str, Any]]
    trace: list[dict[str, Any]]


class ExtractionProvider(Protocol):
    """LLM/provider adapter. Implementations return candidates, never patient dates."""

    def document_structure(self, state: ExtractionState) -> dict[str, Any]: ...
    def discover_schedule_evidence(self, state: ExtractionState) -> list[dict[str, Any]]: ...
    def extract_claims(self, category: str, state: ExtractionState) -> list[dict[str, Any]]: ...
    def build_relationships(self, state: ExtractionState) -> dict[str, Any]: ...
    def completeness_check(self, state: ExtractionState) -> list[dict[str, Any]]: ...
    def consistency_check(self, state: ExtractionState) -> list[dict[str, Any]]: ...
    def assemble_schedule(self, state: ExtractionState) -> dict[str, Any]: ...


def _append_trace(state: ExtractionState, node: str, summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [*state.get("trace", []), {"node": node, **summary}]


def build_extraction_graph(provider: ExtractionProvider):
    graph = StateGraph(ExtractionState)

    def document_structure(state: ExtractionState) -> dict[str, Any]:
        value = provider.document_structure(state)
        return {"document_map": value, "trace": _append_trace(state, "DOCUMENT_STRUCTURE", {"sections": len(value.get("sections", []))})}

    def discovery(state: ExtractionState) -> dict[str, Any]:
        value = provider.discover_schedule_evidence(state)
        return {"candidate_evidence": value, "trace": _append_trace(state, "FIND_SCHEDULE_SECTIONS", {"evidence": len(value)})}

    def claims_node(category: str) -> Callable[[ExtractionState], dict[str, Any]]:
        def run(state: ExtractionState) -> dict[str, Any]:
            values = provider.extract_claims(category, state)
            claims = [*state.get("claims", []), *values]
            return {"claims": claims, "trace": _append_trace(state, f"EXTRACT_{category.upper()}", {"claims": len(values)})}
        return run

    def relationships(state: ExtractionState) -> dict[str, Any]:
        value = provider.build_relationships(state)
        return {"components": {**state.get("components", {}), "relationships": value}, "trace": _append_trace(state, "BUILD_RELATIONSHIPS", {"relationships": len(value)})}

    def evidence_linking(state: ExtractionState) -> dict[str, Any]:
        known = {str(item.get("id")) for item in state.get("candidate_evidence", [])}
        issues = list(state.get("issues", []))
        for claim in state.get("claims", []):
            missing = [item for item in claim.get("evidence_ids", []) if str(item) not in known]
            if missing:
                issues.append({
                    "issue_code": "MISSING_EVIDENCE", "severity": "ERROR", "blocking": True,
                    "message": "Extraction claim references missing evidence", "details": {"claim_id": claim.get("claim_id"), "ids": missing},
                })
        return {"issues": issues, "trace": _append_trace(state, "EVIDENCE_LINKING", {"issues": len(issues)})}

    def reasoning_check(name: str, function: Callable[[ExtractionState], list[dict[str, Any]]]):
        def run(state: ExtractionState) -> dict[str, Any]:
            findings = function(state)
            return {"issues": [*state.get("issues", []), *findings], "trace": _append_trace(state, name, {"findings": len(findings)})}
        return run

    def assembly(state: ExtractionState) -> dict[str, Any]:
        return {"schedule": provider.assemble_schedule(state), "trace": _append_trace(state, "SCHEDULE_ASSEMBLY", {})}

    def final_validation(state: ExtractionState) -> dict[str, Any]:
        try:
            schedule = UniversalSchedule.model_validate(state["schedule"])
        except Exception as error:
            issue = {
                "issue_code": "UNSUPPORTED_PROTOCOL_CONSTRUCT", "severity": "CRITICAL",
                "blocking": True, "message": "Canonical schedule failed schema validation",
                "details": {"error": str(error)},
            }
            return {"issues": [*state.get("issues", []), issue], "trace": _append_trace(state, "FINAL_VALIDATION", {"valid": False})}
        deterministic = [item.model_dump(mode="json") for item in ScheduleValidator().validate(schedule)]
        return {
            "schedule": schedule.model_dump(mode="json"),
            "issues": [*state.get("issues", []), *deterministic],
            "trace": _append_trace(state, "FINAL_VALIDATION", {"valid": True, "issues": len(deterministic)}),
        }

    nodes = [
        ("document_structure", document_structure),
        ("schedule_discovery", discovery),
        ("metadata", claims_node("protocol_metadata")),
        ("epochs", claims_node("epochs")),
        ("dimensions", claims_node("arms_cohorts_populations")),
        ("anchors", claims_node("anchors")),
        ("events", claims_node("events")),
        ("timing", claims_node("timing")),
        ("conditions", claims_node("conditions")),
        ("dependencies", claims_node("dependencies")),
        ("recurrence", claims_node("recurrence")),
        ("activities", claims_node("activities")),
        ("relationships", relationships),
        ("evidence_linking", evidence_linking),
        ("completeness", reasoning_check("COMPLETENESS_CHECK", provider.completeness_check)),
        ("consistency", reasoning_check("CONSISTENCY_CHECK", provider.consistency_check)),
        ("assembly", assembly),
        ("final_validation", final_validation),
    ]
    for name, function in nodes:
        graph.add_node(name, function)
    graph.add_edge(START, nodes[0][0])
    for current, following in zip(nodes, nodes[1:]):
        graph.add_edge(current[0], following[0])
    graph.add_edge(nodes[-1][0], END)
    return graph.compile()


class ExtractionResult(BaseModel):
    schedule: UniversalSchedule | None
    issues: list[ValidationIssue]
    extraction_trace: list[dict[str, Any]]


def run_extraction(
    provider: ExtractionProvider,
    *,
    document_hash: str,
    pages: list[DocumentPage],
) -> ExtractionResult:
    state = build_extraction_graph(provider).invoke({
        "document_hash": document_hash,
        "pages": [page.model_dump() for page in pages],
        "claims": [], "components": {}, "issues": [], "trace": [],
    })
    schedule = None
    try:
        schedule = UniversalSchedule.model_validate(state.get("schedule"))
    except Exception:
        pass
    issues = []
    for value in state.get("issues", []):
        try:
            issues.append(ValidationIssue.model_validate(value))
        except Exception:
            issues.append(ValidationIssue(
                issue_code="UNSUPPORTED_PROTOCOL_CONSTRUCT", severity="CRITICAL",
                blocking=True, message="Extraction emitted an invalid issue",
                details={"raw": value},
            ))
    return ExtractionResult(schedule=schedule, issues=issues, extraction_trace=state.get("trace", []))

