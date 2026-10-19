"""Xero accounting client (direct httpx, no SDK), same shape as `google_ads_client.py`: bounded timeout, bounded retry with backoff on transient failures, typed errors that
carry only Xero's own short message (never a token, secret or request body).

Three hosts: login.xero.com (consent page), identity.xero.com (token endpoint, HTTP Basic with Klaros's own app credentials) and api.xero.com (connections list and the
accounting API, which needs the owner's access token plus the `xero-tenant-id` header naming the organisation).

XERO REFRESH TOKENS ROTATE. Every refresh returns a NEW refresh token; the old one stops working after a short grace window (about 30 minutes) and is single use. So a
caller that refreshes MUST persist the returned tokens before doing anything else; `XeroTokens.to_credential_update()` gives the exact dict to store.

Scopes are a constant. Apps created on or after 2026-03 must use Xero's granular scopes; older apps may still use `accounting.transactions`/`accounting.contacts`. Read-only
granular scopes are listed here; if Xero rejects them for an older app, change SCOPES and re-run the tests.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
API_BASE = "https://api.xero.com"
CONNECTIONS_URL = f"{API_BASE}/connections"
ACCOUNTING_BASE = f"{API_BASE}/api.xro/2.0"
# Read-only. Change here if Xero's scope model for the app changes (see module docstring).
SCOPES = "offline_access accounting.contacts.read accounting.transactions.read accounting.settings.read openid profile email"
TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
PAGE_LIMIT = 100  # Xero returns up to 100 contacts / invoices per page
MAX_PAGES = 200  # hard bound
REFRESH_SKEW_SECONDS = 120


class XeroErrorType(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_CONFIGURED = "not_configured"
    REFRESH_REVOKED = "refresh_revoked"  # the refresh token is expired, already used or revoked: the owner must reconnect


class XeroAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error_type: XeroErrorType = XeroErrorType.PROVIDER_ERROR) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class XeroTokens:
    access_token: str
    refresh_token: str
    expires_in: int

    @property
    def expires_at(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=self.expires_in)).isoformat()

    def to_credential_update(self) -> dict[str, str]:
        """The three fields to merge into the stored credential after any exchange or refresh."""
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "expires_at": self.expires_at}

    def __repr__(self) -> str:
        return "XeroTokens(...)"


@dataclass(frozen=True)
class XeroConnection:
    tenant_id: str
    tenant_name: str
    tenant_type: str


@dataclass(frozen=True)
class XeroContact:
    contact_id: str
    name: str
    email: str | None
    phone: str | None
    status: str
    is_customer: bool
    is_supplier: bool = False


def get_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPES, "state": state}
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def needs_refresh(expires_at: str | None, *, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    """True when the access token is missing an expiry, expired, or about to be."""
    if not expires_at:
        return True
    try:
        when = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when - timedelta(seconds=skew_seconds) <= datetime.now(timezone.utc)


def _safe_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return f"HTTP {response.status_code}"
    msg = body.get("error_description") or body.get("Detail") or body.get("Message") or body.get("error") or body.get("Title")
    return str(msg)[:300] if msg else f"HTTP {response.status_code}"


def _clean(value: Any) -> str:
    return str(value or "").strip()


class XeroClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.XERO_CLIENT_ID
        self._client_secret = s.XERO_CLIENT_SECRET

    def __repr__(self) -> str:
        return "XeroClient()"

    def _require_app(self) -> None:
        if not (self._client_id and self._client_secret):
            raise XeroAPIError("Xero OAuth app is not configured on this platform", error_type=XeroErrorType.NOT_CONFIGURED)

    def _basic_headers(self) -> dict[str, str]:
        raw = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        return {"Authorization": f"Basic {raw}", "Accept": "application/json"}

    @staticmethod
    def _api_headers(access_token: str, xero_tenant_id: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        if xero_tenant_id:
            headers["xero-tenant-id"] = xero_tenant_id
        return headers

    async def _send(
        self, method: str, url: str, *, headers: dict[str, str], data: dict | None = None, params: dict | None = None, token_call: bool = False,
    ) -> Any:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.request(method, url, headers=headers, data=data, params=params)
            except httpx.TimeoutException as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise XeroAPIError("Xero request timed out", error_type=XeroErrorType.TIMEOUT) from exc
            except httpx.HTTPError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise XeroAPIError(f"Xero request failed ({type(exc).__name__})", error_type=XeroErrorType.NETWORK_ERROR) from exc
            code = response.status_code
            if code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise XeroAPIError("Xero returned a non-JSON response", status_code=code) from exc
            if (code == 429 or code >= 500) and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After", "")
                delay = min(float(retry_after), 10.0) if retry_after.replace(".", "", 1).isdigit() else 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
                continue
            message = _safe_message(response)
            if token_call and code == 400 and "invalid_grant" in message.lower():
                kind = XeroErrorType.REFRESH_REVOKED
                message = "the Xero login expired or was revoked"
            elif code in (401, 403):
                kind = XeroErrorType.AUTHENTICATION
            elif code == 429:
                kind = XeroErrorType.RATE_LIMIT
            elif code >= 500:
                kind = XeroErrorType.PROVIDER_ERROR
            else:
                kind = XeroErrorType.INVALID_REQUEST
            logger.warning("xero_request_failed", status=code, error_type=str(kind))
            raise XeroAPIError(f"Xero: {message}", status_code=code, error_type=kind)
        raise XeroAPIError("Xero request failed")  # unreachable

    @staticmethod
    def _tokens(body: Any) -> XeroTokens:
        if not isinstance(body, dict) or not body.get("access_token"):
            raise XeroAPIError("Xero did not return an access token")
        if not body.get("refresh_token"):
            raise XeroAPIError("Xero did not return a refresh token", error_type=XeroErrorType.REFRESH_REVOKED)
        return XeroTokens(access_token=body["access_token"], refresh_token=body["refresh_token"], expires_in=int(body.get("expires_in") or 1800))

    async def exchange_code(self, *, code: str, redirect_uri: str) -> XeroTokens:
        self._require_app()
        body = await self._send("POST", TOKEN_URL, headers=self._basic_headers(), token_call=True,
                                data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
        return self._tokens(body)

    async def refresh_access_token(self, *, refresh_token: str) -> XeroTokens:
        """Returns a NEW refresh token. The caller must persist it before any other work (see module docstring)."""
        self._require_app()
        body = await self._send("POST", TOKEN_URL, headers=self._basic_headers(), token_call=True,
                                data={"grant_type": "refresh_token", "refresh_token": refresh_token})
        return self._tokens(body)

    async def list_connections(self, *, access_token: str) -> list[XeroConnection]:
        body = await self._send("GET", CONNECTIONS_URL, headers=self._api_headers(access_token))
        out: list[XeroConnection] = []
        for row in body if isinstance(body, list) else []:
            if isinstance(row, dict) and row.get("tenantId"):
                out.append(XeroConnection(tenant_id=str(row["tenantId"]), tenant_name=str(row.get("tenantName") or ""), tenant_type=str(row.get("tenantType") or "")))
        return out

    async def get_organisation(self, *, access_token: str, xero_tenant_id: str) -> dict[str, Any]:
        """The first organisation of the tenant, or raise INVALID_REQUEST when Xero returns none."""
        body = await self._send("GET", f"{ACCOUNTING_BASE}/Organisation", headers=self._api_headers(access_token, xero_tenant_id))
        orgs = body.get("Organisations") if isinstance(body, dict) else None
        if not orgs or not isinstance(orgs[0], dict):
            raise XeroAPIError("Xero returned no organisation for this connection", error_type=XeroErrorType.INVALID_REQUEST)
        return orgs[0]

    async def list_contacts(self, *, access_token: str, xero_tenant_id: str, max_records: int = 300) -> list[XeroContact]:
        """Active contacts, paginated (100 per page), bounded by max_records."""
        out: list[XeroContact] = []
        headers = self._api_headers(access_token, xero_tenant_id)
        for page in range(1, MAX_PAGES + 1):
            body = await self._send("GET", f"{ACCOUNTING_BASE}/Contacts", headers=headers, params={"page": page, "includeArchived": "false"})
            rows = body.get("Contacts", []) if isinstance(body, dict) else []
            for row in rows:
                if not isinstance(row, dict) or not row.get("ContactID"):
                    continue
                out.append(XeroContact(
                    contact_id=str(row["ContactID"]), name=_clean(row.get("Name")), email=(row.get("EmailAddress") or None),
                    phone=_phone(row), status=str(row.get("ContactStatus") or ""), is_customer=bool(row.get("IsCustomer")), is_supplier=bool(row.get("IsSupplier")),
                ))
                if len(out) >= max_records:
                    return out
            if len(rows) < PAGE_LIMIT:
                break
        return out

    async def list_invoices(self, *, access_token: str, xero_tenant_id: str, max_records: int = 300) -> list[dict[str, Any]]:
        """Raw invoice rows (read-only listing, paginated). Not used by the contact import; kept for the finance read path."""
        out: list[dict[str, Any]] = []
        headers = self._api_headers(access_token, xero_tenant_id)
        for page in range(1, MAX_PAGES + 1):
            body = await self._send("GET", f"{ACCOUNTING_BASE}/Invoices", headers=headers, params={"page": page})
            rows = body.get("Invoices", []) if isinstance(body, dict) else []
            out.extend(r for r in rows if isinstance(r, dict))
            if len(out) >= max_records:
                return out[:max_records]
            if len(rows) < PAGE_LIMIT:
                break
        return out


def _phone(row: dict[str, Any]) -> str | None:
    """The first filled-in phone (DEFAULT, then MOBILE, then any) as a plain string."""
    phones = [p for p in row.get("Phones") or [] if isinstance(p, dict) and p.get("PhoneNumber")]
    phones.sort(key=lambda p: {"DEFAULT": 0, "MOBILE": 1}.get(p.get("PhoneType"), 2))
    if not phones:
        return None
    p = phones[0]
    local = "".join(x for x in [p.get("PhoneAreaCode") or "", p["PhoneNumber"]] if x)
    return f"+{p['PhoneCountryCode']}{local}" if p.get("PhoneCountryCode") else local


async def ensure_fresh_credential(client: XeroClient, credential: dict[str, Any], *, force: bool = False) -> bool:
    """Refreshes the access token when it is near expiry (or `force`), merging the NEW tokens into `credential` IN PLACE immediately, so the caller can persist the
    rotated refresh token even if a later call fails. Returns True when a refresh happened (the caller must then persist `credential`)."""
    if not force and not needs_refresh(credential.get("expires_at")) and credential.get("access_token"):
        return False
    refresh = credential.get("refresh_token")
    if not refresh:
        raise XeroAPIError("No refresh token is stored; reconnect Xero", error_type=XeroErrorType.REFRESH_REVOKED)
    tokens = await client.refresh_access_token(refresh_token=refresh)
    credential.update(tokens.to_credential_update())
    return True
