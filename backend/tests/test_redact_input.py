"""app/tools/redact.py — sensitive-key redaction and (Phase 7, found via
real PostgreSQL verification) oversized-value truncation before anything
is persisted into the audit log."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from app.tools.redact import _MAX_STRING_LENGTH, redact_input


def test_sensitive_keys_are_redacted() -> None:
    result = redact_input({"password": "hunter2", "api_key": "sk-abc", "name": "Jane"})
    assert result["password"] == "***REDACTED***"
    assert result["api_key"] == "***REDACTED***"
    assert result["name"] == "Jane"


def test_sensitive_keys_are_redacted_case_insensitively_and_nested() -> None:
    result = redact_input({"nested": {"Secret_Token": "xyz"}})
    assert result["nested"]["Secret_Token"] == "***REDACTED***"


def test_non_json_native_types_are_coerced_to_strings() -> None:
    tenant_id = uuid.uuid4()
    result = redact_input({"id": tenant_id, "amount": Decimal("12.50"), "when": datetime(2026, 1, 1), "day": date(2026, 1, 1)})
    assert result["id"] == str(tenant_id)
    assert result["amount"] == "12.50"
    assert isinstance(result["when"], str)
    assert isinstance(result["day"], str)


def test_oversized_string_value_is_truncated() -> None:
    huge = "a" * 5_000_000
    result = redact_input({"content_base64": huge})
    assert len(result["content_base64"]) < 5_000_000
    assert result["content_base64"].startswith("a" * 100)
    assert "truncated" in result["content_base64"]
    assert "5000000" in result["content_base64"]


def test_short_string_value_is_never_truncated() -> None:
    result = redact_input({"name": "Jane Doe"})
    assert result["name"] == "Jane Doe"


def test_oversized_string_inside_a_list_is_also_truncated() -> None:
    huge = "b" * (_MAX_STRING_LENGTH + 1000)
    result = redact_input({"items": [huge, "short"]})
    assert len(result["items"][0]) < len(huge)
    assert result["items"][1] == "short"


def test_truncation_never_exposes_a_redacted_secret_value() -> None:
    """Even an oversized value under a sensitive key must show only the
    redaction marker, never a truncated-but-still-partially-visible secret."""
    huge_secret = "s" * 5000
    result = redact_input({"api_key": huge_secret})
    assert result["api_key"] == "***REDACTED***"
