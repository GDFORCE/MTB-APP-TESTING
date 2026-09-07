"""Adapter: the live Add Trial extraction -> ONE canonical UCTSM draft version.

The product deliberately has one extraction pipeline (``backend/protocol_extraction.py``,
which already emits an evidence-linked ``CanonicalSchedulePlan``) and one schedule
engine (``app/domain/schedule``). What was missing was the join between them: the
Add Trial flow persisted its plan into Mongo and never produced a canonical schedule
version, so the richer UCTSM review and patient screens had nothing to read.

This module does not extract, interpret or score anything. It TRANSLATES an already
extracted plan into the canonical model, then hands it to the existing
``ExtractionService.complete`` so the draft is persisted, versioned and audited by
the same code path a native UCTSM extraction uses. No second engine, no second
persistence path.

Two rules govern every mapping below:

1.  Nothing is invented. A timing the plan could not resolve becomes
    ``UnresolvedTiming``; a window the protocol did not state produces no window;
    a footnote becomes an UNRESOLVED qualifier that blocks approval until a
    reviewer converts it into meaning. The validator is meant to complain about
    these - that is the human-review gate working, not an import failure.
2.  Nothing is discarded. Anything with no canonical home (operational
    constraints, alternative source labels, extraction conflicts) is carried into
    metadata or validation issues so it stays visible to the reviewer.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Literal
from uuid import UUID, uuid4

from app.domain.schedule.condition import ExistsCondition, FieldOperand, MembershipCondition
from app.domain.schedule.models import (
    Activity,
    Anchor,
    ApplicabilityRule,
    ClaimEvidence,
    ConditionalAction,
    ConditionalDefinition,
    ConfinementDayDefinition,
    ConfinementEpisodeDefinition,
    Dependency,
    Epoch,
    Event,
    Evidence,
    GenericDimension,
    InterpretationStatus,
    Qualifier,
    QualifierCategory,
    QualifierScope,
    RecurrenceRule,
    RecurrenceTermination,
    Requiredness,
    SCHEDULE_ORIGIN_ROLE,
    ScheduleMetadata,
    Severity,
    StudyDimension,
    UniversalSchedule,
    ValidationIssue,
)
from app.domain.schedule.timing import (
    AnchorReference,
    ApproximateTiming,
    EventReference,
    NoLaterThanTiming,
    NominalWindowTiming,
    NonNegativeTemporalAmount,
    OffsetTiming,
    ProtocolDefinedTiming,
    RangeTiming,
    TemporalAmount,
    UnresolvedTiming,
    Window,
    WithinTiming,
)
from app.extraction.graph import ExtractionResult
from schedule_schema import select_baseline_anchor

# The live extractor writes lower-case singular units; the canonical model uses
# the enum spelling. Anything outside this set leaves the timing unresolved.
_UNITS = {
    "minute": "MINUTE", "hour": "HOUR", "day": "DAY",
    "week": "WEEK", "month": "MONTH", "year": "YEAR",
}

# Legacy free-text visit categories -> the canonical taxonomy. Only a category
# that actually states HOW the patient attends becomes a specific mode;
# everything else becomes the generic, presentation-safe "VISIT", because
# guessing "site visit" is what tells a patient to travel for a phone call.
_EVENT_TYPES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("telephon", "phone", "t/c", "call"), "TELEPHONE_CONTACT"),
    (("home",), "HOME_VISIT"),
    (("virtual", "remote", "televisit", "telemedicine"), "REMOTE"),
    (("imaging", "scan", "mri", "pet"), "IMAGING"),
    (("laborator", "lab only", "lab-only", "central lab"), "LABORATORY"),
    (("inpatient", "admission", "confinement", "domicil"), "INPATIENT_ADMISSION"),
    (("discharge",), "INPATIENT_DISCHARGE"),
    (("unscheduled", "as needed", "as-needed", "prn", "ad hoc"), "UNSCHEDULED"),
    (("procedure", "biopsy", "surgery"), "PROCEDURE"),
    (("site visit", "study site", "clinic", "on-site", "onsite"), "SITE_VISIT"),
)

_ANCHOR_TYPES = {
    "consent": "CONSENT", "screening": "SCREENING", "randomization": "RANDOMIZATION",
    "first_dose": "FIRST_DOSE", "dose": "DOSE", "cycle_start": "CYCLE_START",
    "period_start": "PERIOD_START", "last_dose": "LAST_DOSE",
    "end_of_treatment": "END_OF_TREATMENT", "discharge": "DISCHARGE",
    "progression": "DISEASE_PROGRESSION", "other": "PROTOCOL_EVENT",
}

# Branch groupings the canonical model has first-class tables for. Everything
# else - period, sequence, substudy, dose level, part - becomes a generic
# dimension, which is what lets an unusual protocol structure survive the import
# instead of being flattened into an arm it is not.
_ARM_TERMS = ("arm", "treatment_arm", "treatment arm", "group")
_COHORT_TERMS = ("cohort",)
_POPULATION_TERMS = ("population",)

BASELINE_ANCHOR_CODE = "BASELINE"

_SLUG = re.compile(r"[^A-Z0-9]+")


def _code(value: str, *, fallback: str) -> str:
    """A stable upper-case code. Codes are the canonical join key everywhere."""
    slug = _SLUG.sub("_", (value or "").strip().upper()).strip("_")
    return slug or fallback


def _unique(code: str, used: set[str]) -> str:
    """Codes must be unique inside a schedule version or the validator blocks it."""
    if code not in used:
        used.add(code)
        return code
    for suffix in range(2, 1000):
        candidate = f"{code}_{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    candidate = f"{code}_{uuid4().hex[:6].upper()}"
    used.add(candidate)
    return candidate


def _amount(raw: Any, *, allow_zero: bool = True) -> TemporalAmount | None:
    """Convert one legacy amount, refusing anything the canonical model cannot hold.

    The canonical model counts in whole units on purpose - half a day is not a
    schedulable quantity - so a fractional value is left for a reviewer rather
    than rounded into a date nobody wrote.
    """
    if not isinstance(raw, dict):
        return None
    unit = _UNITS.get(str(raw.get("unit") or "").strip().lower())
    value = raw.get("value")
    if unit is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if float(value) != int(value):
        return None
    if not allow_zero and int(value) == 0:
        return None
    return TemporalAmount(value=int(value), unit=unit)


def _signed(amount: TemporalAmount, relation: str | None) -> TemporalAmount:
    """"14 days BEFORE surgery" is a negative offset measured from surgery."""
    if relation == "before" and amount.value > 0:
        return TemporalAmount(value=-amount.value, unit=amount.unit)
    return amount


def _event_type(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return "VISIT"
    if text in {"ss", "v", "t/c", "tc"}:
        # Protocol visit-type CODES. Only the telephone code states a mode.
        return {"t/c": "TELEPHONE_CONTACT", "tc": "TELEPHONE_CONTACT",
                "ss": "SITE_VISIT", "v": "REMOTE"}[text]
    for needles, mapped in _EVENT_TYPES:
        if any(needle in text for needle in needles):
            return mapped
    return "VISIT"


_PAGE_EVIDENCE_ID_RE = re.compile(r"^page-(\d+)(?:-|$)")


def _page_number_from_evidence_id(page_evidence_id: str) -> int | None:
    """Recover the real PDF page number already encoded in a page evidence id.

    ``protocol_document_index.py`` builds every page evidence id as
    ``f"page-{position}-{digest}"`` before it ever reaches the model, and the
    extraction prompt tells the model to copy that id verbatim into
    ``page_evidence_id``. The page number is not a new fact - it is already
    sitting inside a string the adapter previously never parsed, which is why
    ``Evidence.page_number`` came out null even for a fact whose page was
    known. Anything that does not match this exact, self-imposed format
    (blank, hand-written, from a different source) yields None rather than a
    guess.
    """
    match = _PAGE_EVIDENCE_ID_RE.match(page_evidence_id.strip())
    return int(match.group(1)) if match else None


class _EvidenceIndex:
    """Legacy string evidence ids -> canonical Evidence rows and claim links.

    The canonical model requires claim-level evidence for an event's identity and
    timing, for every activity, and for every structural field that is populated.
    The live extractor already records which source facts produced each field, so
    the import carries those links across rather than asserting unsourced claims.
    An entity that arrives with no evidence keeps none: the validator then blocks
    approval, which is the correct outcome, not something to paper over here.
    """

    def __init__(self, facts: Iterable[dict[str, Any]]):
        self.evidence: list[Evidence] = []
        self.claims: list[ClaimEvidence] = []
        self._by_legacy_id: dict[str, Evidence] = {}
        self._confidence: dict[UUID, float | None] = {}
        for fact in facts or []:
            if not isinstance(fact, dict):
                continue
            legacy_id = str(fact.get("evidence_id") or "").strip()
            if not legacy_id or legacy_id in self._by_legacy_id:
                continue
            page_evidence_id = str(fact.get("page_evidence_id") or "")
            row = Evidence(
                evidence_type="PROTOCOL_TEXT",
                section_title=(str(fact.get("source_location") or "").strip() or None),
                source_text=(str(fact.get("source_quote") or "").strip() or None),
                page_number=_page_number_from_evidence_id(page_evidence_id),
                source_locator={
                    "legacy_evidence_id": legacy_id,
                    "page_evidence_id": page_evidence_id,
                    "source_location": str(fact.get("source_location") or ""),
                },
                extraction_context={"claim": str(fact.get("claim") or "")},
            )
            self._by_legacy_id[legacy_id] = row
            self.evidence.append(row)
            confidence = fact.get("confidence")
            self._confidence[row.id] = (
                float(confidence)
                if isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                else None
            )

    def refs(self, legacy_ids: Iterable[Any]) -> list[UUID]:
        out: list[UUID] = []
        for legacy_id in legacy_ids or []:
            row = self._by_legacy_id.get(str(legacy_id).strip())
            if row is not None and row.id not in out:
                out.append(row.id)
        return out

    def claim(
        self, refs: list[UUID], *, claim_type: str, entity_type: str,
        entity_id: UUID, path: str | None = None,
    ) -> None:
        for evidence_id in refs:
            self.claims.append(ClaimEvidence(
                evidence_id=evidence_id, claim_type=claim_type,
                claim_entity_type=entity_type, claim_entity_id=entity_id,
                claim_path=path, confidence=self._confidence.get(evidence_id),
            ))


def _timing_for(
    raw: dict[str, Any] | None,
    *,
    anchor_codes: dict[str, str],
    event_codes: dict[str, str],
    default_anchor: str | None,
) -> tuple[Any, bool]:
    """Map one legacy timing expression. Returns (timing, is_on_demand).

    ``is_on_demand`` is only ever True for a timing the protocol itself defines as
    happening when needed rather than on a date. The canonical model treats that
    as a legitimate schedule shape, not as a failed extraction, so it must not be
    confused with a timing that simply could not be read.
    """
    if not isinstance(raw, dict):
        return UnresolvedTiming(reason="No timing was extracted for this visit"), False

    kind = str(raw.get("kind") or "unresolved")
    label = str(raw.get("source_label") or "").strip()
    notes = str(raw.get("notes") or "").strip()
    relation = raw.get("relation")
    qualifier = raw.get("qualifier")
    reason = notes or (f"Protocol timing {label!r} was not resolved" if label
                       else "Protocol timing was not resolved during extraction")
    source_reference = {"source_label": label, "kind": kind, "notes": notes}

    anchor_id = str(raw.get("anchor_id") or "").strip()
    reference: Any = None
    if anchor_id:
        if anchor_id in anchor_codes:
            reference = AnchorReference(code=anchor_codes[anchor_id])
        elif anchor_id in event_codes:
            # A visit counted from another VISIT is a dependency, not an anchor.
            reference = EventReference(event_code=event_codes[anchor_id])
    elif kind in {"offset", "calendar_offset", "range"} and default_anchor:
        # A schedule-of-assessments "Day 8" with no named anchor is measured from
        # the study baseline. That is not an invention: it is the same origin the
        # live product already computes every template date from - and when that
        # origin is itself unresolved there is nothing legitimate to count from,
        # so the timing stays unresolved rather than picking an anchor.
        reference = AnchorReference(code=default_anchor)

    if qualifier == "as_needed":
        return ProtocolDefinedTiming(
            handler="AS_NEEDED",
            extensions=[{"type": "SOURCE_LABEL", "value": label}] if label else [],
        ), True

    if reference is None:
        return UnresolvedTiming(reason=reason, source_reference=source_reference), False

    if kind == "range":
        start = _amount(raw.get("range_start"))
        end = _amount(raw.get("range_end"))
        if start is None or end is None:
            return UnresolvedTiming(reason=reason, source_reference=source_reference), False
        return RangeTiming(
            reference=reference,
            start=_signed(start, relation), end=_signed(end, relation),
        ), False

    offset = _amount(raw.get("offset"))
    if offset is None:
        return UnresolvedTiming(reason=reason, source_reference=source_reference), False
    offset = _signed(offset, relation)

    if relation == "within" or qualifier == "up_to":
        duration = TemporalAmount(value=abs(offset.value), unit=offset.unit)
        if duration.value == 0:
            return UnresolvedTiming(reason=reason, source_reference=source_reference), False
        return WithinTiming(
            reference=reference,
            duration=duration.model_dump(),
            direction="BEFORE" if relation == "before" else "AFTER",
        ), False
    if qualifier == "maximum":
        return NoLaterThanTiming(reference=reference, duration=offset), False
    if qualifier == "approximate":
        return ApproximateTiming(reference=reference, offset=offset), False
    if qualifier == "minimum":
        # "at least N days after" has no canonical earliest-date type. Leaving it
        # unresolved sends it to the reviewer instead of silently scheduling the
        # earliest permitted date as if it were the planned one.
        return UnresolvedTiming(
            reason=f"Minimum-gap timing {label!r} needs a reviewed rule"
                   if label else "Minimum-gap timing needs a reviewed rule",
            source_reference=source_reference,
        ), False
    return OffsetTiming(reference=reference, offset=offset), False


def _recurrence_start(timing: Any) -> Any | None:
    """The reference a repeat's first occurrence falls on, or None if it does not.

    The engine expands a recurrence from the resolved start reference itself, so
    the reference is only usable when the event's own timing puts occurrence one
    exactly there. "Day 1 then every 21 days", where Day 1 IS the baseline, is
    the ordinary case and maps exactly.
    """
    nominal = timing.nominal if isinstance(timing, NominalWindowTiming) else timing
    if isinstance(nominal, OffsetTiming):
        return nominal.reference if nominal.offset.value == 0 else None
    return None


#: Window types the canonical schema can extract that are NOT a visit tolerance
#: (doc s7). A "lab must be within 28 days" (validity) and a "visit may occur
#: +/-3 days" (tolerance) are different clinical rules with different
#: consequences for a missed date, and collapsing one into the other is exactly
#: the silent reinterpretation the adapter must never do.
_NON_TOLERANCE_WINDOW_TYPES = {"validity", "lookback", "minimum_gap", "maximum_gap", "other"}

_WINDOW_TYPE_WORDING = {
    "validity": "is a validity window (a prior result/procedure remains usable "
                "for this long) - not a visit tolerance",
    "lookback": "is a lookback window (how far back a prior event may count) "
                "- not a visit tolerance",
    "minimum_gap": "is a minimum gap between events - not a visit tolerance",
    "maximum_gap": "is a maximum gap between events - not a visit tolerance",
    "other": "is a timing restriction the extractor could not classify as a "
             "visit tolerance",
}


def _window_details(raw: dict[str, Any], window_type: str) -> dict[str, object]:
    """The window's own numbers, kept as data - not only as a sentence."""
    early = raw.get("early") if isinstance(raw.get("early"), dict) else None
    late = raw.get("late") if isinstance(raw.get("late"), dict) else None
    return {
        "window_type": window_type,
        "lower_bound": early,
        "upper_bound": late,
        "source_label": str(raw.get("source_label") or "").strip(),
    }


def _window_for(
    raw: dict[str, Any] | None,
) -> tuple[Window | None, str | None, dict[str, object] | None]:
    """Map a legacy window. Returns (window, unresolved_note, qualifier_details).

    A tolerance ``Window`` is only produced for a window the protocol stated AS a
    tolerance. Every other window TYPE the schema can extract - validity,
    lookback, minimum/maximum gap - is preserved as data (kept in
    ``qualifier_details``) and surfaced as a reviewed qualifier instead of being
    forced into the tolerance shape, because a validity window and a visit
    tolerance permit different things and treating one as the other is a
    clinical misstatement, not a simplification.

    When the extractor reported a window it could not read ("unclear"/
    "conflicting") the note/details are returned the same way, so the caller can
    raise it as a qualifier a reviewer has to settle. Manufacturing a tolerance
    is the one thing this must never do.
    """
    if not isinstance(raw, dict):
        return None, None, None
    state = str(raw.get("state") or "not_stated")
    window_type = str(raw.get("window_type") or "tolerance").strip().lower()

    if state in {"unclear", "conflicting"}:
        label = str(raw.get("source_label") or "").strip()
        return None, (
            f"Visit window is {state}: {label}" if label
            else f"Visit window is {state} and needs a reviewed value"
        ), _window_details(raw, window_type)
    if state != "stated":
        return None, None, None

    if window_type in _NON_TOLERANCE_WINDOW_TYPES:
        label = str(raw.get("source_label") or "").strip()
        wording = _WINDOW_TYPE_WORDING.get(window_type, _WINDOW_TYPE_WORDING["other"])
        note = f"{f'{label!r} ' if label else ''}{wording}."
        return None, note, _window_details(raw, window_type)

    early = _amount(raw.get("early"))
    late = _amount(raw.get("late"))
    stated = early if early is not None else late
    if stated is None:
        return None, None, None
    if early is not None and late is not None and early.unit != late.unit:
        return None, "Visit window uses two different units and needs a reviewed value", (
            _window_details(raw, window_type))
    # A one-sided tolerance ("up to 5 days late") means exactly that: no tolerance
    # was granted on the other side. Zero is the stated reading, not a default.
    return Window(
        before=NonNegativeTemporalAmount(
            value=abs(early.value) if early is not None else 0, unit=stated.unit),
        after=NonNegativeTemporalAmount(
            value=abs(late.value) if late is not None else 0, unit=stated.unit),
    ), None, None


#: Doc s10. Only these action types get built into a real ConditionalDefinition.
#: STOP_BRANCH/PAUSE_TREATMENT/RESUME_TREATMENT target a RepeatBlock, and this
#: adapter does not build repeat blocks; REPEAT_VISIT/CHANGE_FREQUENCY need an
#: interval the extractor does not supply at the condition level. A condition
#: classified into one of those stays exactly what it already was - a
#: reviewed, evidenced Qualifier - rather than being forced into a structural
#: shape this adapter cannot correctly complete.
_ConditionalActionType = Literal["ADD_EVENT", "MANUAL_REVIEW"]
_CONDITIONAL_ACTION_TYPES: dict[str, _ConditionalActionType] = {
    "ADD_VISIT": "ADD_EVENT",
    "ACTIVATE_EOT": "ADD_EVENT",
    "ACTIVATE_SAFETY_FOLLOWUP": "ADD_EVENT",
    "ACTIVATE_SURVIVAL_FOLLOWUP": "ADD_EVENT",
    "MANUAL_REVIEW": "MANUAL_REVIEW",
}


def _confinement_episodes(
    plan: dict[str, Any], *, event_codes: dict[str, str],
    activities_by_id: dict[str, dict[str, Any]], index: "_EvidenceIndex",
    used_codes: set[str],
) -> list[ConfinementEpisodeDefinition]:
    """Doc s11: 'Day -1, Day 1, Day 2, Discharge' becomes ONE episode with study
    days, never four separate hospital visits - but ONLY when the protocol
    explicitly grouped them (event.confinement_episode_id). Consecutive days
    are never grouped by inference; that would invent a clinical fact
    (continuous admission) the document may not actually state.

    An episode missing a required piece (no single admission event, no dose
    day, no single discharge event, or any of those not resolving to a real
    event) is not built at all - its member events stay ordinary visits
    exactly as they already were, never forced into an incomplete or invalid
    structural shape.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for raw in plan.get("events") or []:
        if not isinstance(raw, dict):
            continue
        episode_id = raw.get("confinement_episode_id")
        if episode_id:
            groups.setdefault(str(episode_id), []).append(raw)

    episodes: list[ConfinementEpisodeDefinition] = []
    for episode_id, members in groups.items():
        admission = [m for m in members if m.get("confinement_role") == "admission"]
        dose = [m for m in members if m.get("confinement_role") == "dose"]
        discharge = [m for m in members if m.get("confinement_role") == "discharge"]
        if len(admission) != 1 or not dose or len(discharge) != 1:
            continue
        admission_id = str(admission[0].get("id") or "")
        discharge_id = str(discharge[0].get("id") or "")
        if admission_id not in event_codes or discharge_id not in event_codes:
            continue
        dose_codes = [
            event_codes[str(m.get("id"))] for m in dose
            if str(m.get("id")) in event_codes
        ]
        if not dose_codes:
            continue

        def _sort_key(member: dict[str, Any]) -> int:
            value = member.get("confinement_relative_day")
            return value if isinstance(value, int) else 0

        days: list[ConfinementDayDefinition] = []
        episode_refs: list[UUID] = []
        for member in sorted(members, key=_sort_key):
            member_refs = index.refs(member.get("evidence_ids"))
            for ref in member_refs:
                if ref not in episode_refs:
                    episode_refs.append(ref)
            activity_codes = [
                _code(activities_by_id[str(activity_id)].get("name") or "",
                      fallback=f"ACTIVITY_{order}")
                for order, activity_id in enumerate(member.get("activity_ids") or [], 1)
                if str(activity_id) in activities_by_id
            ]
            relative_day = member.get("confinement_relative_day")
            days.append(ConfinementDayDefinition(
                day_label=str(
                    member.get("source_day_label") or member.get("name") or "Day"),
                relative_day=relative_day if isinstance(relative_day, int) else -1,
                activity_codes=activity_codes,
                evidence_refs=member_refs,
            ))

        code = _unique(_code(episode_id, fallback=f"CONFINEMENT_{len(episodes) + 1}"), used_codes)
        label = f"Confinement: {members[0].get('name') or episode_id}"
        episode = ConfinementEpisodeDefinition(
            code=code, protocol_label=label, display_name=label,
            admission_event_code=event_codes[admission_id],
            dose_event_codes=dose_codes,
            discharge_event_code=event_codes[discharge_id],
            days=days, evidence_refs=episode_refs,
        )
        episodes.append(episode)
        index.claim(episode_refs, claim_type="CONFINEMENT",
                    entity_type="CONFINEMENT_EPISODE", entity_id=episode.id)

    return episodes


def _conditional_definitions(
    plan: dict[str, Any], *, event_codes: dict[str, str],
    branch_dimension: dict[str, tuple[str, str]], index: "_EvidenceIndex",
    used_codes: set[str],
) -> list[ConditionalDefinition]:
    """Doc s10: structured conditional definitions/actions reach UCTSM -
    without the adapter ever deciding WHETHER or WHEN a condition applies to
    any real patient. That determination stays entirely human:
    ``build_conditional_plan`` only activates a definition once a clinician
    records a matching ``PatientCondition`` (``POST /patients/{id}/conditions``)
    keyed by this definition's own code. Every ``condition``/``resolution_condition``
    built here is a deliberately inert placeholder for exactly that reason - it
    asserts nothing about any patient, ever. What this function builds is the
    STRUCTURE the protocol states: what kind of consequence, and which visit it
    targets. "If progression occurs, perform an assessment within 14 days"
    becomes a definition that can add that assessment once a clinician records
    progression - never a fixed Day 14 visit, and never an automatic guess at
    whether progression happened.
    """
    definitions: list[ConditionalDefinition] = []
    for raw in plan.get("conditions") or []:
        if not isinstance(raw, dict):
            continue
        mapped_action = _CONDITIONAL_ACTION_TYPES.get(str(raw.get("action_type") or ""))
        if mapped_action is None:
            continue
        targets = list(dict.fromkeys(
            event_codes[str(item)] for item in (raw.get("applies_to_ids") or [])
            if str(item) in event_codes
        ))
        if not targets:
            continue  # Nothing resolvable to act on; stays qualifier-only.

        expression = str(raw.get("expression") or "").strip() or "Unstated condition"
        resolution_text = str(raw.get("resolution_expression") or "").strip()
        label = (
            f"{expression} (resolves when: {resolution_text})"
            if resolution_text else expression
        )
        code = _unique(_code(expression, fallback=f"CONDITION_{len(definitions) + 1}"), used_codes)
        refs = index.refs(raw.get("evidence_ids"))
        placeholder = ExistsCondition(field=f"{code}_confirmed")

        definition = ConditionalDefinition(
            code=code, protocol_label=label, display_name=label,
            condition=placeholder,
            applicability=_branch_applicability(
                [str(x) for x in (raw.get("applies_to_branch_ids") or [])],
                branch_dimension),
            actions=[
                ConditionalAction(
                    action_type=mapped_action, target_code=target,
                    condition=placeholder, evidence_refs=refs,
                )
                for target in targets
            ],
            resolution_condition=placeholder if resolution_text else None,
            evidence_refs=refs,
            interpretation_status=InterpretationStatus.EXTRACTED,
            requires_review=True,
        )
        definitions.append(definition)

        index.claim(refs, claim_type="CONDITION",
                    entity_type="CONDITION", entity_id=definition.id)
        index.claim(refs, claim_type="CONDITIONAL_ACTION",
                    entity_type="CONDITION", entity_id=definition.id)
        if resolution_text:
            index.claim(refs, claim_type="CONDITIONAL_RESOLUTION",
                        entity_type="CONDITION", entity_id=definition.id)

    return definitions


def _occurrence_condition(occurrence_numbers: list[int]) -> MembershipCondition | None:
    """"MRI every second cycle" -> a real, engine-evaluated per-occurrence gate.

    Doc s8: the numbers must survive as an ENFORCED restriction, not only as a
    qualifier a reviewer reads and trusts by eye. ``occurrence_number`` is
    seeded into the evaluator's condition context per activity occurrence
    (evaluator.py:evaluate_activities); a plain MembershipCondition against it
    reuses that existing, generic mechanism - no new engine, no new condition
    type, and an activity outside the listed cycles resolves NOT_APPLICABLE
    exactly like any other condition-gated activity.
    """
    numbers = [item for item in occurrence_numbers if isinstance(item, int) and not isinstance(item, bool)]
    if not numbers:
        return None
    return MembershipCondition(
        operator="IN", value=FieldOperand(field="occurrence_number"), values=numbers,
    )


def _branch_applicability(
    branch_ids: list[str], branch_dimension: dict[str, tuple[str, str]],
) -> list[ApplicabilityRule]:
    """Doc s9: 'MRI only in Arm B' stays a structural restriction, not a note.

    Every id that resolves to a known arm/cohort/population/generic dimension
    becomes a real ``ApplicabilityRule`` the engine already evaluates
    three-valued (TRUE/FALSE/UNKNOWN - never a silent guess). An id this
    schedule does not recognise is simply skipped here; the condition's own
    qualifier (built alongside this) still carries the raw id so nothing is
    lost, only what could not be structurally resolved.
    """
    rules: list[ApplicabilityRule] = []
    for branch_id in branch_ids:
        mapped = branch_dimension.get(str(branch_id or ""))
        if mapped:
            rules.append(ApplicabilityRule(dimension=mapped[0], values=[mapped[1]]))
    return rules


def _qualifier(
    text: str, *, category: QualifierCategory, scope: QualifierScope,
    target_code: str, refs: list[UUID], marker: str | None = None,
    details: dict[str, object] | None = None,
) -> Qualifier:
    """Every footnote/condition becomes a qualifier that is explicitly UNRESOLVED.

    Requirement doc 8 (footnotes): a detected qualifier is never silently dropped
    and never silently applied. It is carried with its target and blocks approval
    until a reviewer states what it means.
    """
    return Qualifier(
        marker=marker, text=text.strip(), scope=scope, category=category,
        target_codes=[target_code], resolved=False, evidence_refs=refs,
        details=details or {},
    )


def universal_schedule_from_plan(
    plan: dict[str, Any],
    *,
    name: str,
    description: str | None = None,
    schedule_type: str = "PRIMARY",
    evidence_facts: Iterable[dict[str, Any]] = (),
    extra_issues: Iterable[ValidationIssue] = (),
) -> tuple[UniversalSchedule, list[ValidationIssue]]:
    """Translate one ``CanonicalSchedulePlan`` payload into a canonical schedule."""
    index = _EvidenceIndex(evidence_facts)
    issues: list[ValidationIssue] = list(extra_issues)

    used_codes: set[str] = set()
    anchor_codes: dict[str, str] = {}
    anchors: list[Anchor] = []
    for position, raw in enumerate(plan.get("anchors") or [], 1):
        refs = index.refs(raw.get("evidence_ids"))
        code = _unique(_code(raw.get("name") or raw.get("id") or "", fallback=f"ANCHOR_{position}"), used_codes)
        anchor_codes[str(raw.get("id") or "")] = code
        anchors.append(Anchor(
            code=code,
            protocol_label=(str(raw.get("source_label") or "").strip() or None),
            display_name=str(raw.get("name") or code),
            anchor_type=_ANCHOR_TYPES.get(str(raw.get("anchor_type") or "other"), "PROTOCOL_EVENT"),
            status="RESOLVED", evidence_refs=refs,
        ))

    # Doc s4. Day 0 comes from ONE shared resolver, the same call the flat
    # projection makes, so the two can never disagree about where a schedule
    # starts. The chosen anchor keeps its own semantic type - it is marked as
    # the origin, never rewritten into a synthetic "BASELINE" anchor, because
    # "randomisation" and "first dose" are not interchangeable facts.
    baseline = select_baseline_anchor(plan)
    anchor_by_legacy_id = {
        str(raw.get("id") or ""): raw for raw in (plan.get("anchors") or [])
        if isinstance(raw, dict)
    }
    default_anchor: str | None = None

    if baseline.status == "RESOLVED" and baseline.anchor_id in anchor_codes:
        default_anchor = anchor_codes[baseline.anchor_id]
        origin = next(item for item in anchors if item.code == default_anchor)
        origin.derivation_rule = {
            "role": SCHEDULE_ORIGIN_ROLE,
            "resolver": "schedule_schema.select_baseline_anchor",
            "reason": baseline.reason,
            "protocol_anchor_type": str(
                (anchor_by_legacy_id.get(baseline.anchor_id) or {}).get("anchor_type")
                or "other"),
            "source_label": baseline.source_label,
        }
    elif baseline.status == "ABSENT":
        # A plan with no anchors at all states no origin. The live product has
        # always counted from the patient's baseline date, so the import states
        # that origin explicitly instead of leaving offsets pointing at nothing.
        code = _unique(BASELINE_ANCHOR_CODE, used_codes)
        anchors.append(Anchor(
            code=code, display_name="Baseline", anchor_type="BASELINE",
            status="RESOLVED",
            derivation_rule={
                "role": SCHEDULE_ORIGIN_ROLE,
                "resolver": "schedule_schema.select_baseline_anchor",
                "reason": baseline.reason,
                "protocol_anchor_type": "none",
            },
        ))
        default_anchor = code
    else:
        # UNRESOLVED. Two or more anchors are equally plausible origins and the
        # protocol does not say which. Dating the schedule off either one would
        # move every visit for every patient, so nothing is dated and the choice
        # goes to a reviewer as a blocking issue with the real alternatives.
        for item in anchors:
            item.status = "AMBIGUOUS"
        issues.append(ValidationIssue(
            issue_code="AMBIGUOUS_BASELINE", severity=Severity.ERROR, blocking=True,
            entity_type="ANCHOR",
            message=(
                "The schedule origin (Day 0) is ambiguous and must be confirmed "
                f"before any visit can be dated. {baseline.reason}"
            ),
            details={
                "alternatives": baseline.alternatives,
                "resolver": "schedule_schema.select_baseline_anchor",
            },
        ))

    epoch_ids: dict[str, UUID] = {}
    epochs: list[Epoch] = []
    for position, raw in enumerate(plan.get("phases") or [], 1):
        code = _unique(_code(raw.get("name") or "", fallback=f"EPOCH_{position}"), used_codes)
        row = Epoch(
            code=code, protocol_label=str(raw.get("name") or code),
            display_name=str(raw.get("name") or code), sequence_number=position,
            evidence_refs=index.refs(raw.get("evidence_ids")),
        )
        epoch_ids[str(raw.get("id") or "")] = row.id
        epochs.append(row)

    arms: list[StudyDimension] = []
    cohorts: list[StudyDimension] = []
    populations: list[StudyDimension] = []
    dimensions: list[GenericDimension] = []
    branch_dimension: dict[str, tuple[str, str]] = {}
    for position, raw in enumerate(plan.get("branches") or [], 1):
        branch_type = str(raw.get("branch_type") or "arm").strip().lower()
        label = str(raw.get("name") or "").strip()
        code = _unique(_code(label, fallback=f"GROUP_{position}"), used_codes)
        common = {
            "code": code, "protocol_label": label or code, "display_name": label or code,
        }
        if branch_type in _ARM_TERMS:
            arms.append(StudyDimension(**common))
            branch_dimension[str(raw.get("id") or "")] = ("ARM", code)
        elif branch_type in _COHORT_TERMS:
            cohorts.append(StudyDimension(**common))
            branch_dimension[str(raw.get("id") or "")] = ("COHORT", code)
        elif branch_type in _POPULATION_TERMS:
            populations.append(StudyDimension(**common))
            branch_dimension[str(raw.get("id") or "")] = ("POPULATION", code)
        else:
            dimension_type = _code(branch_type, fallback=f"GROUP_{position}")
            dimensions.append(GenericDimension(dimension_type=dimension_type, **common))
            branch_dimension[str(raw.get("id") or "")] = (dimension_type, code)

    activities_by_id = {
        str(raw.get("id") or ""): raw
        for raw in (plan.get("activities") or []) if isinstance(raw, dict)
    }

    # Codes first: a timing may reference another event, and a dependency must be
    # able to name it, so every event code has to exist before any is mapped.
    raw_events = [raw for raw in (plan.get("events") or []) if isinstance(raw, dict)]
    event_codes: dict[str, str] = {}
    for position, raw in enumerate(raw_events, 1):
        event_codes[str(raw.get("id") or "")] = _unique(
            _code(raw.get("name") or "", fallback=f"VISIT_{position}"), used_codes)

    # Doc s29: the plan named something that the import silently could not find
    # a home for. Each is a distinct, mechanically-detected loss - not a
    # judgement about whether a mapping is CORRECT, only whether it is
    # traceable. Collected as the loop runs; turned into blocking issues once,
    # at the end, so a real defect fails the whole draft instead of producing
    # an incomplete one that looks complete.
    lost_activity_refs: list[dict[str, str]] = []
    lost_dependency_refs: list[dict[str, str]] = []
    lost_recurrence_event_ids: list[str] = []

    recurrence_by_event: dict[str, dict[str, Any]] = {}
    for raw in plan.get("recurrences") or []:
        if not isinstance(raw, dict):
            continue
        for legacy_event_id in raw.get("event_ids") or []:
            legacy_event_id = str(legacy_event_id)
            if legacy_event_id not in event_codes:
                lost_recurrence_event_ids.append(legacy_event_id)
                continue
            recurrence_by_event.setdefault(legacy_event_id, raw)

    dependencies_by_event: dict[str, list[dict[str, Any]]] = {}
    for raw in plan.get("transitions") or []:
        if not isinstance(raw, dict):
            continue
        dependencies_by_event.setdefault(str(raw.get("to_event_id") or ""), []).append(raw)

    # A protocol condition names the events/activities it restricts. Attaching it
    # to those targets is what makes "only for measurable disease" reviewable
    # against the row it governs instead of a free-floating note.
    conditions_by_target: dict[str, list[dict[str, Any]]] = {}
    for raw in plan.get("conditions") or []:
        if not isinstance(raw, dict):
            continue
        for target in raw.get("applies_to_ids") or []:
            conditions_by_target.setdefault(str(target), []).append(raw)

    events: list[Event] = []
    for position, raw in enumerate(raw_events, 1):
        legacy_id = str(raw.get("id") or "")
        code = event_codes[legacy_id]
        refs = index.refs(raw.get("evidence_ids"))
        timing, on_demand = _timing_for(
            raw.get("timing"), anchor_codes=anchor_codes, event_codes=event_codes,
            default_anchor=default_anchor,
        )
        window, window_note, window_details = _window_for(raw.get("window"))
        if window is not None and isinstance(timing, OffsetTiming):
            timing = NominalWindowTiming(nominal=timing, window=window)

        event_type = _event_type(raw.get("event_type"))
        if event_type == "UNSCHEDULED" and isinstance(timing, UnresolvedTiming):
            # Doc 10: a protocol-defined unscheduled visit has no date BY DESIGN.
            # Reporting that as ambiguous timing would block approval over a
            # schedule shape the protocol states clearly.
            timing = ProtocolDefinedTiming(handler="UNSCHEDULED")
            on_demand = True

        qualifiers: list[Qualifier] = []
        if window_note:
            window_raw_value = raw.get("window")
            window_marker = (
                window_raw_value.get("marker")
                if isinstance(window_raw_value, dict) else None
            )
            qualifiers.append(_qualifier(
                window_note, category=QualifierCategory.WINDOW,
                scope=QualifierScope.VISIT, target_code=code, refs=refs,
                details=window_details,
                marker=(str(window_marker).strip() or None) if window_marker else None,
            ))
        conditional_text = str(raw.get("conditional_text") or "").strip()
        if conditional_text:
            qualifiers.append(_qualifier(
                conditional_text, category=QualifierCategory.CONDITION,
                scope=QualifierScope.VISIT, target_code=code, refs=refs,
                marker=(str(raw.get("marker")).strip() or None) if raw.get("marker") else None,
            ))
        for condition in conditions_by_target.get(legacy_id, []):
            marker = condition.get("marker")
            event_occurrence_numbers = [
                item for item in (condition.get("occurrence_numbers") or [])
                if isinstance(item, int) and not isinstance(item, bool)]
            event_branch_ids = [str(x) for x in (condition.get("applies_to_branch_ids") or [])]
            # Doc s5/s8/s9: "only for cycles 2, 4, 6" or "only in Arm B" narrows
            # this footnote to a specific intersection of the visit row and a
            # cycle/arm - a CELL, not the whole row - the same distinction
            # already made for an activity-level condition below. Without this
            # the narrowing data still lived in the plan but the qualifier's
            # own scope field claimed it covered every occurrence and every arm.
            qualifiers.append(_qualifier(
                str(condition.get("expression") or "").strip() or "Unstated condition",
                category=QualifierCategory.CONDITION,
                scope=(QualifierScope.CELL if event_occurrence_numbers or event_branch_ids
                       else QualifierScope.VISIT),
                target_code=code, refs=index.refs(condition.get("evidence_ids")),
                marker=(str(marker).strip() or None) if marker else None,
                details={
                    "occurrence_numbers": event_occurrence_numbers,
                    "applies_to_branch_ids": event_branch_ids,
                } if event_occurrence_numbers or event_branch_ids else None,
            ))

        applicability: list[ApplicabilityRule] = []
        for legacy_branch in (raw.get("arm_id"), raw.get("period_id")):
            mapped = branch_dimension.get(str(legacy_branch or ""))
            if mapped:
                applicability.append(ApplicabilityRule(dimension=mapped[0], values=[mapped[1]]))
        # Doc s9: a condition can restrict a visit to specific arms/cohorts
        # without that visit's OWN arm_id/period_id saying so - a factorial
        # design's shared visit with an arm-specific requirement, for example.
        for condition in conditions_by_target.get(legacy_id, []):
            applicability.extend(_branch_applicability(
                [str(x) for x in (condition.get("applies_to_branch_ids") or [])],
                branch_dimension))

        activities: list[Activity] = []
        event_dangling_activity_ids: list[str] = []
        for order, activity_id in enumerate(raw.get("activity_ids") or [], 1):
            template = activities_by_id.get(str(activity_id))
            if not template:
                # The event names an activity the plan never defined a template
                # for. That activity - and everything it would have carried
                # (timing, window, qualifiers) - has nowhere to go; doc s29
                # requires this to fail the draft rather than approve a
                # schedule quietly missing an assessment.
                lost_activity_refs.append({
                    "event_id": legacy_id, "activity_id": str(activity_id)})
                event_dangling_activity_ids.append(str(activity_id))
                continue
            activity_refs = index.refs(template.get("evidence_ids")) or refs
            activity_timing, _ = _timing_for(
                template.get("timing"), anchor_codes=anchor_codes,
                event_codes=event_codes, default_anchor=default_anchor,
            ) if template.get("timing") else (None, False)
            activity_text = str(template.get("conditional_text") or "").strip()
            activity_code = _code(template.get("name") or "", fallback=f"ACTIVITY_{order}")

            activity_marker = template.get("marker")
            activity_qualifiers = [_qualifier(
                activity_text, category=QualifierCategory.CONDITION,
                scope=QualifierScope.ACTIVITY, target_code=activity_code,
                refs=activity_refs,
                marker=(str(activity_marker).strip() or None) if activity_marker else None,
            )] if activity_text else []
            activity_conditions: list[Any] = []
            activity_applicability: list[ApplicabilityRule] = []
            # Doc s8/s9: a condition targeting THIS activity template - "MRI
            # every second cycle", "PK sampling only in Arm B" - carries its
            # cycle list and its branch restriction structurally, not only as
            # prose a reviewer has to trust.
            for condition in conditions_by_target.get(str(activity_id), []):
                occurrence_numbers = [
                    int(item) for item in (condition.get("occurrence_numbers") or [])
                    if isinstance(item, int) and not isinstance(item, bool)]
                branch_ids = [str(x) for x in (condition.get("applies_to_branch_ids") or [])]
                gate = _occurrence_condition(occurrence_numbers)
                if gate is not None:
                    activity_conditions.append(gate)
                activity_applicability.extend(_branch_applicability(branch_ids, branch_dimension))
                condition_refs = index.refs(condition.get("evidence_ids")) or activity_refs
                condition_marker = condition.get("marker")
                # Doc s5: the same row/column distinction as the event-level
                # condition above - "PK sampling only in Arm B" or "only cycles
                # 2, 4, 6" narrows this footnote to one activity in specific
                # occurrences/arms, not the activity column everywhere it
                # appears, so it is a CELL once that narrowing is stated.
                activity_qualifiers.append(_qualifier(
                    str(condition.get("expression") or "").strip() or "Unstated condition",
                    category=QualifierCategory.CONDITION,
                    scope=(QualifierScope.CELL if occurrence_numbers or branch_ids
                           else QualifierScope.ACTIVITY),
                    target_code=activity_code, refs=condition_refs,
                    marker=(str(condition_marker).strip() or None) if condition_marker else None,
                    details={
                        "occurrence_numbers": occurrence_numbers,
                        "applies_to_branch_ids": branch_ids,
                    } if occurrence_numbers or branch_ids else None,
                ))

            activity = Activity(
                code=activity_code,
                protocol_label=str(template.get("name") or activity_code),
                display_name=str(template.get("name") or activity_code),
                activity_type="PROTOCOL_ACTIVITY",
                requiredness=(
                    Requiredness.CONDITIONAL
                    if activity_text or activity_conditions else Requiredness.REQUIRED),
                sequence_number=order,
                timing=activity_timing,
                conditions=activity_conditions,
                applicability=activity_applicability,
                qualifiers=activity_qualifiers,
                evidence_refs=activity_refs,
                metadata=(
                    {"operational_constraints": list(template.get("operational_constraints") or [])}
                    if template.get("operational_constraints") else {}
                ),
            )
            activities.append(activity)
            index.claim(activity_refs, claim_type="ACTIVITY",
                        entity_type="ACTIVITY", entity_id=activity.id, path="display_name")
            if activity_timing is not None:
                index.claim(activity_refs, claim_type="ACTIVITY_TIMING",
                            entity_type="ACTIVITY", entity_id=activity.id, path="timing")
            for item in activity.qualifiers:
                index.claim(activity_refs, claim_type="QUALIFIER",
                            entity_type="QUALIFIER", entity_id=item.id)

        recurrence = None
        raw_recurrence = recurrence_by_event.get(legacy_id)
        if raw_recurrence:
            interval = _amount(raw_recurrence.get("frequency"), allow_zero=False)
            if interval is None:
                issues.append(ValidationIssue(
                    issue_code="UNSUPPORTED_PROTOCOL_CONSTRUCT", severity=Severity.ERROR,
                    blocking=True, entity_type="EVENT",
                    message=f"Repeat interval for {code} could not be read as whole units",
                    details={"event_code": code, "frequency": raw_recurrence.get("frequency")},
                ))
            else:
                end_occurrence = raw_recurrence.get("end_occurrence")
                until_event = str(raw_recurrence.get("until_event_id") or "")
                if isinstance(end_occurrence, int) and end_occurrence > 0:
                    termination = RecurrenceTermination(type="COUNT", count=end_occurrence)
                elif until_event and until_event in event_codes:
                    termination = RecurrenceTermination(
                        type="EVENT", event_code=event_codes[until_event])
                else:
                    # Doc 3: an open-ended repeat is bounded by the rolling
                    # horizon, never by an invented maximum cycle count.
                    termination = RecurrenceTermination(type="HORIZON")
                # The engine places a repeat's FIRST occurrence at its start
                # reference, so the reference has to be the thing the first
                # occurrence actually falls on: the event's own anchor, and only
                # when the event sits exactly on it. A repeat that starts at an
                # offset from its anchor cannot be stated in this model, and
                # placing it on the anchor anyway would move every cycle of a
                # real patient's treatment, so it goes to a reviewer instead.
                start_reference = _recurrence_start(timing)
                if start_reference is None:
                    issues.append(ValidationIssue(
                        issue_code="UNSUPPORTED_PROTOCOL_CONSTRUCT",
                        severity=Severity.ERROR, blocking=True, entity_type="EVENT",
                        message=(
                            f"{code} repeats every {interval.value} "
                            f"{interval.unit.lower()}(s) but its first occurrence is "
                            "offset from its anchor; the repeat needs a reviewed "
                            "start rule before it can be scheduled"
                        ),
                        details={
                            "event_code": code,
                            "interval": {"value": interval.value, "unit": interval.unit},
                            "timing_type": timing.type,
                        },
                    ))
                else:
                    recurrence = RecurrenceRule(
                        interval={"value": interval.value, "unit": interval.unit},
                        start_reference=start_reference,
                        termination=termination,
                    )
                    # The engine's interval is a single whole-unit value - it has
                    # nowhere to hold a protocol-stated range ("every 3-4 months").
                    # When the extraction captured that range (frequency.value_max),
                    # silently using only the lower bound would look like a
                    # confirmed fixed cadence. A REPEAT qualifier is the same
                    # unresolved-until-reviewed mechanism every other footnote/
                    # condition uses here, so this blocks approval exactly like
                    # them instead of only being a cosmetic note.
                    frequency_raw = raw_recurrence.get("frequency")
                    value_max = frequency_raw.get("value_max") \
                        if isinstance(frequency_raw, dict) else None
                    if isinstance(value_max, (int, float)) and not isinstance(value_max, bool):
                        unit_word = interval.unit.lower()
                        unit_word = unit_word if unit_word.endswith("s") else f"{unit_word}s"
                        range_source_label = str(raw_recurrence.get("source_label") or "").strip()
                        qualifiers.append(_qualifier(
                            f"Protocol states this recurs every {interval.value:g}-"
                            f"{value_max:g} {unit_word}"
                            + (f" ('{range_source_label}')" if range_source_label else "")
                            + ", not a fixed interval - this schedule's interval "
                            f"({interval.value:g} {unit_word}) is only the lower bound; "
                            "confirm the actual cadence before approving.",
                            category=QualifierCategory.REPEAT,
                            scope=QualifierScope.VISIT, target_code=code,
                            refs=index.refs(raw_recurrence.get("evidence_ids")) or refs,
                            details={
                                "value": interval.value, "value_max": value_max,
                                "unit": interval.unit,
                            },
                        ))

        dependencies = []
        event_dangling_dependency_ids: list[str] = []
        for item in dependencies_by_event.get(legacy_id, []):
            source_id = str(item.get("from_event_id") or "")
            if source_id not in event_codes:
                # A stated ordering/gap between two visits where one side does
                # not exist in this plan - the rule cannot silently vanish.
                lost_dependency_refs.append({
                    "to_event_id": legacy_id, "from_event_id": source_id})
                event_dangling_dependency_ids.append(source_id)
                continue
            dependencies.append(Dependency(
                source_event_code=event_codes[source_id], dependency_type="TEMPORAL"))

        metadata: dict[str, object] = {"legacy_event_id": legacy_id}
        # Doc s29: recorded ON the event itself (not only in the adapter's
        # one-time return value) so the validator can re-derive this block on
        # every future validate/submit/approve pass - see
        # ScheduleValidator._information_loss. A one-time ValidationIssue would
        # be silently discarded the moment anyone revalidates.
        if event_dangling_activity_ids:
            metadata["unresolved_activity_refs"] = event_dangling_activity_ids
        if event_dangling_dependency_ids:
            metadata["unresolved_dependency_refs"] = event_dangling_dependency_ids
        if raw.get("operational_constraints"):
            metadata["operational_constraints"] = list(raw.get("operational_constraints") or [])
        source_label = str((raw.get("timing") or {}).get("source_label") or "").strip()
        if source_label:
            metadata["protocol_timing_label"] = source_label
        if raw.get("event_type"):
            metadata["protocol_event_type"] = str(raw.get("event_type"))

        event = Event(
            code=code,
            protocol_label=str(raw.get("name") or code),
            display_name=str(raw.get("name") or code),
            event_type=event_type,
            epoch_id=epoch_ids.get(str(raw.get("phase_id") or "")),
            sequence_number=position,
            timing=timing,
            applicability=applicability,
            dependencies=dependencies,
            activation="ON_DEMAND" if on_demand else "SCHEDULED",
            recurrence=recurrence,
            qualifiers=qualifiers,
            activities=activities,
            evidence_refs=refs,
            requires_review=bool(qualifiers) or isinstance(timing, UnresolvedTiming),
            interpretation_status=InterpretationStatus.EXTRACTED,
            metadata=metadata,
        )
        events.append(event)

        index.claim(refs, claim_type="EVENT_NAME", entity_type="EVENT",
                    entity_id=event.id, path="display_name")
        index.claim(refs, claim_type="TIMING", entity_type="EVENT",
                    entity_id=event.id, path="timing")
        if applicability:
            index.claim(refs, claim_type="APPLICABILITY", entity_type="EVENT",
                        entity_id=event.id, path="applicability")
        if dependencies:
            index.claim(refs, claim_type="DEPENDENCY", entity_type="EVENT",
                        entity_id=event.id, path="dependencies")
        if recurrence is not None:
            index.claim(
                index.refs((raw_recurrence or {}).get("evidence_ids")) or refs,
                claim_type="RECURRENCE", entity_type="EVENT",
                entity_id=event.id, path="recurrence")
        for item in qualifiers:
            index.claim(item.evidence_refs or refs, claim_type="QUALIFIER",
                        entity_type="QUALIFIER", entity_id=item.id)
            if not item.evidence_refs:
                item.evidence_refs = list(refs)

    # A condition that named no target, or named one that is not in the plan, is
    # still protocol meaning. It is reported rather than dropped.
    known_targets = set(event_codes) | set(activities_by_id)
    for raw in plan.get("conditions") or []:
        if not isinstance(raw, dict):
            continue
        targets = [str(item) for item in (raw.get("applies_to_ids") or [])]
        if targets and all(target in known_targets for target in targets):
            continue
        issues.append(ValidationIssue(
            issue_code="UNRESOLVED_REFERENCE", severity=Severity.WARNING, blocking=False,
            message=(
                "Protocol condition is not attached to a visit or activity in this "
                f"schedule: {str(raw.get('expression') or '').strip() or 'unstated condition'}"
            ),
            details={"applies_to_ids": targets},
        ))

    for raw in plan.get("conflicts") or []:
        if not isinstance(raw, dict):
            continue
        unresolved = str(raw.get("status") or "unresolved") != "resolved"
        issues.append(ValidationIssue(
            issue_code="CONFLICTING_EVIDENCE",
            severity=Severity.ERROR if unresolved else Severity.INFO,
            blocking=unresolved,
            message=str(raw.get("description") or "Extraction reported a conflict"),
            details={
                "field_path": str(raw.get("field_path") or ""),
                "resolution": str(raw.get("resolution") or ""),
            },
        ))

    # Doc s29: fail the draft rather than approve one silently missing
    # something the plan named. These are mechanical - a reference the plan
    # made that resolved to nothing - not a judgement about mapping quality.
    if lost_activity_refs:
        issues.append(ValidationIssue(
            issue_code="INFORMATION_LOSS", severity=Severity.CRITICAL, blocking=True,
            entity_type="ACTIVITY",
            message=(
                f"{len(lost_activity_refs)} activity reference(s) point to an "
                "activity template this plan never defined; those assessments "
                "would be silently missing from the schedule."
            ),
            details={"activity_references": lost_activity_refs},
        ))
    if lost_dependency_refs:
        issues.append(ValidationIssue(
            issue_code="INFORMATION_LOSS", severity=Severity.CRITICAL, blocking=True,
            entity_type="EVENT",
            message=(
                f"{len(lost_dependency_refs)} dependency/ordering rule(s) name a "
                "visit this plan never defined; that constraint would be "
                "silently dropped."
            ),
            details={"dependency_references": lost_dependency_refs},
        ))
    if lost_recurrence_event_ids:
        issues.append(ValidationIssue(
            issue_code="INFORMATION_LOSS", severity=Severity.CRITICAL, blocking=True,
            entity_type="EVENT",
            message=(
                f"{len(lost_recurrence_event_ids)} recurrence rule(s) name a "
                "visit this plan never defined; that repeat would be silently "
                "dropped."
            ),
            details={"recurrence_event_ids": lost_recurrence_event_ids},
        ))

    # Doc s29: durable, like the per-event refs above - a recurrence naming an
    # event that does not exist anywhere in this plan has no event to hang
    # metadata off, so it is recorded on the schedule itself instead.
    extensions: list[dict[str, object]] = []
    if lost_recurrence_event_ids:
        extensions.append({
            "type": "INFORMATION_LOSS", "kind": "recurrence_target",
            "event_ids": lost_recurrence_event_ids,
        })

    # Doc s10/s12/s13: structured conditional definitions, built AFTER every
    # event code is final so a condition can safely reference any target.
    conditional_definitions = _conditional_definitions(
        plan, event_codes=event_codes, branch_dimension=branch_dimension,
        index=index, used_codes=used_codes,
    )
    # Doc s11/s14/s15: a continuous inpatient stay the protocol explicitly
    # grouped becomes one episode with study days, never separate visits.
    confinement_episodes = _confinement_episodes(
        plan, event_codes=event_codes, activities_by_id=activities_by_id,
        index=index, used_codes=used_codes,
    )

    schedule = UniversalSchedule(
        schedule_metadata=ScheduleMetadata(
            name=name, description=description, schedule_type=schedule_type,
            extensions=extensions,
        ),
        epochs=epochs, arms=arms, cohorts=cohorts, populations=populations,
        dimensions=dimensions, anchors=anchors, events=events,
        conditional_definitions=conditional_definitions,
        confinement_episodes=confinement_episodes,
        evidence=index.evidence, claim_evidence=index.claims,
    )
    return schedule, issues


def extraction_result_from_plan(
    plan: dict[str, Any],
    *,
    name: str,
    description: str | None = None,
    schedule_type: str = "PRIMARY",
    evidence_facts: Iterable[dict[str, Any]] = (),
    source: dict[str, Any] | None = None,
) -> ExtractionResult:
    """Package the translated schedule for ``ExtractionService.complete``.

    Going through the existing service is the point: version numbering, schedule
    definition creation, audit events and the DRAFT/VALIDATION_REQUIRED lifecycle
    are already implemented there and must not be reimplemented per entry point.
    """
    schedule, issues = universal_schedule_from_plan(
        plan, name=name, description=description, schedule_type=schedule_type,
        evidence_facts=evidence_facts,
    )
    return ExtractionResult(
        schedule=schedule,
        issues=issues,
        extraction_trace=[{
            "node": "canonical_import",
            "source": "mtb.protocol_extraction",
            "detail": source or {},
            "counts": {
                "events": len(schedule.events),
                "activities": sum(len(event.activities) for event in schedule.events),
                "anchors": len(schedule.anchors),
                "evidence": len(schedule.evidence),
            },
        }],
    )
