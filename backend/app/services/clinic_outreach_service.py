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
from sqlalchemy.exc import IntegrityError

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.clinic_outreach import (ClinicOutreach, ClinicOutreachCampaign, ClinicOutreachMessage, OutreachMessageKind, OutreachMessageStatus, OutreachMode, OutreachStage,
                                        OutreachSuppression)
from app.models.marketing import Campaign, CampaignChannel, CampaignObjective, CampaignStatus
from app.models.medical_tourism import Provider, ProviderStatus
from app.models.mt_revenue import ProviderTerms
from app.services import clinic_contact_finder
from app.services.clinic_contact_finder import _STRICT_ADDRESS, _STRICT_DOMAIN, dns_mx_exists, usable_address
from app.services.mt_clinic_sourcing import SearchProvider, search_provider_from_settings
from app.services.outreach_blocks import validate_blocks
from app.services.outreach_composer import ClinicRef, check, compose
from app.services.outreach_reply_classifier import ReplyLabel, classify
from app.services.outreach_reply_composer import plan_reply
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

    # --- shadow review -----------------------------------------------------------------------------------------------

    @staticmethod
    def _message_view(m: ClinicOutreachMessage, clinic: str, provider_id: uuid.UUID, *, body: bool = False) -> dict[str, Any]:
        out = {"id": str(m.id), "provider_id": str(provider_id), "clinic": clinic, "kind": m.kind, "status": m.status, "subject": m.subject,
               "scheduled_for": m.scheduled_for.isoformat() if m.scheduled_for else None, "review_mark": m.review_mark, "review_reason": m.review_reason,
               "blocked_reason": m.blocked_reason}
        if body:
            out["body"] = m.body
        return out

    async def list_messages(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, *, stage: str | None = None, status: str | None = None, review: str | None = None,
                            limit: int = 50, offset: int = 0) -> dict[str, Any]:
        async with self._sf() as session:
            await self._load(session, tenant_id, campaign_id)
            conds = [ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id]
            if stage:
                conds.append(ClinicOutreach.stage == stage)
            if status:
                conds.append(ClinicOutreachMessage.status == status)
            if review == "UNREVIEWED":
                conds.append(ClinicOutreachMessage.review_mark.is_(None))
            elif review:
                conds.append(ClinicOutreachMessage.review_mark == review)
            base = select(ClinicOutreachMessage, Provider).join(ClinicOutreach, ClinicOutreach.id == ClinicOutreachMessage.outreach_id).join(Provider, Provider.id == ClinicOutreach.provider_id).where(*conds)
            total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
            rows = (await session.execute(base.order_by(ClinicOutreachMessage.scheduled_for, ClinicOutreachMessage.id).limit(limit).offset(offset))).all()
        return {"items": [self._message_view(m, p.name, p.id) for m, p in rows], "total": int(total)}

    async def _message(self, session, tenant_id: uuid.UUID, campaign_id: uuid.UUID, message_id: uuid.UUID) -> tuple[ClinicOutreachMessage, Provider]:
        await self._load(session, tenant_id, campaign_id)
        row = (await session.execute(
            select(ClinicOutreachMessage, Provider)
            .join(ClinicOutreach, ClinicOutreach.id == ClinicOutreachMessage.outreach_id).join(Provider, Provider.id == ClinicOutreach.provider_id)
            .where(ClinicOutreachMessage.id == message_id, ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreach.tenant_id == tenant_id,
                   ClinicOutreach.campaign_id == campaign_id))).first()
        if row is None:
            raise OutreachNotFound("Outreach message not found.")
        return row[0], row[1]

    async def get_message(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, message_id: uuid.UUID) -> dict[str, Any]:
        async with self._sf() as session:
            m, p = await self._message(session, tenant_id, campaign_id, message_id)
            return self._message_view(m, p.name, p.id, body=True)

    async def review_message(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, campaign_id: uuid.UUID, message_id: uuid.UUID, mark: str | None, reason: str | None) -> None:
        if mark not in (None, "GOOD", "FLAGGED"):
            raise OutreachError("The mark must be GOOD, FLAGGED or empty.")
        reason = (reason or "").strip()
        if mark == "FLAGGED" and not reason:
            raise OutreachError("Say what is wrong with a flagged message.")
        async with self._sf() as session:
            m, _ = await self._message(session, tenant_id, campaign_id, message_id)
            m.review_mark = mark
            m.review_reason = reason[:500] if mark == "FLAGGED" else None
            m.reviewed_by = user_id if mark else None
            m.reviewed_at = _now() if mark else None
            session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="outreach.message_reviewed", entity_type="clinic_outreach_message",
                                 entity_id=m.id, input_summary={"campaign_id": str(campaign_id), "message_id": str(m.id), "mark": mark}, result="success"))
            await session.commit()

    async def preview_sequence(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, provider_id: uuid.UUID) -> list[dict[str, Any]]:
        """What the three emails would say for one clinic, from the approved wording. Nothing is stored and no clinic moves on."""
        blocks = await self.get_blocks(tenant_id)
        if blocks["status"] != "APPROVED":
            raise OutreachError("Approve the wording before previewing a sequence.")
        async with self._sf() as session:
            _, cfg = await self._load(session, tenant_id, campaign_id)
            provider = (await session.execute(
                select(Provider).join(ClinicOutreach, ClinicOutreach.provider_id == Provider.id)
                .where(Provider.id == provider_id, Provider.tenant_id == tenant_id, ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id))).scalar_one_or_none()
            if provider is None:
                raise OutreachNotFound("That clinic is not in this campaign.")
            config = self._config(cfg)
        ref = ClinicRef(name=provider.name)
        out = []
        for kind in (OutreachMessageKind.INTRO, OutreachMessageKind.FOLLOWUP_1, OutreachMessageKind.FOLLOWUP_2):
            subject, body = compose(kind, ref, config.sender, config.term_sheet, blocks["blocks"])
            out.append({"kind": kind.value, "subject": subject, "body": body,
                        "problems": check(subject, body, clinic=ref, sender=config.sender, opt_out_line=blocks["blocks"]["opt_out_line"])})
        return out

    async def shadow_progress(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, *, with_checks: bool = True) -> dict[str, Any]:
        blocks = await self.get_blocks(tenant_id)
        async with self._sf() as session:
            _, cfg = await self._load(session, tenant_id, campaign_id)
            msgs = (await session.execute(
                select(ClinicOutreachMessage.status, ClinicOutreachMessage.review_mark, func.count())
                .join(ClinicOutreach, ClinicOutreach.id == ClinicOutreachMessage.outreach_id)
                .where(ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id)
                .group_by(ClinicOutreachMessage.status, ClinicOutreachMessage.review_mark))).all()
            clinics = (await session.execute(
                select(ClinicOutreach, Provider).join(Provider, Provider.id == ClinicOutreach.provider_id)
                .where(ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id, Provider.tenant_id == tenant_id)
                .order_by(Provider.name))).all()
        shadow = sum(n for st, _, n in msgs if st == OutreachMessageStatus.QUEUED_SHADOW)
        blocked = sum(n for st, _, n in msgs if st == OutreachMessageStatus.BLOCKED)
        reviewed = sum(n for _, mark, n in msgs if mark)
        flagged = sum(n for _, mark, n in msgs if mark == "FLAGGED")
        needs_you = [{"provider_id": str(p.id), "clinic": p.name, "reason": o.handoff_reason or "needs a business email address"}
                     for o, p in clinics if o.stage in (OutreachStage.NEEDS_PERSON, OutreachStage.NO_EMAIL)]
        next_kind = {OutreachStage.READY: OutreachMessageKind.INTRO, OutreachStage.INTRO_SENT: OutreachMessageKind.FOLLOWUP_1, OutreachStage.FOLLOWUP_1: OutreachMessageKind.FOLLOWUP_2}
        due = [(o, p) for o, p in clinics if o.stage in next_kind]
        due.sort(key=lambda r: (r[0].next_action_at is not None, r[0].next_action_at or _now(), str(r[0].id)))
        next_sends = [{"provider_id": str(p.id), "clinic": p.name, "kind": next_kind[o.stage].value, "next_action_at": o.next_action_at.isoformat() if o.next_action_at else None}
                      for o, p in due[:10]]
        started = cfg.shadow_started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        out = {"days_in_shadow": max(0, (_now() - started).days) if started else 0, "shadow_messages": int(shadow), "reviewed": int(reviewed), "flagged": int(flagged),
                "flagged_share": round(flagged / reviewed, 3) if reviewed else 0.0, "blocked": int(blocked),
                "engine_last_run_at": cfg.engine_last_run_at.isoformat() if cfg.engine_last_run_at else None, "blocks_status": blocks["status"],
                "needs_you": needs_you, "next_sends": next_sends,
                "try_a_reply_tests": int(((cfg.exit_checks or {}).get("reply_tests") or {}).get("total", 0)), "try_a_reply_available": True}
        if with_checks:
            out["golive_checks"] = await self.golive_checks(tenant_id, campaign_id, progress=out)
        return out

    # --- replies (Phase 3: classify, plan and draft; nothing is sent) ------------------------------------------------

    REPLY_IN, REPLY_OUT = "reply_in", "reply_out"
    REPLY_TEST_KINDS = ("interested", "terms", "opt_out", "legal", "unclear")

    @staticmethod
    def _test_kind(label: ReplyLabel, confidence: float, handoff_reason: str | None) -> str | None:
        if label in (ReplyLabel.INTERESTED, ReplyLabel.ASKS_WHO):
            return "interested"
        if label is ReplyLabel.ASKS_TERMS:
            return "terms"
        if label is ReplyLabel.OPT_OUT:
            return "opt_out"
        if label is ReplyLabel.HANDOFF and "legal" in (handoff_reason or "").lower():
            return "legal"
        if label is ReplyLabel.OTHER:
            return "unclear"
        return None

    @staticmethod
    def _plan_view(label: ReplyLabel, confidence: float, reasons: tuple[str, ...], plan) -> dict[str, Any]:
        return {"label": label.value, "confidence": confidence, "reasons": list(reasons), "next_stage": plan.next_stage, "suppress": plan.suppress,
                "handoff_reason": plan.handoff_reason, "problems": list(plan.problems),
                "draft": {"subject": plan.draft[0], "body": plan.draft[1]} if plan.draft else None}

    async def try_reply(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, campaign_id: uuid.UUID, text: str, provider_id: uuid.UUID | None = None) -> dict[str, Any]:
        """Classify a sample clinic reply and show the answer the engine would draft. Stores nothing about the clinic and sends nothing; it only counts that a test of that kind ran."""
        text = (text or "").strip()
        if not text:
            raise OutreachError("Paste the clinic's reply to try.")
        async with self._sf() as session:
            await self._load(session, tenant_id, campaign_id)  # another tenant's campaign is a 404 before anything else is said
        blocks = await self.get_blocks(tenant_id)
        if blocks["status"] != "APPROVED":
            raise OutreachError("Approve the wording before trying a reply.")
        async with self._sf() as session:
            _, cfg = await self._load(session, tenant_id, campaign_id)
            config = self._config(cfg)
            name, stage = "Example Clinic", None
            if provider_id is not None:
                row = (await session.execute(
                    select(ClinicOutreach, Provider).join(Provider, Provider.id == ClinicOutreach.provider_id)
                    .where(ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id, ClinicOutreach.provider_id == provider_id,
                           Provider.tenant_id == tenant_id))).first()
                if row is None:
                    raise OutreachNotFound("That clinic is not in this campaign.")
                name, stage = row[1].name, row[0].stage
        c = classify(text)
        plan = plan_reply(c, stage=stage, clinic=ClinicRef(name), sender=config.sender, term_sheet=config.term_sheet, blocks=blocks["blocks"])
        kind = self._test_kind(c.label, c.confidence, c.handoff_reason)
        async with self._sf() as session:
            campaign, cfg = await self._load(session, tenant_id, campaign_id)
            checks = dict(cfg.exit_checks or {})
            tests = dict(checks.get("reply_tests") or {})
            if kind:
                tests[kind] = int(tests.get(kind, 0)) + 1
            tests["total"] = int(tests.get("total", 0)) + 1
            checks["reply_tests"] = tests
            cfg.exit_checks = checks
            session.add(self._audit(tenant_id, user_id, "outreach.reply_tested", campaign.id, {"campaign_id": str(campaign.id), "label": c.label.value, "kind": kind}))
            await session.commit()
        return self._plan_view(c.label, c.confidence, c.reasons, plan)

    async def handle_reply(self, tenant_id: uuid.UUID, user_id: uuid.UUID | None, campaign_id: uuid.UUID, provider_id: uuid.UUID, text: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Apply the reply action table to one clinic of a SHADOW campaign: record the reply, move the clinic, suppress or clear its address where the table says so, and store the
        drafted answer in shadow. Nothing is sent. A live campaign is refused until sending is built."""
        now = now or _now()
        text = (text or "").strip()
        if not text:
            raise OutreachError("The reply is empty.")
        async with self._sf() as session:
            await self._load(session, tenant_id, campaign_id)  # another tenant's campaign is a 404 before anything else is said
        blocks = await self.get_blocks(tenant_id)
        if blocks["status"] != "APPROVED":
            raise OutreachError("Approve the wording before handling replies.")
        async with self._sf() as session:
            _, cfg = await self._load(session, tenant_id, campaign_id)
            if cfg.mode != OutreachMode.SHADOW:
                raise OutreachError("Replies in a live campaign are handled in a later release.")
            config = self._config(cfg)
            found = (await session.execute(
                select(ClinicOutreach, Provider).join(Provider, Provider.id == ClinicOutreach.provider_id)
                .where(ClinicOutreach.tenant_id == tenant_id, ClinicOutreach.campaign_id == campaign_id, ClinicOutreach.provider_id == provider_id, Provider.tenant_id == tenant_id)
                .with_for_update())).first()
            if found is None:
                raise OutreachNotFound("That clinic is not in this campaign.")
            row, provider = found
            c = classify(text)
            last_in = (await session.execute(
                select(ClinicOutreachMessage.status).where(ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreachMessage.outreach_id == row.id, ClinicOutreachMessage.kind == self.REPLY_IN)
                .order_by(ClinicOutreachMessage.scheduled_for.desc(), ClinicOutreachMessage.id.desc()).limit(1))).scalar_one_or_none()
            answered = (await session.execute(
                select(func.count(ClinicOutreachMessage.id)).where(ClinicOutreachMessage.tenant_id == tenant_id, ClinicOutreachMessage.outreach_id == row.id,
                                                                   ClinicOutreachMessage.kind == self.REPLY_OUT))).scalar_one()
            repeated = bool(last_in == f"IN_{c.label.value}" and answered)
            plan = plan_reply(c, stage=row.stage, clinic=ClinicRef(provider.name), sender=config.sender, term_sheet=config.term_sheet, blocks=blocks["blocks"], repeated=repeated)
            session.add(ClinicOutreachMessage(tenant_id=tenant_id, outreach_id=row.id, direction="IN", kind=self.REPLY_IN, status=f"IN_{c.label.value}", subject="(clinic reply)",
                                              body=text[:5000], blocks_version=int(blocks["version"]), scheduled_for=now, schedule_day=now.date()))
            if plan.draft:
                session.add(ClinicOutreachMessage(tenant_id=tenant_id, outreach_id=row.id, direction="OUT", kind=self.REPLY_OUT, status=OutreachMessageStatus.QUEUED_SHADOW,
                                                  subject=plan.draft[0], body=plan.draft[1], blocks_version=int(blocks["version"]), scheduled_for=now, schedule_day=now.date()))
            row.last_reply_at = now
            if plan.next_stage:
                row.stage = plan.next_stage
            if plan.next_stage == OutreachStage.NEEDS_PERSON:
                row.handoff_reason = plan.handoff_reason
            if plan.suppress and row.contact_email:
                address = _norm(row.contact_email)
                if not (await session.execute(select(OutreachSuppression.id).where(OutreachSuppression.tenant_id == tenant_id, OutreachSuppression.value == address))).first():
                    session.add(OutreachSuppression(tenant_id=tenant_id, kind="address", value=address, reason=f"clinic reply: {c.label.value.lower()}"[:255]))
            if plan.next_stage in (OutreachStage.OPTED_OUT, OutreachStage.DECLINED, OutreachStage.NEEDS_PERSON, OutreachStage.NO_EMAIL):
                row.next_action_at = None
            if plan.clear_contact:
                row.contact_email, row.contact_source, row.contact_source_url = None, None, None
            if plan.reschedule and row.next_action_at is not None:
                row.next_action_at = max(row.next_action_at if row.next_action_at.tzinfo else row.next_action_at.replace(tzinfo=UTC), now + plan.reschedule)
            session.add(self._audit(tenant_id, user_id, "outreach.reply_handled", campaign_id, {"campaign_id": str(campaign_id), "outreach_id": str(row.id), "label": c.label.value,
                                                                                             "next_stage": plan.next_stage}))
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise OutreachError("A reply from this clinic was already recorded today.") from exc
        return self._plan_view(c.label, c.confidence, c.reasons, plan)

    async def golive_checks(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, *, progress: dict[str, Any] | None = None) -> dict[str, Any]:
        """The conditions the owner's Go live decision will need (Phase 4 enables the button). Read only."""
        progress = progress or await self.shadow_progress(tenant_id, campaign_id, with_checks=False)
        async with self._sf() as session:
            _, cfg = await self._load(session, tenant_id, campaign_id)
            tests = dict((cfg.exit_checks or {}).get("reply_tests") or {})
            try:
                self._config(cfg)
                settings_ok = True
            except ValidationError:
                settings_ok = False
        covered = [k for k in self.REPLY_TEST_KINDS if int(tests.get(k, 0)) > 0]
        checks = {
            "shadow_days": {"required": 7, "actual": progress["days_in_shadow"], "ok": progress["days_in_shadow"] >= 7},
            "reviewed": {"required": 20, "actual": progress["reviewed"], "flagged_share": progress["flagged_share"], "max_flagged_share": 0.1,
                         "ok": progress["reviewed"] >= 20 and progress["flagged_share"] < 0.1},
            "reply_tests": {"required_kinds": list(self.REPLY_TEST_KINDS), "covered": covered, "total": int(tests.get("total", 0)), "ok": len(covered) == len(self.REPLY_TEST_KINDS)},
            "settings": {"ok": settings_ok},
        }
        checks["all_ok"] = all(v["ok"] for v in checks.values() if isinstance(v, dict))
        return checks

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
