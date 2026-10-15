"""The Marketing page must not say "0 leads" when the business has enquiries: a website or call enquiry is a lead even when no campaign claimed it."""

import pytest

from tests.test_halla_integration import _lead, _register
from tests.test_mt_referrals import _h

pytestmark = pytest.mark.asyncio


async def test_enquiries_with_no_campaign_are_counted_and_split_by_source(client) -> None:
    token = await _register(client, "Mkt Count Co", "mktcount@example.com")
    empty = (await client.get("/api/v1/marketing/summary", headers=_h(token))).json()
    assert (empty["total_leads"], empty["leads"], empty["leads_by_source"], empty["unattributed_leads"]) == (0, 0, {}, 0)
    await _lead(client, token, phone="+971500000071")
    await _lead(client, token, phone="+971500000072")
    s = (await client.get("/api/v1/marketing/summary", headers=_h(token))).json()
    assert s["total_leads"] == 2 and sum(s["leads_by_source"].values()) == 2 and s["unattributed_leads"] == 2
    assert s["leads"] == 0  # campaign-attributed leads stay a separate, honest number


async def test_another_business_enquiries_are_never_counted(client) -> None:
    a = await _register(client, "Mkt A Co", "mkta@example.com")
    b = await _register(client, "Mkt B Co", "mktb@example.com")
    await _lead(client, a, phone="+971500000073")
    assert (await client.get("/api/v1/marketing/summary", headers=_h(b))).json()["total_leads"] == 0
