import uuid
from datetime import date, datetime
from decimal import Decimal

_SENSITIVE_MARKERS = ("password", "secret", "token", "credential", "api_key", "apikey", "private_key")


def redact_input(data: dict) -> dict:
    """Never let a raw credential land in an audit log (section 9).

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
        return value

    return _redact(data)
