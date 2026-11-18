"""Generate a starter scaffold for a new PROTOCOL_DEFINED extension handler.

This is a developer-acceleration tool, not a no-code fix generator. It writes
real evidence examples into the file so a developer understands the pattern
immediately, but the generated handler body always raises NotImplementedError
and is NEVER auto-registered with `register_timing_extension`.

That boundary is deliberate and load-bearing: if a stub handler were
registered while still unimplemented, `validator.py` would see "a handler is
installed" and stop blocking approval for that pattern -- silently turning an
unbuilt, unreviewed rule into one treated as safe. The generated file leaves
the registration call commented out, with an explicit instruction that it
must only be uncommented after the real logic is written, reviewed, and
covered by tests (see `builtin_extensions.py` for the pattern to follow).

Usage:
    python scripts/generate_extension_scaffold.py HANDLER_NAME \
        --evidence "quote one" --evidence "quote two" --out handler_scaffold.py
"""
from __future__ import annotations

import argparse
from pathlib import Path


def generate_extension_handler_scaffold(handler_name: str, sample_evidence: list[str]) -> str:
    if not handler_name or not handler_name.strip():
        raise ValueError("handler_name must be a non-empty string")
    function_name = f"evaluate_{handler_name.lower()}"
    evidence_block = "\n".join(f"    * {quote!r}" for quote in sample_evidence) or "    (no sample evidence provided)"
    return f'''"""DRAFT scaffold for the {handler_name!r} PROTOCOL_DEFINED handler.

Auto-generated starting point -- NOT reviewed, NOT tested, NOT registered.
A developer must design the correct clinical interpretation, implement it,
write tests, and only then register it. Registering this file's handler
before that work is done would make the validator incorrectly treat blocked
schedules as resolved while this function still raises NotImplementedError
at actual patient-evaluation time -- do not do that.

Real evidence examples that triggered this pattern (from real protocols):
{evidence_block}

Design questions a developer/clinical reviewer should answer before writing
the real logic (see builtin_extensions.py's MINIMUM_GAP_AFTER_EVENT handler
for a worked example of this same process):
  * What exact fields does this pattern need in its ProtocolExtension payload?
  * Is there a lower bound, an upper bound, or both?
  * What happens when its reference (anchor/event) is not yet known --
    confirm it returns None (a waiting state), never a fabricated date.
  * Do any of the real evidence examples above actually disagree with each
    other in shape? If so, this may need two handlers, not one.
"""
from __future__ import annotations

from app.domain.schedule.evaluator import _resolve_reference, add_amount  # noqa: F401 (use as needed)
from app.domain.schedule.exceptions import UnsupportedTimingError
from app.domain.schedule.extension_handlers import register_timing_extension  # noqa: F401
from app.domain.schedule.models import PatientContext, ResolvedTiming
from app.domain.schedule.timing import ProtocolDefinedTiming

{handler_name.upper()} = {handler_name.upper()!r}


def {function_name}(expression: ProtocolDefinedTiming, context: PatientContext) -> ResolvedTiming | None:
    """TODO: implement the real, reviewed, tested clinical interpretation."""
    raise NotImplementedError(
        "{handler_name} has a generated scaffold but no reviewed implementation yet"
    )


# Uncomment ONLY after the function above is implemented, clinically reviewed,
# and covered by tests mirroring test_protocol_defined_extensions.py:
# register_timing_extension({handler_name.upper()}, {function_name})
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handler_name", help="The PROTOCOL_DEFINED handler name, e.g. WASHOUT_INTERVAL")
    parser.add_argument("--evidence", action="append", default=[], help="A real evidence quote (repeatable)")
    parser.add_argument("--out", type=Path, help="Write to this file instead of stdout")
    args = parser.parse_args()
    source = generate_extension_handler_scaffold(args.handler_name, args.evidence)
    if args.out:
        args.out.write_text(source, encoding="utf-8")
        print(f"Wrote draft scaffold to {args.out} -- review before implementing.")
    else:
        print(source)


if __name__ == "__main__":
    main()
