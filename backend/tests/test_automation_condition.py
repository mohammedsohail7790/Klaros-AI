"""app/services/automation_condition.py — the safe, no-eval() condition
engine. Explicit malicious-input coverage since automation input is
untrusted (event payloads, owner-authored condition JSON)."""

import pytest

from app.services.automation_condition import (
    InvalidConditionError,
    evaluate_condition,
    validate_condition,
)


def test_no_condition_is_always_true() -> None:
    assert evaluate_condition(None, {}) is True


def test_simple_comparison_operators() -> None:
    ctx = {"lead": {"score": 80}}
    assert evaluate_condition({"field": "lead.score", "op": "gt", "value": 70}, ctx) is True
    assert evaluate_condition({"field": "lead.score", "op": "gte", "value": 80}, ctx) is True
    assert evaluate_condition({"field": "lead.score", "op": "lt", "value": 70}, ctx) is False
    assert evaluate_condition({"field": "lead.score", "op": "eq", "value": 80}, ctx) is True
    assert evaluate_condition({"field": "lead.score", "op": "ne", "value": 80}, ctx) is False


def test_and_or_not_composition() -> None:
    ctx = {"lead": {"score": 80, "status": "NEW"}}
    node = {
        "and": [
            {"field": "lead.score", "op": "gte", "value": 50},
            {"or": [
                {"field": "lead.status", "op": "eq", "value": "NEW"},
                {"field": "lead.status", "op": "eq", "value": "CONTACTED"},
            ]},
        ]
    }
    assert evaluate_condition(node, ctx) is True
    assert evaluate_condition({"not": {"field": "lead.score", "op": "gte", "value": 50}}, ctx) is False


def test_missing_field_resolves_to_none_never_raises() -> None:
    ctx = {"lead": {}}
    assert evaluate_condition({"field": "lead.nonexistent", "op": "eq", "value": "x"}, ctx) is False
    assert evaluate_condition({"field": "lead.nonexistent", "op": "is_null"}, ctx) is True
    assert evaluate_condition({"field": "totally.missing.path", "op": "gt", "value": 5}, ctx) is False


def test_in_and_not_in() -> None:
    ctx = {"lead": {"source": "WEB"}}
    assert evaluate_condition({"field": "lead.source", "op": "in", "value": ["WEB", "PHONE"]}, ctx) is True
    assert evaluate_condition({"field": "lead.source", "op": "not_in", "value": ["WEB"]}, ctx) is False


def test_contains() -> None:
    ctx = {"lead": {"service_requested": "emergency plumbing repair"}}
    assert evaluate_condition({"field": "lead.service_requested", "op": "contains", "value": "plumbing"}, ctx) is True


def test_type_mismatch_never_raises_resolves_false() -> None:
    ctx = {"lead": {"score": "not-a-number"}}
    assert evaluate_condition({"field": "lead.score", "op": "gt", "value": 50}, ctx) is False


# --- Malicious / malformed input ---

def test_validate_rejects_unknown_operator() -> None:
    with pytest.raises(InvalidConditionError):
        validate_condition({"field": "lead.score", "op": "__import__", "value": 1})


def test_validate_rejects_non_dict_node() -> None:
    with pytest.raises(InvalidConditionError):
        validate_condition("os.system('rm -rf /')")  # type: ignore[arg-type]


def test_validate_rejects_unrecognized_shape() -> None:
    with pytest.raises(InvalidConditionError):
        validate_condition({"eval": "1+1"})


def test_validate_rejects_empty_and_or() -> None:
    with pytest.raises(InvalidConditionError):
        validate_condition({"and": []})


def test_validate_rejects_missing_value_for_comparison() -> None:
    with pytest.raises(InvalidConditionError):
        validate_condition({"field": "lead.score", "op": "gt"})


def test_validate_allows_is_null_without_value() -> None:
    validate_condition({"field": "lead.score", "op": "is_null"})  # must not raise


def test_deeply_nested_condition_is_rejected() -> None:
    node: dict = {"field": "x", "op": "eq", "value": 1}
    for _ in range(20):
        node = {"and": [node]}
    with pytest.raises(InvalidConditionError):
        validate_condition(node)


def test_evaluate_never_executes_arbitrary_code_even_if_value_looks_like_code() -> None:
    ctx = {"lead": {"name": "__import__('os').system('echo pwned')"}}
    # The "value" is just a string to compare against — never executed.
    result = evaluate_condition(
        {"field": "lead.name", "op": "eq", "value": "__import__('os').system('echo pwned')"}, ctx
    )
    assert result is True  # equality comparison of two strings, nothing more
