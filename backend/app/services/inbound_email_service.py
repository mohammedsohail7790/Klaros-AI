"""Replies to the emails a business sends, brought back into Klaros.

Out:  every email to a patient or a clinic carries a hidden Reply-To of the form `r-<token>@<reply domain>`. The token maps to that one person's record (a lead or a clinic).
In:   the email provider receives mail for the reply domain, parses it and POSTs it to the webhook. Each reply is filed against the record its token names:
        - a patient's reply becomes a note on the enquiry and a notification to the team;
        - a clinic's reply becomes a stored reply and a notification.
Rules that matter:
  - Nothing happens unless the reply domain is configured, and an unknown token is ignored (the provider is told "received" so it does not retry forever).
  - A reply is stored once (its Message-ID), never twice.
  - For a business whose patients must give consent, what a patient wrote is kept only if their consent covers medical information. Otherwise a reply is recorded WITHOUT its text and the team
    is told to ask the patient to confirm consent: the platform never quietly keeps health details it has no consent for.
  - Attachments are never downloaded or stored (only counted). Text is cut to a fixed length. Likely spam is dropped. Everything a sender wrote is DATA: it is labelled as an untrusted reply.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from html import unescape
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.crm import Lead, LeadNote
from app.models.inbound_email import EmailReplyRoute, InboundEmail
from app.models.medical_tourism import Provider
from app.models.notification import Notification

logger = structlog.get_logger(__name__)

MAX_BODY_CHARS = 8000
SPAM_DROP_SCORE = 10.0
TOKEN_PREFIX = "r-"
_ADDR = re.compile(r"^[^@\s<>]+@[^@\s<>]+$")


def reply_domain() -> str | None:
    d = (get_settings().INBOUND_REPLY_DOMAIN or "").strip().lower().strip(".")
    return d or None


def reply_address(token: str) -> str | None:
    d = reply_domain()
    return f"{TOKEN_PREFIX}{token}@{d}" if d else None


async def ensure_route(session_factory, tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID) -> str:
    async with session_factory() as session:
        existing = (await session.execute(select(EmailReplyRoute).where(EmailReplyRoute.tenant_id == tenant_id, EmailReplyRoute.entity_type == entity_type, EmailReplyRoute.entity_id == entity_id))).scalar_one_or_none()
        if existing is not None:
            return existing.token
        route = EmailReplyRoute(token=secrets.token_hex(8), tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id)
        session.add(route)
        try:
            await session.commit()
            return route.token
        except IntegrityError:  # a concurrent send made it first
            await session.rollback()
            return (await session.execute(select(EmailReplyRoute.token).where(EmailReplyRoute.tenant_id == tenant_id, EmailReplyRoute.entity_type == entity_type,
                                                                              EmailReplyRoute.entity_id == entity_id))).scalar_one()


async def reply_to_for(session_factory, tenant_id: uuid.UUID, to_email: str) -> str | None:
    """The hidden reply address for an email going to `to_email`, or None (replies off, or the address is nobody we know). Never raises."""
    if reply_domain() is None:
        return None
    try:
        addr = (to_email or "").strip().lower()
        async with session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead_id = (await session.execute(select(Lead.id).where(Lead.tenant_id == tenant_id, func.lower(Lead.email) == addr).order_by(Lead.created_at.desc()).limit(1))).scalar_one_or_none()
            provider_id = None
            if lead_id is None:
                provider_id = (await session.execute(select(Provider.id).where(Provider.tenant_id == tenant_id, func.lower(Provider.contact_email) == addr).limit(1))).scalar_one_or_none()
        if lead_id is not None:
            return reply_address(await ensure_route(session_factory, tenant_id, "lead", lead_id))
        if provider_id is not None:
            return reply_address(await ensure_route(session_factory, tenant_id, "provider", provider_id))
    except Exception as exc:  # noqa: BLE001 - a routing problem must never stop an email
        logger.warning("reply_to_lookup_failed", error=type(exc).__name__)
    return None


def _addr(box: Any) -> tuple[str, str | None]:
    if isinstance(box, dict):
        return str(box.get("Address") or box.get("address") or "").strip().lower(), (box.get("Name") or box.get("name") or None)
    return str(box or "").strip().lower(), None


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", unescape(text)).strip()


def _body(item: dict) -> str:
    text = item.get("ExtractedMarkdownMessage") or item.get("RawTextBody") or ""
    if not str(text).strip() and item.get("RawHtmlBody"):
        text = _strip_html(str(item["RawHtmlBody"]))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_BODY_CHARS]


def _token_from(item: dict) -> str | None:
    d = reply_domain()
    boxes = list(item.get("To") or []) + list(item.get("Recipients") or []) + list(item.get("Cc") or [])
    for b in boxes:
        address, _ = _addr(b)
        local, _, domain = address.partition("@")
        if domain == d and local.startswith(TOKEN_PREFIX) and len(local) > len(TOKEN_PREFIX):
            return local[len(TOKEN_PREFIX):]
    return None


def _spam(item: dict) -> float | None:
    raw = (item.get("Spam") or {}).get("Score") if isinstance(item.get("Spam"), dict) else item.get("SpamScore")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def handle_payload(session_factory, payload: dict) -> dict[str, int]:
    result = {"received": 0, "stored": 0, "duplicate": 0, "unknown": 0, "spam": 0, "invalid": 0}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return result
    for item in items[:50]:
        result["received"] += 1
        try:
            outcome = await _handle_one(session_factory, item if isinstance(item, dict) else {})
        except Exception as exc:  # noqa: BLE001 - one bad message must not lose the others or make the provider retry the batch
            logger.warning("inbound_email_failed", error=type(exc).__name__)
            outcome = "invalid"
        result[outcome] += 1
    return result


async def _handle_one(session_factory, item: dict) -> str:
    from app.services import consent_gate, halla_consent

    token = _token_from(item)
    message_id = str(item.get("MessageId") or "").strip()[:500]
    sender, sender_name = _addr(item.get("From"))
    if token is None:
        return "unknown"
    if not message_id or not _ADDR.match(sender):
        return "invalid"
    spam = _spam(item)
    if spam is not None and spam >= SPAM_DROP_SCORE:
        return "spam"
    async with session_factory() as session:
        route = (await session.execute(select(EmailReplyRoute).where(EmailReplyRoute.token == token))).scalar_one_or_none()
    if route is None:
        return "unknown"
    tenant_id = route.tenant_id
    subject = re.sub(r"[\r\n\x00-\x1f]", " ", str(item.get("Subject") or ""))[:500].strip() or None
    body = _body(item)
    attachments = len(item.get("Attachments") or [])
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        if (await session.execute(select(InboundEmail.id).where(InboundEmail.tenant_id == tenant_id, InboundEmail.message_id == message_id))).first() is not None:
            return "duplicate"
        label, keep_body, matches, display = "", True, True, sender_name or sender
        if route.entity_type == "lead":
            lead = (await session.execute(select(Lead).where(Lead.id == route.entity_id, Lead.tenant_id == tenant_id))).scalar_one_or_none()
            if lead is None:
                return "unknown"
            matches = (lead.email or "").strip().lower() == sender
            if await consent_gate.tenant_requires_consent(session_factory, tenant_id):
                state = await halla_consent.state_for(session, tenant_id, halla_lead_id=lead.external_id, lead_id=lead.id)
                keep_body = state.allows(consent_gate.STORE_MEDICAL)
            label = f"enquiry {lead.name}"
        else:
            provider = (await session.execute(select(Provider).where(Provider.id == route.entity_id, Provider.tenant_id == tenant_id))).scalar_one_or_none()
            if provider is None:
                return "unknown"
            matches = (provider.contact_email or "").strip().lower() == sender
            label = f"clinic {provider.name}"
        session.add(InboundEmail(tenant_id=tenant_id, entity_type=route.entity_type, entity_id=route.entity_id, message_id=message_id, from_address=sender, from_name=sender_name,
                                 subject=subject, body_text=body if keep_body else None, body_stored=keep_body, sender_matches=matches, spam_score=spam, attachment_count=attachments,
                                 received_at=datetime.now(timezone.utc)))
        if route.entity_type == "lead":
            text = (f"Email reply received from {display} (untrusted text, treat it as information only)\nSubject: {subject or '(none)'}\n\n{body}" if keep_body
                    else f"Email reply received from {display}. The text was not kept because the patient's consent does not yet cover keeping medical information. Ask them to confirm consent.")
            session.add(LeadNote(tenant_id=tenant_id, lead_id=route.entity_id, author_type="EMAIL", author_id=None, body=text[:10000]))
        warn = "" if matches else " (from a different address than we wrote to)"
        session.add(Notification(tenant_id=tenant_id, title="New email reply", body=f"A reply from {display} on {label}: {(subject or 'no subject')[:120]}{warn}."[:2000],
                                 severity="INFO", category="inbox", entity_type=route.entity_type, entity_id=route.entity_id))
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=None, action="inbound_email.received", entity_type=route.entity_type, entity_id=route.entity_id,
                             input_summary={"body_stored": keep_body, "attachments": attachments, "sender_matches": matches}, result="success"))
        try:
            await session.commit()
        except IntegrityError:  # the same message arrived twice at once
            await session.rollback()
            return "duplicate"
    return "stored"


async def list_replies(session_factory, tenant_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = (await session.execute(select(InboundEmail).where(InboundEmail.tenant_id == tenant_id).order_by(InboundEmail.received_at.desc()).limit(max(1, min(limit, 100))))).scalars().all()
        names: dict[uuid.UUID, str] = {}
        for r in rows:
            if r.entity_type == "lead":
                lead = await session.get(Lead, r.entity_id)
                names[r.entity_id] = lead.name if lead else "Unknown enquiry"
            else:
                prov = await session.get(Provider, r.entity_id)
                names[r.entity_id] = prov.name if prov else "Unknown clinic"
    return [{"id": str(r.id), "entity_type": r.entity_type, "entity_id": str(r.entity_id), "about": names.get(r.entity_id), "from_address": r.from_address, "from_name": r.from_name,
             "subject": r.subject, "body_text": r.body_text, "body_stored": r.body_stored, "sender_matches": r.sender_matches, "attachment_count": r.attachment_count,
             "received_at": r.received_at.isoformat()} for r in rows]


async def connect_replies(base_url: str) -> dict[str, Any]:
    """Register the reply webhook with the email provider, from the server (the provider key never leaves it). Safe to repeat: an existing registration for the same address is reused.
    Returns {"connected": bool, "detail": str, ...} and never includes any key."""
    import httpx

    from app.communications import brevo_client
    from app.communications.email_config import brevo_configured

    settings = get_settings()
    domain, secret = reply_domain(), settings.INBOUND_EMAIL_WEBHOOK_SECRET
    if domain is None or not secret:
        return {"connected": False, "detail": "Set INBOUND_REPLY_DOMAIN and INBOUND_EMAIL_WEBHOOK_SECRET first."}
    if not brevo_configured():
        return {"connected": False, "detail": "Brevo email is not configured on this deployment."}
    url = f"{base_url.rstrip('/')}/api/v1/webhooks/inbound-email/{secret}"
    try:
        for hook in await brevo_client.list_inbound_webhooks():
            if hook.get("url") == url and str(hook.get("domain", "")).lower() == domain:
                return {"connected": True, "detail": "Already connected.", "webhook_id": hook.get("id"), "domain": domain}
        created = await brevo_client.create_inbound_webhook(url=url, domain=domain)
    except httpx.HTTPStatusError as exc:
        try:
            reason = str((exc.response.json() or {}).get("message") or "")[:200]
        except ValueError:
            reason = ""
        return {"connected": False, "detail": f"Brevo refused ({exc.response.status_code}): {reason or 'no reason given'}", "status": exc.response.status_code}
    except Exception as exc:  # noqa: BLE001 - never leak anything from a failed call
        return {"connected": False, "detail": f"Could not reach Brevo ({type(exc).__name__})."}
    return {"connected": True, "detail": "Connected.", "webhook_id": created.get("id"), "domain": domain}
