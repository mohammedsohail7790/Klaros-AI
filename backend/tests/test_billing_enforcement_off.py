"""Klaros is run for the owner's own businesses: with BILLING_ENFORCEMENT off (the default) an ended trial never blocks AI work."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.db.session import async_session_maker
from app.services import billing_service as bs
from app.tools.errors import ToolBillingLimitError


def _org(**over):
    base = dict(id=None, plan="growth", billing_status="trialing", trial_ends_at=datetime.now(UTC) - timedelta(days=3), current_period_end=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_the_gate_is_off_by_default() -> None:
    assert Settings().BILLING_ENFORCEMENT is False


@pytest.mark.asyncio
async def test_an_ended_trial_blocks_ai_only_when_enforcement_is_on(monkeypatch) -> None:
    service = bs.BillingService(async_session_maker)
    with pytest.raises(ToolBillingLimitError):
        await service.check_ai_usage_allowed(_org())  # the suite-wide default is ON
    monkeypatch.setattr(bs, "billing_enforced", lambda: False)
    for status in ("trialing", "past_due", "canceled"):
        await service.check_ai_usage_allowed(_org(billing_status=status))  # nothing raised when it is off, whatever the billing state


def test_billing_enforced_follows_the_setting(monkeypatch) -> None:
    monkeypatch.undo()  # drop the suite-wide "on" patch so the real function runs
    monkeypatch.setenv("BILLING_ENFORCEMENT", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert bs.billing_enforced() is False
        monkeypatch.setenv("BILLING_ENFORCEMENT", "true")
        get_settings.cache_clear()
        assert bs.billing_enforced() is True
    finally:
        get_settings.cache_clear()
