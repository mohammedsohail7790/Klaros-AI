"""Tenant-scoped persistence for the builder. Every method takes the tenant and stamps it on its session first, so under
PostgreSQL row-level security a project, version or run of another tenant is simply not visible; the explicit tenant_id
filters are a second line, not the only one."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.builder.workspace import Workspace, sha
from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.builder import BuilderFile, BuilderMessage, BuilderProject, BuilderRun, BuilderVersion

ACTIVE_STATES = ("queued", "planning", "generating", "validating", "repairing")
TERMINAL_STATES = ("ready", "failed", "cancelled")


class BuilderError(Exception):
    """A refusal the caller may show to the person (quota, conflict, not found)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return s or "project"


class BuilderStore:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def _session(self, tenant_id: uuid.UUID):
        session = self._sf()
        await session.__aenter__()
        await set_tenant_context(session, tenant_id)
        return session

    # ---------------------------------------------------------------- projects
    async def create_project(self, tenant_id: uuid.UUID, *, name: str, prompt: str, created_by: uuid.UUID | None) -> BuilderProject:
        s = await self._session(tenant_id)
        try:
            count = (await s.execute(select(func.count()).select_from(BuilderProject).where(BuilderProject.tenant_id == tenant_id))).scalar_one()
            if count >= get_settings().BUILDER_MAX_PROJECTS_PER_TENANT:
                raise BuilderError(f"You can have up to {get_settings().BUILDER_MAX_PROJECTS_PER_TENANT} projects. Delete one to start another.", 409)
            base = slugify(name)
            slug = base
            for _ in range(5):
                taken = (await s.execute(select(BuilderProject.id).where(BuilderProject.tenant_id == tenant_id, BuilderProject.slug == slug))).first()
                if not taken:
                    break
                slug = f"{base}-{secrets.token_hex(2)}"
            p = BuilderProject(tenant_id=tenant_id, name=name[:80], slug=slug, status="new", initial_prompt=prompt, locked_paths=[], created_by=created_by)
            s.add(p)
            await s.commit()
            await s.refresh(p)
            return p
        finally:
            await s.__aexit__(None, None, None)

    async def get_project(self, tenant_id: uuid.UUID, project_id: uuid.UUID) -> BuilderProject:
        s = await self._session(tenant_id)
        try:
            p = (await s.execute(select(BuilderProject).where(BuilderProject.id == project_id, BuilderProject.tenant_id == tenant_id))).scalar_one_or_none()
            if p is None:
                raise BuilderError("Project not found", 404)
            return p
        finally:
            await s.__aexit__(None, None, None)

    async def get_published(self, tenant_id: uuid.UUID, slug: str) -> BuilderProject | None:
        s = await self._session(tenant_id)
        try:
            return (await s.execute(select(BuilderProject).where(BuilderProject.tenant_id == tenant_id, BuilderProject.slug == slug, BuilderProject.published_version_id.is_not(None)))).scalar_one_or_none()
        finally:
            await s.__aexit__(None, None, None)

    async def list_projects(self, tenant_id: uuid.UUID) -> list[BuilderProject]:
        s = await self._session(tenant_id)
        try:
            return list((await s.execute(select(BuilderProject).where(BuilderProject.tenant_id == tenant_id).order_by(BuilderProject.updated_at.desc()))).scalars())
        finally:
            await s.__aexit__(None, None, None)

    async def update_project(self, tenant_id: uuid.UUID, project_id: uuid.UUID, **fields) -> None:
        s = await self._session(tenant_id)
        try:
            await s.execute(update(BuilderProject).where(BuilderProject.id == project_id, BuilderProject.tenant_id == tenant_id).values(**fields))
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    async def delete_project(self, tenant_id: uuid.UUID, project_id: uuid.UUID) -> None:
        s = await self._session(tenant_id)
        try:
            p = (await s.execute(select(BuilderProject).where(BuilderProject.id == project_id, BuilderProject.tenant_id == tenant_id))).scalar_one_or_none()
            if p is None:
                raise BuilderError("Project not found", 404)
            active = (await s.execute(select(func.count()).select_from(BuilderRun).where(BuilderRun.project_id == project_id, BuilderRun.state.in_(ACTIVE_STATES)))).scalar_one()
            if active:
                raise BuilderError("A build is still running for this project. Cancel it first.", 409)
            for model in (BuilderFile, BuilderMessage, BuilderRun):
                await s.execute(delete(model).where(model.project_id == project_id, model.tenant_id == tenant_id))
            await s.execute(delete(BuilderVersion).where(BuilderVersion.project_id == project_id, BuilderVersion.tenant_id == tenant_id))
            await s.delete(p)
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    # ---------------------------------------------------------------- runs
    async def create_run(self, tenant_id: uuid.UUID, project_id: uuid.UUID, *, kind: str, prompt: str, created_by: uuid.UUID | None) -> BuilderRun:
        cfg = get_settings()
        s = await self._session(tenant_id)
        try:
            active = (await s.execute(select(func.count()).select_from(BuilderRun).where(BuilderRun.tenant_id == tenant_id, BuilderRun.state.in_(ACTIVE_STATES)))).scalar_one()
            if active >= cfg.BUILDER_MAX_ACTIVE_RUNS_PER_TENANT:
                raise BuilderError("A build is already running. Wait for it to finish or cancel it.", 409)
            hour = _now() - timedelta(hours=1)
            recent = (await s.execute(select(func.count()).select_from(BuilderRun).where(BuilderRun.tenant_id == tenant_id, BuilderRun.created_at >= hour))).scalar_one()
            if recent >= cfg.BUILDER_MAX_RUNS_PER_TENANT_PER_HOUR:
                raise BuilderError("You have reached the hourly limit for builds. Try again a little later.", 429)
            run = BuilderRun(tenant_id=tenant_id, project_id=project_id, created_at=_now(), kind=kind, state="queued", prompt=prompt, progress=[], usage={}, repair_attempts=0, cancel_requested=False, created_by=created_by, started_at=_now(), heartbeat_at=_now())
            s.add(run)
            await s.commit()
            await s.refresh(run)
            return run
        finally:
            await s.__aexit__(None, None, None)

    async def get_run(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> BuilderRun:
        s = await self._session(tenant_id)
        try:
            r = (await s.execute(select(BuilderRun).where(BuilderRun.id == run_id, BuilderRun.tenant_id == tenant_id))).scalar_one_or_none()
            if r is None:
                raise BuilderError("Run not found", 404)
            return r
        finally:
            await s.__aexit__(None, None, None)

    async def list_runs(self, tenant_id: uuid.UUID, project_id: uuid.UUID, limit: int = 20) -> list[BuilderRun]:
        s = await self._session(tenant_id)
        try:
            return list((await s.execute(select(BuilderRun).where(BuilderRun.project_id == project_id, BuilderRun.tenant_id == tenant_id).order_by(BuilderRun.created_at.desc()).limit(limit))).scalars())
        finally:
            await s.__aexit__(None, None, None)

    async def set_state(self, tenant_id: uuid.UUID, run_id: uuid.UUID, state: str, message: str, **fields) -> None:
        """Move a run to a new state and append the user-visible progress line in one write."""
        s = await self._session(tenant_id)
        try:
            run = (await s.execute(select(BuilderRun).where(BuilderRun.id == run_id, BuilderRun.tenant_id == tenant_id))).scalar_one()
            events = list(run.progress or [])[-199:]
            events.append({"at": _now().isoformat(), "state": state, "message": message})
            run.progress = events
            run.state = state
            run.heartbeat_at = _now()
            for k, v in fields.items():
                setattr(run, k, v)
            if state in TERMINAL_STATES:
                run.finished_at = _now()
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    async def note(self, tenant_id: uuid.UUID, run_id: uuid.UUID, message: str) -> None:
        s = await self._session(tenant_id)
        try:
            run = (await s.execute(select(BuilderRun).where(BuilderRun.id == run_id, BuilderRun.tenant_id == tenant_id))).scalar_one()
            events = list(run.progress or [])[-199:]
            events.append({"at": _now().isoformat(), "state": run.state, "message": message})
            run.progress = events
            run.heartbeat_at = _now()
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    async def request_cancel(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> BuilderRun:
        s = await self._session(tenant_id)
        try:
            run = (await s.execute(select(BuilderRun).where(BuilderRun.id == run_id, BuilderRun.tenant_id == tenant_id))).scalar_one_or_none()
            if run is None:
                raise BuilderError("Run not found", 404)
            if run.state in TERMINAL_STATES:
                return run
            run.cancel_requested = True
            await s.commit()
            await s.refresh(run)
            return run
        finally:
            await s.__aexit__(None, None, None)

    async def is_cancel_requested(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        s = await self._session(tenant_id)
        try:
            return bool((await s.execute(select(BuilderRun.cancel_requested).where(BuilderRun.id == run_id, BuilderRun.tenant_id == tenant_id))).scalar_one_or_none())
        finally:
            await s.__aexit__(None, None, None)

    async def recover_stale_runs(self, tenant_id: uuid.UUID | None = None) -> int:
        """Mark runs that no worker is driving any more as failed. With a tenant (the normal, RLS-safe path, called whenever that tenant looks at its
        projects) only that tenant's rows are touched; without one (a start-up sweep) it covers every row the connection can see."""
        cutoff = _now() - timedelta(seconds=get_settings().BUILDER_RUN_STALE_AFTER_SECONDS)
        s = await self._session(tenant_id) if tenant_id else self._sf()
        try:
            q = select(BuilderRun).where(BuilderRun.state.in_(ACTIVE_STATES), BuilderRun.heartbeat_at < cutoff)
            if tenant_id:
                q = q.where(BuilderRun.tenant_id == tenant_id)
            rows = list((await s.execute(q)).scalars())
            for r in rows:
                msg = "This build was interrupted (the service restarted). Your previous version is unchanged; start it again."
                r.state, r.error, r.finished_at = "failed", msg, _now()
                r.progress = list(r.progress or [])[-199:] + [{"at": _now().isoformat(), "state": "failed", "message": msg}]
                proj = (await s.execute(select(BuilderProject).where(BuilderProject.id == r.project_id))).scalar_one_or_none()
                if proj is not None and proj.status == "working":
                    proj.status = "ready" if proj.current_version_id else "failed"
            await s.commit()
            return len(rows)
        finally:
            if tenant_id:
                await s.__aexit__(None, None, None)
            else:
                await s.close()

    async def touch(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
        s = await self._session(tenant_id)
        try:
            await s.execute(update(BuilderRun).where(BuilderRun.id == run_id, BuilderRun.tenant_id == tenant_id, BuilderRun.state.in_(ACTIVE_STATES)).values(heartbeat_at=_now()))
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    # ---------------------------------------------------------------- versions and files
    async def next_number(self, s, project_id: uuid.UUID) -> int:
        n = (await s.execute(select(func.max(BuilderVersion.number)).where(BuilderVersion.project_id == project_id))).scalar_one()
        return (n or 0) + 1

    async def save_version(
        self, tenant_id: uuid.UUID, project_id: uuid.UUID, *, kind: str, summary: str, prompt: str, spec: dict, ws: Workspace,
        validation: dict, changes: dict, created_by: uuid.UUID | None, project_status: str = "ready", locked_paths: list[str] | None = None,
    ) -> BuilderVersion:
        """Write the version and its files, and move the project's pointer, in ONE transaction: a failure leaves the previous version current."""
        s = await self._session(tenant_id)
        try:
            number = await self.next_number(s, project_id)
            v = BuilderVersion(tenant_id=tenant_id, project_id=project_id, number=number, kind=kind, summary=summary[:2000], prompt=prompt[:4000], spec=spec, manifest=ws.manifest(), validation=validation, changes=changes, created_by=created_by)
            s.add(v)
            await s.flush()
            for path, content in sorted(ws.snapshot().items()):
                s.add(BuilderFile(tenant_id=tenant_id, version_id=v.id, project_id=project_id, path=path, content=content, sha=sha(content), size=len(content.encode("utf-8"))))
            values = {"current_version_id": v.id, "spec": spec, "status": project_status}
            if locked_paths is not None:
                values["locked_paths"] = locked_paths
            await s.execute(update(BuilderProject).where(BuilderProject.id == project_id, BuilderProject.tenant_id == tenant_id).values(**values))
            await s.commit()
            await s.refresh(v)
            await self._prune(tenant_id, project_id)
            return v
        finally:
            await s.__aexit__(None, None, None)

    async def _prune(self, tenant_id: uuid.UUID, project_id: uuid.UUID) -> None:
        keep = get_settings().BUILDER_VERSIONS_KEPT
        s = await self._session(tenant_id)
        try:
            proj = (await s.execute(select(BuilderProject).where(BuilderProject.id == project_id))).scalar_one()
            versions = list((await s.execute(select(BuilderVersion.id, BuilderVersion.number).where(BuilderVersion.project_id == project_id).order_by(BuilderVersion.number.desc()))).all())
            protected = {proj.current_version_id, proj.published_version_id}
            for vid, _num in versions[keep:]:
                if vid in protected:
                    continue
                await s.execute(delete(BuilderFile).where(BuilderFile.version_id == vid, BuilderFile.tenant_id == tenant_id))
                await s.execute(delete(BuilderVersion).where(BuilderVersion.id == vid, BuilderVersion.tenant_id == tenant_id))
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    async def list_versions(self, tenant_id: uuid.UUID, project_id: uuid.UUID) -> list[BuilderVersion]:
        s = await self._session(tenant_id)
        try:
            return list((await s.execute(select(BuilderVersion).where(BuilderVersion.project_id == project_id, BuilderVersion.tenant_id == tenant_id).order_by(BuilderVersion.number.desc()))).scalars())
        finally:
            await s.__aexit__(None, None, None)

    async def get_version(self, tenant_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID) -> BuilderVersion:
        s = await self._session(tenant_id)
        try:
            v = (await s.execute(select(BuilderVersion).where(BuilderVersion.id == version_id, BuilderVersion.project_id == project_id, BuilderVersion.tenant_id == tenant_id))).scalar_one_or_none()
            if v is None:
                raise BuilderError("Version not found", 404)
            return v
        finally:
            await s.__aexit__(None, None, None)

    async def load_workspace(self, tenant_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID) -> Workspace:
        s = await self._session(tenant_id)
        try:
            rows = (await s.execute(select(BuilderFile.path, BuilderFile.content).where(BuilderFile.version_id == version_id, BuilderFile.project_id == project_id, BuilderFile.tenant_id == tenant_id))).all()
            return Workspace({p: c for p, c in rows})
        finally:
            await s.__aexit__(None, None, None)

    async def load_file(self, tenant_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID, path: str) -> str | None:
        s = await self._session(tenant_id)
        try:
            return (await s.execute(select(BuilderFile.content).where(BuilderFile.version_id == version_id, BuilderFile.project_id == project_id, BuilderFile.tenant_id == tenant_id, BuilderFile.path == path))).scalar_one_or_none()
        finally:
            await s.__aexit__(None, None, None)

    async def publish(self, tenant_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID | None) -> None:
        await self.update_project(tenant_id, project_id, published_version_id=version_id, published_at=_now() if version_id else None)

    # ---------------------------------------------------------------- chat
    async def add_message(self, tenant_id: uuid.UUID, project_id: uuid.UUID, role: str, content: str, run_id: uuid.UUID | None = None) -> None:
        s = await self._session(tenant_id)
        try:
            s.add(BuilderMessage(tenant_id=tenant_id, project_id=project_id, run_id=run_id, role=role, content=content[:6000], created_at=_now()))
            await s.commit()
        finally:
            await s.__aexit__(None, None, None)

    async def list_messages(self, tenant_id: uuid.UUID, project_id: uuid.UUID, limit: int = 100) -> list[BuilderMessage]:
        s = await self._session(tenant_id)
        try:
            rows = list((await s.execute(select(BuilderMessage).where(BuilderMessage.project_id == project_id, BuilderMessage.tenant_id == tenant_id).order_by(BuilderMessage.created_at.desc()).limit(limit))).scalars())
            return list(reversed(rows))
        finally:
            await s.__aexit__(None, None, None)
