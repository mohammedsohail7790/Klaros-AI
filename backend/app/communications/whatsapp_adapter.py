"""WhatsApp sender (Meta WhatsApp Cloud API) using the business's own stored credentials.

Never built or called directly by feature code: a message to a person goes through `get_communication_provider(...).deliver_whatsapp`, whose consent guard decides first. This adapter then applies
WhatsApp's own rule honestly:
  * a template name given        -> an approved template is sent (allowed at any time);
  * no template, inside 24 hours -> a free-form text is sent;
  * no template, outside 24 hours-> NOTHING is sent and the result is FAILED with reason `outside_24h_window_template_required`.
Every attempt (sent, refused or failed) is logged in `communication_logs` with channel WHATSAPP. Nothing here ever returns or logs the access token.
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate, SendResult, SendStatus
from app.db.session import set_tenant_context
from app.integrations import whatsapp_client
from app.models.communication import CommunicationLog
from app.services import whatsapp_service

logger = structlog.get_logger(__name__)


class WhatsAppAdapter(CommunicationProvider):
    provider_name = "whatsapp"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    async def send_email(self, tenant_id, *, to, subject, body, template) -> bool:
        return False

    async def send_sms(self, tenant_id, *, to, body, template) -> bool:
        return False

    async def send_whatsapp(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate,
        template_name: str | None = None, template_language: str = "en_US", template_params: tuple[str, ...] = (),
    ) -> SendResult:
        creds = await whatsapp_service.get_credentials(self._session_factory, tenant_id)
        if creds is None:
            return SendResult(SendStatus.FAILED, "whatsapp_not_connected")
        digits = re.sub(r"\D", "", to or "")
        if len(digits) < 8 or len(digits) > 15 or (len(digits) == 10 and not (to or "").strip().startswith("+")):
            return SendResult(SendStatus.FAILED, "phone_number_needs_country_code")
        use_template = bool((template_name or "").strip())
        if not use_template and not await whatsapp_service.in_window(self._session_factory, tenant_id, digits):
            await self._log(tenant_id, template, digits, body, "NOT_SENT_TEMPLATE_REQUIRED", None)
            return SendResult(SendStatus.FAILED, whatsapp_service.OUTSIDE_WINDOW)
        try:
            if use_template:
                external_id = await whatsapp_client.send_template(
                    creds["phone_number_id"], creds["access_token"], to=digits, name=template_name.strip(), language=template_language or "en_US", body_params=list(template_params),
                )
                logged = f"[template {template_name.strip()}] " + " | ".join(template_params)
            else:
                external_id = await whatsapp_client.send_text(creds["phone_number_id"], creds["access_token"], to=digits, body=body)
                logged = body
        except whatsapp_client.WhatsAppError as exc:
            logger.warning("whatsapp_send_failed", template=template.value, reason=exc.code, meta_code=exc.meta_code, status=exc.status)
            reason = exc.code + (f"_{exc.meta_code}" if exc.meta_code is not None else "")
            await self._log(tenant_id, template, digits, body if not use_template else f"[template {template_name.strip()}]", f"FAILED_{reason.upper()}"[:40], None)
            return SendResult(SendStatus.FAILED, reason)
        await self._log(tenant_id, template, digits, logged, "SENT", external_id)
        return SendResult(SendStatus.SENT, None, external_id=external_id)

    async def _log(self, tenant_id, template: MessageTemplate, recipient: str, body: str, status: str, external_id: str | None) -> None:
        try:
            async with self._session_factory() as session:
                await set_tenant_context(session, tenant_id)
                session.add(CommunicationLog(tenant_id=tenant_id, channel="WHATSAPP", template=template.value, recipient=recipient, external_id=external_id, subject=None, body=body, status=status, provider="whatsapp"))
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - a logging problem must not turn a sent message into a failure
            logger.warning("whatsapp_log_failed", error=type(exc).__name__)
