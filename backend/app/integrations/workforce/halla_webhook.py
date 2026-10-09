"""Halla -> Klaros webhook contract: signature verification, envelope parsing and field extraction.

Pure functions, no I/O. The contract (as given by Halla):

    X-HallaAI-Timestamp: <timestamp>
    X-HallaAI-Signature: HMAC-SHA256(timestamp + "." + raw_body)
    body: {"id", "type", "timestamp", "tenant_id", "data": {}}

The HMAC is computed over the EXACT bytes received — the body is never parsed and re-serialised before
it is verified. Comparison is constant-time. Two encodings are accepted for what the contract leaves
open (the digest may carry a "sha256=" prefix; the timestamp may be epoch seconds, epoch milliseconds
or ISO-8601); the HMAC always covers the timestamp header's exact text.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MAX_BODY_BYTES = 256 * 1024

# Everything Halla emits that Klaros handles. `appointment.requested` is deliberately absent: Halla's
# booking flow has no pending state, so it is not emitted and Klaros must not assume it.
SUPPORTED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "call.started",
        "call.completed",
        "lead.created",
        "lead.updated",
        "lead.qualified",
        "lead.escalated",
        "appointment.confirmed",
        "appointment.rescheduled",
        "appointment.cancelled",
    }
)

QUALIFICATION_STATES: frozenset[str] = frozenset({"qualified", "not_qualified", "needs_human_review", "unknown"})


class HallaWebhookError(Exception):
    """A webhook that must be rejected. `reason` is for logs; `status` is the HTTP answer."""

    def __init__(self, reason: str, status: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _parse_timestamp(text: str) -> datetime:
    t = (text or "").strip()
    if not t:
        raise HallaWebhookError("timestamp_missing", 401)
    try:
        if t.lstrip("-").isdigit():
            n = int(t)
            seconds = n / 1000 if abs(n) > 100_000_000_000 else n  # epoch ms vs s
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        parsed = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise HallaWebhookError("timestamp_malformed", 401) from exc


def verify_signature(
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: str,
    *,
    tolerance_seconds: int,
    now: datetime | None = None,
) -> None:
    """Raises HallaWebhookError unless the request is authentic and fresh."""
    if not secret:
        raise HallaWebhookError("secret_not_configured", 503)
    if not signature_header or not signature_header.strip():
        raise HallaWebhookError("signature_missing", 401)
    ts_text = timestamp_header or ""
    sent_at = _parse_timestamp(ts_text)
    current = now or datetime.now(timezone.utc)
    if abs((current - sent_at).total_seconds()) > tolerance_seconds:
        raise HallaWebhookError("timestamp_stale", 401)

    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:]
    provided = provided.lower()
    if len(provided) != 64 or any(c not in "0123456789abcdef" for c in provided):
        raise HallaWebhookError("signature_malformed", 401)

    expected = hmac.new(secret.encode(), ts_text.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HallaWebhookError("signature_invalid", 401)


def sign(secret: str, timestamp: str, raw_body: bytes) -> str:
    """The signature Halla computes — used by tests and by anyone verifying the contract."""
    return hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class HallaEnvelope:
    id: str
    type: str
    timestamp: str | None
    tenant_id: str
    data: dict[str, Any]


def parse_envelope(raw_body: bytes) -> HallaEnvelope:
    """Called only AFTER the signature verified, on the same bytes."""
    if len(raw_body) > MAX_BODY_BYTES:
        raise HallaWebhookError("body_too_large", 413)
    try:
        doc = json.loads(raw_body)
    except ValueError as exc:
        raise HallaWebhookError("body_not_json", 400) from exc
    if not isinstance(doc, dict):
        raise HallaWebhookError("envelope_not_object", 400)
    event_id, etype, tenant = doc.get("id"), doc.get("type"), doc.get("tenant_id")
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
        raise HallaWebhookError("envelope_id_invalid", 400)
    if not isinstance(etype, str) or not etype:
        raise HallaWebhookError("envelope_type_invalid", 400)
    if not isinstance(tenant, str) or not tenant.strip() or len(tenant) > 100:
        raise HallaWebhookError("envelope_tenant_invalid", 400)
    data = doc.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise HallaWebhookError("envelope_data_invalid", 400)
    if etype not in SUPPORTED_EVENT_TYPES:
        raise HallaWebhookError("event_type_unsupported", 400)
    ts = doc.get("timestamp")
    return HallaEnvelope(id=event_id.strip(), type=etype, timestamp=ts if isinstance(ts, str) else None, tenant_id=tenant.strip(), data=data)


# --- field extraction: tolerant of snake_case / camelCase, strict about types -------------------------


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _text(value: Any, limit: int = 2000) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] if s else None


def klaros_lead_id(data: dict[str, Any]) -> str | None:
    lead = data.get("lead") if isinstance(data.get("lead"), dict) else {}
    return _text(_pick(data, "klaros_lead_id", "klarosLeadId") or _pick(lead, "klaros_lead_id", "klarosLeadId"), 64)


def halla_lead_id(data: dict[str, Any]) -> str | None:
    lead = data.get("lead") if isinstance(data.get("lead"), dict) else {}
    return _text(_pick(data, "lead_id", "leadId") or _pick(lead, "id"), 120)


def call_id(data: dict[str, Any]) -> str | None:
    call = data.get("call") if isinstance(data.get("call"), dict) else {}
    return _text(_pick(data, "call_id", "callId", "call_sid", "callSid") or _pick(call, "id", "call_sid", "callSid"), 120)


def qualification(data: dict[str, Any]) -> str | None:
    """One of the four Halla states, or None when Halla supplied nothing usable. Never inferred."""
    lead = data.get("lead") if isinstance(data.get("lead"), dict) else {}
    raw = _pick(data, "qualification", "qualification_status", "qualificationStatus") or _pick(lead, "qualification", "qualification_status")
    if isinstance(raw, dict):
        raw = _pick(raw, "status", "state")
    value = _text(raw, 40)
    if value is None:
        return None
    value = value.lower().replace("-", "_").replace(" ", "_")
    return value if value in QUALIFICATION_STATES else None


def summary_of(data: dict[str, Any]) -> str | None:
    return _text(_pick(data, "summary", "call_summary", "callSummary", "transcript_summary"), 2000)


def outcome_of(data: dict[str, Any]) -> str | None:
    return _text(_pick(data, "outcome", "call_outcome", "callOutcome"), 60)


def safety_texts(data: dict[str, Any]) -> list[str]:
    """Every free-text field a Halla event can carry that the tenant's safety profile should read: the call summary and outcome, and the
    `reason` Halla attaches to `lead.qualified` / `lead.escalated` (and to an embedded `escalation` object). Halla's real emitter sends
    `reason` but no `summary`, so reading only the summary would leave the safety check blind on real traffic. Strings only, bounded."""
    out: list[str] = []
    for value in (summary_of(data), outcome_of(data), _text(_pick(data, "reason", "qualification_reason"), 2000)):
        if value:
            out.append(value)
    esc = data.get("escalation")
    if isinstance(esc, dict):
        inner = _text(_pick(esc, "reason", "summary"), 2000)
        if inner:
            out.append(inner)
    return out


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "none", "null")
    return bool(value)


def escalated(data: dict[str, Any]) -> bool:
    """Did Halla report that this call was handed to a person? Reads the flat flags and the embedded
    `escalation` object of `call.completed`; an explicit false inside that object means "no"."""
    if _truthy(_pick(data, "escalated", "needs_human", "needsHuman")):
        return True
    esc = data.get("escalation")
    if isinstance(esc, dict):
        if not esc:
            return False
        flag = _pick(esc, "escalated", "transferred", "needs_human", "needsHuman")
        return _truthy(flag) if flag is not None else True
    return _truthy(esc)


@dataclass(frozen=True)
class AppointmentFacts:
    external_id: str
    start_time: datetime | None
    end_time: datetime | None
    service: str | None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        t = str(value).strip()
        parsed = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def appointment_facts(data: dict[str, Any]) -> AppointmentFacts | None:
    appt = data.get("appointment") if isinstance(data.get("appointment"), dict) else data
    ext = _text(_pick(appt, "id", "appointment_id", "appointmentId"), 120)
    if ext is None:
        return None
    return AppointmentFacts(
        external_id=ext,
        start_time=_dt(_pick(appt, "scheduled_time", "scheduledTime", "start_time", "startTime", "time")),
        end_time=_dt(_pick(appt, "end_time", "endTime")),
        service=_text(_pick(appt, "service", "title"), 255),
    )


@dataclass(frozen=True)
class LeadFacts:
    name: str | None
    phone: str | None
    email: str | None
    service: str | None


def lead_facts(data: dict[str, Any]) -> LeadFacts:
    lead = data.get("lead") if isinstance(data.get("lead"), dict) else data
    return LeadFacts(
        name=_text(_pick(lead, "name", "full_name", "fullName"), 255),
        phone=_text(_pick(lead, "phone", "phoneNumber", "phone_number"), 50),
        email=_text(_pick(lead, "email"), 255),
        service=_text(_pick(lead, "service", "interest", "service_requested"), 255),
    )
