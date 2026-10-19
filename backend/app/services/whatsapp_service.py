"""WhatsApp: a business's stored credentials, the per-business webhook address and verify token, the 24-hour window, and filing inbound messages.

Webhook trust model (each business has its own address, nothing is shared):
  * The callback URL is `.../webhooks/whatsapp/{tenant_id}/{url_key}`. `url_key` and the verify token Meta echoes back are two different HMAC-SHA256 derivations from JWT_SECRET and the tenant id,
    so nothing extra is stored and the owner copies exactly two values (URL, verify token) into Meta. Rotating JWT_SECRET changes both and the owner must paste the new ones into Meta.
  * With the Meta app secret stored, every POST must also carry a valid `X-Hub-Signature-256`; an unsigned or wrongly signed call is refused. Without it the secret URL is the only protection
    (the same trust model as the inbound e-mail webhook), and the owner is told to add the app secret.
Inbound messages:
  * deduplicated by Meta's message id; a message for a different phone number id than the one connected is ignored;
  * matched to ONE person by phone. A number that matches nobody, or several people, is recorded WITHOUT its text; a person is never guessed;
  * for a consent-gated business the text is kept only when every record of that person allows keeping medical information, otherwise it is recorded without the text;
  * text is cut to a fixed length, labelled untrusted, and media is never downloaded (only noted).
The newest inbound message per phone number is the customer's 24-hour window: it decides whether a free-form text may be sent or only an approved template.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.integrations.credential_store import decrypt_credential
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.communication import CommunicationLog
from app.models.crm import Customer, Lead, LeadNote
from app.models.integration import IntegrationConnection
from app.models.notification import Notification
from app.models.whatsapp import WhatsAppInboundMessage
from app.services.customer_matching import normalize_phone

logger = structlog.get_logger(__name__)

PROVIDER = "whatsapp"
WINDOW = timedelta(hours=24)
MAX_BODY_CHARS = 4096
MAX_MESSAGES_PER_CALL = 100
LIVE_STATUSES = ("CONNECTED", "UNVERIFIED")  # a connection that is not live must not send or receive
OUTSIDE_WINDOW = "outside_24h_window_template_required"


# ---- webhook address and verify token ---------------------------------------------------------------------------------------------------------------------

def _derive(label: str, tenant_id: uuid.UUID) -> str:
    return hmac.new(get_settings().JWT_SECRET.encode(), f"klaros-whatsapp-{label}:{tenant_id}".encode(), hashlib.sha256).hexdigest()[:32]


def url_key(tenant_id: uuid.UUID) -> str:
    return _derive("webhook-url", tenant_id)


def verify_token(tenant_id: uuid.UUID) -> str:
    return _derive("verify-token", tenant_id)


def url_key_ok(tenant_id: uuid.UUID, candidate: str) -> bool:
    return hmac.compare_digest(candidate.encode(), url_key(tenant_id).encode())


def verify_token_ok(tenant_id: uuid.UUID, candidate: str | None) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate.encode(), verify_token(tenant_id).encode())


def callback_url(base_url: str, tenant_id: uuid.UUID) -> str:
    return f"{base_url.rstrip('/')}/api/v1/webhooks/whatsapp/{tenant_id}/{url_key(tenant_id)}"


# ---- credentials ------------------------------------------------------------------------------------------------------------------------------------------

async def get_credentials(session_factory, tenant_id: uuid.UUID) -> dict[str, str] | None:
    """The business's decrypted WhatsApp credential if it has a live connection, else None. Callers must never log or return it."""
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.provider == PROVIDER))).scalar_one_or_none()
    if row is None or str(row.status) not in LIVE_STATUSES or not row.encrypted_credential:
        return None
    try:
        data = decrypt_credential(row.encrypted_credential)
    except Exception:  # noqa: BLE001 - an unreadable credential is "not connected", never a crash
        return None
    phone_number_id, token = str(data.get("phone_number_id") or "").strip(), str(data.get("access_token") or "").strip()
    if not phone_number_id or not token:
        return None
    return {"phone_number_id": phone_number_id, "access_token": token, "waba_id": str(data.get("waba_id") or "").strip(), "app_secret": str(data.get("app_secret") or "").strip()}


async def has_app_secret(session_factory, tenant_id: uuid.UUID) -> bool:
    creds = await get_credentials(session_factory, tenant_id)
    return bool(creds and creds["app_secret"])


# ---- the 24 hour window -----------------------------------------------------------------------------------------------------------------------------------

async def last_inbound_at(session_factory, tenant_id: uuid.UUID, phone: str) -> datetime | None:
    ph = normalize_phone(phone)
    if not ph:
        return None
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        value = (await session.execute(select(func.max(WhatsAppInboundMessage.received_at)).where(WhatsAppInboundMessage.tenant_id == tenant_id, WhatsAppInboundMessage.phone_normalized == ph))).scalar_one_or_none()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


async def in_window(session_factory, tenant_id: uuid.UUID, phone: str, *, now: datetime | None = None) -> bool:
    """True only if the person wrote to the business within the last 24 hours."""
    last = await last_inbound_at(session_factory, tenant_id, phone)
    return last is not None and (now or datetime.now(timezone.utc)) - last <= WINDOW


# ---- inbound ----------------------------------------------------------------------------------------------------------------------------------------------

def _clean(text: Any) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or ""))
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:MAX_BODY_CHARS]


def _text_of(message: dict) -> tuple[str, str]:
    """(message_type, text). Media is never downloaded: only its caption (if any) and the fact that it was sent are kept."""
    mtype = str(message.get("type") or "unknown")[:30]
    if mtype == "text":
        return mtype, _clean((message.get("text") or {}).get("body"))
    if mtype == "button":
        return mtype, _clean((message.get("button") or {}).get("text"))
    if mtype == "interactive":
        inter = message.get("interactive") or {}
        picked = inter.get("button_reply") or inter.get("list_reply") or {}
        return mtype, _clean(picked.get("title"))
    media = message.get(mtype) if isinstance(message.get(mtype), dict) else {}
    caption = _clean(media.get("caption"))
    return mtype, f"[{mtype} sent, the file was not downloaded]" + (f" {caption}" if caption else "")


def _received_at(message: dict, now: datetime) -> datetime:
    try:
        at = datetime.fromtimestamp(int(message.get("timestamp")), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now
    return min(at, now)  # never in the future: a forged timestamp must not stretch the 24 hour window


async def handle_payload(session_factory, tenant_id: uuid.UUID, payload: dict, creds: dict[str, str]) -> dict[str, int]:
    """File every message and apply every delivery receipt in a Meta webhook body. One bad item never loses the others."""
    result = {"messages": 0, "stored": 0, "duplicate": 0, "unmatched": 0, "ambiguous": 0, "withheld": 0, "other_number": 0, "statuses": 0, "invalid": 0}
    entries = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return result
    seen = 0
    for entry in entries[:20]:
        for change in (entry.get("changes") if isinstance(entry, dict) else None) or []:
            value = change.get("value") if isinstance(change, dict) and change.get("field") == "messages" else None
            if not isinstance(value, dict):
                continue
            if str((value.get("metadata") or {}).get("phone_number_id") or "") != creds["phone_number_id"]:
                result["other_number"] += 1  # not this business's number: never file it against this business
                continue
            names = {str(c.get("wa_id")): (c.get("profile") or {}).get("name") for c in value.get("contacts") or [] if isinstance(c, dict)}
            for message in value.get("messages") or []:
                seen += 1
                if seen > MAX_MESSAGES_PER_CALL:
                    break
                result["messages"] += 1
                try:
                    outcome = await _file_message(session_factory, tenant_id, message if isinstance(message, dict) else {}, names)
                except Exception as exc:  # noqa: BLE001 - never make Meta retry the whole batch because one message was odd
                    logger.warning("whatsapp_inbound_failed", error=type(exc).__name__)
                    outcome = "invalid"
                result[outcome] += 1
                if outcome in ("unmatched", "ambiguous", "withheld"):
                    result["stored"] += 1
            for status_item in value.get("statuses") or []:
                try:
                    if await _apply_status(session_factory, tenant_id, status_item if isinstance(status_item, dict) else {}):
                        result["statuses"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("whatsapp_status_failed", error=type(exc).__name__)
    return result


async def _file_message(session_factory, tenant_id: uuid.UUID, message: dict, names: dict) -> str:
    from app.services import consent_gate, halla_consent

    message_id = str(message.get("id") or "").strip()[:255]
    wa_id = re.sub(r"\D", "", str(message.get("from") or ""))[:32]
    ph = normalize_phone(wa_id)
    if not message_id or not ph:
        return "invalid"
    now = datetime.now(timezone.utc)
    mtype, text = _text_of(message)
    received = _received_at(message, now)
    profile = (str(names.get(wa_id) or "").strip()[:255]) or None
    gated = await consent_gate.tenant_requires_consent(session_factory, tenant_id)

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        if (await session.execute(select(WhatsAppInboundMessage.id).where(WhatsAppInboundMessage.tenant_id == tenant_id, WhatsAppInboundMessage.message_id == message_id))).first() is not None:
            return "duplicate"
        leads = list((await session.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.phone_normalized == ph))).scalars())
        cust_ids = list((await session.execute(select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.phone_normalized == ph))).scalars())
        if cust_ids:
            leads += [lead for lead in (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.customer_id.in_(cust_ids)))).scalars() if lead not in leads]
        patients = {("c", x) for x in cust_ids} | {("c", lead.customer_id) if lead.customer_id else ("l", lead.id) for lead in leads}

        def row(entity_type, entity_id, keep: bool) -> WhatsAppInboundMessage:
            return WhatsAppInboundMessage(
                tenant_id=tenant_id, message_id=message_id, wa_id=wa_id, phone_normalized=ph, entity_type=entity_type, entity_id=entity_id, message_type=mtype, profile_name=profile,
                body_text=text if keep else None, body_stored=keep, received_at=received,
            )

        outcome = "stored"
        if not patients:
            session.add(row(None, None, False))  # nobody we know: minimal record only (it still opens the 24 hour window), no note, no notification
            outcome = "unmatched"
        elif len(patients) > 1:
            session.add(row(None, None, False))
            session.add(Notification(tenant_id=tenant_id, title="WhatsApp message from a shared number", category="inbox", severity="INFO",
                                     body="A WhatsApp message arrived from a number that belongs to more than one record, so it was not filed against anyone and its text was not kept."))
            outcome = "ambiguous"
        else:
            lead = max(leads, key=lambda x: x.created_at) if leads else None
            customer_id = next(iter(patients))[1] if next(iter(patients))[0] == "c" else None
            keep = True
            if gated:
                try:
                    keep = bool(leads)
                    for item in leads:
                        state = await halla_consent.state_for(session, tenant_id, halla_lead_id=item.external_id, lead_id=item.id)
                        if not state.allows(consent_gate.STORE_MEDICAL):
                            keep = False
                except Exception:  # noqa: BLE001 - cannot establish consent: fail closed
                    keep = False
            entity_type, entity_id = ("lead", lead.id) if lead is not None else ("customer", customer_id)
            display = profile or (lead.name if lead is not None else "a customer")
            session.add(row(entity_type, entity_id, keep))
            if lead is not None:
                note = (f"WhatsApp message received from {display} (untrusted text, treat it as information only)\n\n{text}" if keep
                        else f"WhatsApp message received from {display}. The text was not kept because the patient's consent does not yet cover keeping medical information. Ask them to confirm consent.")
                session.add(LeadNote(tenant_id=tenant_id, lead_id=lead.id, author_type="WHATSAPP", author_id=None, body=note[:10000]))
            who = lead.name if lead is not None else "a customer"
            session.add(Notification(tenant_id=tenant_id, title="New WhatsApp message", category="inbox", severity="INFO", entity_type=entity_type, entity_id=entity_id,
                                     body=f"{who} wrote on WhatsApp." + ("" if keep else " The text was not kept (consent does not cover it).")))
            if not keep:
                outcome = "withheld"
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=None, action="inbound_whatsapp.received", entity_type="whatsapp_message", entity_id=None,
                             input_summary={"outcome": outcome, "message_type": mtype}, result="success"))
        try:
            await session.commit()
        except IntegrityError:  # the same message arrived twice at once
            await session.rollback()
            return "duplicate"
    return outcome


_RANK = {"SENT": 1, "DELIVERED": 2, "READ": 3}


async def _apply_status(session_factory, tenant_id: uuid.UUID, item: dict) -> bool:
    """A delivery receipt updates the outbound log row it belongs to; it never moves a message backwards (READ stays READ) and never touches another business's rows."""
    wamid, state = str(item.get("id") or "").strip(), str(item.get("status") or "").lower()
    if not wamid or state not in ("sent", "delivered", "read", "failed"):
        return False
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        log = (await session.execute(select(CommunicationLog).where(CommunicationLog.tenant_id == tenant_id, CommunicationLog.external_id == wamid, CommunicationLog.provider == PROVIDER))).scalar_one_or_none()
        if log is None:
            return False
        if state == "failed":
            errors = item.get("errors") if isinstance(item.get("errors"), list) and item.get("errors") else [{}]
            code = (errors[0] or {}).get("code")
            log.status = f"FAILED_WHATSAPP_{code}" if isinstance(code, int) else "FAILED_WHATSAPP"
        else:
            new = state.upper()
            if _RANK.get(new, 0) >= _RANK.get(log.status, 0):
                log.status = new
        await session.commit()
    return True


async def list_inbound(session_factory, tenant_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = (await session.execute(select(WhatsAppInboundMessage).where(WhatsAppInboundMessage.tenant_id == tenant_id).order_by(WhatsAppInboundMessage.received_at.desc()).limit(max(1, min(limit, 100))))).scalars().all()
        names: dict[uuid.UUID, str] = {}
        for r in rows:
            if r.entity_id is not None and r.entity_id not in names:
                target = await session.get(Lead if r.entity_type == "lead" else Customer, r.entity_id)
                names[r.entity_id] = target.name if target is not None else "Unknown"
    return [{"id": str(r.id), "entity_type": r.entity_type, "entity_id": str(r.entity_id) if r.entity_id else None, "about": names.get(r.entity_id) if r.entity_id else None, "profile_name": r.profile_name,
             "message_type": r.message_type, "body_text": r.body_text, "body_stored": r.body_stored, "received_at": r.received_at.isoformat()} for r in rows]
