"""Meta (Facebook) Graph API client for Marketing API reads (ad accounts, daily campaign spend). Direct httpx, bounded timeout, retries on transient failures, typed errors.

Meta tokens: the code exchange gives a short-lived user token (about an hour); `fb_exchange_token` turns it into a long-lived one (about 60 days). A long-lived token can be
exchanged again while it is still valid to push the expiry out; once it has expired the owner must reconnect. Error messages carry only Meta's own short message, never
a token or the app secret. The Graph version is a constant, overridable with META_GRAPH_VERSION, because Meta retires each version about two years after release.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full request URLs at INFO; keep any query-string credential out of the logs

GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
SCOPES = ("ads_read",)  # /me/adaccounts and the insights reads need only ads_read; business_management would add review work for nothing
TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
REFRESH_WITHIN = timedelta(days=10)  # re-exchange the long-lived token when this close to expiry


class MetaErrorType(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_CONFIGURED = "not_configured"


class MetaAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error_type: MetaErrorType = MetaErrorType.PROVIDER_ERROR) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class MetaToken:
    access_token: str
    expires_at: datetime | None


@dataclass(frozen=True)
class MetaAdAccount:
    id: str  # act_123
    name: str
    account_status: int | None
    currency: str | None


@dataclass(frozen=True)
class DailyCampaignCost:
    campaign_id: str
    campaign_name: str
    day: date
    cost: Decimal


def get_authorization_url(*, app_id: str, redirect_uri: str, state: str, config_id: str | None = None) -> str:
    """A Facebook Login for Business app takes its permissions from a login configuration (`config_id`) and ignores `scope`; a classic app takes `scope`."""
    params = {"client_id": app_id, "redirect_uri": redirect_uri, "state": state, "response_type": "code"}
    if config_id:
        params.update({"config_id": config_id, "override_default_response_type": "true"})
    else:
        params["scope"] = ",".join(SCOPES)
    return f"{DIALOG_URL}?{urlencode(params)}"


def parse_expires_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def token_expiry_state(expires_at: Any, now: datetime | None = None) -> str:
    """'expired' | 'expiring' (inside the refresh window) | 'ok' | 'unknown'."""
    exp = parse_expires_at(expires_at)
    if exp is None:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    if exp <= now:
        return "expired"
    return "expiring" if exp - now <= REFRESH_WITHIN else "ok"


def _safe_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return str(msg)[:300] if msg else f"HTTP {response.status_code}"


class MetaClient:
    def __init__(self) -> None:
        s = get_settings()
        self._app_id = s.META_ADS_APP_ID
        self._app_secret = s.META_ADS_APP_SECRET

    def __repr__(self) -> str:
        return "MetaClient()"

    def _require_app(self) -> None:
        if not (self._app_id and self._app_secret):
            raise MetaAPIError("Meta Ads app is not configured on this platform", error_type=MetaErrorType.NOT_CONFIGURED)

    async def _get(self, url: str, params: dict[str, Any] | None = None, *, token: str | None = None, post: bool = False) -> dict[str, Any]:
        """The access token travels in the Authorization header and the app secret/code in a POST body, so no credential is ever in a URL."""
        headers = {"Authorization": f"Bearer {token}"} if token else None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await (client.post(url, data=params, headers=headers) if post else client.get(url, params=params, headers=headers))
            except httpx.TimeoutException as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise MetaAPIError("Meta request timed out", error_type=MetaErrorType.TIMEOUT) from exc
            except httpx.HTTPError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise MetaAPIError(f"Meta request failed ({type(exc).__name__})", error_type=MetaErrorType.NETWORK_ERROR) from exc
            code = response.status_code
            if code < 400:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise MetaAPIError("Meta returned a non-JSON response", status_code=code) from exc
                return body if isinstance(body, dict) else {}
            if (code == 429 or code >= 500) and attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            message = _safe_message(response)
            if code in (401, 403) or (code == 400 and "token" in message.lower()):
                kind = MetaErrorType.AUTHENTICATION
            elif code == 429:
                kind = MetaErrorType.RATE_LIMIT
            elif code >= 500:
                kind = MetaErrorType.PROVIDER_ERROR
            else:
                kind = MetaErrorType.INVALID_REQUEST
            logger.warning("meta_request_failed", status=code, error_type=str(kind))
            raise MetaAPIError(f"Meta: {message}", status_code=code, error_type=kind)
        raise MetaAPIError("Meta request failed")  # unreachable

    @staticmethod
    def _token(body: dict[str, Any]) -> MetaToken:
        token = body.get("access_token")
        if not token:
            raise MetaAPIError("Meta did not return an access token")
        expires_in = body.get("expires_in")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None
        return MetaToken(access_token=token, expires_at=expires_at)

    async def exchange_code(self, *, code: str, redirect_uri: str) -> MetaToken:
        self._require_app()
        return self._token(await self._get(f"{GRAPH_BASE}/oauth/access_token", {
            "client_id": self._app_id, "client_secret": self._app_secret, "redirect_uri": redirect_uri, "code": code,
        }, post=True))

    async def exchange_for_long_lived(self, *, access_token: str) -> MetaToken:
        """Also used to extend a still-valid long-lived token."""
        self._require_app()
        return self._token(await self._get(f"{GRAPH_BASE}/oauth/access_token", {
            "grant_type": "fb_exchange_token", "client_id": self._app_id, "client_secret": self._app_secret, "fb_exchange_token": access_token,
        }, post=True))

    async def list_ad_accounts(self, *, access_token: str) -> list[MetaAdAccount]:
        body = await self._get(f"{GRAPH_BASE}/me/adaccounts", {"fields": "id,name,account_status,currency", "limit": 100}, token=access_token)
        return [
            MetaAdAccount(id=str(a["id"]), name=a.get("name") or str(a["id"]), account_status=a.get("account_status"), currency=a.get("currency"))
            for a in body.get("data", []) if a.get("id")
        ]

    async def get_ad_account(self, *, access_token: str, ad_account_id: str) -> MetaAdAccount:
        a = await self._get(f"{GRAPH_BASE}/{ad_account_id}", {"fields": "id,name,account_status,currency"}, token=access_token)
        return MetaAdAccount(id=str(a.get("id") or ad_account_id), name=a.get("name") or ad_account_id, account_status=a.get("account_status"), currency=a.get("currency"))

    async def daily_campaign_costs(self, *, access_token: str, ad_account_id: str, start: date, end: date) -> list[DailyCampaignCost]:
        params: dict[str, Any] | None = {
            "level": "campaign", "fields": "campaign_id,campaign_name,spend,date_start", "time_increment": 1, "limit": 500,
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()})
        }
        url = f"{GRAPH_BASE}/{ad_account_id}/insights"
        out: list[DailyCampaignCost] = []
        for _ in range(200):
            body = await self._get(url, params, token=access_token)
            for row in body.get("data", []):
                if not row.get("campaign_id") or not row.get("date_start"):
                    continue
                try:
                    cost = Decimal(str(row.get("spend") or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                except InvalidOperation:
                    continue
                out.append(DailyCampaignCost(str(row["campaign_id"]), row.get("campaign_name") or f"Campaign {row['campaign_id']}", date.fromisoformat(row["date_start"]), cost))
            nxt = (body.get("paging") or {}).get("next")
            if not nxt:
                break
            nxt_url = urlsplit(nxt)  # Meta's next link is a complete URL and embeds the token; drop it and keep sending it as a header
            url = urlunsplit(nxt_url._replace(query=urlencode([(k, v) for k, v in parse_qsl(nxt_url.query) if k != "access_token"])))
            params = None
        return out
