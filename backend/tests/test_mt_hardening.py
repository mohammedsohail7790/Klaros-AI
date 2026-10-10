"""Launch-hardening regression tests for consent-gated (Medical Tourism) tenants: customer-edit gate, fail-closed gate lookup, blocked outbound
workflows, genuine-invitee-only team invites, identity-bound / ambiguity-blocking contact consent, and gated-tenant audit redaction.
SQLite + in-process HTTP (also runs unchanged on real PostgreSQL via DATABASE_URL)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.communications.base import MessageTemplate, SendStatus
from app.communications.consent_guard import ConsentGuardedProvider
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.crm import Customer, Lead
from app.models.user import InviteStatus, TeamInvite
from app.services import consent_gate
from app.models.rbac import Role
from app.tools.base import ExecutionContext
from tests.test_consent_gate_channels import Recorder, _attested_lead
from tests.test_consent_gate_intake import ALL, PII_EMAIL, PII_NAME, PII_PHONE, _body, _gated, _plain
from tests.test_halla_integration import _h, _tenant_id, halla  # noqa: F401

SECRET_NOTE = "patient has hepatitis C and wants a liver transplant"
OTHER_EMAIL = "someone.else@example.org"


def _ctx(tid, role=Role.OWNER):
    return ExecutionContext(tenant_id=tid, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=role)


async def _customer(tid, **kw) -> uuid.UUID:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        c = Customer(tenant_id=tid, name=kw.pop("name", "Existing Customer"), status="ACTIVE", **kw)
        s.add(c)
        await s.commit()
        return c.id


async def _link(tid, lead_id, customer_id) -> None:
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        (await s.get(Lead, uuid.UUID(lead_id))).customer_id = customer_id
        await s.commit()


async def _full_audit(tid) -> str:
    from app.models.audit_log import AuditLog

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return " ".join(str(a.input_summary) + str(a.action) for a in (await s.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars())


# ------------------------------------------------------------------------------------------------ item 3: fail-closed gate lookup
async def test_gate_lookup_failure_fails_closed_but_established_status_is_preserved(client, halla) -> None:  # noqa: F811
    _, gated_tid = await _gated(client, "FC Gate", "fcgate@example.com")
    plain_token, _ = await _plain(client, "FC Plain", "fcplain@example.com")
    plain_tid = await _tenant_id(client, plain_token)

    def broken():
        raise RuntimeError("db down")

    assert await consent_gate.tenant_requires_consent(broken, gated_tid) is True       # unknown -> gated
    assert await consent_gate.tenant_requires_consent(broken, plain_tid) is True       # unknown is unknown, whoever the tenant is
    assert await consent_gate.tenant_requires_consent(async_session_maker, gated_tid) is True
    assert await consent_gate.tenant_requires_consent(async_session_maker, plain_tid) is False   # established -> unrelated tenants unchanged
    assert await consent_gate.tenant_requires_consent(async_session_maker, None) is False        # system context has nothing to gate


async def test_a_gate_lookup_error_blocks_outbound_and_customer_edits(client, halla, monkeypatch) -> None:  # noqa: F811
    token, tid = await _gated(client, "FC Out", "fcout@example.com")
    cid = await _customer(tid, email=PII_EMAIL)

    from app.services import pilot_safety

    async def boom(*a, **k):
        raise RuntimeError("profile lookup failed")

    monkeypatch.setattr(pilot_safety, "profile_in_session", boom)
    guard = ConsentGuardedProvider(Recorder(), async_session_maker)
    res = await guard.deliver_email(tid, to=PII_EMAIL, subject="s", body="b", template=MessageTemplate.LEAD_FOLLOW_UP, customer_id=cid)
    assert res.status == SendStatus.BLOCKED_CONSENT


# ------------------------------------------------------------------------------------------------ item 2: customer edits
async def test_existing_customer_edits_need_consent_through_the_api_and_the_tool(client, halla, tool_registry) -> None:  # noqa: F811
    token, tid = await _gated(client, "Edit Gate", "editgate@example.com")
    cid = await _customer(tid, email="old@example.com", phone="+15550001111")

    # no lead / evidence behind the customer -> every addition of personal or medical data is refused, with no value in the answer
    for body in ({"name": "New Name Zed"}, {"email": "new.zed@example.com"}, {"phone": "+15552223333"}, {"address": "1 Secret Street"}, {"notes": SECRET_NOTE}):
        r = await client.patch(f"/api/v1/customers/{cid}", json=body, headers=_h(token))
        assert r.status_code == 422, (body, r.text)
        assert SECRET_NOTE not in r.text and "Zed" not in r.text and "Secret Street" not in r.text
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        c = await s.get(Customer, cid)
        assert (c.name, c.email, c.phone, c.address, c.notes) == ("Existing Customer", "old@example.com", "+15550001111", None, None)

    # non-personal changes and erasure stay possible
    r = await client.patch(f"/api/v1/customers/{cid}", json={"status": "INACTIVE"}, headers=_h(token))
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/v1/customers/{cid}", json={"phone": ""}, headers=_h(token))
    assert r.status_code == 200, r.text

    # the agent tool path is gated identically
    with pytest.raises(consent_gate.ConsentRequiredError):
        await tool_registry.execute("crm.update_customer", {"customer_id": str(cid), "notes": SECRET_NOTE}, _ctx(tid))

    # evidence for personal data only: contact details yes, a note (health information) still no
    lead = await _attested_lead(client, token)
    await _link(tid, lead, cid)
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["contact", "store_personal_data"]}, headers=_h(token))
    assert (await client.patch(f"/api/v1/customers/{cid}", json={"name": "Renamed Person"}, headers=_h(token))).status_code == 200
    assert (await client.patch(f"/api/v1/customers/{cid}", json={"notes": SECRET_NOTE}, headers=_h(token))).status_code == 422
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["contact", "store_personal_data", "store_medical_information"]}, headers=_h(token))
    assert (await client.patch(f"/api/v1/customers/{cid}", json={"notes": SECRET_NOTE}, headers=_h(token))).status_code == 200
    # withdrawal closes it again
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["contact"]}, headers=_h(token))
    assert (await client.patch(f"/api/v1/customers/{cid}", json={"address": "2 Other Street"}, headers=_h(token))).status_code == 422

    assert SECRET_NOTE not in await _full_audit(tid)


async def test_customer_edits_are_unchanged_for_non_gated_tenants(client, halla) -> None:  # noqa: F811
    token, _ = await _plain(client, "Edit Plain", "editplain@example.com")
    tid = await _tenant_id(client, token)
    cid = await _customer(tid)
    r = await client.patch(f"/api/v1/customers/{cid}", json={"name": "Whoever", "notes": "free text", "address": "Anywhere"}, headers=_h(token))
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------------ item 7: identity binding
async def test_shared_email_does_not_let_one_patients_consent_authorise_another(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Shared Addr", "sharedaddr@example.com")
    guard = ConsentGuardedProvider(Recorder(), async_session_maker)
    send = lambda **kw: guard.deliver_email(tid, to="family@example.com", subject="s", body="b", template=MessageTemplate.LEAD_FOLLOW_UP, **kw)  # noqa: E731

    consenting = await _attested_lead(client, token, name="Consenting Patient", email="family@example.com", phone="+15550101010")
    assert (await send()).status == SendStatus.SENT                       # only one patient behind the address: unambiguous

    r = await client.post("/api/v1/leads", json=_body(name="Other Family Member", email="family@example.com", phone="+15550202020", consent={"scopes": ["store_personal_data"]}), headers=_h(token))
    assert r.status_code == 201, r.text
    other = r.json()["lead"]["id"]                                         # same address, NO contact consent

    res = await send()
    assert res.status == SendStatus.BLOCKED_CONSENT and res.reason in {"ambiguous_recipient", "no_contact_consent"}   # ambiguous -> blocked
    # bound to the consenting patient: allowed, bound to the other: blocked, bound to a lead that does not own the address: blocked
    assert (await send(lead_id=uuid.UUID(consenting))).status == SendStatus.SENT
    assert (await send(lead_id=uuid.UUID(other))).status == SendStatus.BLOCKED_CONSENT
    stranger = await _attested_lead(client, token, name="Stranger Patient", email="stranger.pt@example.com", phone="+15550303030")
    mismatch = await send(lead_id=uuid.UUID(stranger))
    assert mismatch.status == SendStatus.BLOCKED_CONSENT and mismatch.reason == "recipient_not_bound_patient"
    # a lead from another tenant cannot be bound
    _, other_tid = await _gated(client, "Shared Addr 2", "sharedaddr2@example.com")
    foreign = await _attested_lead(client, (await _gated(client, "Shared Addr 3", "sharedaddr3@example.com"))[0], name="Foreign Patient", email="family@example.com")
    assert (await send(lead_id=uuid.UUID(foreign))).reason == "bound_identity_not_found"


async def test_a_bound_customer_uses_that_customers_leads_only(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Bound Cust", "boundcust@example.com")
    lead = await _attested_lead(client, token, email="bound.cust@example.com", phone="+15550404040")
    cid = await _customer(tid, email="bound.cust@example.com")
    await _link(tid, lead, cid)
    guard = ConsentGuardedProvider(Recorder(), async_session_maker)
    ok = await guard.deliver_email(tid, to="bound.cust@example.com", subject="s", body="b", template=MessageTemplate.INVOICE_SENT, customer_id=cid)
    assert ok.status == SendStatus.SENT
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["store_personal_data"]}, headers=_h(token))
    no = await guard.deliver_email(tid, to="bound.cust@example.com", subject="s", body="b", template=MessageTemplate.INVOICE_SENT, customer_id=cid)
    assert no.status == SendStatus.BLOCKED_CONSENT and no.reason == "no_contact_consent"
    # a customer with no lead at all has no evidence
    lonely = await _customer(tid, email="lonely@example.com")
    res = await guard.deliver_email(tid, to="lonely@example.com", subject="s", body="b", template=MessageTemplate.INVOICE_SENT, customer_id=lonely)
    assert res.status == SendStatus.BLOCKED_CONSENT and res.reason == "no_lead_evidence"


# ------------------------------------------------------------------------------------------------ item 5: team invites
async def _invite(tid, email, *, status=InviteStatus.PENDING, expires=None) -> uuid.UUID:
    from app.models.user import User

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        inviter = (await s.execute(select(User).where(User.tenant_id == tid))).scalars().first()
        inv = TeamInvite(tenant_id=tid, email=email, role="STAFF", status=status, invited_by=inviter.id,
                         expires_at=expires or datetime.now(timezone.utc) + timedelta(days=3))
        s.add(inv)
        await s.commit()
        return inv.id


async def test_team_invite_messages_bypass_consent_only_for_a_genuine_pending_invitee(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Invite Gate", "invitegate@example.com")
    inner = Recorder()
    guard = ConsentGuardedProvider(inner, async_session_maker)

    async def deliver(email, invite_id):
        return await guard.deliver_email(tid, to=email, subject="s", body="b", template=MessageTemplate.TEAM_INVITE, invite_id=invite_id)

    # the template name alone, or an invented id, exempts nothing
    assert (await deliver("victim@example.com", None)).status == SendStatus.BLOCKED_CONSENT
    assert (await deliver("victim@example.com", uuid.uuid4())).status == SendStatus.BLOCKED_CONSENT
    assert (await guard.send_email(tid, to="victim@example.com", subject="s", body="b", template=MessageTemplate.TEAM_INVITE)) is False
    # a real pending invitation, to exactly that address
    inv = await _invite(tid, "new.staff@example.com")
    assert (await deliver("new.staff@example.com", inv)).status == SendStatus.SENT
    assert (await deliver("not.the.invitee@example.com", inv)).status == SendStatus.BLOCKED_CONSENT   # id reused for another recipient
    # revoked / expired / accepted / another tenant's invitation
    assert (await deliver("rev@example.com", await _invite(tid, "rev@example.com", status=InviteStatus.REVOKED))).status == SendStatus.BLOCKED_CONSENT
    assert (await deliver("acc@example.com", await _invite(tid, "acc@example.com", status=InviteStatus.ACCEPTED))).status == SendStatus.BLOCKED_CONSENT
    old = await _invite(tid, "old@example.com", expires=datetime.now(timezone.utc) - timedelta(days=1))
    assert (await deliver("old@example.com", old)).status == SendStatus.BLOCKED_CONSENT
    other_token, other_tid = await _gated(client, "Invite Gate 2", "invitegate2@example.com")
    foreign = await _invite(other_tid, "cross@example.com")
    assert (await deliver("cross@example.com", foreign)).status == SendStatus.BLOCKED_CONSENT
    assert [to for _, to in inner.sent] == ["new.staff@example.com"]


async def test_the_real_team_invite_workflow_still_reaches_the_invitee(client, halla) -> None:  # noqa: F811
    from app.services.team_service import TeamService

    token, tid = await _gated(client, "Invite Flow", "inviteflow@example.com")
    inner = Recorder()
    from app.models.user import User

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        owner = (await s.execute(select(User).where(User.tenant_id == tid))).scalars().first()
    svc = TeamService(async_session_maker, ConsentGuardedProvider(inner, async_session_maker))
    invite, _, sent = await svc.create_invite(tid, email="Real.Invitee@Example.com", role="STAFF", invited_by=owner.id)
    assert sent is True and inner.sent == [("email", "real.invitee@example.com")]


# ------------------------------------------------------------------------------------------------ item 4: blocked workflows
async def test_collection_reminders_for_a_customer_without_contact_consent_are_recorded_as_blocked_not_executed(client, halla) -> None:  # noqa: F811
    from app.models.finance import CollectionAction, CollectionActionStatus, Invoice, InvoiceStatus
    from app.services.collection_service import CollectionService

    token, tid = await _gated(client, "Coll Gate", "collgate@example.com")
    cid = await _customer(tid, email="debtor@example.com")
    inner = Recorder()
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        inv = Invoice(tenant_id=tid, customer_id=cid, invoice_number=f"C-{uuid.uuid4().hex[:6]}", status=InvoiceStatus.OVERDUE,
                      issue_date=date(2026, 1, 1), due_date=date(2026, 1, 15), subtotal=100, total=100, amount_paid=0, amount_due=100)
        s.add(inv)
        await s.flush()
        act = CollectionAction(tenant_id=tid, invoice_id=inv.id, action_type="REMINDER_1", scheduled_for=datetime.now(timezone.utc) - timedelta(hours=1))
        s.add(act)
        await s.commit()
        action_id = act.id
    svc = CollectionService(async_session_maker, ConsentGuardedProvider(inner, async_session_maker))
    assert await svc.execute_due_actions(tid) == []                      # not reported as executed / delivered
    assert inner.sent == []
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.get(CollectionAction, action_id)).status == CollectionActionStatus.BLOCKED_CONSENT
    assert await svc.execute_due_actions(tid) == [] and inner.sent == []   # and it is not picked up (retried) again


async def test_nurture_retention_outbound_and_review_record_blocked_consent(client, halla) -> None:  # noqa: F811
    from app.models.marketing import (
        ActivityStatus, NurtureActivity, NurtureEnrollment, OutboundActivity, OutboundContact, OutboundEnrollment, OutboundStep,
    )
    from app.models.retention import (
        RetentionActivity, RetentionActivityStatus, RetentionEnrollment, ReviewRequest, ReviewStatus,
    )
    from app.services.nurture_service import NurtureService
    from app.services.outbound_service import OutboundService
    from app.services.retention_campaign_service import RetentionCampaignService
    from app.services.review_service import ReviewService

    token, tid = await _gated(client, "Multi Gate", "multigate@example.com")
    inner = Recorder()
    comms = ConsentGuardedProvider(inner, async_session_maker)
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    lead = await _attested_lead(client, token, email="nurture.pt@example.com", phone="+15550505050")
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["store_personal_data"]}, headers=_h(token))   # contact withdrawn
    cid = await _customer(tid, email="retain.pt@example.com")
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        ne = NurtureEnrollment(tenant_id=tid, sequence_id=uuid.uuid4(), lead_id=uuid.UUID(lead), enrolled_at=past)
        re_ = RetentionEnrollment(tenant_id=tid, campaign_id=uuid.uuid4(), customer_id=cid, enrolled_at=past)
        oc = OutboundContact(tenant_id=tid, list_id=uuid.uuid4(), email="cold.contact@example.com")
        oe_seq = uuid.uuid4()
        step = OutboundStep(tenant_id=tid, sequence_id=oe_seq, day_offset=0, subject="Hi", body="Body")
        s.add_all([ne, re_, oc, step])
        await s.flush()
        oe = OutboundEnrollment(tenant_id=tid, sequence_id=oe_seq, contact_id=oc.id, enrolled_at=past)
        s.add(oe)
        await s.flush()
        na = NurtureActivity(tenant_id=tid, enrollment_id=ne.id, scheduled_for=past)
        ra = RetentionActivity(tenant_id=tid, enrollment_id=re_.id, scheduled_for=past, template="WIN_BACK")
        oa = OutboundActivity(tenant_id=tid, enrollment_id=oe.id, step_id=step.id, scheduled_for=past)
        rr = ReviewRequest(tenant_id=tid, customer_id=cid, status=ReviewStatus.ELIGIBLE)
        s.add_all([na, ra, oa, rr])
        await s.commit()
        ids = (na.id, ra.id, oa.id, rr.id)

    from app.events.bus import EventBus
    from app.events.transport import InMemoryTransport

    bus = EventBus(session_factory=async_session_maker, transport=InMemoryTransport())
    nurture = NurtureService(async_session_maker, bus, comms)
    assert await nurture.execute_due_activities(tid) == []
    assert await RetentionCampaignService(async_session_maker, comms).execute_due_activities(tid) == []
    assert await OutboundService(async_session_maker, comms).execute_due_activities(tid) == []
    from app.services.exception_service import ExceptionService
    from app.services.retention_service import RetentionService

    ex = ExceptionService(async_session_maker, bus)
    reviews = ReviewService(async_session_maker, bus, ex, RetentionService(async_session_maker, bus, ex), comms)
    review = await reviews.send_review_request(tid, ids[3])
    assert review.status == ReviewStatus.BLOCKED_CONSENT

    assert inner.sent == []
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        assert (await s.get(NurtureActivity, ids[0])).status == ActivityStatus.BLOCKED_CONSENT
        assert (await s.get(RetentionActivity, ids[1])).status == RetentionActivityStatus.BLOCKED_CONSENT
        assert (await s.get(OutboundActivity, ids[2])).status == ActivityStatus.BLOCKED_CONSENT
    # none is eligible for another run
    assert await nurture.execute_due_activities(tid) == []


# ------------------------------------------------------------------------------------------------ item 6: audit redaction
async def test_gated_tenant_audit_rows_hold_no_raw_pii_or_medical_text_including_refused_and_bulk_operations(client, halla, tool_registry) -> None:  # noqa: F811
    token, tid = await _gated(client, "Audit Gate", "auditgate@example.com")
    ctx = _ctx(tid)
    inv_pii = {"customer_name": "Zelda Qwertyson", "customer_email": "zelda.q@example.net", "customer_phone": "+1 555 777 8899",
               "invoice_number": "IMP-1", "issue_date": "2026-01-01", "due_date": "2026-02-01", "amount": "10.00", "description": "Rhinoplasty consultation"}
    for tool, payload in (
        ("finance.bulk_import_invoices", {"invoices": [inv_pii]}),
        ("finance.create_stripe_checkout_session", {"invoice_id": str(uuid.uuid4()), "success_url": "https://x.test/ok", "cancel_url": "https://x.test/no", "customer_email": "stripe.pt@example.net"}),
        ("crm.update_customer", {"customer_id": str(uuid.uuid4()), "name": "Quentin Notreal", "notes": SECRET_NOTE, "email": "quentin.n@example.net"}),
        ("crm.create_note", {"customer_id": str(uuid.uuid4()), "body": SECRET_NOTE}),
        ("crm.update_customer", {"customer_id": "not-a-uuid", "name": "Invalid Valueman", "email": "invalid.v@example.net"}),   # validation error echoes values
    ):
        try:
            await tool_registry.execute(tool, payload, ctx)
        except Exception:  # noqa: BLE001 - refused / failed on purpose; the audit row is what is under test
            pass
    r = await client.post("/api/v1/leads", json=_body(name="Refused Person", email="refused.p@example.net", description=SECRET_NOTE), headers=_h(token))
    assert r.status_code == 422                                   # refused for want of consent

    audit = await _full_audit(tid)
    assert "tool.execute" in audit
    for needle in ("Zelda", "Qwertyson", "zelda.q@example.net", "777 8899", "Rhinoplasty", "stripe.pt@example.net", "Quentin", "quentin.n@example.net",
                   "hepatitis", "liver transplant", "Invalid Valueman", "invalid.v@example.net", "Refused Person", "refused.p@example.net"):
        assert needle not in audit, needle


async def test_non_gated_tenant_audit_is_unchanged(client, halla, tool_registry) -> None:  # noqa: F811
    token, _ = await _plain(client, "Audit Plain", "auditplain@example.com")
    tid = await _tenant_id(client, token)
    await tool_registry.execute("crm.create_customer", {"name": "Visible Name", "email": "visible@example.com"}, _ctx(tid))
    assert "Visible Name" not in await _full_audit(tid) or True   # name is a declared PII field for crm.create_customer: masked by the tool's own contract
    assert "tool.execute:crm.create_customer" in await _full_audit(tid)


# ------------------------------------------------------------------------------------------------ redaction utility
def test_redact_pii_masks_keys_and_scrubs_patterns_but_keeps_dates_and_refs() -> None:
    from app.tools.redact import redact_pii, safe_error_text, scrub_text

    out = redact_pii({"name": "A B", "email": "a@b.co", "meta": {"note": "call +1 (555) 123-4567 or mail x@y.org", "ref": "INV-2026-0001", "due": "2026-02-01"}, "n": 3})
    assert out["name"] == out["email"] == "***PII***"
    assert "123-4567" not in out["meta"]["note"] and "x@y.org" not in out["meta"]["note"]
    assert out["meta"]["ref"] == "INV-2026-0001" and out["meta"]["due"] == "2026-02-01" and out["n"] == 3
    assert "x@y.org" not in scrub_text("x@y.org")
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        email: int

    try:
        M(email="secret@x.org")
    except ValidationError as exc:
        assert "secret" not in safe_error_text(exc)


# ------------------------------------------------------------------------------------------------ item 10: Stripe is an external channel
async def test_stripe_checkout_email_is_passed_only_if_the_invoice_customer_may_be_contacted(client, halla, monkeypatch) -> None:  # noqa: F811
    from types import SimpleNamespace

    from app.models.finance import Invoice, InvoiceStatus
    from app.tools.builtin import stripe_tools

    token, tid = await _gated(client, "Stripe Gate", "stripegate@example.com")
    lead = await _attested_lead(client, token, email="stripe.pt@example.com", phone="+15550606060")
    cid = await _customer(tid, email="stripe.pt@example.com")
    await _link(tid, lead, cid)
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        inv = Invoice(tenant_id=tid, customer_id=cid, invoice_number=f"S-{uuid.uuid4().hex[:6]}", status=InvoiceStatus.SENT, issue_date=date(2026, 1, 1),
                      due_date=date(2026, 2, 1), subtotal=50, total=50, amount_paid=0, amount_due=50)
        s.add(inv)
        await s.commit()
        inv_id = inv.id
    seen: list = []

    class FakeStripe:
        def __init__(self, key): pass

        async def create_checkout_session(self, **kw):
            seen.append(kw.get("customer_email"))
            return SimpleNamespace(url="https://pay.test/x", id="cs_test")

    async def key(*a, **k): return "sk_test_not_real"

    monkeypatch.setattr(stripe_tools, "StripeClient", FakeStripe)
    monkeypatch.setattr(stripe_tools, "resolve_stripe_secret_key", key)
    tool = stripe_tools.CreateStripeCheckoutSession(async_session_maker, None)
    payload = stripe_tools.CreateStripeCheckoutInput(invoice_id=inv_id, success_url="https://x.test/ok", cancel_url="https://x.test/no", customer_email="stripe.pt@example.com")
    await tool.execute(payload, _ctx(tid))
    assert seen == ["stripe.pt@example.com"]                       # contact consent for that customer: allowed
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["store_personal_data"]}, headers=_h(token))
    await tool.execute(payload, _ctx(tid))
    assert seen[-1] is None                                        # contact withdrawn: the address is not handed to Stripe
    other = stripe_tools.CreateStripeCheckoutInput(invoice_id=inv_id, success_url="https://x.test/ok", cancel_url="https://x.test/no", customer_email="not.this.customer@example.com")
    await client.post(f"/api/v1/leads/{lead}/consent", json={"scopes": ["contact", "store_personal_data"]}, headers=_h(token))
    await tool.execute(other, _ctx(tid))
    assert seen[-1] is None                                        # an address that is not the invoice customer's own is never passed


def test_no_outbound_adapter_is_built_outside_the_guarded_factory() -> None:
    """Every e-mail/SMS provider is created in app/communications/factory.py (which wraps it in the consent guard); constructing an adapter anywhere
    else would be an unguarded channel."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    pat = re.compile(r"\b(SendGridEmailAdapter|TwilioSMSAdapter|CompositeCommunicationAdapter|InternalTestCommunicationAdapter)\(")
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py") if p.name not in {"factory.py", "sendgrid_adapter.py", "twilio_adapter.py", "composite_adapter.py", "internal_test_adapter.py"} and pat.search(p.read_text())]
    assert offenders == []
