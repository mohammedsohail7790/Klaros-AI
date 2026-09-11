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
