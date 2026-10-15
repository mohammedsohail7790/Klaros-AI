"""`/api/v1/site-hosting` (owner screens) and `/api/v1/public/domains` (what a visitor's browser fetches).

Owner side: upload a finished site (one HTML file or a zip, sent as base64 in JSON), publish or unpublish it, and attach a custom domain after proving control with a DNS TXT record.
Visitor side: a request arrives with only a hostname. A VERIFIED domain resolves to its tenant, and the tenant's published uploaded site is served file by file, exactly as uploaded.
A draft is never reachable from here, and neither is a domain that has not been verified.
"""

import base64
import binascii
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, require_permission
from app.db.session import async_session_maker
from app.models.rbac import Permission
from app.services.site_hosting_service import DomainError, SiteHostingService, SiteUploadError, domain_summary, site_summary

router = APIRouter(prefix="/site-hosting", tags=["site-hosting"])
public_router = APIRouter(prefix="/public/domains", tags=["public-domains"])

_service = SiteHostingService(async_session_maker)


def get_site_hosting_service() -> SiteHostingService:
    return _service


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=4, max_length=14_500_000)


class DomainRequest(BaseModel):
    hostname: str = Field(min_length=3, max_length=253)


@router.get("")
async def overview(current_user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service)) -> dict[str, Any]:
    sites = await service.list_sites(current_user.tenant_id)
    domains = await service.list_domains(current_user.tenant_id)
    return {"tenant_id": str(current_user.tenant_id), "uploads": [site_summary(s) for s in sites], "domains": [domain_summary(d) for d in domains]}


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_site(
    body: UploadRequest, current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service),
) -> dict[str, Any]:
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file could not be read.") from exc
    try:
        site = await service.upload(current_user.tenant_id, body.filename, data, user_id=current_user.id)
    except SiteUploadError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return site_summary(site)


@router.post("/uploads/{site_id}/publish")
async def publish_site(site_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service)) -> dict[str, Any]:
    site = await service.publish(current_user.tenant_id, site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return site_summary(site)


@router.post("/unpublish")
async def unpublish_site(current_user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service)) -> dict[str, Any]:
    return {"unpublished": await service.unpublish(current_user.tenant_id)}


@router.post("/domains", status_code=status.HTTP_201_CREATED)
async def add_domain(body: DomainRequest, current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service)) -> dict[str, Any]:
    try:
        return domain_summary(await service.add_domain(current_user.tenant_id, body.hostname))
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/domains/{domain_id}/verify")
async def verify_domain(domain_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service)) -> dict[str, Any]:
    domain, verified = await service.verify_domain(current_user.tenant_id, domain_id)
    if domain is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return {"verified": verified, "domain": domain_summary(domain)}


@router.delete("/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_domain(domain_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), service: SiteHostingService = Depends(get_site_hosting_service)) -> Response:
    if not await service.remove_domain(current_user.tenant_id, domain_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- public ---------------------------------------------------------------------------------------------------------------------------------------------

@public_router.get("/{hostname}")
async def resolve_host(hostname: str, service: SiteHostingService = Depends(get_site_hosting_service)) -> dict[str, Any]:
    """Which site answers for a hostname: `upload` (the business's own files), `klaros` (the generated Klaros site) or nothing (404)."""
    tenant_id = await service.tenant_for_host(hostname)
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown domain")
    return {"tenant_id": str(tenant_id), "kind": "upload" if await service.has_published_upload(tenant_id) else "klaros"}


@public_router.get("/{hostname}/{path:path}")
async def serve_host_file(hostname: str, path: str, service: SiteHostingService = Depends(get_site_hosting_service)) -> Response:
    tenant_id = await service.tenant_for_host(hostname)
    found = await service.published_file(tenant_id, path) if tenant_id is not None else None
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    content, content_type = found
    return Response(
        content=content, media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=60",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "object-src 'none'; base-uri 'self'; frame-ancestors 'self'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        },
    )
