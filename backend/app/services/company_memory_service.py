"""Phase 13: Company Memory — the governed, tenant-scoped, auditable
persistence layer for durable owner preferences/business rules/context
(see app/models/company_memory.py for the full design rationale).

Governance boundary: AI never writes an ACTIVE row. `propose_memory` is
the only entry point available to AI-driven code paths and always creates
`status=PENDING` regardless of the caller — only a human, through
`confirm_memory` (itself gated by `Permission.MANAGE_MEMORY` at the API
layer), can move a memory into ACTIVE and therefore into AI context. A
human calling `create_memory` directly (source=OWNER_EXPLICIT/
OWNER_CORRECTION) creates it ACTIVE immediately — an owner stating their
own preference needs no confirmation step.

Authority: a write that would set a new ACTIVE value for a key already
ACTIVE under a HIGHER-authority source is rejected (`MemoryAuthorityError`)
rather than silently superseding it — see `_SOURCE_RANK` and
`_check_authority`. This can only realistically bind on the human path
(AI never reaches ACTIVE directly), but it is enforced unconditionally as
real defense-in-depth, not just documentation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.company_memory import (
    MAX_DESCRIPTION_LENGTH,
    MAX_KEY_LENGTH,
    MAX_VALUE_LENGTH,
    CompanyMemory,
    MemorySource,
    MemoryStatus,
    MemoryType,
)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,%d}$" % (MAX_KEY_LENGTH - 1))

# Highest authority first — see module docstring.
_SOURCE_RANK: dict[str, int] = {
    MemorySource.OWNER_EXPLICIT: 0,
    MemorySource.OWNER_CORRECTION: 1,
    MemorySource.OWNER_APPROVAL: 2,
    MemorySource.SYSTEM_DERIVED: 3,
    MemorySource.AI_PROPOSED: 4,
}

# Rule 11/24: never dump unbounded memory into an AI prompt.
MAX_CONTEXT_ENTRIES = 50


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """SQLite (this codebase's default test engine) does not actually
    preserve tzinfo through a DateTime(timezone=True) column — a value
    round-trips back naive even though it was written as UTC-aware (real
    PostgreSQL preserves it correctly). Normalize defensively so
    get_context()'s comparisons never raise regardless of dialect."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class MemoryNotFoundError(Exception):
    pass


class MemoryValidationError(Exception):
    pass


class MemoryAuthorityError(Exception):
    pass


class MemoryStateError(Exception):
    pass


class MemoryConcurrentUpdateError(Exception):
    """Raised when a real database constraint (the partial unique index
    on (tenant_id, key) WHERE status='ACTIVE' — migration 0034) rejects
    this write because another transaction committed a competing ACTIVE
    row for the same key first. This is real, DB-enforced conflict
    detection (Rule 30), not a Python-level lock — the caller should
    re-read current state and retry if the update is still wanted."""


def validate_key(key: str) -> None:
    if not _KEY_RE.match(key):
        raise MemoryValidationError(
            "key must be lowercase, start with a letter, contain only letters/digits/underscores, "
            f"3-{MAX_KEY_LENGTH} chars (e.g. 'preferred_appointment_time')"
        )


def validate_type(memory_type: str) -> None:
    if memory_type not in set(MemoryType):
        raise MemoryValidationError(f"invalid memory_type: {memory_type!r}")


def _validate_value_and_description(value: str, description: str | None) -> None:
    if not value or len(value) > MAX_VALUE_LENGTH:
        raise MemoryValidationError(f"value must be 1-{MAX_VALUE_LENGTH} chars")
    if description is not None and len(description) > MAX_DESCRIPTION_LENGTH:
        raise MemoryValidationError(f"description must be at most {MAX_DESCRIPTION_LENGTH} chars")


@dataclass
class MemoryContextEntry:
    memory_type: str
    key: str
    value: str
    source: str


def format_context_as_text(entries: list[MemoryContextEntry]) -> str | None:
    """The single shared plain-text rendering of a get_context() result,
    used by every AI flow that injects Company Memory into a prompt
    (Morning Brief, AI Qualification, ...) — one formatter, not one per
    caller, so the fenced-block content is byte-identical everywhere.
    Returns None for an empty context so callers can omit the section
    entirely rather than fencing an empty block (same "no fabricated
    guidance" honesty as brand_voice=None)."""
    if not entries:
        return None
    return "\n".join(f"[{e.memory_type}] {e.key}: {e.value}" for e in entries)


class CompanyMemoryService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # --- Human-authored: creates ACTIVE immediately -------------------

    async def create_memory(
        self, tenant_id: uuid.UUID, *, memory_type: str, key: str, value: str, description: str | None,
        source: str, created_by: uuid.UUID | None, source_entity_type: str | None = None,
        source_entity_id: uuid.UUID | None = None, effective_from: datetime | None = None,
        effective_until: datetime | None = None, reason: str | None = None,
    ) -> CompanyMemory:
        if source not in (MemorySource.OWNER_EXPLICIT, MemorySource.OWNER_CORRECTION, MemorySource.OWNER_APPROVAL):
            raise MemoryValidationError(
                f"create_memory only accepts human-authored sources "
                f"(OWNER_EXPLICIT/OWNER_CORRECTION/OWNER_APPROVAL), got {source!r} — "
                "use propose_memory for AI-originated candidates"
            )
        validate_type(memory_type)
        validate_key(key)
        _validate_value_and_description(value, description)

        async with self._session_factory() as session:
            existing_active = (
                await session.execute(
                    select(CompanyMemory).where(
                        CompanyMemory.tenant_id == tenant_id, CompanyMemory.key == key,
                        CompanyMemory.status == MemoryStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()
            self._check_authority(existing_active, source)

            memory = CompanyMemory(
                tenant_id=tenant_id, memory_type=memory_type, key=key, value=value, description=description,
                source=source, source_entity_type=source_entity_type, source_entity_id=source_entity_id,
                created_by=created_by, status=MemoryStatus.ACTIVE, effective_from=effective_from,
                effective_until=effective_until, supersedes_id=existing_active.id if existing_active else None,
                reason=reason,
            )
            session.add(memory)
            if existing_active is not None:
                existing_active.status = MemoryStatus.ARCHIVED
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise MemoryConcurrentUpdateError(
                    f"key {key!r} was superseded by a concurrent write — re-read and retry"
                ) from exc
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=created_by,
                    action="memory.create", tool=None, entity_type="company_memory", entity_id=memory.id,
                    input_summary={
                        "memory_type": memory_type, "key": key, "source": source,
                        "superseded": str(existing_active.id) if existing_active else None,
                    },
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(memory)
            return memory

    # --- AI-originated: always PENDING, never ACTIVE ------------------

    async def propose_memory(
        self, tenant_id: uuid.UUID, *, memory_type: str, key: str, value: str, description: str | None,
        source_entity_type: str | None, source_entity_id: uuid.UUID | None, confidence: float | None = None,
        reason: str | None = None,
    ) -> CompanyMemory:
        """The ONLY way AI-driven code can write a CompanyMemory row —
        always status=PENDING, source=AI_PROPOSED, regardless of caller.
        Never enters AI context until a human calls confirm_memory."""
        validate_type(memory_type)
        validate_key(key)
        _validate_value_and_description(value, description)
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            raise MemoryValidationError("confidence must be between 0.0 and 1.0")

        async with self._session_factory() as session:
            memory = CompanyMemory(
                tenant_id=tenant_id, memory_type=memory_type, key=key, value=value, description=description,
                source=MemorySource.AI_PROPOSED, source_entity_type=source_entity_type,
                source_entity_id=source_entity_id, created_by=None, status=MemoryStatus.PENDING,
                confidence=confidence, reason=reason,
            )
            session.add(memory)
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.AI, actor_id=None, action="memory.propose",
                    tool=None, entity_type="company_memory", entity_id=memory.id,
                    input_summary={"memory_type": memory_type, "key": key}, result="success",
                )
            )
            await session.commit()
            await session.refresh(memory)
            return memory

    # --- State transitions (all human-gated at the API layer) ---------

    async def confirm_memory(self, tenant_id: uuid.UUID, memory_id: uuid.UUID, *, confirmed_by: uuid.UUID | None) -> CompanyMemory:
        async with self._session_factory() as session:
            memory = await self._get_owned(session, tenant_id, memory_id)
            if memory.status != MemoryStatus.PENDING:
                raise MemoryStateError(f"cannot confirm a memory in status {memory.status!r} (must be PENDING)")

            existing_active = (
                await session.execute(
                    select(CompanyMemory).where(
                        CompanyMemory.tenant_id == tenant_id, CompanyMemory.key == memory.key,
                        CompanyMemory.status == MemoryStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()
            self._check_authority(existing_active, memory.source)

            memory.status = MemoryStatus.ACTIVE
            memory.supersedes_id = existing_active.id if existing_active else None
            if existing_active is not None:
                existing_active.status = MemoryStatus.ARCHIVED
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise MemoryConcurrentUpdateError(
                    f"key {memory.key!r} was superseded by a concurrent write — re-read and retry"
                ) from exc
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=confirmed_by, action="memory.confirm",
                    tool=None, entity_type="company_memory", entity_id=memory.id,
                    input_summary={"key": memory.key, "superseded": str(existing_active.id) if existing_active else None},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(memory)
            return memory

    async def reject_memory(self, tenant_id: uuid.UUID, memory_id: uuid.UUID, *, rejected_by: uuid.UUID | None, reason: str | None = None) -> CompanyMemory:
        async with self._session_factory() as session:
            memory = await self._get_owned(session, tenant_id, memory_id)
            if memory.status != MemoryStatus.PENDING:
                raise MemoryStateError(f"cannot reject a memory in status {memory.status!r} (must be PENDING)")
            memory.status = MemoryStatus.REJECTED
            if reason:
                memory.reason = reason
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=rejected_by, action="memory.reject",
                    tool=None, entity_type="company_memory", entity_id=memory.id,
                    input_summary={"key": memory.key, "reason": reason}, result="success",
                )
            )
            await session.commit()
            await session.refresh(memory)
            return memory

    async def revoke_memory(self, tenant_id: uuid.UUID, memory_id: uuid.UUID, *, revoked_by: uuid.UUID | None, reason: str | None = None) -> CompanyMemory:
        """Soft-deletion only (Rule 3): an ACTIVE memory's full history
        must remain auditable, so this never hard-deletes a row — it
        transitions to REVOKED and simply stops appearing in
        get_context()."""
        async with self._session_factory() as session:
            memory = await self._get_owned(session, tenant_id, memory_id)
            if memory.status != MemoryStatus.ACTIVE:
                raise MemoryStateError(f"cannot revoke a memory in status {memory.status!r} (must be ACTIVE)")
            memory.status = MemoryStatus.REVOKED
            if reason:
                memory.reason = reason
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=revoked_by, action="memory.revoke",
                    tool=None, entity_type="company_memory", entity_id=memory.id,
                    input_summary={"key": memory.key, "reason": reason}, result="success",
                )
            )
            await session.commit()
            await session.refresh(memory)
            return memory

    async def update_pending_memory(
        self, tenant_id: uuid.UUID, memory_id: uuid.UUID, *, value: str, description: str | None,
        updated_by: uuid.UUID | None,
    ) -> CompanyMemory:
        """Edits a PENDING (not-yet-confirmed) candidate's own content in
        place — safe because it isn't real memory yet. An ACTIVE memory
        is never edited in place; "editing" an active preference is
        create_memory with the same key, which supersedes it (Rule 7:
        every change is a new, auditable version, never a silent
        overwrite)."""
        _validate_value_and_description(value, description)
        async with self._session_factory() as session:
            memory = await self._get_owned(session, tenant_id, memory_id)
            if memory.status != MemoryStatus.PENDING:
                raise MemoryStateError(
                    f"cannot edit a memory in status {memory.status!r} — only PENDING candidates can be "
                    "edited in place; an ACTIVE memory's 'edit' is create_memory with the same key"
                )
            memory.value = value
            memory.description = description
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=updated_by, action="memory.update",
                    tool=None, entity_type="company_memory", entity_id=memory.id,
                    input_summary={"key": memory.key}, result="success",
                )
            )
            await session.commit()
            await session.refresh(memory)
            return memory

    # --- Reads -----------------------------------------------------

    async def get_memory(self, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> CompanyMemory:
        async with self._session_factory() as session:
            return await self._get_owned(session, tenant_id, memory_id)

    async def list_memories(
        self, tenant_id: uuid.UUID, *, memory_type: str | None = None, status: str | None = None,
        key: str | None = None, source: str | None = None,
    ) -> list[CompanyMemory]:
        async with self._session_factory() as session:
            query = select(CompanyMemory).where(CompanyMemory.tenant_id == tenant_id)
            if memory_type is not None:
                query = query.where(CompanyMemory.memory_type == memory_type)
            if status is not None:
                query = query.where(CompanyMemory.status == status)
            if key is not None:
                query = query.where(CompanyMemory.key == key)
            if source is not None:
                query = query.where(CompanyMemory.source == source)
            rows = (await session.execute(query.order_by(CompanyMemory.created_at.desc()))).scalars().all()
            return list(rows)

    async def get_history(self, tenant_id: uuid.UUID, key: str) -> list[CompanyMemory]:
        """Every version ever recorded for this key, oldest first —
        walks naturally from created_at since every supersession is a
        brand-new row (Rule 7)."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(CompanyMemory)
                    .where(CompanyMemory.tenant_id == tenant_id, CompanyMemory.key == key)
                    .order_by(CompanyMemory.created_at.asc())
                )
            ).scalars().all()
            return list(rows)

    async def get_context(self, tenant_id: uuid.UUID, *, now: datetime | None = None) -> list[MemoryContextEntry]:
        """Bounded, tenant-scoped, in-effect-only context for AI prompts
        (Rule 11). Never includes PENDING/REVOKED/REJECTED/ARCHIVED rows,
        never includes a not-yet-effective or expired ACTIVE row."""
        now = now or datetime.now(timezone.utc)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(CompanyMemory)
                    .where(CompanyMemory.tenant_id == tenant_id, CompanyMemory.status == MemoryStatus.ACTIVE)
                    .order_by(CompanyMemory.memory_type.asc(), CompanyMemory.key.asc())
                    .limit(MAX_CONTEXT_ENTRIES)
                )
            ).scalars().all()

        entries = []
        for row in rows:
            effective_from = _as_aware_utc(row.effective_from)
            effective_until = _as_aware_utc(row.effective_until)
            if effective_from is not None and effective_from > now:
                continue
            if effective_until is not None and effective_until < now:
                continue
            entries.append(MemoryContextEntry(memory_type=row.memory_type, key=row.key, value=row.value, source=row.source))
        return entries

    # --- Internal -----------------------------------------------------

    async def _get_owned(self, session, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> CompanyMemory:
        memory = await session.get(CompanyMemory, memory_id)
        if memory is None or memory.tenant_id != tenant_id:
            raise MemoryNotFoundError(f"CompanyMemory {memory_id} not found")
        return memory

    def _check_authority(self, existing_active: CompanyMemory | None, incoming_source: str) -> None:
        if existing_active is None:
            return
        if _SOURCE_RANK[incoming_source] > _SOURCE_RANK[existing_active.source]:
            raise MemoryAuthorityError(
                f"cannot supersede key {existing_active.key!r} (currently ACTIVE via {existing_active.source}) "
                f"with a lower-authority source {incoming_source!r}"
            )
