"""Phase 12: the Company-OS Knowledge Layer service. Every read/write of a
`KnowledgeFile` goes through here — tools, the API, and (below) the AI
provider's prompt construction all use the same service, never a direct
query, so tenant scoping and the audit-on-write guarantee are enforced in
exactly one place.
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.knowledge import KnowledgeChunk, KnowledgeFile

logger = structlog.get_logger(__name__)

# The default categories from the Company-OS concept this layer implements
# a real, working (if intentionally smaller) version of. A brand-new tenant
# gets these seeded with short, honest placeholder content — never fake
# "already configured" data — so the concept and the UI are immediately
# visible, not an empty page nobody knows to use.
DEFAULT_FILES: dict[str, str] = {
    "office/service-catalog.md": "# Service Catalog\n\nList the services you offer here — one per line or section.\n",
    "office/pricing-rules.md": "# Pricing Rules\n\nDescribe how you price jobs: base rates, minimums, travel fees, "
    "rush charges. `finance.approve_invoice`'s automatic-approval threshold is a separate, code-level "
    "setting (see /settings/automation) — this file is for YOUR reference and, where wired in, for the "
    "AI to phrase recommendations consistently with your real pricing, never to invent a price itself.\n",
    "office/qualification-criteria.md": "# Lead Qualification Criteria\n\nWhat makes a lead worth pursuing? "
    "Service area, budget range, urgency, property type — whatever matters for your business.\n",
    "brand/voice-guide.md": "# Brand Voice\n\nHow should Klaros sound when it writes on your behalf — "
    "Morning Brief prose, customer messages? A few sentences is enough: tone, words to use, words to avoid.\n",
    "compliance/approval-limits.md": "# Approval Limits\n\nNotes on what should always require your sign-off "
    "regardless of automation policy, and why. The actual enforcement lives in /settings/automation — "
    "this file is the reasoning behind those settings, for your own reference and anyone you delegate to.\n",
}


class KnowledgeService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def seed_defaults(self, tenant_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            for path, content in DEFAULT_FILES.items():
                session.add(KnowledgeFile(tenant_id=tenant_id, path=path, content=content, updated_by=None))
            await session.commit()

    async def list_files(self, tenant_id: uuid.UUID, *, prefix: str | None = None) -> list[KnowledgeFile]:
        async with self._session_factory() as session:
            query = select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id)
            if prefix:
                query = query.where(KnowledgeFile.path.like(f"{prefix}%"))
            return (await session.execute(query.order_by(KnowledgeFile.path))).scalars().all()

    async def get_file(self, tenant_id: uuid.UUID, path: str) -> KnowledgeFile | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id, KnowledgeFile.path == path)
                )
            ).scalar_one_or_none()

    async def get_content(self, tenant_id: uuid.UUID, path: str) -> str | None:
        """Convenience for read-only internal callers (e.g. the AI provider's
        prompt construction) that just want the text, or None if the tenant
        has never configured that file — callers must treat None as "no
        real content available," never substitute a fabricated default."""
        file = await self.get_file(tenant_id, path)
        return file.content if file is not None else None

    async def set_file(
        self, tenant_id: uuid.UUID, path: str, content: str, *, actor_id: uuid.UUID | None
    ) -> KnowledgeFile:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id, KnowledgeFile.path == path)
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.content = content
                existing.updated_by = actor_id
                existing.updated_at = now
                file = existing
            else:
                file = KnowledgeFile(tenant_id=tenant_id, path=path, content=content, updated_by=actor_id)
                session.add(file)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="knowledge.write",
                    tool=None,
                    entity_type="knowledge_file",
                    entity_id=None,
                    input_summary={"path": path, "content_length": len(content)},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(file)
            return file

    async def delete_file(self, tenant_id: uuid.UUID, path: str, *, actor_id: uuid.UUID | None) -> bool:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(KnowledgeFile).where(KnowledgeFile.tenant_id == tenant_id, KnowledgeFile.path == path)
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            # Explicit chunk cleanup rather than relying solely on the
            # database's ON DELETE CASCADE (real on PostgreSQL, but SQLite
            # only enforces it when foreign_keys=ON is set per-connection,
            # which this codebase's engine does not currently do) — this
            # way deletion is correct regardless of engine/pragma state.
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.file_id == existing.id))
            await session.delete(existing)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=actor_id,
                    action="knowledge.delete",
                    tool=None,
                    entity_type="knowledge_file",
                    entity_id=None,
                    input_summary={"path": path},
                    result="success",
                )
            )
            await session.commit()
            return True
