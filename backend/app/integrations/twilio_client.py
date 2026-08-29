"""Twilio webhook signature verification (Phase 12C).

Implements Twilio's documented request-validation algorithm directly (no
SDK dependency, matching this project's established pattern): base64(
HMAC-SHA1(auth_token, url + sorted_concatenated_params)). See
https://www.twilio.com/docs/usage/webhooks/webhooks-security for the
exact scheme this mirrors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


class TwilioWebhookSignatureError(Exception):
    pass


def verify_webhook_signature(
    url: str, params: dict[str, str], signature_header: str, auth_token: str
) -> None:
    """`url` must be the EXACT public URL Twilio POSTed to (including any
    query string) — Twilio signs over that full URL, not just the path.
    `params` are the POST form fields Twilio sent. Raises
    TwilioWebhookSignatureError on any mismatch; returns None (does not
    raise) on success."""
    if not signature_header:
        raise TwilioWebhookSignatureError("missing X-Twilio-Signature header")

    data = url
    for key in sorted(params.keys()):
        data += key + params[key]

    computed = base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()

    if not hmac.compare_digest(computed, signature_header):
        raise TwilioWebhookSignatureError("signature mismatch")
