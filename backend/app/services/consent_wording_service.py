"""Owner-approved consent wording for a business's public forms.

The owner writes (or accepts a suggested draft of) the exact words shown beside the consent boxes and approves them. Approval creates an immutable, numbered
version (WEB-v1, WEB-v2, ...). A public form shows the LATEST approved wording and sends its version with the visitor's answers; the intake gate
(services/consent_gate.approved_wording_versions) accepts any approved version, so a form loaded before a newer version was approved still files correctly.
Klaros never invents or approves wording on the owner's behalf: the suggestion is only a draft until the owner approves it.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.consent_wording import ConsentWording

MIN_LENGTH, MAX_LENGTH = 40, 2000
LANGUAGES = ("en", "ar")
_UNSAFE = ("<", ">", "javascript:", "data:text/html")
_SPACES = re.compile(r"[ \t]+")


class ConsentWordingError(Exception):
    def __init__(self, code: str, message: str | None = None, *, status: int = 422) -> None:
        super().__init__(message or code)
        self.code, self.message, self.status = code, message or code, status


def suggested_text(business_name: str) -> str:
    """A plain-language DRAFT only. The owner must read it, change it to fit their business and approve it; nothing here is approved until they do."""
    name = (business_name or "this business").strip() or "this business"
    return (
        f"I agree that {name} may keep the details I enter on this form, and may contact me about my enquiry. "
        "I understand I can ask for my details to be removed, or withdraw this agreement, at any time by contacting the business. "
        "I have not been asked to give any health information on this form, and I will only share health information when I choose to."
    )


def clean_text(value: Any) -> str:
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in str(value or "").replace("\r\n", "\n").split("\n")).strip()
    if not MIN_LENGTH <= len(text) <= MAX_LENGTH:
        raise ConsentWordingError("wording_length", f"The wording must be between {MIN_LENGTH} and {MAX_LENGTH} characters.")
    lowered = text.lower()
    if any(marker in lowered for marker in _UNSAFE):
        raise ConsentWordingError("wording_unsafe", "The wording can't contain markup or links to scripts. Use plain text.")
    return text


def _view(w: ConsentWording) -> dict[str, Any]:
    return {"version": w.version, "text": w.text, "language": w.language, "approved_at": w.approved_at.isoformat() if w.approved_at else None}


async def list_wordings(session_factory, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = (await session.execute(select(ConsentWording).where(ConsentWording.tenant_id == tenant_id).order_by(ConsentWording.approved_at.desc(), ConsentWording.version.desc()))).scalars().all()
    return [_view(w) for w in rows]


async def latest_wording(session_factory, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        w = (await session.execute(select(ConsentWording).where(ConsentWording.tenant_id == tenant_id).order_by(ConsentWording.approved_at.desc(), ConsentWording.version.desc()).limit(1))).scalar_one_or_none()
    return _view(w) if w is not None else None


async def approve(session_factory, tenant_id: uuid.UUID, user_id: uuid.UUID, *, text: Any, language: Any = "en") -> dict[str, Any]:
    body = clean_text(text)
    lang = str(language or "en").lower()
    if lang not in LANGUAGES:
        raise ConsentWordingError("wording_language", "Choose English or Arabic.")
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        existing = (await session.execute(select(ConsentWording).where(ConsentWording.tenant_id == tenant_id).order_by(ConsentWording.approved_at.desc()).limit(1))).scalar_one_or_none()
        if existing is not None and existing.text == body and existing.language == lang:
            raise ConsentWordingError("wording_unchanged", "That is already the approved wording.", status=409)
        count = (await session.execute(select(func.count()).select_from(ConsentWording).where(ConsentWording.tenant_id == tenant_id))).scalar_one()
        row = ConsentWording(tenant_id=tenant_id, version=f"WEB-v{count + 1}", text=body, language=lang, approved_by=user_id, approved_at=datetime.now(timezone.utc))
        session.add(row)
        session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=user_id, action="consent_wording.approved", entity_type="consent_wording", entity_id=row.id,
                             input_summary={"version": row.version, "language": lang, "characters": len(body)}, result="success"))
        await session.commit()
        return _view(row)
