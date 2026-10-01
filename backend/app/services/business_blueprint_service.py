"""Phase 2 (KLAROS_BUSINESS_BLUEPRINT_SPEC.md): the BusinessBlueprint
service — owns creation, section reads/writes, claim propose/confirm/
reject, activation, and full-row versioning.

Tenant isolation: every method takes an explicit `tenant_id` and filters
every query by it (this codebase's unenforced-by-DB-RLS convention — see
app/api/deps.py's set_tenant_context docstring); RLS audit-mode is applied
in the migration but is not itself the enforcement boundary yet.

Every confirm/reject is audited via an AuditLog row, mirroring
CompanyMemoryService.create_memory's exact pattern, and a confirmed claim
mirrors into CompanyMemory (KLAROS_ARCHITECTURE_RECONCILIATION.md #4 /
KLAROS_FINAL_DOMAIN_MODEL.md's BlueprintClaim relationships note) via the
existing propose/confirm service — never a second, parallel audit or
memory-write mechanism.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.business_blueprint import (
    MINIMUM_BAR_SECTIONS,
    BlueprintClaim,
    BlueprintSection,
    BlueprintSectionKey,
    BlueprintSectionStatus,
    BlueprintStatus,
    BusinessBlueprint,
    ClaimStatus,
)
from app.models.company_memory import MemorySource, MemoryType
from app.services.company_memory_service import CompanyMemoryService


class BlueprintNotFoundError(Exception):
    pass


class ClaimNotFoundError(Exception):
    pass


class InvalidClaimTransitionError(Exception):
    pass


class BlueprintActivationError(Exception):
    pass


class BusinessBlueprintService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._memory = CompanyMemoryService(session_factory)

    # --- creation / retrieval -----------------------------------------

    async def get_or_create_draft(
        self, tenant_id: uuid.UUID, *, created_by: uuid.UUID | None
    ) -> BusinessBlueprint:
        """Returns the tenant's current DRAFT or ACTIVE blueprint (the
        thing Discovery should attach new claims to), creating a fresh
        version=1 DRAFT with all 20 empty sections if none exists yet.
        A SUPERSEDED blueprint is never returned here — only the current
        DRAFT (if still forming) or ACTIVE (if a new DiscoverySession is
        proposing further claims against an already-live blueprint)."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (
                await session.execute(
                    select(BusinessBlueprint).where(
                        BusinessBlueprint.tenant_id == tenant_id,
                        BusinessBlueprint.status.in_([BlueprintStatus.DRAFT, BlueprintStatus.ACTIVE]),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

            blueprint = BusinessBlueprint(
                tenant_id=tenant_id, status=BlueprintStatus.DRAFT, version=1, created_by=created_by
            )
            session.add(blueprint)
            await session.flush()
            for key in BlueprintSectionKey:
                session.add(
                    BlueprintSection(
                        tenant_id=tenant_id,
                        blueprint_id=blueprint.id,
                        section_key=key.value,
                        status=BlueprintSectionStatus.EMPTY,
                        data={},
                    )
                )
            await session.commit()
            await session.refresh(blueprint)
            return blueprint

    async def get_active(self, tenant_id: uuid.UUID) -> BusinessBlueprint | None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            return (
                await session.execute(
                    select(BusinessBlueprint).where(
                        BusinessBlueprint.tenant_id == tenant_id,
                        BusinessBlueprint.status == BlueprintStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()

    async def get_version(self, tenant_id: uuid.UUID, version: int) -> BusinessBlueprint:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            blueprint = (
                await session.execute(
                    select(BusinessBlueprint).where(
                        BusinessBlueprint.tenant_id == tenant_id, BusinessBlueprint.version == version
                    )
                )
            ).scalar_one_or_none()
            if blueprint is None:
                raise BlueprintNotFoundError(f"Blueprint version {version} not found")
            return blueprint

    async def get_by_id(self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID) -> BusinessBlueprint:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            blueprint = await session.get(BusinessBlueprint, blueprint_id)
            if blueprint is None or blueprint.tenant_id != tenant_id:
                raise BlueprintNotFoundError(f"Blueprint {blueprint_id} not found")
            return blueprint

    async def list_sections(self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID) -> list[BlueprintSection]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(BlueprintSection).where(
                        BlueprintSection.tenant_id == tenant_id, BlueprintSection.blueprint_id == blueprint_id
                    )
                )
            ).scalars().all()
            return list(rows)

    async def list_claims(
        self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID, *, status: str | None = None
    ) -> list[BlueprintClaim]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            stmt = select(BlueprintClaim).where(
                BlueprintClaim.tenant_id == tenant_id, BlueprintClaim.blueprint_id == blueprint_id
            )
            if status is not None:
                stmt = stmt.where(BlueprintClaim.status == status)
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)

    # --- claim propose / confirm / reject -------------------------------

    async def propose_claim(
        self,
        tenant_id: uuid.UUID,
        blueprint_id: uuid.UUID,
        *,
        section_key: str,
        claim_type: str,
        key: str,
        value: object | None,
        confidence: float | None,
        provenance: str,
        discovery_turn_id: uuid.UUID | None,
        evidence_ref: str | None,
    ) -> BlueprintClaim:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            claim = BlueprintClaim(
                tenant_id=tenant_id,
                blueprint_id=blueprint_id,
                section_key=section_key,
                claim_type=claim_type,
                key=key,
                value=value,
                confidence=confidence,
                provenance=provenance,
                discovery_turn_id=discovery_turn_id,
                evidence_ref=evidence_ref,
                status=ClaimStatus.PROPOSED,
            )
            session.add(claim)
            # Keep the section at least DRAFT once it has any proposed
            # claim (still EMPTY only when it has none at all).
            section = (
                await session.execute(
                    select(BlueprintSection).where(
                        BlueprintSection.tenant_id == tenant_id,
                        BlueprintSection.blueprint_id == blueprint_id,
                        BlueprintSection.section_key == section_key,
                    )
                )
            ).scalar_one_or_none()
            if section is not None and section.status == BlueprintSectionStatus.EMPTY:
                section.status = BlueprintSectionStatus.DRAFT
            await session.commit()
            await session.refresh(claim)
            return claim

    async def confirm_claim(
        self, tenant_id: uuid.UUID, claim_id: uuid.UUID, *, confirmed_by: uuid.UUID | None
    ) -> BlueprintClaim:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            claim = await session.get(BlueprintClaim, claim_id)
            if claim is None or claim.tenant_id != tenant_id:
                raise ClaimNotFoundError(f"Claim {claim_id} not found")
            if claim.status == ClaimStatus.REJECTED:
                raise InvalidClaimTransitionError("Claim already rejected — cannot confirm")
            if claim.status == ClaimStatus.CONFIRMED:
                # Idempotent — safe to repeat (KLAROS_FINAL_API_ARCHITECTURE.md).
                return claim

            claim.status = ClaimStatus.CONFIRMED
            claim.confirmed_by = confirmed_by
            claim.confirmed_at = datetime.now(timezone.utc)

            section = (
                await session.execute(
                    select(BlueprintSection).where(
                        BlueprintSection.tenant_id == tenant_id,
                        BlueprintSection.blueprint_id == claim.blueprint_id,
                        BlueprintSection.section_key == claim.section_key,
                    )
                )
            ).scalar_one_or_none()
            if section is not None:
                confirmed_claims = (
                    await session.execute(
                        select(BlueprintClaim).where(
                            BlueprintClaim.tenant_id == tenant_id,
                            BlueprintClaim.blueprint_id == claim.blueprint_id,
                            BlueprintClaim.section_key == claim.section_key,
                            BlueprintClaim.status == ClaimStatus.CONFIRMED,
                        )
                    )
                ).scalars().all()
                # BlueprintSection.data is recomputed from CONFIRMED claims
                # only (KLAROS_BUSINESS_DISCOVERY_SPEC.md §3) — never
                # hand-mutated independently of this recomputation.
                data = dict(section.data or {})
                for c in confirmed_claims:
                    if c.value is not None:
                        data[c.key] = c.value
                if claim.value is not None:
                    data[claim.key] = claim.value
                section.data = data
                section.status = BlueprintSectionStatus.COMPLETE
                section.updated_by = confirmed_by

            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=confirmed_by,
                    action="blueprint.confirm_claim",
                    tool=None,
                    entity_type="blueprint_claim",
                    entity_id=claim.id,
                    input_summary={"section_key": claim.section_key, "claim_type": claim.claim_type},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(claim)

        # Mirror into CompanyMemory (existing propose/confirm service, not
        # a new write path) — outside the transaction above so a memory
        # supersession conflict never rolls back the claim confirmation
        # itself; CompanyMemoryService has its own commit/audit.
        if claim.value is not None:
            try:
                # CompanyMemoryService.validate_key requires
                # ^[a-z][a-z0-9_]{2,99}$ — no dots. The claim's own `key`
                # is a dotted path (e.g. "identity.description"); sanitize
                # it to underscores so the mirror write actually succeeds
                # instead of silently failing validation every time.
                import re as _re

                sanitized_key = _re.sub(r"[^a-z0-9_]+", "_", f"blueprint_{claim.section_key}_{claim.key}".lower())
                sanitized_key = sanitized_key[:100].rstrip("_") or "blueprint_claim"
                if not sanitized_key[0].isalpha():
                    sanitized_key = f"k_{sanitized_key}"[:100]
                await self._memory.create_memory(
                    tenant_id,
                    memory_type=MemoryType.COMPANY_CONTEXT,
                    key=sanitized_key,
                    value=str(claim.value)[:2000],
                    description=f"Confirmed Business Blueprint claim ({claim.claim_type})",
                    source=MemorySource.OWNER_EXPLICIT,
                    created_by=confirmed_by,
                    source_entity_type="blueprint_claim",
                    source_entity_id=claim.id,
                )
            except Exception:  # noqa: BLE001 — best-effort mirror, never blocks confirmation
                pass

        return claim

    async def reject_claim(
        self, tenant_id: uuid.UUID, claim_id: uuid.UUID, *, rejected_by: uuid.UUID | None, reason: str | None
    ) -> BlueprintClaim:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            claim = await session.get(BlueprintClaim, claim_id)
            if claim is None or claim.tenant_id != tenant_id:
                raise ClaimNotFoundError(f"Claim {claim_id} not found")
            if claim.status == ClaimStatus.CONFIRMED:
                raise InvalidClaimTransitionError("Claim already confirmed — cannot reject")
            if claim.status == ClaimStatus.REJECTED:
                return claim  # idempotent

            claim.status = ClaimStatus.REJECTED
            claim.rejected_reason = reason
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=rejected_by,
                    action="blueprint.reject_claim",
                    tool=None,
                    entity_type="blueprint_claim",
                    entity_id=claim.id,
                    input_summary={"section_key": claim.section_key, "reason": reason},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(claim)
            return claim

    # --- section edit (human, creates a new version once ACTIVE) -------

    async def update_section(
        self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID, *, section_key: str, data: dict, updated_by: uuid.UUID | None
    ) -> tuple[BusinessBlueprint, BlueprintSection]:
        blueprint = await self.get_by_id(tenant_id, blueprint_id)

        if blueprint.status == BlueprintStatus.SUPERSEDED:
            raise InvalidClaimTransitionError("Cannot edit a SUPERSEDED blueprint version")

        if blueprint.status == BlueprintStatus.DRAFT:
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                section = (
                    await session.execute(
                        select(BlueprintSection).where(
                            BlueprintSection.tenant_id == tenant_id,
                            BlueprintSection.blueprint_id == blueprint_id,
                            BlueprintSection.section_key == section_key,
                        )
                    )
                ).scalar_one_or_none()
                if section is None:
                    raise BlueprintNotFoundError(f"Section {section_key} not found")
                section.data = data
                section.status = BlueprintSectionStatus.COMPLETE if data else BlueprintSectionStatus.EMPTY
                section.updated_by = updated_by
                await session.commit()
                await session.refresh(section)
                return blueprint, section

        # ACTIVE: clone to a new version (module docstring's versioning model).
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            old_sections = (
                await session.execute(
                    select(BlueprintSection).where(
                        BlueprintSection.tenant_id == tenant_id, BlueprintSection.blueprint_id == blueprint_id
                    )
                )
            ).scalars().all()

            old_blueprint = await session.get(BusinessBlueprint, blueprint_id)
            old_blueprint.status = BlueprintStatus.SUPERSEDED

            new_blueprint = BusinessBlueprint(
                tenant_id=tenant_id,
                status=BlueprintStatus.ACTIVE,
                version=old_blueprint.version + 1,
                created_by=old_blueprint.created_by,
                confirmed_at=old_blueprint.confirmed_at,
                vertical_extension_id=old_blueprint.vertical_extension_id,
                supersedes_id=old_blueprint.id,
            )
            session.add(new_blueprint)
            await session.flush()

            new_target_section = None
            for old_section in old_sections:
                is_target = old_section.section_key == section_key
                new_section = BlueprintSection(
                    tenant_id=tenant_id,
                    blueprint_id=new_blueprint.id,
                    section_key=old_section.section_key,
                    status=(
                        (BlueprintSectionStatus.COMPLETE if data else BlueprintSectionStatus.EMPTY)
                        if is_target
                        else old_section.status
                    ),
                    data=(data if is_target else dict(old_section.data or {})),
                    updated_by=updated_by if is_target else old_section.updated_by,
                )
                session.add(new_section)
                if is_target:
                    new_target_section = new_section

            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=updated_by,
                    action="blueprint.new_version",
                    tool=None,
                    entity_type="business_blueprint",
                    entity_id=new_blueprint.id,
                    input_summary={"section_key": section_key, "prior_version": old_blueprint.version},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(new_blueprint)
            await session.refresh(new_target_section)
            return new_blueprint, new_target_section

    # --- activation ------------------------------------------------------

    async def activate(self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID, *, activated_by: uuid.UUID | None) -> BusinessBlueprint:
        blueprint = await self.get_by_id(tenant_id, blueprint_id)
        if blueprint.status != BlueprintStatus.DRAFT:
            raise BlueprintActivationError(f"Blueprint is {blueprint.status}, not DRAFT — cannot activate")

        sections = await self.list_sections(tenant_id, blueprint_id)
        by_key = {s.section_key: s for s in sections}
        missing = [
            key.value
            for key in MINIMUM_BAR_SECTIONS
            if by_key.get(key.value) is None or by_key[key.value].status != BlueprintSectionStatus.COMPLETE
        ]
        if missing:
            raise BlueprintActivationError(
                "Cannot activate — minimum-bar sections not COMPLETE: " + ", ".join(missing)
            )

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = await session.get(BusinessBlueprint, blueprint_id)
            row.status = BlueprintStatus.ACTIVE
            row.confirmed_at = datetime.now(timezone.utc)
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    actor_type=ActorType.USER,
                    actor_id=activated_by,
                    action="blueprint.activate_version",
                    tool=None,
                    entity_type="business_blueprint",
                    entity_id=row.id,
                    input_summary={"version": row.version},
                    result="success",
                )
            )
            await session.commit()
            await session.refresh(row)
            return row
