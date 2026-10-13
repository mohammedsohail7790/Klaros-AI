import pytest

from app.services import ai_provider as ap
from app.services.ai_provider import AICallBudget, AIErrorType, OpenAIAIProvider, ai_call_budget

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, body):
        self._b = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


def _fake_client(body, seen):
    class C:
        def __init__(self, timeout=None):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            seen["json"] = json
            return _Resp(body)

    return C


def ok_body(content="{}", finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"prompt_tokens": 5, "completion_tokens": 7}}


async def test_default_budget_is_unchanged_for_existing_callers(monkeypatch):
    seen = {}
    monkeypatch.setattr(ap.httpx, "AsyncClient", _fake_client(ok_body(), seen))
    p = OpenAIAIProvider("k")
    out = await p.generate_structured("hi")
    assert out.success and seen["json"]["max_tokens"] == p._max_output_tokens and seen["timeout"] == p._timeout_seconds


async def test_a_task_budget_raises_limits_only_inside_the_block(monkeypatch):
    seen = {}
    monkeypatch.setattr(ap.httpx, "AsyncClient", _fake_client(ok_body(), seen))
    p = OpenAIAIProvider("k")
    with ai_call_budget(AICallBudget(max_output_tokens=9000, timeout_seconds=111, model="special-model")):
        await p.generate_structured("plan")
    assert seen["json"]["max_tokens"] == 9000 and seen["timeout"] == 111 and seen["json"]["model"] == "special-model"
    await p.generate_structured("brief")
    assert seen["json"]["max_tokens"] == p._max_output_tokens and seen["json"]["model"] == p.model


async def test_a_cut_off_answer_is_a_clean_failure_not_broken_json(monkeypatch):
    calls = {"n": 0}

    class C:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls["n"] += 1
            return _Resp(ok_body('{"routes": [{"path": "/", "tit', finish="length"))

    monkeypatch.setattr(ap.httpx, "AsyncClient", C)
    out = await OpenAIAIProvider("k").generate_structured("plan")
    assert not out.success and out.error_type is AIErrorType.MALFORMED_RESPONSE
    assert calls["n"] == 1  # a truncated answer is not retried (it would be truncated again)


async def test_the_api_key_never_appears_in_errors(monkeypatch):
    class C:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("boom sk-SECRETSECRETSECRET1234567890")

    monkeypatch.setattr(ap.httpx, "AsyncClient", C)
    out = await OpenAIAIProvider("sk-SECRETSECRETSECRET1234567890").generate_structured("x")
    assert not out.success and "SECRETSECRET" not in (out.error_detail or "")
