"""Dropshipping: the daily stock and price sync of one supplier's CSV feed (rules in dropshipping_sync_rules.py).

Where the feed comes from: a link the supplier gives (https only, fetched by the server) or a CSV pasted/uploaded by staff for a one-off run. A run is a PREVIEW (dry run) unless
staff apply it; the daily job applies. Every applied run writes one audit row and a stock snapshot per row that carried a quantity, and the supplier remembers the last run's
summary so staff can see what the job did. A fetch that fails changes nothing and is remembered too.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.dropshipping import InventorySnapshot, Sku, SkuStatus, Supplier
from app.services.dropshipping_catalog_import import CatalogValidationError
from app.services.dropshipping_catalog_service import CatalogNotFoundError, supplier_to_dict
from app.services.dropshipping_sync_rules import MAX_FEED_CHARS, FeedError, SkuView, parse_feed, plan_sync

logger = structlog.get_logger(__name__)

FETCH_TIMEOUT_SECONDS = 20.0
DUE_AFTER = timedelta(hours=23)


class FeedFetchError(Exception):
    pass


def _check_public_https(url: str) -> None:
    """Refuse anything but an https address on the public internet: the server must never be talked into reading its own network."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise FeedFetchError("The feed link must be an https address.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FeedFetchError("The feed address could not be found.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise FeedFetchError("The feed link points to a private address, which is not allowed.")


async def fetch_feed(url: str) -> str:
    await asyncio.to_thread(_check_public_https, url)
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"Accept": "text/csv, text/plain, */*"}) as response:
                if response.status_code != 200:
                    raise FeedFetchError(f"The feed answered with status {response.status_code}.")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_FEED_CHARS * 2:
                        raise FeedFetchError("The feed is too large.")
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise FeedFetchError("The feed could not be downloaded.") from exc
    return b"".join(chunks).decode("utf-8-sig", errors="replace")


def _dec(value: object, name: str, lo: Decimal, hi: Decimal, problems: list[str], *, allow_none: bool = False) -> Decimal | None:
    if value is None or value == "":
        if not allow_none:
            problems.append(f"{name} is required")
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        problems.append(f"{name} must be a number")
        return None
    if not lo <= d <= hi:
        problems.append(f"{name} must be between {lo} and {hi}")
        return None
    return d.quantize(Decimal("0.01"))


class DropshippingSyncService:
    def __init__(self, session_factory: async_sessionmaker, fetcher=None) -> None:
        self._session_factory = session_factory
        self._fetch = fetcher or fetch_feed

    async def _supplier(self, session, tenant_id: uuid.UUID, supplier_id: uuid.UUID) -> Supplier:
        row = (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.id == supplier_id))).scalar_one_or_none()
        if row is None:
            raise CatalogNotFoundError("Supplier not found")
        return row

    async def save_settings(self, tenant_id: uuid.UUID, supplier_id: uuid.UUID, body: dict, *, actor_id: uuid.UUID | None = None) -> dict:
        problems: list[str] = []
        url = (body.get("feed_url") or "").strip() or None
        if url is not None and (not url.lower().startswith("https://") or len(url) > 500 or any(c.isspace() for c in url)):
            problems.append("feed_url must be an https address of at most 500 characters")
        markup = _dec(body.get("markup_percent"), "markup_percent", Decimal(0), Decimal(500), problems, allow_none=True)
        max_move = _dec(body.get("max_price_change_percent", 30), "max_price_change_percent", Decimal(1), Decimal(100), problems)
        try:
            buffer = int(body.get("stock_buffer", 0))
            if not 0 <= buffer <= 1000:
                raise ValueError
        except (TypeError, ValueError):
            problems.append("stock_buffer must be a whole number from 0 to 1000")
            buffer = 0
        enabled = bool(body.get("sync_enabled", False))
        if enabled and url is None:
            problems.append("sync_enabled needs a feed_url (or leave the daily sync off and paste a CSV by hand)")
        if problems:
            raise CatalogValidationError(problems)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            s = await self._supplier(session, tenant_id, supplier_id)
            s.feed_url, s.markup_percent, s.stock_buffer, s.max_price_change_percent, s.sync_enabled = url, markup, buffer, max_move, enabled
            session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=actor_id, action="dropshipping.supplier.sync_settings", entity_type="dropshipping_supplier",
                                 entity_id=s.id, input_summary={"feed_url_set": bool(url), "markup_percent": str(markup) if markup is not None else None, "stock_buffer": buffer,
                                                                 "max_price_change_percent": str(max_move), "sync_enabled": enabled}, result="success"))
            await session.commit()
            return supplier_to_dict(s)

    async def run(self, tenant_id: uuid.UUID, supplier_id: uuid.UUID, *, csv_text: str | None = None, dry_run: bool = True, force: bool = False,
                  actor_id: uuid.UUID | None = None, source: str = "MANUAL") -> dict:
        """Returns the run's summary. Raises CatalogNotFoundError / CatalogValidationError (a bad feed or a feed that could not be read)."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            s = await self._supplier(session, tenant_id, supplier_id)
            url, markup, buffer, max_move = s.feed_url, s.markup_percent, s.stock_buffer, s.max_price_change_percent
        try:
            text = csv_text if csv_text is not None else await self._fetch(url) if url else None
            if text is None:
                raise CatalogValidationError(["This supplier has no feed link: set one, or paste the CSV."])
            feed = parse_feed(text)
        except (FeedError, FeedFetchError) as exc:
            if not dry_run:
                await self._remember(tenant_id, supplier_id, {"applied": False, "error": str(exc), "source": source})
            raise CatalogValidationError([str(exc)]) from exc
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            s = await self._supplier(session, tenant_id, supplier_id)
            skus = list((await session.execute(select(Sku).where(Sku.tenant_id == tenant_id, Sku.supplier_id == supplier_id, Sku.status == SkuStatus.ACTIVE.value))).scalars())
            views = [SkuView(id=str(k.id), sku_code=k.sku_code, supplier_sku=k.supplier_sku or "", price=Decimal(k.price), supplier_cost=Decimal(k.supplier_cost) if k.supplier_cost is not None else None,
                             availability=k.availability) for k in skus]
            plan = plan_sync(views, feed, markup_percent=Decimal(markup) if markup is not None else None, stock_buffer=int(buffer or 0), max_price_change_percent=Decimal(max_move), force=force)
            if not dry_run and plan.changes:
                by_id = {str(k.id): k for k in skus}
                now = datetime.now(timezone.utc)
                for ch in plan.changes:
                    k = by_id[ch.sku_id]
                    if ch.availability is not None:
                        k.availability = ch.availability
                    if ch.supplier_cost is not None:
                        k.supplier_cost = ch.supplier_cost
                    if ch.price is not None:
                        k.price = ch.price
                    if ch.quantity is not None:
                        session.add(InventorySnapshot(tenant_id=tenant_id, sku_id=k.id, quantity=ch.quantity, source="FEED", observed_at=now))
            if not dry_run:
                plan.applied = bool(plan.changes) or plan.matched > 0
                summary = {**plan.summary(), "source": source}
                s.last_sync_at, s.last_sync_summary = datetime.now(timezone.utc), summary
                session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER if actor_id else ActorType.SYSTEM, actor_id=actor_id, action="dropshipping.supplier.sync", entity_type="dropshipping_supplier",
                                     entity_id=s.id, input_summary={k: v for k, v in summary.items() if k not in ("unknown_in_feed_sample", "held_prices", "problems")}, result="success"))
                await session.commit()
            else:
                await session.rollback()
        return {**plan.summary(), "dry_run": dry_run, "source": source}

    async def _remember(self, tenant_id: uuid.UUID, supplier_id: uuid.UUID, summary: dict) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            s = await self._supplier(session, tenant_id, supplier_id)
            s.last_sync_at, s.last_sync_summary = datetime.now(timezone.utc), summary
            await session.commit()

    async def run_due(self, tenant_id: uuid.UUID) -> int:
        """The daily job for the store's tenant: every supplier with the sync on whose last run is older than 23 hours. One supplier's trouble never stops the next."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            cutoff = datetime.now(timezone.utc) - DUE_AFTER
            due = [s.id for s in (await session.execute(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.sync_enabled.is_(True), Supplier.feed_url.is_not(None)))).scalars()
                   if s.last_sync_at is None or _aware(s.last_sync_at) < cutoff]
        ran = 0
        for supplier_id in due:
            try:
                await self.run(tenant_id, supplier_id, dry_run=False, source="DAILY")
                ran += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("supplier_sync_failed", supplier_id=str(supplier_id), error_type=type(exc).__name__)
        return ran


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def run_supplier_sync_sweeper(service: DropshippingSyncService, tenant_id_getter, stop: "asyncio.Event", every_seconds: float = 1800.0) -> None:
    """Background loop: the store's suppliers sync about once a day without anyone pressing a button. `tenant_id_getter` returns the store's tenant (None while no store is set)."""
    while not stop.is_set():
        try:
            tenant_id = tenant_id_getter()
            if tenant_id is not None:
                await service.run_due(tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("supplier_sync_sweeper_failed", error_type=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_seconds)
        except asyncio.TimeoutError:
            pass
