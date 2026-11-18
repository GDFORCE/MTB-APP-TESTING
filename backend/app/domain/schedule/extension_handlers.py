"""Pluggable deterministic-handler registry for PROTOCOL_DEFINED timing.

Rationale (short-turnaround extensibility, not model "learning"):

Every built-in timing kind (OFFSET, RANGE, WITHIN, CYCLE_DAY, ...) is a fixed
branch in `evaluator.evaluate_timing`. Adding a genuinely new clinical pattern
the AI has never anticipated would otherwise mean touching that core dispatch
function, its tests, and every caller -- a slow, risky change for each new
pattern discovered across real protocols.

`ProtocolDefinedTiming` already carries a `handler: str | None` field (see
`timing.py`) naming which deterministic interpreter understands its attached
`extensions` payload, but nothing previously looked that name up: every such
timing unconditionally raised `UnsupportedTimingError`, and the validator
unconditionally blocked approval for it, regardless of what `handler` said.

This module closes that gap with a small, explicit registry:

    1. A recurring unsupported pattern is captured as a `ProtocolExtension`
       inside a `ProtocolDefinedTiming` (AI/extraction side -- already
       possible, no schema change needed) with a chosen `handler` name.
    2. A developer writes ONE pure function that deterministically resolves
       that pattern from its extension payload, and registers it here under
       the same name.
    3. From that point on, every schedule naming that handler becomes
       deterministically executable and is no longer blocked at validation --
       without any change to `evaluate_timing`, `validator.py`'s dispatch, or
       any other installed handler.

An unregistered handler name still fails safely exactly as before: blocked at
validation, and a clear `UnsupportedTimingError` if evaluation is ever
attempted anyway.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PatientContext, ResolvedTiming
    from .timing import ProtocolDefinedTiming

TimingExtensionHandler = Callable[["ProtocolDefinedTiming", "PatientContext"], "ResolvedTiming | None"]

_REGISTRY: dict[str, TimingExtensionHandler] = {}


def register_timing_extension(name: str, handler: TimingExtensionHandler) -> None:
    """Install the deterministic handler for a named PROTOCOL_DEFINED pattern.

    Re-registering the same name replaces the previous handler (useful for
    tests); production code should treat names as stable and unique.
    """
    if not name or not name.strip():
        raise ValueError("extension handler name must be a non-empty string")
    _REGISTRY[name] = handler


def get_timing_extension(name: str | None) -> TimingExtensionHandler | None:
    if not name:
        return None
    return _REGISTRY.get(name)


def registered_timing_extensions() -> frozenset[str]:
    """Every handler name currently installed -- for diagnostics/ops views."""
    return frozenset(_REGISTRY)
