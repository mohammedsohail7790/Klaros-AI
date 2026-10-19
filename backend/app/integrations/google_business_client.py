"""Google Business Profile REST client (direct httpx, no SDK), same shape as `google_ads_client.py`: bounded timeout, retry with backoff on transient failures, typed errors.

Two kinds of calls: Google's OAuth token endpoint (Klaros's own OAuth app, GOOGLE_BUSINESS_CLIENT_ID/SECRET) and the Business Profile APIs (the owner's access token):
  * Account Management API (v1)  accounts the signed-in user manages
  * Business Information API (v1) the locations (listings) of an account
  * Business Profile v4 reviews endpoint (reviews are still served only by v4)
Google gates all of these behind an access request form: a Cloud project without approval gets 403 (or 429 with quota limit 0) on every call, which is mapped to
`ACCESS_NOT_APPROVED` with a message that says so. Errors carry only Google's short message, never a token, secret or request body.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/business.manage"
ACCOUNT_MANAGEMENT_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFORMATION_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1"
REVIEWS_BASE = "https://mybusiness.googleapis.com/v4"
LOCATION_READ_MASK = "name,title,storefrontAddress"
TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
MAX_PAGES = 100  # hard bound on pagination loops
REVIEWS_PAGE_SIZE = 50  # Google's maximum for reviews
LOCATIONS_PAGE_SIZE = 100  # Google's maximum for locations

STAR_RATING = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

NOT_APPROVED_MESSAGE = (
    "Google has not approved this Google Cloud project for the Business Profile APIs yet, or the APIs are not enabled. Submit Google's Business Profile API access request "
    "form for the project, enable the three Business Profile APIs, wait for approval, then try again."
)


class GoogleBusinessErrorType(StrEnum):
    AUTHENTICATION = "authentication"
    ACCESS_NOT_APPROVED = "access_not_approved"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    NETWORK_ERROR = "network_error"
    INVALID_REQUEST = "invalid_request"
    NOT_CONFIGURED = "not_configured"


class GoogleBusinessAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, error_type: GoogleBusinessErrorType = GoogleBusinessErrorType.PROVIDER_ERROR) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None


@dataclass(frozen=True)
class GbpAccount:
    name: str  # "accounts/123"
    account_name: str | None
    type: str | None


@dataclass(frozen=True)
class GbpLocation:
    name: str  # "locations/456" (Business Information API form)
    title: str
    address: str | None
    city: str | None
    state: str | None


@dataclass(frozen=True)
class GbpReview:
    review_id: str
    rating: int | None  # 1..5, None when Google sent no recognised starRating
    comment: str | None
    reviewer: str | None
    create_time: str | None
    update_time: str | None
    reply_comment: str | None


def get_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def location_path(account_name: str, location_name: str) -> str:
    """Full v4 resource path 'accounts/A/locations/L' from an account and a Business Information location name ('locations/L' or already full)."""
    return f"{account_name.strip('/')}/locations/{location_name.strip('/').split('/')[-1]}"


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


class GoogleBusinessClient:
    def __init__(self) -> None:
        s = get_settings()
        self._client_id = s.GOOGLE_BUSINESS_CLIENT_ID
        self._client_secret = s.GOOGLE_BUSINESS_CLIENT_SECRET

    def __repr__(self) -> str:
        return "GoogleBusinessClient()"

    async def _send(
        self, method: str, url: str, *, headers: dict[str, str] | None = None, data: dict | None = None, params: dict | None = None, api: bool = True,
    ) -> dict[str, Any]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    response = await client.request(method, url, headers=headers, data=data, params=params)
            except httpx.TimeoutException as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise GoogleBusinessAPIError("Google Business Profile request timed out", error_type=GoogleBusinessErrorType.TIMEOUT) from exc
            except httpx.HTTPError as exc:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise GoogleBusinessAPIError(f"Google Business Profile request failed ({type(exc).__name__})", error_type=GoogleBusinessErrorType.NETWORK_ERROR) from exc
            code = response.status_code
            if code < 400:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise GoogleBusinessAPIError("Google returned a non-JSON response", status_code=code) from exc
                return body if isinstance(body, dict) else {}
            message = _safe_message(response)
            quota_zero = code == 429 and "limit: 0" in message.lower()  # a project without approval has quota 0: retrying cannot help
            if ((code == 429 and not quota_zero) or code >= 500) and attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            if api and (code == 403 or quota_zero):
                kind, shown = GoogleBusinessErrorType.ACCESS_NOT_APPROVED, NOT_APPROVED_MESSAGE
            elif code == 401 or (code == 400 and "invalid_grant" in message.lower()):
                kind, shown = GoogleBusinessErrorType.AUTHENTICATION, f"Google Business Profile: {message}"
            elif code == 429:
                kind, shown = GoogleBusinessErrorType.RATE_LIMIT, f"Google Business Profile: {message}"
            elif code >= 500:
                kind, shown = GoogleBusinessErrorType.PROVIDER_ERROR, f"Google Business Profile: {message}"
            else:
                kind, shown = GoogleBusinessErrorType.INVALID_REQUEST, f"Google Business Profile: {message}"
            logger.warning("google_business_request_failed", status=code, error_type=str(kind))
            raise GoogleBusinessAPIError(shown, status_code=code, error_type=kind)
        raise GoogleBusinessAPIError("Google Business Profile request failed")  # unreachable

    def _require_app(self) -> None:
        if not (self._client_id and self._client_secret):
            raise GoogleBusinessAPIError("Google Business Profile OAuth app is not configured on this platform", error_type=GoogleBusinessErrorType.NOT_CONFIGURED)

    @staticmethod
    def _tokens(body: dict[str, Any]) -> GoogleTokens:
        access = body.get("access_token")
        if not access:
            raise GoogleBusinessAPIError("Google did not return an access token")
        return GoogleTokens(access_token=access, refresh_token=body.get("refresh_token"), expires_in=body.get("expires_in"))

    async def exchange_code(self, *, code: str, redirect_uri: str) -> GoogleTokens:
        self._require_app()
        body = await self._send("POST", OAUTH_TOKEN_URL, api=False, data={
            "code": code, "client_id": self._client_id, "client_secret": self._client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        })
        return self._tokens(body)

    async def refresh_access_token(self, *, refresh_token: str) -> GoogleTokens:
        self._require_app()
        body = await self._send("POST", OAUTH_TOKEN_URL, api=False, data={
            "refresh_token": refresh_token, "client_id": self._client_id, "client_secret": self._client_secret, "grant_type": "refresh_token",
        })
        return self._tokens(body)

    @staticmethod
    def _bearer(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    async def list_accounts(self, *, access_token: str) -> list[GbpAccount]:
        out: list[GbpAccount] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"pageSize": 20}
            if page_token:
                params["pageToken"] = page_token
            body = await self._send("GET", f"{ACCOUNT_MANAGEMENT_BASE}/accounts", headers=self._bearer(access_token), params=params)
            for a in body.get("accounts", []):
                if a.get("name"):
                    out.append(GbpAccount(name=a["name"], account_name=a.get("accountName"), type=a.get("type")))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return out

    async def list_locations(self, *, access_token: str, account_name: str) -> list[GbpLocation]:
        out: list[GbpLocation] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"readMask": LOCATION_READ_MASK, "pageSize": LOCATIONS_PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            body = await self._send("GET", f"{BUSINESS_INFORMATION_BASE}/{account_name}/locations", headers=self._bearer(access_token), params=params)
            for loc in body.get("locations", []):
                if not loc.get("name"):
                    continue
                addr = loc.get("storefrontAddress") or {}
                lines = addr.get("addressLines") or []
                out.append(GbpLocation(
                    name=loc["name"], title=loc.get("title") or loc["name"], address=", ".join(lines)[:255] or None,
                    city=addr.get("locality"), state=addr.get("administrativeArea"),
                ))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return out

    async def list_reviews(self, *, access_token: str, account_name: str, location_name: str) -> list[GbpReview]:
        """Every review of one location (newest first, as Google returns them), following nextPageToken."""
        loc = location_name.strip("/").split("/")[-1]
        url = f"{REVIEWS_BASE}/{account_name.strip('/')}/locations/{quote(loc, safe='')}/reviews"
        out: list[GbpReview] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"pageSize": REVIEWS_PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            body = await self._send("GET", url, headers=self._bearer(access_token), params=params)
            for r in body.get("reviews", []):
                review_id = r.get("reviewId") or (r.get("name") or "").split("/")[-1]
                if not review_id:
                    continue
                reply = r.get("reviewReply") or {}
                out.append(GbpReview(
                    review_id=review_id, rating=STAR_RATING.get(str(r.get("starRating", "")).upper()), comment=r.get("comment"),
                    reviewer=(r.get("reviewer") or {}).get("displayName"), create_time=r.get("createTime"), update_time=r.get("updateTime"),
                    reply_comment=reply.get("comment"),
                ))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return out
