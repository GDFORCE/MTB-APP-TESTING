"""The Directive-2 safety boundary: scaffold generation must never produce a
handler that can accidentally become "live" (registered) without a developer
deliberately implementing and reviewing it first.

If this boundary failed, a generated-but-unimplemented scaffold could get
auto-registered, which would make `validator.py` treat the pattern as
resolved (a handler exists) while `evaluator.py` would still raise
NotImplementedError the moment a real patient schedule tried to use it --
turning a developer-acceleration tool into a silent production outage.
"""
import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from scripts.generate_extension_scaffold import generate_extension_handler_scaffold


def test_scaffold_includes_real_evidence_and_never_auto_registers():
    source = generate_extension_handler_scaffold(
        "WASHOUT_INTERVAL",
        ["at least 21 days after Period I dosing", "minimum 14-day interval between doses"],
    )
    assert "at least 21 days after Period I dosing" in source
    assert "minimum 14-day interval between doses" in source

    # The hard safety guard: a register_timing_extension(...) call for this
    # handler must never appear as live, executable code -- only inside a
    # comment instructing the developer to uncomment it later.
    tree = ast.parse(source)
    live_registration_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "register_timing_extension"
    ]
    assert live_registration_calls == [], "scaffold must never auto-register its own stub handler"
    assert "# register_timing_extension(" in source


def test_scaffold_handler_body_always_raises_when_actually_called():
    source = generate_extension_handler_scaffold("RARE_PATTERN", [])
    namespace: dict[str, object] = {}
    # The generated module imports real app modules; executing it here proves
    # it is syntactically valid, importable Python, not just a text template.
    exec(compile(source, "<scaffold>", "exec"), namespace)
    handler = namespace["evaluate_rare_pattern"]
    with pytest.raises(NotImplementedError, match="generated scaffold but no reviewed implementation"):
        handler(None, None)


def test_scaffold_rejects_an_empty_handler_name():
    with pytest.raises(ValueError, match="non-empty"):
        generate_extension_handler_scaffold("", ["some evidence"])


def test_scaffold_includes_clinical_review_prompts_not_just_code():
    source = generate_extension_handler_scaffold("SOME_PATTERN", [])
    assert "clinical reviewer" in source
    assert "waiting state" in source
