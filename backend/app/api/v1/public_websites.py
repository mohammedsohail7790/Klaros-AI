"""Public, UNAUTHENTICATED read of a tenant's PUBLISHED website (Phase 11,
PHASE_11_WEBSITE_BUILDER_DESIGN.md §15 "Deliberately deferred" /
HARD SCOPE "customer-facing website"). Mirrors app/api/v1/public_leads.py's
trust boundary exactly: the caller supplies a `tenant_id` path parameter,
there is no signed token, and this is treated the same way that endpoint's
own module docstring treats it — "this tenant_id exists" is the entire
trust boundary, since a website's public pages are meant to be public.

Only ever reads `Website.current_published_version_id` — a DRAFT version is
never reachable through this router, regardless of what version_id a caller
might guess, because this code never accepts a version_id from the caller
at all (design doc §15: no public preview-by-guessable-id surface exists).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.db.session import async_session_maker
from app.services.website_renderer import render_website
from app.services.website_service import NoPublishedVersionError, WebsiteNotFoundError, WebsiteService

router = APIRouter(prefix="/public/websites", tags=["public-websites"])

_service = WebsiteService(async_session_maker)


@router.get("/{tenant_id}")
async def get_public_website(tenant_id: uuid.UUID) -> dict[str, Any]:
    website = await _service.get_website(tenant_id)
    if website is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
    try:
        spec = await _service.get_published_specification(tenant_id, website.id)
    except (WebsiteNotFoundError, NoPublishedVersionError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await render_website(tenant_id, spec)
