"""Business operations: the registry through which an industry module contributes
operational data (metrics, record counts, breakdowns, lead-workflow stages) to the
generic Business Operations console, without the generic code ever naming it.

Identical in spirit to `website_data_providers.py`: a module registers one async
function under its own registry key (the `VerticalExtension.key` it is enabled by),
and the generic `BusinessOperationsService` looks the function up by the tenant's
*enabled* verticals — never by an `if business_type == ...` branch.

A provider returns (every field optional):

    {
      "metrics":     [{"key","label","value","route"?}],
      "data":        [{"key","label","count","route"?}],
      "breakdowns":  [{"key","label","items":[{"label","value"}]}],
      "lead_stages": [{"key","label","kind","state","detail","route"?}],
    }
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

OperationsProvider = Callable[[uuid.UUID], Awaitable[dict[str, Any]]]

_REGISTRY: dict[str, OperationsProvider] = {}


def register_operations_provider(vertical_key: str, fn: OperationsProvider) -> None:
    _REGISTRY[vertical_key] = fn


def get_operations_provider(vertical_key: str) -> OperationsProvider | None:
    return _REGISTRY.get(vertical_key)
