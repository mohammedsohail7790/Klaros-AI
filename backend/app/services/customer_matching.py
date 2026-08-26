"""section 2: safe customer matching.

Only exact matches on normalized phone or email are used to find an existing
customer automatically. Fuzzy/similarity matching is deliberately not
implemented here — the spec is explicit that fuzzy matching alone must never
merge customers automatically. A future "possible duplicate" review queue
(fuzzy-matched, human-confirmed) is a reasonable Phase 4+ addition, not this.
"""

import re
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm import Customer


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    # Treat US-style 10-digit numbers and their 11-digit (leading 1) form as
    # the same number so "(555) 123-4567" and "+15551234567" match.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_email(email: str | None) -> str | None:
    return email.strip().lower() if email else None


async def find_matching_customer(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str | None, phone: str | None
) -> Customer | None:
    norm_email = normalize_email(email)
    norm_phone = normalize_phone(phone)

    if not norm_email and not norm_phone:
        return None

    conditions = []
    if norm_email:
        conditions.append(Customer.email == norm_email)
    if norm_phone:
        conditions.append(Customer.phone_normalized == norm_phone)

    result = await session.execute(
        select(Customer).where(Customer.tenant_id == tenant_id, or_(*conditions))
    )
    return result.scalars().first()
