"""Phase 4: static no-hardcoding guard. The Agent Runtime must operate on
generic agent/version/tool-permission/execution/autonomy/tenant concepts
only — never a hardcoded `if vertical == "medical_tourism"` (or
"dropshipping") branch, and never a literal reference to a vertical-
specific domain noun (hospital/clinic/doctor/procedure/supplier/inventory/
SKU/fulfillment) in the Agent Runtime's own source files. Mirrors
tests/test_vertical_extension_no_hardcoding_guard.py's grep-based CI-guard
style.
"""

from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# The Agent Runtime's own source files (this phase's new/modified files) —
# narrow on purpose so this guard cannot false-positive on unrelated parts
# of the codebase that may legitimately mention these nouns (e.g. a
# vertical extension's own seed data).
_AGENT_RUNTIME_FILES = [
    _APP_ROOT / "models" / "agent.py",
    _APP_ROOT / "services" / "agent_service.py",
    _APP_ROOT / "services" / "agent_execution_service.py",
    _APP_ROOT / "api" / "v1" / "agents.py",
    _APP_ROOT / "api" / "tool_deps_agents.py",
    # Phase 5: the bounded LLM-driven reasoning loop — same generic-runtime
    # requirement extended to the new files.
    _APP_ROOT / "services" / "agent_reasoning_service.py",
    # Phase 6: crash recovery + scheduled/event triggers — same generic-
    # runtime requirement extended to the new files. Deliberately generic:
    # these files must work identically for the medical-tourism-shaped and
    # dropshipping-shaped cross-vertical test scenarios
    # (test_cross_vertical_agent_trigger_validation.py) without ever naming
    # either.
    _APP_ROOT / "services" / "agent_recovery_service.py",
    _APP_ROOT / "services" / "agent_trigger_service.py",
    _APP_ROOT / "events" / "agent_trigger_handlers.py",
]

_FORBIDDEN_TERMS = [
    "medical_tourism",
    "dropshipping",
    "hospital",
    "clinic",
    "doctor",
    "procedure",
    "supplier",
    "inventory",
    "sku",
    "fulfillment",
]


def test_agent_runtime_source_never_names_a_vertical_or_domain_noun() -> None:
    violations = []
    for path in _AGENT_RUNTIME_FILES:
        assert path.exists(), f"expected Agent Runtime file missing: {path}"
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for term in _FORBIDDEN_TERMS:
            if term in text:
                violations.append((str(path), term))
    assert not violations, f"Agent Runtime source hardcodes vertical/domain terms: {violations}"


def test_agent_runtime_never_branches_on_vertical_key() -> None:
    import re

    pattern = re.compile(r"\b(if|elif)\b[^:\n]*==\s*[\"'](medical_tourism|dropshipping)[\"']")
    violations = []
    for path in _AGENT_RUNTIME_FILES:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            violations.append(str(path))
    assert not violations, f"Agent Runtime source branches on a vertical-key literal: {violations}"


def test_phase_5_reasoning_loop_never_stores_hidden_chain_of_thought() -> None:
    """Phase 5's mandatory grep check: no field/column/variable name in the
    reasoning-loop source (or its model/migration) may be named for
    unrestricted hidden chain-of-thought storage — only a short, safe
    `reasoning_summary`/`decision_summary` may ever be persisted."""
    forbidden = ["chain_of_thought", "internal_reasoning", "hidden_reasoning"]
    files = [
        _APP_ROOT / "services" / "agent_reasoning_service.py",
        _APP_ROOT / "models" / "agent.py",
        _APP_ROOT / "api" / "v1" / "agents.py",
        _APP_ROOT.parent / "alembic" / "versions" / "0046_agent_reasoning.py",
    ]
    violations = []
    for path in files:
        assert path.exists(), f"expected Phase 5 file missing: {path}"
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for term in forbidden:
            if term in text:
                violations.append((str(path), term))
    assert not violations, f"Phase 5 source stores hidden chain-of-thought: {violations}"


def test_phase_5_reasoning_service_has_no_production_shell_or_eval() -> None:
    """The reasoning loop must never gain a generic code/shell/HTTP
    execution surface — every capability still flows through
    ToolRegistry.execute() only."""
    text = (_APP_ROOT / "services" / "agent_reasoning_service.py").read_text(encoding="utf-8")
    for term in ("subprocess", "eval(", "exec(", "os.system"):
        assert term not in text, f"forbidden execution primitive found: {term}"
