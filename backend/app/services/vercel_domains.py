"""Registers a business's domain with the frontend host (Vercel) so the owner never has to open Vercel.

Configured by VERCEL_API_TOKEN and VERCEL_PROJECT_ID (and VERCEL_TEAM_ID for a team project). With them missing, `configured()` is False and nothing here is called: the owner is shown the manual step instead.
Every method returns a plain result and never raises into the caller; a problem comes back as an error message to show and retry.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
_API = "https://api.vercel.com"


@dataclass
class EdgeState:
    registered: bool
    live: bool = False  # DNS points at the host and the certificate can be issued
    error: str | None = None


class VercelDomains:
    def configured(self) -> bool:
        s = get_settings()
        return bool(s.VERCEL_API_TOKEN and s.VERCEL_PROJECT_ID)

    def _params(self) -> dict[str, str]:
        team = get_settings().VERCEL_TEAM_ID
        return {"teamId": team} if team else {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {get_settings().VERCEL_API_TOKEN}"}

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.request(method, f"{_API}{path}", headers=self._headers(), params=self._params(), **kw)

    async def add(self, hostname: str) -> EdgeState:
        try:
            r = await self._call("POST", f"/v10/projects/{get_settings().VERCEL_PROJECT_ID}/domains", json={"name": hostname})
            if r.status_code in (200, 201):
                return EdgeState(registered=True)
            code = (r.json().get("error") or {}).get("code", "") if r.headers.get("content-type", "").startswith("application/json") else ""
            if code == "domain_already_in_use" or r.status_code == 409:
                return EdgeState(registered=True) if await self._on_this_project(hostname) else EdgeState(registered=False, error="That domain is already in use on another Vercel project.")
            return EdgeState(registered=False, error=f"The host refused the domain ({r.status_code}).")
        except Exception as exc:  # network trouble must not break adding a domain
            logger.warning("vercel_add_domain_failed", hostname=hostname, error=type(exc).__name__)
            return EdgeState(registered=False, error="Could not reach the host just now; this will be retried.")

    async def _on_this_project(self, hostname: str) -> bool:
        r = await self._call("GET", f"/v9/projects/{get_settings().VERCEL_PROJECT_ID}/domains/{hostname}")
        return r.status_code == 200

    async def state(self, hostname: str) -> EdgeState:
        try:
            r = await self._call("GET", f"/v9/projects/{get_settings().VERCEL_PROJECT_ID}/domains/{hostname}")
            if r.status_code == 404:
                return EdgeState(registered=False)
            if r.status_code != 200:
                return EdgeState(registered=True, error=f"Could not read the domain's status ({r.status_code}).")
            cfg = await self._call("GET", f"/v6/domains/{hostname}/config")
            misconfigured = bool(cfg.json().get("misconfigured", True)) if cfg.status_code == 200 else True
            return EdgeState(registered=True, live=bool(r.json().get("verified", True)) and not misconfigured)
        except Exception as exc:
            logger.warning("vercel_domain_state_failed", hostname=hostname, error=type(exc).__name__)
            return EdgeState(registered=True, error="Could not check the domain's status just now.")

    async def remove(self, hostname: str) -> None:
        try:
            await self._call("DELETE", f"/v9/projects/{get_settings().VERCEL_PROJECT_ID}/domains/{hostname}")
        except Exception as exc:
            logger.warning("vercel_remove_domain_failed", hostname=hostname, error=type(exc).__name__)
