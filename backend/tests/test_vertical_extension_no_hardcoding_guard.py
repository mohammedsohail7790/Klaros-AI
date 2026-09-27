"""Phase 1.1 (KLAROS_PHASE_1_IMPLEMENTATION_PLAN.md §1.1): "a static-
analysis CI check (extends 0.1's pipeline) asserting no application code
branches on a vertical name string outside this registry's own lookup
path."

This is a grep-based CI guard, matching the style already established by
tests/test_production_secret_guard.py and named explicitly in
KLAROS_FINAL_TESTING_ARCHITECTURE.md's "Domain extensions" test-layer row
("core services never import a vertical by name — static-analysis check").

Scope: `app/` only (not `tests/`, not `alembic/` migrations/seed data,
which legitimately reference vertical keys as literal seed values — see
app/data/vertical_extension_seed.py — and not this file itself, or the
model/service/migration files that ARE the registry's own lookup path).
"""

import re
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# The registry's own legitimate references to vertical keys (seed data,
# the model/service files that define the registry itself) are excluded —
# everything else in app/ must never branch on one of these literal
# strings.
_ALLOWED_FILES = {
    _APP_ROOT / "data" / "vertical_extension_seed.py",
    _APP_ROOT / "models" / "vertical_extension.py",
    _APP_ROOT / "services" / "vertical_extension_service.py",
}

_VERTICAL_KEYS = ["medical_tourism", "dropshipping"]

# Matches an `if`/`elif` (or ternary-style ` if ... ==`) branching directly
# on one of the known vertical-key literals — e.g.
# `if business_type == "medical_tourism":` or
# `if vertical_key == 'dropshipping':`. Deliberately narrow (branching
# constructs only) so it does not flag comments/docstrings/seed data that
# merely mention the word.
_BRANCH_PATTERN = re.compile(
    r"\b(if|elif)\b[^:\n]*==\s*[\"'](" + "|".join(_VERTICAL_KEYS) + r")[\"']"
)


def _iter_app_python_files():
    for path in _APP_ROOT.rglob("*.py"):
        if path in _ALLOWED_FILES:
            continue
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_application_code_branches_on_a_vertical_name_literal() -> None:
    violations = []
    for path in _iter_app_python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _BRANCH_PATTERN.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(_APP_ROOT.parent)}:{line_no}: {match.group(0)!r}")

    assert not violations, (
        "Found application code branching on a vertical-name string literal "
        "outside the VerticalExtension registry's own lookup path (see "
        "app/models/vertical_extension.py's module docstring — a vertical must "
        "plug in via registry rows/data, never a compiled-in switch statement):\n"
        + "\n".join(violations)
    )


def test_guard_pattern_actually_detects_a_hardcoded_branch() -> None:
    """Proves the regex isn't vacuously passing — a real violation string
    must be caught."""
    sample = 'if business_type == "medical_tourism":\n    do_something()\n'
    assert _BRANCH_PATTERN.search(sample) is not None


@pytest.mark.parametrize("key", _VERTICAL_KEYS)
def test_guard_catches_either_known_vertical_key(key: str) -> None:
    sample = f'if vertical == "{key}":\n    pass\n'
    assert _BRANCH_PATTERN.search(sample) is not None
