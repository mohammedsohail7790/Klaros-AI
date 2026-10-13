"""App Builder API (app/builder). Authenticated and tenant-scoped; reads need READ_WEBSITE, building and editing MANAGE_WEBSITE, going live PUBLISH_WEBSITE.

Preview and published files are served by the two unauthenticated routers at the bottom (a signed short-lived token for drafts; tenant + slug for the
version the owner published), always as inert static files under a strict Content-Security-Policy.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, require_permission
from app.builder import preview as pv
from app.builder.orchestrator import BuilderOrchestrator
from app.builder.store import BuilderError
from app.builder.workspace import WorkspaceError, normalise_path
from app.core.config import get_settings
from app.core.rate_limit import rate_limit, tenant_and_ip_key
from app.db.session import async_session_maker
from app.models.rbac import Permission

router = APIRouter(prefix="/builder", tags=["builder"])
preview_router = APIRouter(prefix="/builder-preview", tags=["builder-preview"])
site_router = APIRouter(prefix="/public/sites", tags=["public-sites"])

_orchestrator: BuilderOrchestrator | None = None


def get_orchestrator() -> BuilderOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = BuilderOrchestrator(async_session_maker)
    return _orchestrator


def _err(exc: BuilderError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _project(p) -> dict[str, Any]:
    spec = p.spec or {}
    return {
        "id": str(p.id), "name": p.name, "slug": p.slug, "status": p.status,
        "current_version_id": str(p.current_version_id) if p.current_version_id else None,
        "published_version_id": str(p.published_version_id) if p.published_version_id else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "routes": [{"path": r["path"], "title": r["title"], "nav": r.get("nav", True), "detail": r["path"].endswith("[slug]")} for r in spec.get("routes", [])],
        "features": spec.get("features", []), "assumptions": spec.get("assumptions", []),
        "design": spec.get("design"), "locked_paths": p.locked_paths or [],
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _run(r) -> dict[str, Any]:
    return {
        "id": str(r.id), "project_id": str(r.project_id), "kind": r.kind, "state": r.state, "source": r.source, "progress": r.progress or [], "error": r.error,
        "validation": r.validation, "usage": r.usage or {}, "repair_attempts": r.repair_attempts, "cancel_requested": r.cancel_requested,
        "result_version_id": str(r.result_version_id) if r.result_version_id else None,
        "started_at": r.started_at.isoformat() if r.started_at else None, "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def _version(v) -> dict[str, Any]:
    return {"id": str(v.id), "number": v.number, "kind": v.kind, "summary": v.summary, "prompt": v.prompt, "created_at": v.created_at.isoformat(), "changes": v.changes, "validation": {k: v.validation.get(k) for k in ("ok", "errors", "warnings", "checks", "stats")} if v.validation else {}}


class CreateProject(BaseModel):
    prompt: str = Field(min_length=10, max_length=6000)
    name: str | None = Field(default=None, max_length=80)


class Message(BaseModel):
    message: str = Field(min_length=2, max_length=3000)


class FileBody(BaseModel):
    content: str = Field(max_length=300_000)


class PublishBody(BaseModel):
    acknowledge_examples: bool = False


_limit = rate_limit("builder_start", limit_setting="RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE", window_seconds=60, key_func=tenant_and_ip_key)


@router.post("/projects", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(_limit)])
async def create_project(body: CreateProject, user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    name = (body.name or "").strip() or "My Project"
    try:
        project = await o.store.create_project(user.tenant_id, name=name, prompt=body.prompt, created_by=user.id)
        run = await o.start_create(user.tenant_id, user.id, project.id, body.prompt, body.name.strip() if body.name and body.name.strip() else None)
    except BuilderError as exc:
        raise _err(exc) from exc
    return {"project": _project(await o.store.get_project(user.tenant_id, project.id)), "run": _run(run)}


@router.get("/projects")
async def list_projects(user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> list[dict[str, Any]]:
    await o.store.recover_stale_runs(user.tenant_id)
    return [_project(p) for p in await o.store.list_projects(user.tenant_id)]


@router.get("/projects/{project_id}")
async def get_project(project_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        await o.store.recover_stale_runs(user.tenant_id)
        p = await o.store.get_project(user.tenant_id, project_id)
        runs = await o.store.list_runs(user.tenant_id, project_id, limit=1)
    except BuilderError as exc:
        raise _err(exc) from exc
    return {"project": _project(p), "latest_run": _run(runs[0]) if runs else None}


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> Response:
    try:
        await o.store.delete_project(user.tenant_id, project_id)
    except BuilderError as exc:
        raise _err(exc) from exc
    return Response(status_code=204)


@router.get("/projects/{project_id}/runs/{run_id}")
async def get_run(project_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        await o.store.recover_stale_runs(user.tenant_id)
        r = await o.store.get_run(user.tenant_id, run_id)
    except BuilderError as exc:
        raise _err(exc) from exc
    if r.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run(r)


@router.post("/projects/{project_id}/runs/{run_id}/cancel")
async def cancel_run(project_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        r = await o.store.get_run(user.tenant_id, run_id)
        if r.project_id != project_id:
            raise BuilderError("Run not found", 404)
        return _run(await o.cancel(user.tenant_id, run_id))
    except BuilderError as exc:
        raise _err(exc) from exc


@router.post("/projects/{project_id}/messages", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(_limit)])
async def send_message(project_id: uuid.UUID, body: Message, user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        run = await o.start_edit(user.tenant_id, user.id, project_id, body.message.strip())
    except BuilderError as exc:
        raise _err(exc) from exc
    return {"run": _run(run)}


@router.get("/projects/{project_id}/messages")
async def list_messages(project_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> list[dict[str, Any]]:
    try:
        await o.store.get_project(user.tenant_id, project_id)
    except BuilderError as exc:
        raise _err(exc) from exc
    return [{"id": str(m.id), "role": m.role, "content": m.content, "run_id": str(m.run_id) if m.run_id else None, "created_at": m.created_at.isoformat()} for m in await o.store.list_messages(user.tenant_id, project_id)]


@router.get("/projects/{project_id}/versions")
async def list_versions(project_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> list[dict[str, Any]]:
    try:
        await o.store.get_project(user.tenant_id, project_id)
    except BuilderError as exc:
        raise _err(exc) from exc
    return [_version(v) for v in await o.store.list_versions(user.tenant_id, project_id)]


@router.post("/projects/{project_id}/versions/{version_id}/restore", status_code=status.HTTP_201_CREATED)
async def restore_version(project_id: uuid.UUID, version_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        return _version(await o.rollback(user.tenant_id, user.id, project_id, version_id))
    except BuilderError as exc:
        raise _err(exc) from exc


async def _version_or_current(o, user, project_id, version_id: uuid.UUID | None):
    p = await o.store.get_project(user.tenant_id, project_id)
    vid = version_id or p.current_version_id
    if vid is None:
        raise BuilderError("Nothing has been built yet.", 404)
    await o.store.get_version(user.tenant_id, project_id, vid)
    return p, vid


@router.get("/projects/{project_id}/files")
async def list_files(project_id: uuid.UUID, version_id: uuid.UUID | None = None, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        _p, vid = await _version_or_current(o, user, project_id, version_id)
        v = await o.store.get_version(user.tenant_id, project_id, vid)
    except BuilderError as exc:
        raise _err(exc) from exc
    return {"version_id": str(vid), "files": v.manifest, "validation": v.validation}


@router.get("/projects/{project_id}/files/{path:path}")
async def read_file(project_id: uuid.UUID, path: str, version_id: uuid.UUID | None = None, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        _p, vid = await _version_or_current(o, user, project_id, version_id)
        safe = normalise_path(path)
        content = await o.store.load_file(user.tenant_id, project_id, vid, safe)
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BuilderError as exc:
        raise _err(exc) from exc
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {"path": safe, "content": content}


@router.put("/projects/{project_id}/files/{path:path}")
async def write_file(project_id: uuid.UUID, path: str, body: FileBody, user: CurrentUser = Depends(require_permission(Permission.MANAGE_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        return _version(await o.save_file(user.tenant_id, user.id, project_id, path, body.content))
    except BuilderError as exc:
        raise _err(exc) from exc


@router.post("/projects/{project_id}/preview-token")
async def preview_token(project_id: uuid.UUID, request: Request, version_id: uuid.UUID | None = None, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        _p, vid = await _version_or_current(o, user, project_id, version_id)
    except BuilderError as exc:
        raise _err(exc) from exc
    token, exp = pv.sign_preview(user.tenant_id, project_id, vid)
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/api/v1/builder-preview/{token}/", "expires_at": exp, "version_id": str(vid)}


@router.post("/projects/{project_id}/publish")
async def publish(project_id: uuid.UUID, request: Request, body: PublishBody, user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        await o.publish(user.tenant_id, project_id, acknowledge_examples=body.acknowledge_examples)
        p = await o.store.get_project(user.tenant_id, project_id)
    except BuilderError as exc:
        raise _err(exc) from exc
    base = (get_settings().BUILDER_PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    return {"project": _project(p), "url": f"{base}/api/v1/public/sites/{user.tenant_id}/{p.slug}/"}


@router.delete("/projects/{project_id}/publish")
async def unpublish(project_id: uuid.UUID, user: CurrentUser = Depends(require_permission(Permission.PUBLISH_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> dict[str, Any]:
    try:
        await o.store.get_project(user.tenant_id, project_id)
        await o.store.publish(user.tenant_id, project_id, None)
        return {"project": _project(await o.store.get_project(user.tenant_id, project_id))}
    except BuilderError as exc:
        raise _err(exc) from exc


@router.get("/projects/{project_id}/export")
async def export(project_id: uuid.UUID, version_id: uuid.UUID | None = None, user: CurrentUser = Depends(require_permission(Permission.READ_WEBSITE)), o: BuilderOrchestrator = Depends(get_orchestrator)) -> Response:
    try:
        p, vid = await _version_or_current(o, user, project_id, version_id)
        data = await o.export_zip(user.tenant_id, project_id, vid)
    except BuilderError as exc:
        raise _err(exc) from exc
    return Response(content=data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{p.slug}.zip"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- unauthenticated static serving
async def _serve(tenant_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID, raw_path: str, *, preview: bool, endpoint: str | None) -> Response:
    o = get_orchestrator()
    path = pv.resolve_request_path(raw_path)
    headers = pv.security_headers(preview=preview, enquiry_origin=_origin(endpoint))
    if path is None:
        return Response("Not found", status_code=404, media_type="text/plain", headers=headers)
    content = await o.store.load_file(tenant_id, project_id, version_id, path)
    status_code = 200
    if content is None:
        missing = await o.store.load_file(tenant_id, project_id, version_id, "404.html")
        if missing is None or not path.endswith(".html"):
            return Response("Not found", status_code=404, media_type="text/plain", headers=headers)
        content, path, status_code = missing, "404.html", 404
    if path.endswith(".html"):
        content = pv.inject_enquiry(content, endpoint)
    return Response(content=content, status_code=status_code, media_type=pv.mime_for(path), headers=headers)


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else None


@preview_router.get("/{token}/{path:path}")
async def serve_preview(token: str, path: str = "") -> Response:
    claims = pv.verify_preview(token)
    if claims is None:
        return Response("This preview link has expired or is not valid.", status_code=403, media_type="text/plain", headers={"Cache-Control": "no-store"})
    tenant_id, project_id, version_id = claims
    return await _serve(tenant_id, project_id, version_id, path, preview=True, endpoint=None)


@site_router.get("/{tenant_id}/{slug}/{path:path}")
async def serve_published(tenant_id: uuid.UUID, slug: str, request: Request, path: str = "") -> Response:
    o = get_orchestrator()
    project = await o.store.get_published(tenant_id, slug)
    if project is None or project.published_version_id is None:
        return Response("Not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "no-store"})
    base = (get_settings().BUILDER_PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    return await _serve(tenant_id, project.id, project.published_version_id, path, preview=False, endpoint=f"{base}/api/v1/public/leads/{tenant_id}")
