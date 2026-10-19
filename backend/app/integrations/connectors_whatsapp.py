"""WhatsApp (Meta WhatsApp Cloud API). Each business pastes the values from its OWN Meta WhatsApp Business account, so Klaros needs no platform setting for it.

Verification is a real read-only GET on the phone number. The webhook address and verify token the owner copies into Meta come from `GET /api/v1/integrations/whatsapp/webhook-info`
(see `app/api/v1/whatsapp_webhook.py`); they are derived per business, never typed in by the owner.
"""

from __future__ import annotations

from app.integrations import whatsapp_client
from app.integrations.connectors import Connector, CredentialField


async def verify(credential: dict) -> tuple[bool, str]:
    """(ok, detail). `detail` never contains the token or the app secret."""
    phone_number_id = str(credential.get("phone_number_id") or "").strip()
    token = str(credential.get("access_token") or "").strip()
    if not phone_number_id or not token:
        return False, "The phone number ID and the access token are both required."
    if not phone_number_id.isdigit():
        return False, "The phone number ID should be digits only. Copy the 'Phone number ID' (not the phone number itself) from WhatsApp Manager > API setup."
    try:
        info = await whatsapp_client.get_phone_number(phone_number_id, token)
    except whatsapp_client.WhatsAppAuthError as exc:
        return False, f"Meta rejected the access token or it lacks permission for this number. {exc}"
    except whatsapp_client.WhatsAppError as exc:
        return False, f"Could not confirm the WhatsApp number. {exc}"
    except Exception as exc:  # noqa: BLE001 - a verifier must report, never leak a raw exception
        return False, f"Could not confirm the WhatsApp number ({type(exc).__name__})."
    display = str(info.get("display_phone_number") or "").strip()
    name = str(info.get("verified_name") or "").strip()
    quality = str(info.get("quality_rating") or "").strip()
    if not display and not name:
        return False, "Meta answered but did not describe a WhatsApp number for that ID. Check the phone number ID."
    parts = [f"WhatsApp number {display}" if display else "WhatsApp number"]
    if name:
        parts.append(f"display name {name}")
    if quality:
        parts.append(f"quality rating {quality}")
    return True, ", ".join(parts)


CONNECTORS = [
    Connector(
        key="whatsapp", name="WhatsApp", category="communication", mode="credentials",
        description="Message customers and receive their replies on WhatsApp through the business's own WhatsApp Business number. Replies are filed on the enquiry; messages follow each person's consent and WhatsApp's 24-hour rule.",
        required_settings=(),
        fields=(
            CredentialField("phone_number_id", "Phone number ID", secret=False, help="WhatsApp Manager > API setup > 'Phone number ID' (digits). Not the phone number itself."),
            CredentialField("access_token", "Permanent access token", secret=True, help="A system user token with whatsapp_business_messaging and whatsapp_business_management. A temporary 24-hour token will stop working."),
            CredentialField("waba_id", "WhatsApp Business Account ID (optional)", secret=False, help="Shown next to the phone number in WhatsApp Manager. Used to list approved templates."),
            CredentialField("app_secret", "Meta app secret (optional, recommended)", secret=True, help="App settings > Basic > App secret. When set, Klaros rejects any webhook call that Meta did not sign."),
        ),
        verify=verify,
        owner_steps=(
            "In Meta Business Suite (business.facebook.com) open Business settings and confirm your business portfolio exists. Start Business Verification there now: it takes about 2 to 10 working days and decides your messaging limits.",
            "At developers.facebook.com create an app of type Business and add the WhatsApp product, linked to that business portfolio.",
            "In WhatsApp Manager add your WhatsApp Business Account and register the business phone number (it cannot be tied to a normal WhatsApp app account; confirm it with the SMS or voice code). Number registration is usually minutes to a day.",
            "In Business settings > Users > System users create a system user with Admin access, assign it your app and your WhatsApp account, then Generate token with the whatsapp_business_messaging and whatsapp_business_management permissions and 'Never' expiry. Copy the token once; Meta never shows it again.",
            "Copy the Phone number ID from WhatsApp Manager > API setup (and optionally the WhatsApp Business Account ID, and the App secret from App settings > Basic) and paste them here with the token, then press Connect. Klaros checks them with a real call to Meta.",
            "In the Meta app go to WhatsApp > Configuration > Webhook > Edit. Paste the Callback URL and the Verify token that Klaros shows after connecting, press Verify and save.",
            "Under Webhook fields press Subscribe next to 'messages' so replies and delivery receipts reach Klaros.",
            "In WhatsApp Manager > Message templates create and submit the templates you will send first (for example an enquiry follow-up). Utility templates are usually approved within minutes to 24 hours; marketing templates can take up to 48 hours. A template is the only way to start a conversation or message after 24 hours of silence.",
            "Until business verification completes you can only message about 250 people per rolling 24 hours; after verification the limit rises in tiers (1,000, 10,000, 100,000) as your quality rating stays good. Only message people who agreed to be contacted.",
        ),
    )
]
