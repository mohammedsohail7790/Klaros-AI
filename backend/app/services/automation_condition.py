"""A safe, constrained condition-tree evaluator for the Automation Engine.

Deliberately NOT eval(), NOT arbitrary SQL, NOT a general expression
language — a condition is a bounded JSON tree of a handful of node types,
evaluated purely in Python against a plain dict `context` the automation
engine itself built (never raw user/event input passed through
unvalidated — see automation_service.py). Every possible input is
data, never code.

Node shapes:
  {"field": "lead.score", "op": ">", "value": 70}
  {"and": [<node>, <node>, ...]}
  {"or": [<node>, <node>, ...]}
  {"not": <node>}

`field` is a dotted path resolved against `context` (e.g. "lead.score" ->
context["lead"]["score"]). A missing field resolves to None, never an
exception — a condition referencing a field that doesn't exist on this
event's context is simply false for any comparison except "is_null"/"ne".
"""

from __future__ import annotations

from typing import Any

_COMPARISON_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null", "is_not_null"}
_MAX_DEPTH = 8
_MAX_FIELD_PARTS = 6


class InvalidConditionError(Exception):
    pass


def _resolve_field(context: dict, dotted_path: str) -> Any:
    parts = dotted_path.split(".")
    if len(parts) > _MAX_FIELD_PARTS:
        raise InvalidConditionError(f"field path too deep: {dotted_path!r}")
    node: Any = context
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "is_null":
        return actual is None
    if op == "is_not_null":
        return actual is not None
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected if isinstance(expected, (list, tuple, set)) else False
    if op == "not_in":
        return actual not in expected if isinstance(expected, (list, tuple, set)) else True
    if op == "contains":
        try:
            return expected in actual
        except TypeError:
            return False
    # Ordering comparisons: only meaningful for numbers/strings/dates of
    # the same type — never raise on a mismatched type, just resolve false
    # (a condition that can't be meaningfully evaluated is not satisfied).
    if op in ("gt", "gte", "lt", "lte"):
        if actual is None or expected is None:
            return False
        try:
            if op == "gt":
                return actual > expected
            if op == "gte":
                return actual >= expected
            if op == "lt":
                return actual < expected
            if op == "lte":
                return actual <= expected
        except TypeError:
            return False
    raise InvalidConditionError(f"unknown operator: {op!r}")


def validate_condition(node: dict | None, *, _depth: int = 0) -> None:
    """Raises InvalidConditionError on anything malformed/unsafe — call
    this at save time (automation_service.py) so a bad condition is
    rejected before it's ever persisted, not discovered mid-execution."""
    if node is None:
        return
    if _depth > _MAX_DEPTH:
        raise InvalidConditionError("condition tree too deep")
    if not isinstance(node, dict):
        raise InvalidConditionError("condition node must be an object")

    if "and" in node or "or" in node:
        key = "and" if "and" in node else "or"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise InvalidConditionError(f"{key!r} must be a non-empty list")
        for child in children:
            validate_condition(child, _depth=_depth + 1)
        return
    if "not" in node:
        validate_condition(node["not"], _depth=_depth + 1)
        return
    if "field" in node:
        if not isinstance(node["field"], str) or not node["field"]:
            raise InvalidConditionError("'field' must be a non-empty string")
        op = node.get("op")
        if op not in _COMPARISON_OPS:
            raise InvalidConditionError(f"unknown operator: {op!r}")
        if op not in ("is_null", "is_not_null") and "value" not in node:
            raise InvalidConditionError(f"operator {op!r} requires 'value'")
        return
    raise InvalidConditionError(f"unrecognized condition node: {node!r}")


def evaluate_condition(node: dict | None, context: dict) -> bool:
    """No condition (None) means "always true" — an automation with no
    condition runs on every matching trigger. Assumes `node` already
    passed validate_condition (automation_service.py validates at save
    time); still defensively bounded here (depth limit) in case a
    pre-0030 row or a direct DB edit bypassed that."""
    return _evaluate(node, context, depth=0)


def _evaluate(node: dict | None, context: dict, *, depth: int) -> bool:
    if node is None:
        return True
    if depth > _MAX_DEPTH:
        raise InvalidConditionError("condition tree too deep")
    if not isinstance(node, dict):
        raise InvalidConditionError("condition node must be an object")

    if "and" in node:
        return all(_evaluate(child, context, depth=depth + 1) for child in node["and"])
    if "or" in node:
        return any(_evaluate(child, context, depth=depth + 1) for child in node["or"])
    if "not" in node:
        return not _evaluate(node["not"], context, depth=depth + 1)
    if "field" in node:
        actual = _resolve_field(context, node["field"])
        op = node["op"]
        expected = node.get("value")
        return _compare(op, actual, expected)

    raise InvalidConditionError(f"unrecognized condition node: {node!r}")
