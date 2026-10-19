"""One-click connectors: a registry that describes, for every outside service a business can connect, what the platform needs, what the owner does, and how
the connection starts and completes. The same code serves every tenant; nothing here knows a business type.

A connector has a `mode`:
  * `oauth`       the owner's browser visits the provider's consent page and the provider calls our callback with a `code` (`start` builds the URL, `complete`
                  exchanges the code and returns the credential to store).
  * `link`        a provider-hosted widget (Plaid Link): `start` returns data for the widget, the browser posts back a result to `complete`.
  * `credentials` the owner pastes values the provider gave them (`fields`); the generic connect endpoint stores and verifies them.

`required_settings` are platform env names (Klaros's own developer app with the provider). While any is unset the connector reports `platform_ready=False`
with the missing NAMES (never values), so the owner sees exactly which key is still needed instead of a dead button.

Credentials are stored only through `IntegrationConnectionService.connect()` (encrypted, then verified with a real call). A connector never marks itself connected.
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings

# Modules that contribute connectors. Each exposes `CONNECTORS: list[Connector]`. A missing module is skipped, so a connector can ship on its own.
_CONTRIBUTORS = (
    "app.integrations.connectors_quickbooks",
    "app.integrations.connectors_google_ads",
    "app.integrations.connectors_meta",
    "app.integrations.connectors_plaid",
    "app.integrations.connectors_whatsapp",
    "app.integrations.connectors_xero",
    "app.integrations.connectors_google_business",
    "app.integrations.connectors_esign",
)


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    secret: bool = True
    help: str = ""


@dataclass
class CompleteResult:
    credential: dict[str, Any]
    external_account_id: str | None = None
    scopes: str | None = None


# start(tenant_id, user_id, redirect_uri, state) -> {"authorization_url": ...} or provider widget data
StartFn = Callable[[uuid.UUID, uuid.UUID, str, str], Awaitable[dict[str, Any]]]
# complete(params, redirect_uri, tenant_id) -> CompleteResult; params are the callback query or the posted body
CompleteFn = Callable[[dict[str, Any], str, uuid.UUID], Awaitable[CompleteResult]]
VerifyFn = Callable[[dict], Awaitable[tuple[bool, str]]]


@dataclass(frozen=True)
class Connector:
    key: str  # the IntegrationConnection.provider value
    name: str
    category: str  # finance | marketing | communication | contracts
    description: str
    mode: str  # oauth | link | credentials
    required_settings: tuple[str, ...] = ()
    owner_steps: tuple[str, ...] = ()  # what the platform owner (or the business owner for credentials mode) does, in order
    fields: tuple[CredentialField, ...] = ()
    start: StartFn | None = None
    complete: CompleteFn | None = None
    verify: VerifyFn | None = None
    # True for a built-in capability that needs no outside account (Klaros's own e-signature).
    built_in: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def missing_settings(self) -> list[str]:
        s = get_settings()
        return [name for name in self.required_settings if not (getattr(s, name, None) or "").strip()]

    def platform_ready(self) -> bool:
        return not self.missing_settings()


def all_connectors() -> dict[str, Connector]:
    found: dict[str, Connector] = {}
    for module_name in _CONTRIBUTORS:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        for connector in module.CONNECTORS:
            found[connector.key] = connector
    return found


def get_connector(key: str) -> Connector | None:
    return all_connectors().get(key)


class ConnectorCompleteError(Exception):
    """A provider refused the code or token. The message is shown to the owner, so it must never contain a secret."""


def redirect_uri_for(key: str) -> str | None:
    """The exact callback address to register with the provider; None while KLAROS_PUBLIC_API_URL is unset."""
    base = (get_settings().KLAROS_PUBLIC_API_URL or "").strip().rstrip("/")
    if not base:
        return None
    connector = all_connectors().get(key)
    path = connector.extra.get("redirect_uri_path") if connector else None
    return f"{base}{path or f'/api/v1/integrations/connectors/{key}/callback'}"
