"""The setup plan: what it takes to run THIS business on Klaros, derived from the tenant's own state. The same code for every business.

A plan is a short list of steps. Each says who does it (`klaros` does it on its own, or `you` need to connect something), and where it stands (`done`, `todo`, or
`platform` for a channel that Klaros itself must have configured on this deployment, which the business owner cannot change). Nothing here is hard-coded to a
business type: the crew comes from the modules the business enabled (`operating_crew`), and the channels are the ones Klaros can use for any business.

`activate` does what Klaros can do on its own: it sets up the AI crew (idempotent, respects the owner's switched-off members and auto-provision choice). It
sends nothing to anyone and connects nothing: every outward action still goes through the governed tools, approvals, the audit log and the AI kill switch.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models.integration import ConnectionStatus, IntegrationConnection
from app.models.website import Website
from app.services import operating_crew
from app.services.agent_service import AgentService

LIVE = (ConnectionStatus.CONNECTED, ConnectionStatus.UNVERIFIED)


async def _connected_providers(session_factory, tenant_id: uuid.UUID) -> set[str]:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        rows = (await session.execute(select(IntegrationConnection.provider, IntegrationConnection.status).where(IntegrationConnection.tenant_id == tenant_id))).all()
    return {provider for provider, status in rows if status in LIVE}


async def _has_website(session_factory, tenant_id: uuid.UUID) -> bool:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        return (await session.execute(select(func.count()).select_from(Website).where(Website.tenant_id == tenant_id))).scalar_one() > 0


def _step(key: str, title: str, why: str, owner: str, state: str, href: str | None = None) -> dict[str, Any]:
    return {"key": key, "title": title, "why": why, "owner": owner, "state": state, "href": href}


async def build_plan(session_factory, tenant_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings()
    verticals = await operating_crew.enabled_verticals(session_factory, tenant_id)
    crew = operating_crew.crew_for(verticals)
    have = await operating_crew.existing_crew(AgentService(session_factory), tenant_id)
    running = sum(1 for m in crew if (a := have.get(m.name)) is not None and a.status == "ACTIVE" and a.current_version_id)
    connected = await _connected_providers(session_factory, tenant_id)
    website = await _has_website(session_factory, tenant_id)
    email_ready = bool(settings.SENDGRID_API_KEY and settings.SENDGRID_FROM_EMAIL)
    sms_ready = bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER)

    steps = [
        _step("crew", "Your AI crew", f"{running} of {len(crew)} crew members are running. They qualify leads, follow up, chase invoices and brief you each morning.",
              "klaros", "done" if crew and running == len(crew) else "todo", "/business/home"),
        _step("receptionist", "AI receptionist and calls", "Answers calls, turns them into leads, and lets the crew phone leads (each call waits for your approval).",
              "you", "done" if "halla" in connected else "todo", "/workforce"),
        _step("email", "Email to customers", "Quotes, invoices and follow-ups can be emailed.", "klaros", "done" if email_ready else "platform", None),
        _step("sms", "Text messages to customers", "Reminders and follow-ups can be sent by text.", "klaros", "done" if sms_ready else "platform", None),
        _step("calendar", "Calendar", "Bookings land on your calendar and free slots are checked against it.", "you", "done" if "google_calendar" in connected else "todo", "/settings/integrations"),
        _step("payments", "Take payments", "Customers can pay invoices and deposits online.", "you", "done" if "stripe" in connected else "todo", "/settings/integrations"),
        _step("accounting", "Accounting", "Payments and invoices sync to your books.", "you", "done" if connected & {"quickbooks", "xero"} else "todo", "/settings/integrations"),
        _step("ads", "Ad accounts", "Spend and results flow into marketing reports.", "you", "done" if connected & {"google_ads", "meta_ads"} else "todo", "/settings/integrations"),
        _step("website", "Your website", "Where enquiries arrive from, straight into the CRM.", "you", "done" if website else "todo", "/website"),
    ]
    return {"verticals": verticals, "crew": {"running": running, "total": len(crew)}, "steps": steps, "todo": sum(1 for s in steps if s["owner"] == "you" and s["state"] == "todo")}


async def activate(session_factory, tenant_id: uuid.UUID, actor_id: uuid.UUID | None) -> dict[str, Any]:
    """Do what Klaros can do on its own for this business, then return the plan as it now stands."""
    created: list[str] = []
    result = await operating_crew.ensure_crew(session_factory, tenant_id, actor_id)
    if result is not None:
        created = result.created
    plan = await build_plan(session_factory, tenant_id)
    return {**plan, "activated": created}
