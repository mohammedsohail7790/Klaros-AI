"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md): the authenticated,
tenant-scoped Website Builder API.

Routes:
  POST /websites/generate                             generate a new DRAFT version from the tenant's ACTIVE blueprint
  GET  /websites                                       the tenant's Website (single-site-per-tenant, design doc §1)
  GET  /websites/{website_id}/versions                 list versions
  GET  /websites/{website_id}/versions/{version_id}/preview   render a DRAFT or PUBLISHED version (design doc §Phase 8)
  POST /websites/{website_id}/versions/{version_id}/pages     add a page to a DRAFT version
  PUT  /websites/{website_id}/versions/{version_id}/pages/{slug}/sections   replace a page's sections
  PUT  /websites/{website_id}/versions/{version_id}/theme     update theme tokens
  POST /websites/{website_id}/versions/{version_id}/new-draft clone a version into a fresh editable DRAFT
  POST /websites/{website_id}/versions/{version_id}/publish   publish (design doc §Phase 9)
  POST /websites/{website_id}/unpublish                        unpublish (design doc §14)

Reads gated by READ_WEBSITE; draft edits/generation by MANAGE_WEBSITE;
publish/unpublish by PUBLISH_WEBSITE (app/models/rbac.py's own
documentation of why these are three separate permissions, not the usual
two-way split). Preview requires only READ_WEBSITE — there is no separate
public/token-based preview mechanism (design doc §18's explicit deferral).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps_websites import get_website_generation_service, get_website_service
from app.models.rbac import Permission
from app.schemas.website_specification import PageSpec, SectionSpec, ThemeTokens, UnsafeContentError
from app.services.website_generation_service import NoActiveBlueprintError, WebsiteGenerationService
from app.services.website_renderer import render_website
from app.services.website_service import (
    NoPublishedVersionError,
    PageNotFoundError,
    WebsiteNotFoundError,
    WebsiteService,
    WebsiteVersionImmutableError,
    WebsiteVersionNotFoundError,
)

router = APIRouter(prefix="/websites", tags=["websites"])


def _version_to_dict(v) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "website_id": str(v.website_id),
        "version": v.version,
        "status": v.status,
        "theme": v.theme,
        "navigation": v.navigation,
        "seo_defaults": v.seo_defaults,
        "generation_provenance": v.generation_provenance,
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "created_at": v.created_at.isoformat(),
    }


def _website_to_dict(w) -> dict[str, Any]:
    return {
        "id": str(w.id),
        "name": w.name,
        "slug": w.slug,
        "blueprint_id": str(w.blueprint_id) if w.blueprint_id else None,
        "current_published_version_id": (
            str(w.current_published_version_id) if w.current_published_version_id else None
        ),
    }


@router.post("/generate")
async def generate_website(
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)),
    generation_service: WebsiteGenerationService = Depends(get_website_generation_service),
    website_service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        result = await generation_service.generate_from_active_blueprint(
            current_user.tenant_id, actor_id=current_user.id
        )
    except NoActiveBlueprintError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    existing = await website_service.get_website(current_user.tenant_id)
    website, version = await website_service.create_website_with_specification(
        current_user.tenant_id,
        name=existing.name if existing else result.specification.pages[0].title if result.specification.pages else "Website",
        slug=existing.slug if existing else "site",
        blueprint_id=existing.blueprint_id if existing else None,
        specification=result.specification,
        provenance=result.provenance,
        created_by=current_user.id,
    )
    return {"website": _website_to_dict(website), "version": _version_to_dict(version), "ai_used": result.ai_used}


@router.get("")
async def get_website(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any] | None:
    website = await service.get_website(current_user.tenant_id)
    if website is None:
        return None
    return _website_to_dict(website)


@router.get("/{website_id}/versions")
async def list_versions(
    website_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> list[dict[str, Any]]:
    versions = await service.list_versions(current_user.tenant_id, website_id)
    return [_version_to_dict(v) for v in versions]


@router.get("/{website_id}/versions/{version_id}/preview")
async def preview_version(
    website_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        spec = await service.load_specification(current_user.tenant_id, version_id)
    except WebsiteVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    rendered = await render_website(current_user.tenant_id, spec)
    # Editor-only overlay (this authenticated preview endpoint is never used
    # by the public read path — app/api/v1/public_websites.py calls
    # render_website() directly on its own spec load, so this never reaches
    # unauthenticated output): `render_website`/`render_section` deliberately
    # never put `data_source` on a rendered section (shared contract with
    # the public renderer, app/services/website_renderer.py). But the
    # Website Builder editor (frontend/components/website/SectionEditor.tsx)
    # needs the saved provider_key back to rehydrate its form after a
    # save/reload, and this is the only endpoint it has to ask. `spec.pages`
    # and `rendered["pages"]` are built from the exact same ordered
    # `page.sections` list (website_renderer.render_page), so a positional
    # zip is safe here without re-touching the renderer itself.
    for page_spec, page_rendered in zip(spec.pages, rendered["pages"], strict=True):
        for section_spec, section_rendered in zip(page_spec.sections, page_rendered["sections"], strict=True):
            section_rendered["data_source"] = (
                section_spec.data_source.model_dump() if section_spec.data_source is not None else None
            )
    return rendered


class AddPageRequest(BaseModel):
    slug: str
    title: str
    sections: list[dict] = []


@router.post("/{website_id}/versions/{version_id}/pages")
async def add_page(
    website_id: uuid.UUID,
    version_id: uuid.UUID,
    body: AddPageRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        page_spec = PageSpec(
            slug=body.slug, title=body.title, sections=[SectionSpec.model_validate(s) for s in body.sections]
        )
    except (ValidationError, UnsafeContentError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        page = await service.add_page(current_user.tenant_id, version_id, page_spec, updated_by=current_user.id)
    except WebsiteVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WebsiteVersionImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"id": str(page.id), "slug": page.slug, "title": page.title}


class ReplaceSectionsRequest(BaseModel):
    sections: list[dict]


@router.put("/{website_id}/versions/{version_id}/pages/{slug}/sections")
async def replace_page_sections(
    website_id: uuid.UUID,
    version_id: uuid.UUID,
    slug: str,
    body: ReplaceSectionsRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        sections = [SectionSpec.model_validate(s) for s in body.sections]
    except (ValidationError, UnsafeContentError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        page = await service.replace_page_sections(
            current_user.tenant_id, version_id, slug, sections, updated_by=current_user.id
        )
    except (WebsiteVersionNotFoundError, PageNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WebsiteVersionImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"id": str(page.id), "slug": page.slug, "section_count": len(sections)}


@router.put("/{website_id}/versions/{version_id}/theme")
async def update_theme(
    website_id: uuid.UUID,
    version_id: uuid.UUID,
    body: dict,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        theme = ThemeTokens.model_validate(body)
    except (ValidationError, UnsafeContentError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        version = await service.update_theme(current_user.tenant_id, version_id, theme, updated_by=current_user.id)
    except WebsiteVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WebsiteVersionImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _version_to_dict(version)


@router.post("/{website_id}/versions/{version_id}/new-draft")
async def new_draft_from_version(
    website_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        version = await service.create_draft_from_version(
            current_user.tenant_id, website_id, version_id, created_by=current_user.id
        )
    except (WebsiteNotFoundError, WebsiteVersionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _version_to_dict(version)


@router.post("/{website_id}/versions/{version_id}/publish")
async def publish_version(
    website_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        version = await service.publish_version(
            current_user.tenant_id, website_id, version_id, published_by=current_user.id
        )
    except (WebsiteNotFoundError, WebsiteVersionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WebsiteVersionImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - spec re-validation failure (design doc §14)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _version_to_dict(version)


@router.post("/{website_id}/unpublish")
async def unpublish_website(
    website_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)),
    service: WebsiteService = Depends(get_website_service),
) -> dict[str, Any]:
    try:
        website = await service.unpublish(current_user.tenant_id, website_id, unpublished_by=current_user.id)
    except WebsiteNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoPublishedVersionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _website_to_dict(website)
