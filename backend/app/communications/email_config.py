"""Which real e-mail provider this deployment has, if any. One answer for every part of Klaros that needs to know (the communication stack, the system mailer, the Medical Tourism
referral transport, the setup plan), so none of them decides differently. SendGrid wins when both are configured."""

from app.core.config import get_settings


def sendgrid_configured() -> bool:
    s = get_settings()
    return bool(s.SENDGRID_API_KEY and s.SENDGRID_FROM_EMAIL)


def smtp_configured() -> bool:
    s = get_settings()
    return bool(s.SMTP_HOST and s.SMTP_USERNAME and s.SMTP_PASSWORD and s.SMTP_FROM_EMAIL)


def email_configured() -> bool:
    return sendgrid_configured() or smtp_configured()
