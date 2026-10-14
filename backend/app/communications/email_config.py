"""Which real e-mail provider this deployment has, if any. One answer for every part of Klaros that needs to know (the communication stack, the system mailer, the Medical Tourism
referral transport, the setup plan), so none of them decides differently. Order of preference: SendGrid, then Brevo's web API, then plain SMTP. The web APIs go over HTTPS, which hosts that
block outbound SMTP (Render's free plan) still allow."""

from app.core.config import get_settings


def sendgrid_configured() -> bool:
    s = get_settings()
    return bool(s.SENDGRID_API_KEY and s.SENDGRID_FROM_EMAIL)


def brevo_from_email() -> str | None:
    s = get_settings()
    return s.BREVO_FROM_EMAIL or s.SMTP_FROM_EMAIL


def brevo_configured() -> bool:
    return bool(get_settings().BREVO_API_KEY and brevo_from_email())


def smtp_configured() -> bool:
    s = get_settings()
    return bool(s.SMTP_HOST and s.SMTP_USERNAME and s.SMTP_PASSWORD and s.SMTP_FROM_EMAIL)


def email_configured() -> bool:
    return sendgrid_configured() or brevo_configured() or smtp_configured()
