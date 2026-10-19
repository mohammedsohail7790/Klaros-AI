"""Plaid (bank feed): a plain httpx client, same house style as `stripe_client.py` / `quickbooks_client.py` (timeouts, bounded retries, typed errors, no SDK).

Klaros has ONE Plaid developer account (PLAID_CLIENT_ID / PLAID_SECRET / PLAID_ENV in the platform settings). Every business connects its own bank through Plaid Link and gets its
own `access_token`, which only ever lives in the encrypted `IntegrationConnection` credential. Nothing here logs or puts a client id, secret, access token or public token into an
error message: Plaid's own `error_code` / `error_message` / `request_id` are the only provider text that is passed on.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_BASES = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
# Countries offered in Plaid Link. Plaid also supports ES, FR, IE, NL, DE, IT, PL, DK, NO, SE, EE, LT, LV, PT, BE (EU/UK products need extra enablement on the Plaid account).
# Add a code here only after Plaid has enabled that country for Klaros's account; Link refuses countries the account cannot serve.
PLAID_COUNTRY_CODES: tuple[str, ...] = ("US", "CA", "GB")
TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds; a module constant so tests can zero it


class PlaidErrorType(StrEnum):
    AUTHENTICATION = "authentication"  # Klaros's own client id / secret rejected
    ITEM_ERROR = "item_error"  # the business's bank connection needs attention (ITEM_LOGIN_REQUIRED, PRODUCT_NOT_READY, ...)
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_CONFIGURED = "not_configured"


class PlaidAPIError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, error_type: PlaidErrorType = PlaidErrorType.PROVIDER_ERROR,
        error_code: str | None = None, request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code  # Plaid's own code, e.g. ITEM_LOGIN_REQUIRED, PRODUCT_NOT_READY
        self.request_id = request_id


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


def _error_from(response: httpx.Response) -> tuple[str, str | None, str | None, str | None]:
    """(message, error_code, error_type, request_id) from Plaid's error body, never the raw body."""
    try:
        body = response.json()
    except ValueError:
        return f"Plaid returned HTTP {response.status_code}", None, None, None
    if not isinstance(body, dict):
        return f"Plaid returned HTTP {response.status_code}", None, None, None
    code, kind = body.get("error_code"), body.get("error_type")
    text = body.get("display_message") or body.get("error_message") or f"Plaid returned HTTP {response.status_code}"
    return (f"{code}: {text}" if code else str(text)), code, kind, body.get("request_id")


class PlaidClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client_id = (s.PLAID_CLIENT_ID or "").strip()
        self._secret = (s.PLAID_SECRET or "").strip()
        self._base = _BASES.get((s.PLAID_ENV or "sandbox").strip().lower(), _BASES["sandbox"])

    def __repr__(self) -> str:
        return "PlaidClient(configured=%s)" % bool(self._client_id and self._secret)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._client_id or not self._secret:
            raise PlaidAPIError("Plaid is not configured on this deployment (PLAID_CLIENT_ID / PLAID_SECRET unset)", error_type=PlaidErrorType.NOT_CONFIGURED)
        headers = {"PLAID-CLIENT-ID": self._client_id, "PLAID-SECRET": self._secret, "Content-Type": "application/json", "Accept": "application/json"}
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with _http_client() as client:
                    response = await client.post(f"{self._base}{path}", headers=headers, json=body)
            except httpx.TimeoutException as exc:
                logger.warning("plaid_request_attempt_failed", path=path, attempt=attempt, error_type="timeout")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                raise PlaidAPIError(f"Plaid request timed out after {MAX_RETRIES} attempts", error_type=PlaidErrorType.TIMEOUT) from exc
            except httpx.HTTPError as exc:
                logger.warning("plaid_request_attempt_failed", path=path, attempt=attempt, error_type="network_error")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                raise PlaidAPIError(f"Plaid request failed ({type(exc).__name__})", error_type=PlaidErrorType.NETWORK_ERROR) from exc

            status = response.status_code
            if status == 429 or status >= 500:
                kind = PlaidErrorType.RATE_LIMIT if status == 429 else PlaidErrorType.PROVIDER_ERROR
                logger.warning("plaid_request_attempt_failed", path=path, attempt=attempt, error_type=kind.value, status_code=status)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_BACKOFF_BASE * (2 ** (attempt - 1)))
                    continue
                message, code, _, rid = _error_from(response)
                raise PlaidAPIError(message, status_code=status, error_type=kind, error_code=code, request_id=rid)
            if status >= 400:
                message, code, plaid_type, rid = _error_from(response)
                if code == "RATE_LIMIT_EXCEEDED" or plaid_type == "RATE_LIMIT_EXCEEDED":
                    kind = PlaidErrorType.RATE_LIMIT
                elif plaid_type == "ITEM_ERROR":
                    kind = PlaidErrorType.ITEM_ERROR
                elif code in ("INVALID_API_KEYS", "INVALID_CLIENT_ID", "INVALID_SECRET", "UNAUTHORIZED_ENVIRONMENT") or status in (401, 403):
                    kind = PlaidErrorType.AUTHENTICATION
                else:
                    kind = PlaidErrorType.INVALID_REQUEST
                raise PlaidAPIError(message, status_code=status, error_type=kind, error_code=code, request_id=rid)
            try:
                data = response.json() if response.content else {}
            except ValueError as exc:
                raise PlaidAPIError("Plaid returned a response that is not JSON", status_code=status) from exc
            if not isinstance(data, dict):
                raise PlaidAPIError("Plaid returned an unexpected response shape", status_code=status)
            return data
        raise PlaidAPIError("Plaid request failed after retries", error_type=PlaidErrorType.NETWORK_ERROR)  # pragma: no cover

    async def create_link_token(self, *, client_user_id: str) -> dict[str, Any]:
        """The short-lived token the browser hands to Plaid Link. `client_user_id` is an opaque Klaros id, never a name, email or phone."""
        return await self._post("/link/token/create", {
            "client_name": "Klaros", "language": "en", "products": ["transactions"], "country_codes": list(PLAID_COUNTRY_CODES),
            "user": {"client_user_id": client_user_id}, "transactions": {"days_requested": 365},
        })

    async def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        return await self._post("/item/public_token/exchange", {"public_token": public_token})

    async def get_item(self, access_token: str) -> dict[str, Any]:
        return await self._post("/item/get", {"access_token": access_token})

    async def get_accounts(self, access_token: str) -> dict[str, Any]:
        return await self._post("/accounts/get", {"access_token": access_token})

    async def transactions_sync(self, access_token: str, *, cursor: str | None, count: int = 500) -> dict[str, Any]:
        body: dict[str, Any] = {"access_token": access_token, "count": count}
        if cursor:
            body["cursor"] = cursor
        return await self._post("/transactions/sync", body)
