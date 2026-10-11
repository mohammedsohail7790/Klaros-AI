"""The public contact form. Unauthenticated, so it is narrow and defensive: tiny fixed fields, a per-IP rate limit, a hidden honeypot field that real people never
fill in, and no echo of what was stored. Every message is saved; it is also emailed to SUPPORT_INBOX_EMAIL when that is configured."""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.account import ContactMessage
from app.services.system_mailer import send_system_email

router = APIRouter(prefix="/public/contact", tags=["public-contact"])

_rate_limit_dependency = Depends(rate_limit("public_contact", limit_setting="RATE_LIMIT_CONTACT_PER_MINUTE", window_seconds=60))


class ContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    message: str = Field(min_length=10, max_length=4000)
    website: str | None = Field(default=None, max_length=200)  # honeypot: a real visitor never sees or fills this


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[_rate_limit_dependency])
async def send_message(payload: ContactRequest, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    if payload.website:  # a bot filled the hidden field: accept silently, store nothing
        return {"message": "Thanks, we got your message."}
    db.add(ContactMessage(name=payload.name.strip(), email=payload.email.lower(), message=payload.message.strip()))
    await db.commit()
    inbox = get_settings().SUPPORT_INBOX_EMAIL
    if inbox:
        body = f"From: {payload.name.strip()} <{payload.email}>\n\n{payload.message.strip()}"
        await send_system_email(to=inbox, subject="Klaros AI contact form", body=body)
    return {"message": "Thanks, we got your message."}
