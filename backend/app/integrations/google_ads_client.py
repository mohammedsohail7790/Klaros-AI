"""Google Ads REST client (direct httpx, no SDK), same shape as `google_calendar_client.py`: bounded timeout, retry with backoff on transient failures, typed errors.

Two kinds of calls: Google's OAuth token endpoint (Klaros's own OAuth app credentials, GOOGLE_ADS_CLIENT_ID/SECRET) and the Google Ads API (the owner's access token plus
Klaros's developer token). Errors carry only Google's own short message, never a token, secret or request body.

The API version is a constant, overridable with the GOOGLE_ADS_API_VERSION env var, because Google retires each version roughly a year after release; when Google sunsets
the pinned one, change the constant (or env var) and re-run the tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

GOOGLE_ADS_API_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v22")
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API_BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"
SCOPE = "https://www.googleapis.com/auth/adwords"
TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3


class GoogleAdsErrorType(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_CONFIGURED = "not_configured"


class GoogleAdsAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error_type: GoogleAdsErrorType = GoogleAdsErrorType.PROVIDER_ERROR) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None


@dataclass(frozen=True)
class DailyCampaignCost:
    campaign_id: str
    campaign_name: str
    status: str
    day: date
    cost: Decimal  # account currency, two decimals


def get_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def micros_to_money(micros: str | int | None) -> Decimal:
    return (Decimal(int(micros or 0)) / Decimal(1_000_000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _safe_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("status")
    else:
        msg = (body.get("error_description") or err) if isinstance(body, dict) else None
    return str(msg)[:300] if msg else f"HTTP {response.status_code}"


class GoogleAdsClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.GOOGLE_ADS_CLIENT_ID
        self._client_secret = s.GOOGLE_ADS_CLIENT_SECRET
        self._developer_token = s.GOOGLE_ADS_DEVELOPER_TOKEN

    def __repr__(self) -> str:
        return "GoogleAdsClient()"

    async def _send(self, method: str, url: str, *, headers: dict[str, str] | None = None, data: dict | None = None, json_body: dict | None = None) -> dict[str, Any]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.request(method, url, headers=headers, data=data, json=json_body)
            except httpx.TimeoutException as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise GoogleAdsAPIError("Google Ads request timed out", error_type=GoogleAdsErrorType.TIMEOUT) from exc
            except httpx.HTTPError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise GoogleAdsAPIError(f"Google Ads request failed ({type(exc).__name__})", error_type=GoogleAdsErrorType.NETWORK_ERROR) from exc
            code = response.status_code
            if code < 400:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise GoogleAdsAPIError("Google returned a non-JSON response", status_code=code) from exc
                return body if isinstance(body, dict) else {}
            if (code == 429 or code >= 500) and attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            message = _safe_message(response)
            if code in (401, 403) or (code == 400 and "invalid_grant" in message.lower()):
                kind = GoogleAdsErrorType.AUTHENTICATION
            elif code == 429:
                kind = GoogleAdsErrorType.RATE_LIMIT
            elif code >= 500:
                kind = GoogleAdsErrorType.PROVIDER_ERROR
            else:
                kind = GoogleAdsErrorType.INVALID_REQUEST
            logger.warning("google_ads_request_failed", status=code, error_type=str(kind))
            raise GoogleAdsAPIError(f"Google Ads: {message}", status_code=code, error_type=kind)
        raise GoogleAdsAPIError("Google Ads request failed")  # unreachable

    def _require_app(self) -> None:
        if not (self._client_id and self._client_secret):
            raise GoogleAdsAPIError("Google Ads OAuth app is not configured on this platform", error_type=GoogleAdsErrorType.NOT_CONFIGURED)

    def _api_headers(self, access_token: str, login_customer_id: str | None = None) -> dict[str, str]:
        if not self._developer_token:
            raise GoogleAdsAPIError("GOOGLE_ADS_DEVELOPER_TOKEN is not configured on this platform", error_type=GoogleAdsErrorType.NOT_CONFIGURED)
        headers = {"Authorization": f"Bearer {access_token}", "developer-token": self._developer_token}
        if login_customer_id:
            headers["login-customer-id"] = login_customer_id
        return headers

    @staticmethod
    def _tokens(body: dict[str, Any]) -> GoogleTokens:
        access = body.get("access_token")
        if not access:
            raise GoogleAdsAPIError("Google did not return an access token")
        return GoogleTokens(access_token=access, refresh_token=body.get("refresh_token"), expires_in=body.get("expires_in"))

    async def exchange_code(self, *, code: str, redirect_uri: str) -> GoogleTokens:
        self._require_app()
        body = await self._send("POST", OAUTH_TOKEN_URL, data={
            "code": code, "client_id": self._client_id, "client_secret": self._client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        })
        return self._tokens(body)

    async def refresh_access_token(self, *, refresh_token: str) -> GoogleTokens:
        self._require_app()
        body = await self._send("POST", OAUTH_TOKEN_URL, data={
            "refresh_token": refresh_token, "client_id": self._client_id, "client_secret": self._client_secret, "grant_type": "refresh_token",
        })
        return self._tokens(body)

    async def list_accessible_customers(self, *, access_token: str) -> list[str]:
        """Customer ids (digits only) the signed-in Google user can reach."""
        body = await self._send("GET", f"{API_BASE}/customers:listAccessibleCustomers", headers=self._api_headers(access_token))
        return [str(n).split("/")[-1] for n in body.get("resourceNames", [])]

    async def daily_campaign_costs(
        self, *, access_token: str, customer_id: str, start: date, end: date, login_customer_id: str | None = None,
    ) -> list[DailyCampaignCost]:
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, metrics.cost_micros, segments.date FROM campaign "
            f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        )
        headers = self._api_headers(access_token, login_customer_id)
        out: list[DailyCampaignCost] = []
        page_token: str | None = None
        for _ in range(200):  # hard bound on pages
            payload: dict[str, Any] = {"query": query}
            if page_token:
                payload["pageToken"] = page_token
            body = await self._send("POST", f"{API_BASE}/customers/{customer_id}/googleAds:search", headers=headers, json_body=payload)
            for row in body.get("results", []):
                campaign, metrics, segments = row.get("campaign", {}), row.get("metrics", {}), row.get("segments", {})
                if not campaign.get("id") or not segments.get("date"):
                    continue
                out.append(DailyCampaignCost(
                    campaign_id=str(campaign["id"]), campaign_name=campaign.get("name") or f"Campaign {campaign['id']}", status=campaign.get("status", ""),
                    day=date.fromisoformat(segments["date"]), cost=micros_to_money(metrics.get("costMicros")),
                ))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return out
