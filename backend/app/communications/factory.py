"""Phase 12C: chooses a real provider per channel when genuinely configured,
falling back to the internal-test adapter otherwise — the same honest
"NOT_CONNECTED unless real" pattern as app/storage/factory.py and
app/services/ai_provider.py's get_ai_provider(). This is the ONE place that
decides which communication provider is active; nothing else should
construct InternalTestCommunicationAdapter/SendGridEmailAdapter/
TwilioSMSAdapter directly.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.communications.base import CommunicationProvider
from app.communications.composite_adapter import CompositeCommunicationAdapter
from app.communications.consent_guard import ConsentGuardedProvider
from app.communications.internal_test_adapter import InternalTestCommunicationAdapter
from app.communications.brevo_adapter import BrevoEmailAdapter
from app.communications.email_config import brevo_configured, sendgrid_configured, smtp_configured
from app.communications.sendgrid_adapter import SendGridEmailAdapter
from app.communications.smtp_adapter import SmtpEmailAdapter
from app.communications.twilio_adapter import TwilioSMSAdapter
from app.core.config import get_settings


def get_communication_provider(session_factory: async_sessionmaker) -> CommunicationProvider:
    return ConsentGuardedProvider(_build_provider(session_factory), session_factory)


def get_clinic_channel(session_factory: async_sessionmaker) -> CommunicationProvider:
    """The raw provider, for messages addressed to a CLINIC (a business, not a patient). Used only by the Medical Tourism referral transport and clinic reminders,
    which send ONLY to a clinic's own stored contact address and only after the referral gate (verified clinic, patient approval, permitted fields) has passed.
    A message addressed to a patient never uses this: it always goes through `get_communication_provider` and its consent guard."""
    return _build_provider(session_factory)


def get_internal_channel(session_factory: async_sessionmaker) -> CommunicationProvider:
    """The raw provider, for messages addressed to the business's OWN team (an owner's morning digest). Never used for a patient or a clinic."""
    return _build_provider(session_factory)


def _build_provider(session_factory: async_sessionmaker) -> CommunicationProvider:
    settings = get_settings()
    internal_test = InternalTestCommunicationAdapter(session_factory)

    email_provider: CommunicationProvider = internal_test
    if sendgrid_configured():
        email_provider = SendGridEmailAdapter(
            session_factory, settings.SENDGRID_API_KEY, settings.SENDGRID_FROM_EMAIL
        )
    elif brevo_configured():
        email_provider = BrevoEmailAdapter(session_factory)
    elif smtp_configured():
        email_provider = SmtpEmailAdapter(session_factory)

    sms_provider: CommunicationProvider = internal_test
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER:
        sms_provider = TwilioSMSAdapter(
            session_factory, settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_FROM_NUMBER
        )

    if email_provider is internal_test and sms_provider is internal_test:
        return internal_test
    return CompositeCommunicationAdapter(session_factory, email_provider, sms_provider)
