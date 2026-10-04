"""Typed server-side HTTP client for the Halla AI platform (a separate service).

Klaros -> Halla over HTTPS only, always from the backend, always with the TENANT's own credential.
The routes are exactly the ones Halla's Klaros contract defines — nothing is invented here:

    GET  /api/v1/integrations/klaros/health
    GET  /api/v1/integrations/klaros/workforce       PUT  /api/v1/integrations/klaros/workforce
    GET  /api/v1/integrations/klaros/agents
    POST /api/v1/leads                               PUT  /api/v1/leads/{id}
    POST /api/v1/calls/outbound

Safety properties: the base URL is deployment configuration (never tenant input) and is validated
(https in production, no userinfo, host allow-list); redirects are never followed; every request has a
timeout; only idempotent requests (GET/PUT) are retried, a bounded number of times, and only for
failures a retry can fix; error messages carry a status, never a response body (it may hold PII); the
credential is never logged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from app.integrations.workforce.contract import WorkforceUnavailableError

logger = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"


class HallaConfigError(Exception):
    """This Klaros deployment is not (correctly) configured to reach Halla."""


@dataclass(frozen=True)
class HallaEndpoint:
    base_url: str
    credential_header: str
    credential_scheme: str | None


def validate_base_url(url: str, *, allowed_hosts: str | None, production: bool) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise HallaConfigError("HALLA_API_BASE_URL must be an absolute http(s) URL")
    if parts.scheme != "https" and (production or parts.hostname not in ("localhost", "127.0.0.1", "::1")):
        raise HallaConfigError("HALLA_API_BASE_URL must use https (plain http is allowed only for localhost outside production)")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise HallaConfigError("HALLA_API_BASE_URL must not contain credentials, a query or a fragment")
    allowed = {h.strip().lower() for h in (allowed_hosts or "").split(",") if h.strip()} or {parts.hostname.lower()}
    if parts.hostname.lower() not in allowed:
        raise HallaConfigError("HALLA_API_BASE_URL host is not in HALLA_ALLOWED_HOSTS")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def endpoint_from_settings(settings: Any) -> HallaEndpoint:
    if not settings.HALLA_API_BASE_URL:
        raise HallaConfigError("HALLA_API_BASE_URL is not set")
    if not settings.HALLA_API_KEY_HEADER:
        raise HallaConfigError("HALLA_API_KEY_HEADER is not set — the credential header is defined by Halla's contract")
    header = settings.HALLA_API_KEY_HEADER.strip()
    if not header or any(c in header for c in " \r\n:"):
        raise HallaConfigError("HALLA_API_KEY_HEADER is not a valid header name")
    base = validate_base_url(
        settings.HALLA_API_BASE_URL,
        allowed_hosts=settings.HALLA_ALLOWED_HOSTS,
        production=str(settings.ENV).lower() in ("production", "prod"),
    )
    scheme = (settings.HALLA_API_KEY_SCHEME or "").strip() or None
    return HallaEndpoint(base_url=base, credential_header=header, credential_scheme=scheme)


class HallaClient:
    def __init__(
        self,
        endpoint: HallaEndpoint,
        api_key: str,
        *,
        timeout: float = 8.0,
        max_retries: int = 2,
        tenant_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._tenant_id = (tenant_id or "").strip() or None
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._transport = transport
        self._sleep = sleep

    # ------------------------------------------------------------------ routes

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/integrations/klaros/health", operation="health")

    async def get_workforce(self) -> dict[str, Any]:
        return await self._request("GET", "/integrations/klaros/workforce", operation="workforce_get")

    async def put_workforce(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", "/integrations/klaros/workforce", operation="workforce_put", json=body)

    async def list_agents(self) -> dict[str, Any]:
        return await self._request("GET", "/integrations/klaros/agents", operation="agents_list")

    async def create_lead(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/leads", operation="lead_create", json=body)

    async def update_lead(self, halla_lead_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/leads/{_segment(halla_lead_id)}", operation="lead_update", json=body)

    async def outbound_call(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/calls/outbound", operation="call_outbound", json=body)

    # ----------------------------------------------------------------- plumbing

    def _headers(self) -> dict[str, str]:
        value = f"{self._endpoint.credential_scheme} {self._api_key}" if self._endpoint.credential_scheme else self._api_key
        headers = {self._endpoint.credential_header: value, "Accept": "application/json", "User-Agent": "klaros-halla-client"}
        if self._tenant_id:
            # Halla's rate limiter runs before it authenticates, and only reads this header to tell tenants apart.
            # Without it every Klaros request is counted in the small shared per-IP bucket.
            headers["x-tenant-id"] = self._tenant_id
        return headers

    async def _request(self, method: str, path: str, *, operation: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._endpoint.base_url}{API_PREFIX}{path}"
        idempotent = method in ("GET", "PUT")
        attempts = 1 + (self._max_retries if idempotent else 0)
        last: WorkforceUnavailableError | None = None
        for attempt in range(attempts):
            started = time.monotonic()
            status: int | None = None
            try:
                async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False, transport=self._transport) as client:
                    response = await client.request(method, url, headers=self._headers(), json=json)
                status = response.status_code
                result = self._interpret(response)
                self._log(operation, status, started, attempt, ok=True)
                return result
            except WorkforceUnavailableError as exc:
                last = exc
            except httpx.TimeoutException:
                last = WorkforceUnavailableError("Halla did not answer in time", retryable=True)
            except httpx.RequestError:
                last = WorkforceUnavailableError("Halla could not be reached", retryable=True)
            self._log(operation, status if status is not None else last.status_code, started, attempt, ok=False,
                      retry_after=getattr(last, "retry_after", None))
            if not last.retryable or last.status_code == 429 or attempt == attempts - 1:
                raise last
            await self._sleep(min(2.0, 0.25 * (2**attempt)))
        raise last or WorkforceUnavailableError("Halla request failed")  # pragma: no cover

    @staticmethod
    def _interpret(response: httpx.Response) -> dict[str, Any]:
        code = response.status_code
        if 300 <= code < 400:
            raise WorkforceUnavailableError("Halla answered with a redirect, which Klaros does not follow", status_code=code)
        if code in (401, 403):
            raise WorkforceUnavailableError("Halla rejected the credential", status_code=code)
        if code == 404:
            raise WorkforceUnavailableError("Halla does not know that resource", status_code=code)
        if code == 409:
            raise WorkforceUnavailableError("Halla reported a conflict", status_code=code)
        if code == 429:
            raise WorkforceUnavailableError(
                "Halla is rate limiting Klaros", status_code=code, retryable=True,
                retry_after=_retry_after_seconds(response.headers.get("retry-after")),
            )
        if code >= 500:
            raise WorkforceUnavailableError("Halla had an internal error", status_code=code, retryable=True)
        if code >= 400:
            raise WorkforceUnavailableError("Halla rejected the request", status_code=code)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise WorkforceUnavailableError("Halla returned a malformed response", status_code=code) from exc
        if isinstance(data, list):
            return {"data": data}
        if not isinstance(data, dict):
            raise WorkforceUnavailableError("Halla returned a malformed response", status_code=code)
        return data

    @staticmethod
    def _log(operation: str, status: int | None, started: float, attempt: int, *, ok: bool, retry_after: int | None = None) -> None:
        # Operation, status, latency and Retry-After only — never the credential, a body or a header value.
        logger.info("halla_request", operation=operation, status_code=status, ok=ok, attempt=attempt + 1,
                    latency_ms=int((time.monotonic() - started) * 1000), retry_after=retry_after)


def _retry_after_seconds(value: str | None) -> int | None:
    """Seconds from a `Retry-After` header (delta-seconds form only); None when absent or unusable."""
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 3600 else None


def _segment(value: str) -> str:
    """A path segment built from an id Halla returned — never allow it to change the path."""
    v = str(value)
    if not v or any(c in v for c in "/?#\\ ") or v in (".", ".."):
        raise WorkforceUnavailableError("Halla returned an unusable identifier")
    return v
