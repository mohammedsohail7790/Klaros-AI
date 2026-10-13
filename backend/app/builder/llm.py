"""The builder's one door to a model: bounded, budgeted, usage-tracked, and never trusted.

Every call goes through `BuilderLLM.json_call`. It applies the per-task budget (tokens, timeout, model), refuses to exceed
the run's call allowance, passes through the consent boundary (`bound_ai_provider`), records the call in the existing AI
invocation log, and returns parsed JSON or None. None means "no usable AI answer" and every caller has a deterministic path.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

import structlog

from app.core.config import get_settings
from app.models.actor import ActorType
from app.services.ai_boundary import bound_ai_provider
from app.services.ai_invocation_log_service import record_ai_invocation
from app.services.ai_provider import AICallBudget, AIProvider, ai_call_budget

logger = structlog.get_logger(__name__)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0
    models: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "failures": self.failures, "models": sorted(self.models)}


def extract_json(text: str) -> dict | None:
    """Parse a JSON object out of a model reply (tolerates a code fence or a sentence around it)."""
    if not text:
        return None
    t = _FENCE.sub("", text.strip())
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except ValueError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            v = json.loads(t[start : end + 1])
            return v if isinstance(v, dict) else None
        except ValueError:
            return None
    return None


class BuilderLLM:
    def __init__(self, provider: AIProvider | None, session_factory, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, *, max_calls: int | None = None) -> None:
        self._provider = provider
        self._sf = session_factory
        self._tenant = tenant_id
        self._actor = actor_id
        self.usage = Usage()
        self.max_calls = max_calls if max_calls is not None else get_settings().BUILDER_MAX_LLM_CALLS_PER_RUN

    @property
    def available(self) -> bool:
        return bool(self._provider and getattr(self._provider, "is_connected", False)) and self.usage.calls < self.max_calls

    async def json_call(self, prompt: str, *, task: str, max_output_tokens: int, timeout_seconds: float | None = None) -> dict | None:
        if not self.available:
            return None
        s = get_settings()
        budget = AICallBudget(max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds or s.BUILDER_LLM_TIMEOUT_SECONDS, model=s.BUILDER_PLAN_MODEL)
        self.usage.calls += 1
        with ai_call_budget(budget):
            outcome = await bound_ai_provider(self._provider, self._sf, self._tenant, f"builder_{task}").generate_structured(prompt)
        self.usage.input_tokens += outcome.input_tokens or 0
        self.usage.output_tokens += outcome.output_tokens or 0
        if getattr(outcome, "model", None):
            self.usage.models.add(str(outcome.model))
        try:
            await record_ai_invocation(
                self._sf, tenant_id=self._tenant, actor_type=ActorType.AI, actor_id=self._actor, operation=f"builder_{task}", outcome=outcome,
                input_metadata={"prompt_chars": len(prompt)}, output_metadata={"success": outcome.success},
            )
        except Exception:  # noqa: BLE001 - audit trouble must never break a build
            pass
        if not outcome.success or not outcome.raw_text:
            self.usage.failures += 1
            logger.warning("builder_llm_unusable", task=task, error=str(getattr(outcome, "error_type", None)))
            return None
        parsed = extract_json(outcome.raw_text)
        if parsed is None:
            self.usage.failures += 1
        return parsed
