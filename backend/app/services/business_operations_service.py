"""Business Operations: the read-model behind the operating console (Business Home,
Data, Analytics, Workflows, Integrations, AI workforce).

It composes what Klaros already stores — leads, customers, automations and their
executions, agents, the audit log, website versions, integration connections and the
integration catalog — and asks each *enabled* industry module (through
`operations_providers`) for its own metrics. Nothing is persisted here and nothing is
invented: an empty business shows zeros and "nothing yet", never sample data.

Generic by construction: this module never names a business type or vertical.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations.workforce import get_workforce_integration
from app.models.agent import Agent, AgentExecution, AgentToolPermission
from app.models.audit_log import AuditLog
from app.models.automation import Automation, AutomationExecution, AutomationStatus, AutomationVersion
from app.models.crm import Customer, Lead
from app.models.integration import ConnectionStatus, IntegrationConnection
from app.models.integration_catalog import IntegrationProviderCatalog, ProviderImplementationStatus
from app.models.vertical_extension import VerticalExtension
from app.models.website import WebsiteVersion, WebsiteVersionStatus
from app.services.business_builder_service import integration_state
from app.services.capability_vocabulary import canonical_key
from app.services.operations_providers import get_operations_provider
from app.services.vertical_extension_service import VerticalExtensionService

QUALIFIED_STATUSES = ("QUALIFIED", "BOOKED", "CONVERTED")
STARTER_WORKFLOW_NAME = "New lead alert"

# Integration groups shown in the Integration Center, matched on a provider's own
# capability tags (reference data) — never on a provider or business name.
_GROUPS: list[tuple[str, set[str]]] = [
    ("Payments", {"payment_processing", "billing"}),
    ("Accounting", {"accounting", "invoicing", "bookkeeping"}),
    ("Calendar", {"appointment_scheduling", "scheduling", "calendar_sync"}),
    ("Marketing", {"marketing", "advertising", "lead_generation", "local_listing", "reviews"}),
    ("CRM & operations", {"field_service_management", "job_scheduling", "dispatch"}),
]
GROUP_ORDER = ["CRM & operations", "Communication", "Calendar", "Payments", "Accounting", "Marketing", "Ecommerce", "Analytics", "AI Workforce"]

_AUDIT_LABEL = {
    "crm.qualify_lead": "Lead qualification recorded",
    "crm.ai_qualify_lead_advisory": "AI qualification advisory generated",
    "crm.update_lead": "Lead updated",
    "crm.create_appointment": "Appointment booked",
}


def _group_for(capabilities: list[str], category: str) -> str:
    keys = {canonical_key(c) for c in capabilities} | set(capabilities)
    for name, tags in _GROUPS:
        if keys & tags or set(capabilities) & tags:
            return name
    return {"calendar": "Calendar", "finance": "Payments", "marketing": "Marketing", "operations": "CRM & operations"}.get(category, "CRM & operations")


def _step_actions(steps: list[dict]) -> list[str]:
    return [s.get("action", "") for s in steps or [] if isinstance(s, dict)]


class BusinessOperationsService:
    def __init__(self, session_factory: async_sessionmaker, verticals: VerticalExtensionService) -> None:
        self._session_factory = session_factory
        self._verticals = verticals

    # ------------------------------------------------------------------ read

    async def get_operations(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)

            status_rows = (await session.execute(select(Lead.status, func.count()).where(Lead.tenant_id == tenant_id).group_by(Lead.status))).all()
            by_status = {s: n for s, n in status_rows}
            source_rows = (await session.execute(select(Lead.source, func.count()).where(Lead.tenant_id == tenant_id).group_by(Lead.source))).all()
            by_source = {s: n for s, n in source_rows}
            new_7d = int((await session.execute(select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant_id, Lead.created_at >= week_ago))).scalar_one())
            waiting = (
                await session.execute(
                    select(Lead).where(Lead.tenant_id == tenant_id, Lead.status == "NEW").order_by(Lead.created_at.desc()).limit(5)
                )
            ).scalars().all()
            recent_leads = (
                await session.execute(select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc()).limit(6))
            ).scalars().all()
            customers = int((await session.execute(select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id))).scalar_one())

            automations = (await session.execute(select(Automation).where(Automation.tenant_id == tenant_id).order_by(Automation.created_at))).scalars().all()
            versions = {
                v.id: v
                for v in (
                    await session.execute(
                        select(AutomationVersion).where(
                            AutomationVersion.tenant_id == tenant_id,
                            AutomationVersion.id.in_([a.published_version_id for a in automations if a.published_version_id] or [uuid.uuid4()]),
                        )
                    )
                ).scalars()
            }
            exec_rows = (
                await session.execute(
                    select(AutomationExecution).where(AutomationExecution.tenant_id == tenant_id).order_by(AutomationExecution.created_at.desc()).limit(200)
                )
            ).scalars().all()

            agents = (await session.execute(select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.created_at))).scalars().all()
            agent_tools: dict[uuid.UUID, list[str]] = {}
            for ap in (await session.execute(select(AgentToolPermission).where(AgentToolPermission.tenant_id == tenant_id))).scalars():
                agent_tools.setdefault(ap.agent_id, []).append(ap.tool_name)
            agent_execs = (
                await session.execute(select(AgentExecution).where(AgentExecution.tenant_id == tenant_id).order_by(AgentExecution.created_at.desc()).limit(200))
            ).scalars().all()

            audits = (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.tenant_id == tenant_id, AuditLog.tool.in_(list(_AUDIT_LABEL)))
                    .order_by(AuditLog.created_at.desc())
                    .limit(10)
                )
            ).scalars().all()
            published = (
                await session.execute(
                    select(WebsiteVersion)
                    .where(WebsiteVersion.tenant_id == tenant_id, WebsiteVersion.published_at.is_not(None))
                    .order_by(WebsiteVersion.published_at.desc())
                    .limit(3)
                )
            ).scalars().all()
            conns = {c.provider: c for c in (await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id))).scalars()}

        catalog = await self._catalog()

        # --- module contributions (only the tenant's enabled industry modules) ---
        module: dict[str, list] = {"metrics": [], "data": [], "breakdowns": [], "lead_stages": [], "activity": []}
        for key in await self._enabled_vertical_keys(tenant_id):
            fn = get_operations_provider(key)
            if fn is None:
                continue
            try:
                part = await fn(tenant_id)
            except Exception:  # noqa: BLE001 - one module's failure must never blank the console
                continue
            for k in module:
                module[k].extend(part.get(k, []))

        # --- workflows -----------------------------------------------------------
        last_by_auto: dict[uuid.UUID, AutomationExecution] = {}
        counts: dict[uuid.UUID, Counter] = {}
        for e in exec_rows:
            last_by_auto.setdefault(e.automation_id, e)
            counts.setdefault(e.automation_id, Counter())[e.status] += 1
        auto_by_id = {a.id: a for a in automations}
        workflows = []
        for a in automations:
            v = versions.get(a.published_version_id) if a.published_version_id else None
            last = last_by_auto.get(a.id)
            c = counts.get(a.id, Counter())
            workflows.append(
                {
                    "id": str(a.id), "name": a.name, "description": a.description, "status": a.status,
                    "trigger": (v.trigger_type if v else None), "trigger_event": ((v.trigger_config or {}).get("event_type") if v else None),
                    "actions": _step_actions(v.steps) if v else [],
                    "published": v is not None,
                    "runs": sum(c.values()), "failed_runs": c.get("FAILED", 0),
                    "last_run": {"status": last.status, "at": last.created_at.isoformat()} if last else None,
                }
            )
        notify_on_lead = any(
            w["status"] == AutomationStatus.ENABLED and w["trigger_event"] == "lead.created" and "notifications.create_notification" in w["actions"]
            for w in workflows
        )

        website_live = bool(published) and any(v.status == WebsiteVersionStatus.PUBLISHED for v in published)
        lead_stages = [
            {"key": "form", "label": "Website form submitted", "kind": "SYSTEM", "state": "READY" if website_live else "NOT_READY",
             "detail": "Your published website accepts enquiries." if website_live else "Publish your website so visitors can send enquiries.", "route": "/website"},
            {"key": "lead", "label": "Lead created", "kind": "SYSTEM", "state": "READY",
             "detail": "Every enquiry becomes a lead automatically.", "route": "/leads"},
            {"key": "notify", "label": "Team notified", "kind": "AUTOMATED",
             "state": "READY" if notify_on_lead else "CONFIGURATION_REQUIRED",
             "detail": "A workflow alerts your team about each new lead." if notify_on_lead else "No workflow alerts your team yet — add the starter workflow.",
             "route": "/business/workflows"},
            {"key": "qualify", "label": "Qualification", "kind": "ASSISTED", "state": "READY",
             "detail": "Run AI-assisted qualification from any lead (advisory — a person decides).", "route": "/leads"},
            *module["lead_stages"],
        ]

        # --- agents --------------------------------------------------------------
        last_agent: dict[uuid.UUID, AgentExecution] = {}
        agent_counts: Counter = Counter()
        for e in agent_execs:
            last_agent.setdefault(e.agent_id, e)
            agent_counts[e.agent_id] += 1
        agents_out = [
            {
                "id": str(a.id), "name": a.name, "purpose": a.purpose, "status": a.status, "autonomy_tier": a.autonomy_tier,
                "tools": sorted(agent_tools.get(a.id, [])),
                "executions": agent_counts.get(a.id, 0),
                "last_execution": {"status": last_agent[a.id].status, "at": last_agent[a.id].created_at.isoformat()} if a.id in last_agent else None,
            }
            for a in agents
        ]

        # --- activity (only real records) ----------------------------------------
        activity: list[dict[str, Any]] = []
        for l in recent_leads:
            activity.append({"at": l.created_at, "kind": "lead", "text": f"Lead received: {l.name}", "route": f"/leads/{l.id}"})
        for v in published:
            activity.append({"at": v.published_at, "kind": "website", "text": "Website version published", "route": "/website"})
        for e in exec_rows[:8]:
            a = auto_by_id.get(e.automation_id)
            if a:
                activity.append({"at": e.created_at, "kind": "workflow", "text": f"Workflow “{a.name}” {e.status.lower()}", "route": "/business/workflows"})
        for e in agent_execs[:6]:
            activity.append({"at": e.created_at, "kind": "agent", "text": f"Agent execution {e.status.lower()}", "route": "/agents"})
        for au in audits:
            activity.append({"at": au.created_at, "kind": "action", "text": _AUDIT_LABEL[au.tool] + ("" if au.result == "success" else " (failed)"),
                             "route": f"/leads/{au.entity_id}" if au.entity_id and au.tool.startswith("crm.") else None})
        for c in conns.values():
            if c.status == ConnectionStatus.CONNECTED and c.last_verified_at:
                activity.append({"at": c.last_verified_at, "kind": "integration", "text": f"Integration connected: {c.provider}", "route": "/settings/integrations"})
        for item in module["activity"]:
            activity.append({**item, "at": item["at"]})
        activity.sort(key=lambda x: x["at"], reverse=True)
        activity_out = [{**x, "at": x["at"].isoformat()} for x in activity[:15]]

        # --- integrations ---------------------------------------------------------
        integrations = []
        for p in catalog:
            conn = conns.get(p.provider_key)
            state = integration_state(p.implementation_status, conn.status if conn is not None else None)
            if state == "NOT_CONNECTED":
                state = "AVAILABLE"  # a real adapter exists; the tenant has not connected it
            integrations.append(
                {
                    "provider_key": p.provider_key, "name": p.display_name, "category": _group_for(list(p.capabilities or []), p.category),
                    "purpose": p.description, "capabilities": [str(c).replace("_", " ") for c in (p.capabilities or [])],
                    "state": state, "implementation": p.implementation_status,
                    "last_error": conn.last_error if conn is not None and state == "CONFIGURATION_REQUIRED" else None,
                }
            )
        wf = await get_workforce_integration().get_status(tenant_id)
        integrations.append(
            {
                "provider_key": wf.provider, "name": "Halla AI", "category": "AI Workforce",
                "purpose": "Voice, calls, qualification, booking and support — the AI workforce that talks to your customers.",
                "capabilities": [c.label.lower() for c in wf.capabilities],
                "state": "CONNECTED" if wf.status.value == "CONNECTED" else ("INTEGRATION_REQUIRED" if not wf.adapter_implemented else "AVAILABLE"),
                "implementation": "EXTERNAL", "last_error": None,
            }
        )

        # --- attention -------------------------------------------------------------
        attention = []
        if by_status.get("NEW", 0):
            n = by_status["NEW"]
            attention.append({"id": "new-leads", "text": f"{n} new lead{'s' if n != 1 else ''} waiting for a first response", "route": "/leads?status=NEW", "count": n})
        failed = sum(w["failed_runs"] for w in workflows)
        if failed:
            attention.append({"id": "failed-runs", "text": f"{failed} workflow run{'s' if failed != 1 else ''} failed", "route": "/business/workflows", "count": failed})

        total = sum(by_status.values())
        return {
            "leads": {
                "total": total, "new_7d": new_7d, "by_status": by_status, "by_source": by_source,
                "qualified": sum(by_status.get(s, 0) for s in QUALIFIED_STATUSES),
                "website_enquiries": by_source.get("WEB", 0),
                "waiting": [{"id": str(l.id), "name": l.name, "source": l.source, "created_at": l.created_at.isoformat()} for l in waiting],
                "recent": [{"id": str(l.id), "name": l.name, "status": l.status, "source": l.source, "created_at": l.created_at.isoformat()} for l in recent_leads],
            },
            "customers": customers,
            "workflows": workflows,
            "lead_pipeline": lead_stages,
            "agents": agents_out,
            "activity": activity_out,
            "integrations": integrations,
            "integration_groups": GROUP_ORDER,
            "attention": attention,
            "module": {"metrics": module["metrics"], "data": module["data"], "breakdowns": module["breakdowns"]},
            "starter_workflow_exists": any(w["name"] == STARTER_WORKFLOW_NAME for w in workflows),
        }

    # ----------------------------------------------------------------- helpers

    async def _catalog(self) -> list[IntegrationProviderCatalog]:
        async with self._session_factory() as session:  # global reference table
            return list((await session.execute(select(IntegrationProviderCatalog).order_by(IntegrationProviderCatalog.display_name))).scalars().all())

    async def _enabled_vertical_keys(self, tenant_id: uuid.UUID) -> list[str]:
        links = await self._verticals.list_enabled_for_organization(tenant_id)
        if not links:
            return []
        async with self._session_factory() as session:  # global reference table
            rows = (await session.execute(select(VerticalExtension.key).where(VerticalExtension.id.in_([l.vertical_extension_id for l in links])))).scalars().all()
        return list(rows)
