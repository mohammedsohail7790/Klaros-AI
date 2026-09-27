"""Phase 11 (Phase 6/18 of PHASE_11_WEBSITE_BUILDER_DESIGN.md): extends
tests/test_vertical_extension_no_hardcoding_guard.py's exact grep-based
static-analysis pattern to explicitly cover this phase's new files.

The pre-existing guard (test_vertical_extension_no_hardcoding_guard.py)
already scans all of app/ automatically, so it already covers these new
files for the narrow `if x == "medical_tourism"` branch pattern. This file
adds a second, stricter check specific to the Website Builder's generic
core: the renderer and the generation service must not even MENTION a
vertical-key literal anywhere in their actual code (not just avoid
branching on it) — the strongest form of "the generic renderer has zero
vertical-name hardcoding" the phase's success criteria calls for.
"""

from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VERTICAL_KEYS = ["medical_tourism", "dropshipping"]

# The two files that must be provably generic (design doc §12/§18). Their
# module docstrings are allowed to name these terms in PROSE explaining the
# guarantee (see PHASE_11_WEBSITE_BUILDER_DESIGN.md's own text) — this test
# checks each file's executable code with the module docstring stripped.
_GENERIC_CORE_FILES = (
    _APP_ROOT / "services" / "website_renderer.py",
    _APP_ROOT / "services" / "website_generation_service.py",
)


def _code_without_module_docstring(path: Path) -> str:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree, clean=False)
    text = path.read_text(encoding="utf-8")
    if docstring:
        # Remove the literal docstring text (best-effort; the docstring is
        # a contiguous quoted block near the top of the file).
        text = text.replace(docstring, "")
    return text.lower()


@pytest.mark.parametrize("path", _GENERIC_CORE_FILES)
def test_generic_core_file_never_names_a_vertical_in_code(path: Path) -> None:
    assert path.exists(), f"{path} does not exist"
    code = _code_without_module_docstring(path)
    for key in _VERTICAL_KEYS:
        assert key not in code, f"{path.name} names vertical key {key!r} outside its module docstring"


def test_website_data_providers_registry_is_the_only_bridge() -> None:
    """Confirms the registration call for the Medical Tourism provider
    lives in the vertical's OWN service module, never in generic Website
    Builder code."""
    generic_files_source = "\n".join(p.read_text(encoding="utf-8") for p in _GENERIC_CORE_FILES)
    assert "register_website_data_provider" not in generic_files_source

    medical_tourism_service = (_APP_ROOT / "services" / "medical_tourism_service.py").read_text(encoding="utf-8")
    assert "register_website_data_provider(" in medical_tourism_service


# Phase 12 (§Genericity Guard): the same proof, extended to the frontend's
# own generic core — the closed component_type -> React component
# registry (frontend/components/website/ComponentRegistry.tsx) and the
# public website runtime page must never branch on a vertical name either.
# A .tsx file has no module-docstring convention to strip, so this check
# is simpler: the vertical key must not appear ANYWHERE in the file,
# including comments — these files' own prose describes the data-provider
# CONTRACT (generic field names like "name"/"location"/"offerings"), never
# the vertical itself, so this is a stricter bar than the backend check
# above and is expected to hold trivially.
_FRONTEND_GENERIC_CORE_FILES = (
    _REPO_ROOT / "frontend" / "components" / "website" / "ComponentRegistry.tsx",
    _REPO_ROOT / "frontend" / "app" / "w" / "[tenantId]" / "page.tsx",
    _REPO_ROOT / "frontend" / "components" / "website" / "SectionEditor.tsx",
)


@pytest.mark.parametrize("path", _FRONTEND_GENERIC_CORE_FILES)
def test_frontend_generic_core_file_never_names_a_vertical(path: Path) -> None:
    assert path.exists(), f"{path} does not exist"
    code = path.read_text(encoding="utf-8").lower()
    for key in _VERTICAL_KEYS:
        assert key not in code, f"{path.name} names vertical key {key!r}"
