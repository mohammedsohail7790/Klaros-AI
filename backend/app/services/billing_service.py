"""Klaros's own SaaS subscription billing — a Stripe integration entirely
separate from app/tools/builtin/stripe_tools.py's tenant-owned Stripe
Connect keys (those collect payments FROM a tenant's own customers; this
one collects Klaros's subscription revenue FROM the tenant). Uses its own
platform credential (settings.STRIPE_PLATFORM_SECRET_KEY), never a
tenant's IntegrationConnection.

Every new Organization starts on a real 14-day trial (see
app/services/auth_service.py::register_organization) with full,
unmetered access. After the trial, continued use of the metered feature
(insights.generate_morning_brief — see app/tools/registry.py's
counts_toward_ai_usage check) requires an active subscription; on the
Solo plan that subscription is capped at PLAN_LIMITS["solo"]
recommendations per calendar month, counted directly from
AIInvocationLog (the existing real record of every AI call — no separate
counter table needed).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.integrations.stripe_client import StripeClient
from app.models.ai_invocation import AIInvocationLog
from app.models.organization import Organization
from app.tools.errors import ToolBillingLimitError

# AI recommendations (insights.generate_morning_brief calls that actually
# invoked a real AI provider) allowed per calendar month. None = unlimited.
PLAN_LIMITS: dict[str, int | None] = {"solo": 50, "growth": None, "scale": None}

PLAN_PRICE_ENV_VAR: dict[str, str] = {"solo": "STRIPE_PRICE_SOLO", "growth": "STRIPE_PRICE_GROWTH"}


class BillingNotConfiguredError(Exception):
    pass


class NoBillingCustomerError(Exception):
    pass


class UnknownPlanError(Exception):
    pass


@dataclass
class BillingStatus:
    plan: str
    billing_status: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    ai_usage_this_month: int
    ai_usage_limit: int | None


def _platform_stripe_client() -> StripeClient:
    settings = get_settings()
    if not settings.STRIPE_PLATFORM_SECRET_KEY:
        raise BillingNotConfiguredError("Klaros billing is not configured yet — contact support.")
    return StripeClient(settings.STRIPE_PLATFORM_SECRET_KEY)


def _price_id_for_plan(plan: str) -> str:
    settings = get_settings()
    env_var = PLAN_PRICE_ENV_VAR.get(plan)
    if env_var is None:
        raise UnknownPlanError(f"'{plan}' has no self-serve Checkout price — contact us for Scale.")
    price_id = getattr(settings, env_var)
    if not price_id:
        raise BillingNotConfiguredError(f"The {plan} plan isn't configured for checkout yet — contact support.")
    return price_id


def _start_of_month(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite (used in tests) drops tzinfo on round-trip even for a
    DateTime(timezone=True) column, unlike real Postgres — a naive value
    read back here was always written as UTC (see auth_service.py /
    billing_service.py, both always datetime.now(UTC)), so it's safe to
    assume UTC rather than raise on the offset-naive/aware comparison."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class BillingService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def _count_ai_usage_this_month(self, tenant_id: uuid.UUID, now: datetime) -> int:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            result = await session.execute(
                select(func.count()).select_from(AIInvocationLog).where(
                    AIInvocationLog.tenant_id == tenant_id,
                    AIInvocationLog.operation == "morning_brief_enrichment",
                    AIInvocationLog.created_at >= _start_of_month(now),
                )
            )
            return int(result.scalar_one())

    async def get_billing_status(self, org: Organization) -> BillingStatus:
        now = datetime.now(UTC)
        usage = await self._count_ai_usage_this_month(org.id, now)
        limit = PLAN_LIMITS.get(org.plan)
        return BillingStatus(
            plan=org.plan,
            billing_status=org.billing_status,
            trial_ends_at=_as_utc(org.trial_ends_at),
            current_period_end=_as_utc(org.current_period_end),
            ai_usage_this_month=usage,
            ai_usage_limit=limit,
        )

    async def check_ai_usage_allowed(self, org: Organization) -> None:
        now = datetime.now(UTC)

        trial_ends_at = _as_utc(org.trial_ends_at)
        trial_active = org.billing_status == "trialing" and trial_ends_at is not None and now < trial_ends_at
        if trial_active:
            return

        if org.billing_status in ("trialing", "past_due", "canceled"):
            raise ToolBillingLimitError(
                "Your 14-day free trial has ended — subscribe from Settings → Billing to keep "
                "generating AI recommendations."
                if org.billing_status == "trialing"
                else "Your Klaros subscription is not active — update billing from Settings → Billing "
                "to keep generating AI recommendations."
            )

        limit = PLAN_LIMITS.get(org.plan)
        if limit is None:
            return

        usage = await self._count_ai_usage_this_month(org.id, now)
        if usage >= limit:
            raise ToolBillingLimitError(
                f"You've used all {limit} AI recommendations included in the Solo plan this month — "
                "upgrade to Growth for unlimited recommendations."
            )

    async def create_checkout_session(
        self, org: Organization, plan: Literal["solo", "growth"], *, success_url: str, cancel_url: str
    ) -> str:
        price_id = _price_id_for_plan(plan)
        client = _platform_stripe_client()

        async with self._session_factory() as session:
            await set_tenant_context(session, org.id)
            db_org = await session.get(Organization, org.id)
            if db_org is None:
                raise UnknownPlanError("Organization not found")
            if db_org.stripe_customer_id is None:
                customer = await client.create_customer(metadata={"tenant_id": str(org.id)})
                db_org.stripe_customer_id = customer.id
                await session.commit()
                await session.refresh(db_org)
            customer_id = db_org.stripe_customer_id

        session_resp = await client.create_subscription_checkout_session(
            price_id=price_id,
            customer_id=customer_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"tenant_id": str(org.id), "plan": plan},
        )
        return session_resp.url

    async def create_portal_session(self, org: Organization, *, return_url: str) -> str:
        if org.stripe_customer_id is None:
            raise NoBillingCustomerError("Subscribe to a plan first before managing billing.")
        client = _platform_stripe_client()
        session_resp = await client.create_billing_portal_session(
            customer_id=org.stripe_customer_id, return_url=return_url
        )
        return session_resp.url

    async def apply_checkout_completed(self, tenant_id: uuid.UUID, plan: str, customer_id: str, subscription_id: str) -> None:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            org = await session.get(Organization, tenant_id)
            if org is None:
                return
            org.stripe_customer_id = customer_id
            org.stripe_subscription_id = subscription_id
            org.plan = plan
            org.billing_status = "active"
            await session.commit()

    async def apply_subscription_updated(self, subscription_id: str, *, status: str, current_period_end: datetime | None) -> None:
        mapped_status = {
            "active": "active",
            "trialing": "active",
            "past_due": "past_due",
            "unpaid": "past_due",
            "canceled": "canceled",
            "incomplete_expired": "canceled",
        }.get(status, status)
        async with self._session_factory() as session:
            org = (
                await session.execute(select(Organization).where(Organization.stripe_subscription_id == subscription_id))
            ).scalar_one_or_none()
            if org is None:
                return
            # Phase 17B-2R: tenant identity here comes from the verified
            # Stripe webhook payload's subscription id, resolved to an
            # Organization row by this SAME query above — no tenant_id
            # parameter exists on this method because none is known until
            # this lookup returns one (matches the "resolve trusted
            # identity first, stamp after" shape used by
            # McpCredentialService.authenticate / TeamService.preview_invite).
            await set_tenant_context(session, org.id)
            org.billing_status = mapped_status
            if current_period_end is not None:
                org.current_period_end = current_period_end
            await session.commit()

    async def apply_subscription_deleted(self, subscription_id: str) -> None:
        async with self._session_factory() as session:
            org = (
                await session.execute(select(Organization).where(Organization.stripe_subscription_id == subscription_id))
            ).scalar_one_or_none()
            if org is None:
                return
            await set_tenant_context(session, org.id)
            org.billing_status = "canceled"
            await session.commit()
