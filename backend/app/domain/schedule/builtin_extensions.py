"""Built-in PROTOCOL_DEFINED timing handlers.

Each handler here is the concrete proof that a brand-new clinical pattern can
be accommodated without touching `evaluator.evaluate_timing`'s core dispatch:
one typed extension payload, one pure resolution function, one registration
call. See `extension_handlers.py` for the registry this module populates.

To make a new pattern deterministically executable:

    1. Define what its ProtocolExtension payload looks like (a dict the AI
       extraction step fills in, validated here with ordinary Pydantic
       models -- never trusted as-is).
    2. Write a handler function with the signature
       `(ProtocolDefinedTiming, PatientContext) -> ResolvedTiming | None`,
       returning `None` for a genuine waiting state (an unresolved reference),
       never a fabricated date -- the same contract every other timing kind
       in `evaluator.py` already follows.
    3. Call `register_timing_extension("YOUR_HANDLER_NAME", your_function)`
       at the bottom of this module (or import time of a new module imported
       from `__init__.py`).

This module is imported once, for its side effect, from the package
`__init__.py` -- importing `app.domain.schedule` anywhere is enough to make
every handler defined here immediately available.
"""
from __future__ import annotations

from pydantic import TypeAdapter

from .evaluator import _resolve_reference, add_amount
from .exceptions import UnsupportedTimingError
from .extension_handlers import register_timing_extension
from .models import PatientContext, ResolvedTiming
from .timing import NonNegativeTemporalAmount, ProtocolDefinedTiming, TemporalAmount, TemporalReference

MINIMUM_GAP_AFTER_EVENT = "MINIMUM_GAP_AFTER_EVENT"

_REFERENCE_ADAPTER: TypeAdapter[object] = TypeAdapter(TemporalReference)


def evaluate_minimum_gap_after_event(
    expression: ProtocolDefinedTiming, context: PatientContext,
) -> ResolvedTiming | None:
    """Resolve a washout / mandatory-gap pattern: "no earlier than <reference>
    plus <minimum_gap>" -- a lower bound only, with no implied nominal date
    and no upper bound unless the protocol separately states one.

    Highest-frequency confirmed schema gap (washout/non-visit interval failed
    in 8-9 of 11 real protocols analysed; see
    docs/protocol-schedule-schema-analysis.md). The built-in timing kinds
    cover "no later than" (`NoLaterThanTiming`) but had no lower-bound-only
    counterpart, so protocols stating a mandatory minimum gap after a prior
    dose/visit had nowhere safe to go except UNRESOLVED.

    Expected `ProtocolExtension` payload (`type="MINIMUM_GAP_AFTER_EVENT"`):
        {"reference": <AnchorReference or EventReference, as a plain dict>,
         "minimum_gap": {"value": <int>=0>, "unit": "DAY"|"WEEK"|...}}
    """
    extension = next(
        (item for item in expression.extensions if item.type == MINIMUM_GAP_AFTER_EVENT), None,
    )
    if extension is None:
        raise UnsupportedTimingError(
            f"PROTOCOL_DEFINED timing names handler {MINIMUM_GAP_AFTER_EVENT!r} but carries "
            "no matching extension payload"
        )
    payload = extension.value
    if not isinstance(payload, dict):
        raise UnsupportedTimingError(f"{MINIMUM_GAP_AFTER_EVENT} extension payload must be an object")
    try:
        reference = _REFERENCE_ADAPTER.validate_python(payload.get("reference"))
        minimum_gap = NonNegativeTemporalAmount.model_validate(payload.get("minimum_gap"))
    except Exception as error:  # pydantic ValidationError, TypeError, ...
        raise UnsupportedTimingError(
            f"{MINIMUM_GAP_AFTER_EVENT} extension payload is malformed: {error}"
        ) from error
    resolved_reference = _resolve_reference(reference, context)
    if resolved_reference is None:
        return None  # the reference event/anchor has not occurred yet -- wait, never guess
    earliest = add_amount(resolved_reference, minimum_gap)
    return ResolvedTiming(
        earliest=earliest, precision="CONSTRAINT",
        constraints=[{
            "type": MINIMUM_GAP_AFTER_EVENT,
            "reference": payload.get("reference"),
            "minimum_gap": payload.get("minimum_gap"),
        }],
    )


register_timing_extension(MINIMUM_GAP_AFTER_EVENT, evaluate_minimum_gap_after_event)


SAME_DAY_AS_EVENT = "SAME_DAY_AS_EVENT"


def evaluate_same_day_as_event(
    expression: ProtocolDefinedTiming, context: PatientContext,
) -> ResolvedTiming | None:
    """Resolve a same-day-merge pattern: this event occurs on exactly the
    same calendar day as a named prior/later event or anchor -- never its own
    independently-computed date, even if its own timing rule would otherwise
    land on a different day.

    Highest-frequency confirmed schema gap (10 of 11 real protocols analysed
    needed this; see docs/protocol-schedule-schema-analysis.md). Reuses
    `_resolve_reference`, the same anchor/event lookup every built-in timing
    kind already uses -- an unresolved reference is a genuine waiting state
    (None), never a fabricated date.

    Expected `ProtocolExtension` payload (`type="SAME_DAY_AS_EVENT"`):
        {"reference": <AnchorReference or EventReference, as a plain dict>}
    """
    extension = next(
        (item for item in expression.extensions if item.type == SAME_DAY_AS_EVENT), None,
    )
    if extension is None:
        raise UnsupportedTimingError(
            f"PROTOCOL_DEFINED timing names handler {SAME_DAY_AS_EVENT!r} but carries "
            "no matching extension payload"
        )
    payload = extension.value
    if not isinstance(payload, dict):
        raise UnsupportedTimingError(f"{SAME_DAY_AS_EVENT} extension payload must be an object")
    try:
        reference = _REFERENCE_ADAPTER.validate_python(payload.get("reference"))
    except Exception as error:
        raise UnsupportedTimingError(
            f"{SAME_DAY_AS_EVENT} extension payload is malformed: {error}"
        ) from error
    resolved_reference = _resolve_reference(reference, context)
    if resolved_reference is None:
        return None  # the merged-with event/anchor has no date yet -- wait, never guess
    return ResolvedTiming(
        nominal_start=resolved_reference, nominal_end=resolved_reference, precision="EXACT",
        constraints=[{"type": SAME_DAY_AS_EVENT, "reference": payload.get("reference")}],
    )


register_timing_extension(SAME_DAY_AS_EVENT, evaluate_same_day_as_event)


TREATMENT_DELAY_AFTER_HOLD = "TREATMENT_DELAY_AFTER_HOLD"


def evaluate_treatment_delay_after_hold(
    expression: ProtocolDefinedTiming, context: PatientContext,
) -> ResolvedTiming | None:
    """Resolve a treatment-delay/hold pattern: this event's otherwise-normal
    offset from a reference is additionally shifted by a recorded delay (in
    days), e.g. "subsequent visits resume this many days later after a
    toxicity hold." The delay is never computed or guessed here -- it must
    already be a recorded clinical fact in `PatientContext.state`, set by the
    same application workflow that records any other patient fact (compare
    `regimen_code` already proven in test_uctsm_domain.py). No delay recorded
    yet means zero delay (an explicit, legitimate default), not an invented
    one.

    Confirmed Tier-2 schema gap (treatment delay/hold rules failed in 8 of 11
    real protocols analysed; see docs/protocol-schedule-schema-analysis.md).
    Unlike MINIMUM_GAP_AFTER_EVENT (a lower bound only) or SAME_DAY_AS_EVENT
    (pinned to another date), this produces its own EXACT nominal date --
    the reference offset plus whatever delay has actually accrued.

    Expected `ProtocolExtension` payload (`type="TREATMENT_DELAY_AFTER_HOLD"`):
        {"reference": <AnchorReference or EventReference, as a plain dict>,
         "base_offset": {"value": <int>, "unit": "DAY"|"WEEK"|...},
         "delay_state_field": <str, the PatientContext.state key holding the
             accrued delay in days as a non-negative integer>}
    """
    extension = next(
        (item for item in expression.extensions if item.type == TREATMENT_DELAY_AFTER_HOLD), None,
    )
    if extension is None:
        raise UnsupportedTimingError(
            f"PROTOCOL_DEFINED timing names handler {TREATMENT_DELAY_AFTER_HOLD!r} but carries "
            "no matching extension payload"
        )
    payload = extension.value
    if not isinstance(payload, dict):
        raise UnsupportedTimingError(f"{TREATMENT_DELAY_AFTER_HOLD} extension payload must be an object")
    delay_field = payload.get("delay_state_field")
    if not isinstance(delay_field, str) or not delay_field:
        raise UnsupportedTimingError(
            f"{TREATMENT_DELAY_AFTER_HOLD} extension payload must name a non-empty delay_state_field"
        )
    try:
        reference = _REFERENCE_ADAPTER.validate_python(payload.get("reference"))
        base_offset = TemporalAmount.model_validate(payload.get("base_offset"))
    except Exception as error:
        raise UnsupportedTimingError(
            f"{TREATMENT_DELAY_AFTER_HOLD} extension payload is malformed: {error}"
        ) from error
    resolved_reference = _resolve_reference(reference, context)
    if resolved_reference is None:
        return None  # the reference event/anchor has no date yet -- wait, never guess
    delay_days = context.state.get(delay_field, 0)
    if not isinstance(delay_days, int) or isinstance(delay_days, bool) or delay_days < 0:
        raise UnsupportedTimingError(
            f"{delay_field!r} must be a recorded non-negative integer number of days, got {delay_days!r}"
        )
    base_date = add_amount(resolved_reference, base_offset)
    final_date = add_amount(base_date, TemporalAmount(value=delay_days, unit="DAY"))
    return ResolvedTiming(
        nominal_start=final_date, nominal_end=final_date, precision="EXACT",
        constraints=[{
            "type": TREATMENT_DELAY_AFTER_HOLD,
            "reference": payload.get("reference"), "base_offset": payload.get("base_offset"),
            "applied_delay_days": delay_days,
        }],
    )


register_timing_extension(TREATMENT_DELAY_AFTER_HOLD, evaluate_treatment_delay_after_hold)
