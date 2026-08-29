"""Phase 12C: Stripe webhook signature verification — pure crypto/parsing
logic, fully testable without any real Stripe API access or credentials."""

import hashlib
import hmac
import json
import time

import pytest

from app.integrations.stripe_client import StripeWebhookSignatureError, verify_webhook_signature

_SECRET = "whsec_test_secret_1234567890"


def _sign(payload: bytes, timestamp: int, secret: str = _SECRET) -> str:
    signed_payload = f"{timestamp}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def test_valid_signature_is_accepted_and_body_parsed() -> None:
    payload = json.dumps({"id": "evt_1", "type": "payment_intent.succeeded"}).encode()
    header = _sign(payload, int(time.time()))
    parsed = verify_webhook_signature(payload, header, _SECRET)
    assert parsed["id"] == "evt_1"


def test_wrong_secret_is_rejected() -> None:
    payload = json.dumps({"id": "evt_1"}).encode()
    header = _sign(payload, int(time.time()), secret="whsec_wrong")
    with pytest.raises(StripeWebhookSignatureError, match="signature mismatch"):
        verify_webhook_signature(payload, header, _SECRET)


def test_tampered_payload_is_rejected() -> None:
    payload = json.dumps({"id": "evt_1", "amount": 100}).encode()
    header = _sign(payload, int(time.time()))
    tampered_payload = json.dumps({"id": "evt_1", "amount": 999999}).encode()
    with pytest.raises(StripeWebhookSignatureError, match="signature mismatch"):
        verify_webhook_signature(tampered_payload, header, _SECRET)


def test_expired_timestamp_is_rejected_as_possible_replay() -> None:
    payload = json.dumps({"id": "evt_1"}).encode()
    old_timestamp = int(time.time()) - 3600  # 1 hour old, well past the 300s default tolerance
    header = _sign(payload, old_timestamp)
    with pytest.raises(StripeWebhookSignatureError, match="tolerance"):
        verify_webhook_signature(payload, header, _SECRET)


def test_missing_header_is_rejected() -> None:
    payload = b'{"id": "evt_1"}'
    with pytest.raises(StripeWebhookSignatureError, match="missing"):
        verify_webhook_signature(payload, "", _SECRET)


def test_malformed_header_is_rejected() -> None:
    payload = b'{"id": "evt_1"}'
    with pytest.raises(StripeWebhookSignatureError, match="malformed"):
        verify_webhook_signature(payload, "not-a-valid-header", _SECRET)
