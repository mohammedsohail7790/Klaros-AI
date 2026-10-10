import uuid
from datetime import date, datetime
from decimal import Decimal

_SENSITIVE_MARKERS = ("password", "secret", "token", "credential", "api_key", "apikey", "private_key")

# Phase 7: found via real PostgreSQL verification — a rejected oversized
# file upload (e.g. a >25MB base64 payload the tool itself correctly
# rejects) was still landing whole in the audit log's `input_summary` JSON
# column, because redact_input only ever redacted sensitive KEYS, never
# bounded value SIZE. SQLite tolerated the resulting multi-megabyte JSON
# row silently; real Postgres/asyncpg does not, and the connection was
# dropped mid-INSERT. An audit log is a governance record, not blob
# storage, regardless of engine — any string this long is truncated here
# unconditionally, independent of which field it's under.
_MAX_STRING_LENGTH = 2000


def redact_input(data: dict) -> dict:
    """Never let a raw credential — or an oversized payload — land in an
    audit log (section 9).

    raw_input isn't always a JSON-decoded API body — internal callers (e.g.
    ExecuteRecommendation, tests) sometimes pass Python objects like UUID or
    Decimal straight through. The audit log's input_summary is a JSON
    column, so anything non-JSON-native must be coerced to a JSON-safe form
    here rather than letting the DB insert raise.
    """

    def _redact(value):
        if isinstance(value, dict):
            return {
                k: ("***REDACTED***" if any(m in k.lower() for m in _SENSITIVE_MARKERS) else _redact(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_redact(v) for v in value]
        if isinstance(value, (uuid.UUID, Decimal, datetime, date)):
            return str(value)
        if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
            return f"{value[:_MAX_STRING_LENGTH]}...<truncated, {len(value)} chars total>"
        return value

    return _redact(data)


# ---------------------------------------------------------------------------------------------------------------------------------------------
# PII / medical-information redaction for CONSENT-GATED tenants (Medical Tourism).
#
# `redact_input` above only hides credentials. For a consent-gated tenant, personal data and anything that can carry health information must also
# never reach (a) the audit log, (b) agent step summaries, or (c) an external AI provider. Two complementary mechanisms, both deterministic:
#   * KEY masking  - any value stored under a key that names a person, a contact point, an address or free text is replaced wholesale
#   * TEXT scrubbing - e-mail addresses and phone numbers inside any remaining string are replaced
# Names and diagnoses typed into unlabelled free text cannot be detected reliably; that is why free-text fields are masked by KEY, why the AI
# boundary (app/services/ai_boundary.py) refuses external providers for gated tenants by default, and why the residual is documented.
# ---------------------------------------------------------------------------------------------------------------------------------------------
import re
from collections.abc import Iterable
from typing import Any

PII_MASK = "***PII***"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w-])\+?\d[\d\s().\-]{6,}\d(?![\w])")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Case-insensitive substrings of a key that mark its value as personal, contact, address, free text or health information.
_PII_KEY_FRAGMENTS = (
    "name", "email", "phone", "mobile", "address", "street", "city", "postal", "zip", "note", "body", "message", "description", "comment",
    "summary", "symptom", "diagnos", "medical", "health", "treatment", "condition", "allerg", "medicat", "procedure", "service_requested",
    "insurance", "passport", "birth", "contact", "subject", "text", "transcript", "reason", "location", "company",
)
_PII_EXACT_KEYS = frozenset({"state", "country", "dob", "ssn", "age", "gender", "sex", "to", "from"})


def _key_is_pii(key: object, declared: frozenset[str]) -> bool:
    k = str(key).lower()
    if key in declared or k in declared:
        return True
    if k == "id" or k.endswith("_id") or k.endswith("_ids"):
        return False  # identifiers are not personal data and keep audit rows useful
    return k in _PII_EXACT_KEYS or any(fragment in k for fragment in _PII_KEY_FRAGMENTS)


def _phone_or_unchanged(match: "re.Match[str]") -> str:
    """A digit run is a phone number only when it has 7-15 digits, is not an ISO date, and is `+`-prefixed, separator-formatted or a bare 10-15
    digit run. Shorter bare runs (invoice / reference numbers) and dates are left alone so legitimate content keeps its meaning."""
    raw = match.group(0)
    digits = sum(c.isdigit() for c in raw)
    if digits < 7 or digits > 15 or _ISO_DATE_RE.match(raw.strip()):
        return raw
    bare = raw.strip().isdigit()
    if bare and digits < 10:
        return raw
    return "[phone removed]"


def scrub_text(text: str) -> str:
    """Replace e-mail addresses and phone numbers inside a string. Never raises."""
    if not isinstance(text, str):
        return text
    return _PHONE_CANDIDATE_RE.sub(_phone_or_unchanged, _EMAIL_RE.sub("[email removed]", text))


def redact_pii(data: Any, declared_fields: Iterable[str] = ()) -> Any:
    """Return a copy of `data` with personal / contact / free-text / health values masked by key and e-mail/phone patterns scrubbed from the rest.
    `declared_fields` are extra keys a tool declares as personal (`Tool.pii_input_fields`)."""
    declared = frozenset(declared_fields)

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (PII_MASK if _key_is_pii(k, declared) else _walk(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_walk(v) for v in value]
        if isinstance(value, str):
            return scrub_text(value)
        return value

    return _walk(data)


def safe_error_text(exc: BaseException | str, *, limit: int = 300) -> str:
    """An error description with no input values. A pydantic ValidationError echoes the offending VALUE (`input_value=...`), so it is reduced to
    field paths and error types; any other message has e-mail/phone patterns scrubbed and is truncated."""
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            parts = [f"{'.'.join(str(p) for p in e.get('loc', ())) or '(input)'}: {e.get('type', 'invalid')}" for e in exc.errors(include_input=False)]
            return ("validation_error: " + "; ".join(parts))[:limit]
    except Exception:  # noqa: BLE001
        pass
    return scrub_text(str(exc))[:limit]
