"""Phase 12C: Twilio webhook signature verification — pure crypto, fully
testable without real Twilio credentials.

Note on verification honesty: Twilio's own docs
(https://www.twilio.com/docs/usage/webhooks/webhooks-security) describe the
algorithm (HMAC-SHA1 over "{url}{sorted params concatenated as key+value}",
base64-encoded, keyed by the Auth Token) and give an example URL/param set,
but Twilio deliberately never publishes a real Auth Token — so there is no
independently-verifiable (token, url, params) -> signature triple to test
against, unlike Stripe (whose webhook secret format has no such
restriction). These tests verify this implementation is internally
consistent and correctly rejects tampering; they cannot additionally prove
byte-for-byte parity with Twilio's real signer the way the Stripe tests
can, since Twilio provides no real signed fixture to check against."""

import base64
import hashlib
import hmac

import pytest

from app.integrations.twilio_client import TwilioWebhookSignatureError, verify_webhook_signature

_AUTH_TOKEN = "test_auth_token_12345"
_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
_PARAMS = {"Digits": "1234", "To": "+18005551212", "From": "+14158675310", "Caller": "+14158675310", "CallSid": "CA1234567890ABCDE"}


def _sign(url: str, params: dict[str, str], auth_token: str) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def test_correctly_signed_request_is_accepted() -> None:
    signature = _sign(_URL, _PARAMS, _AUTH_TOKEN)
    verify_webhook_signature(_URL, _PARAMS, signature, _AUTH_TOKEN)  # must not raise


def test_wrong_auth_token_is_rejected() -> None:
    signature = _sign(_URL, _PARAMS, _AUTH_TOKEN)
    with pytest.raises(TwilioWebhookSignatureError, match="mismatch"):
        verify_webhook_signature(_URL, _PARAMS, signature, "wrong-token")


def test_tampered_params_are_rejected() -> None:
    signature = _sign(_URL, _PARAMS, _AUTH_TOKEN)
    tampered = dict(_PARAMS)
    tampered["To"] = "+19995551234"
    with pytest.raises(TwilioWebhookSignatureError, match="mismatch"):
        verify_webhook_signature(_URL, tampered, signature, _AUTH_TOKEN)


def test_wrong_url_is_rejected() -> None:
    signature = _sign(_URL, _PARAMS, _AUTH_TOKEN)
    with pytest.raises(TwilioWebhookSignatureError, match="mismatch"):
        verify_webhook_signature("https://attacker.com/myapp.php?foo=1&bar=2", _PARAMS, signature, _AUTH_TOKEN)


def test_missing_signature_is_rejected() -> None:
    with pytest.raises(TwilioWebhookSignatureError, match="missing"):
        verify_webhook_signature(_URL, _PARAMS, "", _AUTH_TOKEN)
