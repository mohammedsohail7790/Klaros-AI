"""Real Twilio SMS delivery (Phase 12C).

Direct httpx calls against Twilio's REST API (Basic Auth with Account
SID/Auth Token — no SDK dependency added, matching this project's
established pattern; see app/services/ai_provider.py). Twilio doesn't do
email; send_email here always returns False rather than a silent no-op
success.
"""

import uuid

import httpx
import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider, MessageTemplate
from app.models.communication import CommunicationLog

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 15.0


class TwilioSMSAdapter(CommunicationProvider):
    """Only ever constructed when both TWILIO_ACCOUNT_SID and
    TWILIO_AUTH_TOKEN are set — see app/communications/factory.py."""

    provider_name = "twilio"

    def __init__(
        self, session_factory: async_sessionmaker, account_sid: str, auth_token: str, from_number: str
    ) -> None:
        self._session_factory = session_factory
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number

    def __repr__(self) -> str:
        return f"{type(self).__name__}(is_connected=True)"

    async def send_email(
        self, tenant_id: uuid.UUID, *, to: str, subject: str, body: str, template: MessageTemplate
    ) -> bool:
        await self._log(
            tenant_id, channel="EMAIL", template=template, recipient=to, subject=subject, body=body,
            status="FAILED_NO_EMAIL_PROVIDER",
        )
        return False

    async def send_sms(
        self, tenant_id: uuid.UUID, *, to: str, body: str, template: MessageTemplate
    ) -> bool:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json"
        status = "SENT"
        external_id: str | None = None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    auth=(self._account_sid, self._auth_token),
                    data={"To": to, "From": self._from_number, "Body": body},
                )
                response.raise_for_status()
            external_id = response.json().get("sid")
            logger.info("twilio_sms_sent", to=to, template=template.value, status_code=response.status_code, sid=external_id)
        except httpx.HTTPStatusError as exc:
            status = f"FAILED_HTTP_{exc.response.status_code}"
            logger.warning("twilio_sms_failed", to=to, template=template.value, status_code=exc.response.status_code)
        except httpx.HTTPError as exc:
            status = "FAILED_NETWORK"
            logger.warning("twilio_sms_failed", to=to, template=template.value, error=str(exc))

        await self._log(
            tenant_id, channel="SMS", template=template, recipient=to, subject=None, body=body,
            status=status, external_id=external_id,
        )
        return status == "SENT"

    async def _log(self, tenant_id, *, channel, template, recipient, subject, body, status, external_id=None) -> None:
        async with self._session_factory() as session:
            session.add(
                CommunicationLog(
                    tenant_id=tenant_id,
                    channel=channel,
                    template=template.value,
                    recipient=recipient,
                    external_id=external_id,
                    subject=subject,
                    body=body,
                    status=status,
                    provider=self.provider_name,
                )
            )
            await session.commit()
