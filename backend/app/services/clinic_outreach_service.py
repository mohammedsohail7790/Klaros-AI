"""Clinic outreach campaigns: choose the clinics, find each one's business email, and start, pause or stop the campaign.

Phase 1 stores state only. Nothing in this module sends an email, SMS or any other message, and it never touches a
message channel. Contact discovery only reads public web search results. Every query is scoped to the caller's tenant.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
from sqlalchemy import case, func, select

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import ClinicOutreach, ClinicOutreachCampaign, OutreachMode, OutreachStage, OutreachSuppression
from app.models.marketing import Campaign, CampaignChannel, CampaignObjective, CampaignStatus
from app.models.medical_tourism import Provider, ProviderStatus
from app.models.mt_revenue import ProviderTerms
from app.services import clinic_contact_finder
from app.services.clinic_contact_finder import _STRICT_ADDRESS, _STRICT_DOMAIN, dns_mx_exists, usable_address
from app.services.mt_clinic_sourcing import SearchProvider, search_provider_from_settings
from app.services.outreach_blocks import validate_blocks
from app.services.outreach_settings import OutreachConfig
from app.services.tenant_settings_service import OUTREACH_BLOCKS, TenantSettingsService

TERMINAL_STAGES = frozenset({OutreachStage.DECLINED, OutreachStage.OPTED_OUT, OutreachStage.NO_RESPONSE, OutreachStage.HANDED_TO_VERIFICATION})
HOLDING_STATUSES = (CampaignStatus.DRAFT, CampaignStatus.ACTIVE, CampaignStatus.PAUSED)  # a clinic in one of these campaigns is not free for another
NO_EMAIL_RETRY_AFTER = timedelta(days=14)
SUPPRESSION_KINDS = ("address", "domain")
CONTACT_EDITABLE_STAGES = (OutreachStage.QUEUED, OutreachStage.NO_EMAIL, OutreachStage.READY)

# One lock per tenant around campaign creation, so two creates in this process never both claim a clinic. Across processes the
# Provider row locks taken inside the create transaction (SELECT ... FOR UPDATE) do the same job on Postgres.
_create_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _create_lock(tenant_id: uuid.UUID) -> asyncio.Lock:
    lock = _create_locks.get(tenant_id)
    if lock is None:
        lock = _create_locks[tenant_id] = asyncio.Lock()
    return lock


class OutreachError(ValueError):
    """A request the campaign's current state does not allow; the message says what to do instead."""


class OutreachNotFound(OutreachError):
    """No such campaign for this tenant."""


def _now() -> datetime:
    return datetime.now(UTC)


def _norm(value: str) -> str:
    return (value or "").strip().lower()


class ClinicOutreachService:
    def __init__(self, session_factory, search_factory: Callable[[], SearchProvider | None] = search_provider_from_settings,
                 mx_exists: Callable[[str], Awaitable[bool]] = dns_mx_exists) -> None:
        self._sf = session_factory
        self._search_factory = search_factory
        self._mx_exists = mx_exists

    # --- selection ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _eligible_query(tenant_id: uuid.UUID, countries: list[str]):
        busy = (select(ClinicOutreach.provider_id)
                .join(Campaign, Campaign.id == ClinicOutreach.campaign_id)
                .where(ClinicOutreach.tenant_id == tenant_id, Campaign.tenant_id == tenant_id,
                       Campaign.status.in_([s.value for s in HOLDING_STATUSES]),
                       ClinicOutreach.stage.not_in([s.value for s in TERMINAL_STAGES])))
        termed = select(ProviderTerms.provider_id).where(ProviderTerms.tenant_id == tenant_id)
        return (select(Provider)
                .where(Provider.tenant_id == tenant_id, Provider.status == ProviderStatus.ACTIVE, Provider.country.in_(countries),
                       Provider.id.not_in(busy), Provider.id.not_in(termed))
                .order_by(Provider.country, Provider.name, Provider.id))

    async def eligible_clinics(self, tenant_id: uuid.UUID, countries: list[str]) -> list[dict[str, Any]]:
        codes = [c.strip().upper() for c in countries if isinstance(c, str) and c.strip()]
        if not codes:
            return []
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            providers = (await session.execute(self._eligible_query(tenant_id, codes))).scalars().all()
        return [{"id": str(p.id), "name": p.name, "country": p.country, "city": p.city, "has_email": bool((p.contact_email or "").strip())} for p in providers]

    async def create_campaign(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, *, name: str, config: OutreachConfig,
                              provider_ids: list[uuid.UUID] | None) -> uuid.UUID:
        name = (name or "").strip()
        if not name:
            raise OutreachError("Give the campaign a name.")
        stored = config.model_dump(mode="json")  # Decimal -> str, so it can live in JSON columns
        no_clinics = "No eligible clinics: choose countries with active clinics that have no agreed terms and are not already in another open outreach campaign."
        async with _create_lock(tenant_id), self._sf() as session:
            await set_tenant_context(session, tenant_id)
            providers = list((await session.execute(self._eligible_query(tenant_id, config.countries))).scalars().all())
            if provider_ids is not None:
                wanted = set(provider_ids)
                providers = [p for p in providers if p.id in wanted]
            if not providers:
                raise OutreachError(no_clinics)
            # Lock the chosen clinics (in id order, so two creates never deadlock), then re-check eligibility while holding the locks:
            # only the clinics that pass this second check are inserted.
            chosen = sorted(p.id for p in providers)
            await session.execute(select(Provider.id).where(Provider.tenant_id == tenant_id, Provider.id.in_(chosen)).order_by(Provider.id).with_for_update())
            providers = list((await session.execute(
                self._eligible_query(tenant_id, config.countries).where(Provider.id.in_(chosen)).execution_options(populate_existing=True))).scalars().all())
            if not providers:
                raise OutreachError(no_clinics)
            campaign = Campaign(tenant_id=tenant_id, name=name[:255], channel=CampaignChannel.OUTBOUND, objective=CampaignObjective.LEAD_GEN,
                                status=CampaignStatus.DRAFT, external_provider="internal")
            session.add(campaign)
            await session.flush()
            session.add(ClinicOutreachCampaign(tenant_id=tenant_id, campaign_id=campaign.id, mode=OutreachMode.SHADOW, term_sheet=stored["term_sheet"],
                                               sender=stored["sender"], limits=stored["limits"], countries=stored["countries"]))
            for p in providers:
                session.add(ClinicOutreach(tenant_id=tenant_id, campaign_id=campaign.id, provider_id=p.id, stage=OutreachStage.QUEUED))
            session.add(self._audit(tenant_id, user_id, "outreach.campaign_created", campaign.id, {"clinics": len(providers)}))
            await session.commit()
            return campaign.id

    # --- reading -----------------------------------------------------------------------------------------------------

    async def _load(self, session, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> tuple[Campaign, ClinicOutreachCampaign]:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(
            select(Campaign, ClinicOutreachCampaign)
            .join(ClinicOutreachCampaign, ClinicOutreachCampaign.campaign_id == Campaign.id)
            .where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id, ClinicOutreachCampaign.tenant_id == tenant_id))).first()
        if row is None:
            raise OutreachNotFound("Outreach campaign not found.")
        return row[0], row[1]

    @staticmethod
    def _config(cfg: ClinicOutreachCampaign) -> OutreachConfig:
        return OutreachConfig.model_validate({"term_sheet": cfg.term_sheet, "sender": cfg.sender, "limits": cfg.limits or {}, "countries": cfg.countries})

    async def get(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> dict[str, Any]:
        async with self._sf() as session:
            campaign, cfg = await self._load(session, tenant_id, campaign_id)
            rows = (await session.execute(
                select(ClinicOutreach, Provider)
                .join(Provider, Provider.id == ClinicOutreach.provider_id)
                .where(ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id, Provider.tenant_id == tenant_id)
                .order_by(Provider.country, Provider.name))).all()
        try:
            config = self._config(cfg).model_dump(mode="json")
        except ValidationError:
            config = None
        funnel = {s.value: 0 for s in OutreachStage}
        clinics = []
        for o, p in rows:
            funnel[o.stage] = funnel.get(o.stage, 0) + 1
            clinics.append({"provider_id": str(p.id), "name": p.name, "country": p.country, "city": p.city, "stage": o.stage,
                            "contact_email": o.contact_email, "contact_source": o.contact_source,
                            "contact_checked_at": o.contact_checked_at.isoformat() if o.contact_checked_at else None})
        return {"campaign": {"id": str(campaign.id), "name": campaign.name, "status": campaign.status}, "config": config, "mode": cfg.mode,
                "funnel": funnel, "clinics": clinics}

    # --- contact discovery -------------------------------------------------------------------------------------------

    async def find_contacts(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, *, limit: int = 5) -> dict[str, Any]:
        search = self._search_factory()
        result = {"status": "ok", "checked": 0, "found": 0, "no_email": 0, "search_failed": 0, "search_configured": search is not None}
        retry_before = _now() - NO_EMAIL_RETRY_AFTER
        async with self._sf() as session:
            campaign, _ = await self._load(session, tenant_id, campaign_id)
            if campaign.status not in HOLDING_STATUSES:
                raise OutreachError(f"This campaign is {campaign.status}; contact discovery only runs for a draft, active or paused campaign.")
            q = (select(ClinicOutreach.id, Provider.name, Provider.city, Provider.country, Provider.contact_email, Provider.idempotency_key)
                 .join(Provider, Provider.id == ClinicOutreach.provider_id)
                 .where(ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id, Provider.tenant_id == tenant_id,
                        (ClinicOutreach.stage == OutreachStage.QUEUED)
                        | ((ClinicOutreach.stage == OutreachStage.NO_EMAIL) & (ClinicOutreach.contact_checked_at <= retry_before))))
            if search is None:  # without search only a person-entered clinic address can be used (an auto-sourced one is not trusted)
                q = q.where(func.trim(func.coalesce(Provider.contact_email, "")) != "", Provider.idempotency_key.is_(None))
            q = q.order_by(case((ClinicOutreach.stage == OutreachStage.QUEUED, 0), else_=1), ClinicOutreach.contact_checked_at, ClinicOutreach.created_at,
                           ClinicOutreach.id).limit(max(0, int(limit)))
            rows = (await session.execute(q)).all()
        todo = []
        for outreach_id, name, city, country, contact_email, idempotency_key in rows:
            own = _norm(contact_email or "")
            if idempotency_key is not None:
                own = ""  # auto-sourcing copied this address from a search hit; it is not the clinic's own entry, so the site is searched
            if own and (not usable_address(own) or await self.is_suppressed(tenant_id, own)):
                own = ""  # a free-mail, role or suppressed own address is ignored for this attempt; the clinic's site is searched instead
            if search is None and not own:
                continue  # nothing to work with until a search provider is configured
            todo.append((outreach_id, SimpleNamespace(name=name, city=city, country=country, contact_email=own or None)))
        if search is None and not todo:
            return {**result, "status": "not_configured"}

        for outreach_id, snapshot in todo:
            found = await clinic_contact_finder.find_contact(snapshot, search, self._mx_exists)
            if found.reason == "search_failed":  # left untouched so the next run retries it
                result["checked"] += 1
                result["search_failed"] += 1
                continue
            if found.reason == "not_configured":  # cannot happen (filtered above); never mark a clinic NO_EMAIL for want of a search provider
                continue
            email = _norm(found.email) if found.email else None
            if email and (not usable_address(email) or await self.is_suppressed(tenant_id, email)):
                email = None
            now = _now()
            async with self._sf() as session:
                await set_tenant_context(session, tenant_id)
                row = (await session.execute(select(ClinicOutreach).where(ClinicOutreach.id == outreach_id, ClinicOutreach.tenant_id == tenant_id))).scalar_one_or_none()
                if row is None or row.stage not in (OutreachStage.QUEUED, OutreachStage.NO_EMAIL):
                    continue  # changed by someone else meanwhile; not counted
                result["checked"] += 1  # checked == found + no_email + search_failed
                row.contact_checked_at = now
                if email:
                    row.stage, row.contact_email, row.contact_source = OutreachStage.READY, email, found.source
                    row.contact_source_url, row.contact_found_at = (found.source_url or None) and found.source_url[:2000], now
                    session.add(self._audit(tenant_id, None, "outreach.contact_found", campaign_id,
                                            {"outreach_id": str(row.id), "provider_id": str(row.provider_id), "source": found.source}, actor_type=ActorType.SYSTEM))
                    result["found"] += 1
                else:
                    row.stage = OutreachStage.NO_EMAIL
                    result["no_email"] += 1
                await session.commit()
        return result

    async def set_contact(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, provider_id: uuid.UUID, email: str, user_id: uuid.UUID | None = None) -> None:
        address = _norm(email)
        if not usable_address(address):
            raise OutreachError("Use the clinic's own business address: one plain address, not free-mail, a role address such as noreply, or a website-builder domain.")
        if await self.is_suppressed(tenant_id, address):
            raise OutreachError("This address or its domain is on the do-not-contact list.")
        async with self._sf() as session:
            campaign, _ = await self._load(session, tenant_id, campaign_id)
            if campaign.status not in HOLDING_STATUSES:
                raise OutreachError(f"This campaign is {campaign.status}; a contact can only be set in a draft, active or paused campaign.")
            row = (await session.execute(select(ClinicOutreach).where(
                ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id, ClinicOutreach.provider_id == provider_id))).scalar_one_or_none()
            if row is None:
                raise OutreachError("That clinic is not part of this campaign.")
            if row.stage not in CONTACT_EDITABLE_STAGES:
                raise OutreachError(f"This clinic is at stage {row.stage}; its contact can only be set before any email goes out (stage QUEUED, NO_EMAIL or READY).")
            now = _now()
            row.stage, row.contact_email, row.contact_source, row.contact_source_url = OutreachStage.READY, address, "owner", None
            row.contact_checked_at, row.contact_found_at = now, now
            session.add(self._audit(tenant_id, user_id, "outreach.contact_set", campaign_id, {"outreach_id": str(row.id), "provider_id": str(row.provider_id)}))
            await session.commit()

    # --- lifecycle ---------------------------------------------------------------------------------------------------

    async def start_shadow(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, campaign_id: uuid.UUID) -> None:
        async with self._sf() as session:
            campaign, cfg = await self._load(session, tenant_id, campaign_id)
            if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
                raise OutreachError(f"This campaign is {campaign.status}; only a draft or paused campaign can be started.")
            try:
                self._config(cfg)
            except ValidationError as exc:
                raise OutreachError("The campaign settings are incomplete (term sheet, sender details and countries are required); fix them before starting.") from exc
            if not await self.blocks_approved(tenant_id):
                raise OutreachError("The clinic email wording has not been approved yet; approve it first.")
            sender = self._config(cfg).sender
            for field, label in (("company_name", "sender company name"), ("postal_address", "sender postal address")):
                if not (getattr(sender, field, "") or "").strip():
                    raise OutreachError(f"The {label} is blank; set it before starting.")
            clinics = (await session.execute(select(func.count(ClinicOutreach.id)).where(
                ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id))).scalar_one()
            if not clinics:
                raise OutreachError("The campaign has no clinics; create a new campaign with at least one clinic.")
            campaign.status = CampaignStatus.ACTIVE
            cfg.mode = OutreachMode.SHADOW
            if cfg.shadow_started_at is None:
                cfg.shadow_started_at = _now()
            session.add(self._audit(tenant_id, user_id, "outreach.campaign_started", campaign.id, {"mode": OutreachMode.SHADOW.value}))
            await session.commit()

    async def pause(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, campaign_id: uuid.UUID) -> None:
        async with self._sf() as session:
            campaign, _ = await self._load(session, tenant_id, campaign_id)
            if campaign.status != CampaignStatus.ACTIVE:
                raise OutreachError(f"This campaign is {campaign.status}; only an active campaign can be paused.")
            campaign.status = CampaignStatus.PAUSED
            session.add(self._audit(tenant_id, user_id, "outreach.campaign_paused", campaign.id, None))
            await session.commit()

    async def stop(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, campaign_id: uuid.UUID) -> None:
        async with self._sf() as session:
            campaign, _ = await self._load(session, tenant_id, campaign_id)
            if campaign.status not in HOLDING_STATUSES:
                raise OutreachError(f"This campaign is already {campaign.status}.")
            campaign.status = CampaignStatus.COMPLETED
            rows = (await session.execute(select(ClinicOutreach).where(
                ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id))).scalars().all()
            for row in rows:
                row.next_action_at = None
            session.add(self._audit(tenant_id, user_id, "outreach.campaign_stopped", campaign.id, None))
            await session.commit()

    # --- wording blocks ----------------------------------------------------------------------------------------------

    @staticmethod
    def _blocks_view(value: dict[str, Any]) -> dict[str, Any]:
        return {"status": value.get("status", "DRAFT"), "version": int(value.get("version", 0) or 0), "approved_by": value.get("approved_by"),
                "approved_at": value.get("approved_at"), "blocks": dict(value.get("blocks") or {})}

    async def get_blocks(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        return self._blocks_view(await TenantSettingsService(self._sf).get(tenant_id, OUTREACH_BLOCKS))

    async def save_blocks(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, blocks: dict[str, str]) -> dict[str, Any]:
        problems = validate_blocks(blocks)
        if problems:
            raise OutreachError("The wording has problems; fix them and save again.", problems)
        current = await self.get_blocks(tenant_id)
        value = {"status": "DRAFT", "version": current["version"] + 1, "approved_by": None, "approved_at": None, "blocks": dict(blocks)}
        await TenantSettingsService(self._sf).set(tenant_id, OUTREACH_BLOCKS, value, updated_by=user_id)
        return self._blocks_view(value)

    async def approve_blocks(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> dict[str, Any]:
        current = await self.get_blocks(tenant_id)
        if current["status"] == "APPROVED":
            raise OutreachError("This wording is already approved.")
        problems = validate_blocks(current["blocks"])
        if problems:
            raise OutreachError("The wording has problems; fix them before approving.", problems)
        value = {**current, "status": "APPROVED", "approved_by": str(user_id) if user_id else None, "approved_at": _now().isoformat()}
        await TenantSettingsService(self._sf).set(tenant_id, OUTREACH_BLOCKS, value, updated_by=user_id)
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="outreach.blocks_approved", entity_type="outreach_blocks",
                                 entity_id=tenant_id, input_summary={"tenant_id": str(tenant_id), "approved_by": str(user_id) if user_id else None,
                                                                      "version": current["version"]}, result="success"))
            await session.commit()
        return self._blocks_view(value)

    async def blocks_approved(self, tenant_id: uuid.UUID) -> bool:
        try:
            return (await self.get_blocks(tenant_id))["status"] == "APPROVED"
        except Exception:  # noqa: BLE001 - never raises; an unreadable wording is treated as not approved
            return False

    # --- suppression -------------------------------------------------------------------------------------------------

    async def suppress(self, tenant_id: uuid.UUID, value: str, kind: str, reason: str) -> None:
        value, kind = _norm(value), _norm(kind)
        if kind not in SUPPRESSION_KINDS:
            raise OutreachError("kind must be 'address' or 'domain'.")
        if kind == "address":
            ok = bool(_STRICT_ADDRESS.fullmatch(value))
        else:
            ok = bool(_STRICT_DOMAIN.fullmatch(value))
        if not ok:
            raise OutreachError("Give a plain full email address for kind 'address', or a bare domain such as clinic.com for kind 'domain'.")
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            existing = (await session.execute(select(OutreachSuppression).where(
                OutreachSuppression.tenant_id == tenant_id, OutreachSuppression.value == value))).scalar_one_or_none()
            if existing is None:
                session.add(OutreachSuppression(tenant_id=tenant_id, kind=kind, value=value, reason=(reason or "").strip()[:255] or None))
                await session.commit()

    async def list_suppressions(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            rows = (await session.execute(select(OutreachSuppression).where(OutreachSuppression.tenant_id == tenant_id)
                                          .order_by(OutreachSuppression.kind, OutreachSuppression.value))).scalars().all()
        return [{"kind": r.kind, "value": r.value, "reason": r.reason} for r in rows]

    async def is_suppressed(self, tenant_id: uuid.UUID, email: str) -> bool:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            return await self.is_suppressed_in(session, tenant_id, email)

    @staticmethod
    async def is_suppressed_in(session, tenant_id: uuid.UUID, email: str) -> bool:
        """The same check on a caller's open session (so it joins that caller's transaction instead of opening another)."""
        address = _norm(email)
        if not address:
            return False
        candidates = [address]
        if "@" in address:
            candidates.append(address.rsplit("@", 1)[1])
        hit = (await session.execute(select(OutreachSuppression.id).where(
            OutreachSuppression.tenant_id == tenant_id, OutreachSuppression.value.in_(candidates)).limit(1))).first()
        return hit is not None

    # --- helpers -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _audit(tenant_id: uuid.UUID, actor_id: uuid.UUID | None, action: str, campaign_id: uuid.UUID, summary: dict[str, Any] | None,
               actor_type: str = ActorType.USER) -> AuditLog:
        return AuditLog(tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id, action=action, entity_type="campaign", entity_id=campaign_id,
                        input_summary=summary, result="success")
