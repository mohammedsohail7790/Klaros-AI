#!/usr/bin/env bash
# Phase 0 (KLAROS_PHASE_0_IMPLEMENTATION_PLAN.md §0.4): guardrail that fails
# CI if a PR introduces a new *read* of the deprecated, unenforced
# Organization.autonomy_level field anywhere in application logic outside
# its own definition in app/models/organization.py. See that file's
# AutonomyLevel docstring for the full "why" — the real, enforced AI kill
# switch is Organization.ai_paused, checked in
# app/tools/registry.py::ToolRegistry.execute().
#
# This is deliberately a plain grep, not a static-analysis tool: the field
# is a small, known, named symbol, and a grep is the whole of "the test" per
# the approved plan (§0.4's Tests: "the grep-check itself is the test").
set -euo pipefail

cd "$(dirname "$0")/.."

MATCHES=$(grep -rn "autonomy_level\|AutonomyLevel" app/ \
  --include="*.py" \
  | grep -v "^app/models/organization.py:" \
  || true)

if [ -n "$MATCHES" ]; then
  echo "ERROR: found a reference to the deprecated Organization.autonomy_level"
  echo "/ AutonomyLevel outside its own definition in app/models/organization.py:"
  echo ""
  echo "$MATCHES"
  echo ""
  echo "autonomy_level is unenforced/decorative (see its docstring). The real,"
  echo "enforced kill switch is Organization.ai_paused, checked in"
  echo "app/tools/registry.py::ToolRegistry.execute(). If you have a genuine"
  echo "reason to read this field (e.g. a settings UI showing its current"
  echo "stored value, never gating behavior on it), update this script's"
  echo "allowlist and explain why in your PR description."
  exit 1
fi

echo "OK: no new reads of the deprecated autonomy_level field found."
