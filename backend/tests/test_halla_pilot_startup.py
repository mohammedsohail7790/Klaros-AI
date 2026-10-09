"""Pilot entrypoint (scripts/start.py), the connect tool, UNVERIFIED connections, secret rotation, and out-of-order Halla deliveries.

REAL Klaros code on the test database (SQLite here; the PostgreSQL run of this file is separate). Halla is never contacted: the `halla`
fixture's outbound transport is MOCKED. Inbound deliveries are signed in the test with the contract's algorithm and posted to the real route.
"""

import itertools
import json
import time
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio

from app.integrations.workforce import halla_webhook as hw  # noqa: E402
from app.models.crm import LeadStatus, QualificationStatus  # noqa: E402
from app.services.halla_event_processor import apply_qualification  # noqa: E402
from scripts import start  # noqa: E402
from tests.test_halla_integration import (  # noqa: E402,F401
    API_KEY, HALLA_TENANT, SECRET, _body, _connect, _connected, _deliver, _h, _lead, _lead_state, _tenant_id, halla,
)
from tests.test_business_journey_api import _register  # noqa: E402

MARK = "ZXQ-secret-marker-7741"


# ============================================================================== 3. out-of-order qualification
@pytest.mark.parametrize("status", list(LeadStatus))
@pytest.mark.parametrize("q", ["qualified", "not_qualified", "needs_human_review", "unknown", None])
@pytest.mark.parametrize("current", list(QualificationStatus))
def test_apply_qualification_records_the_state_and_never_moves_the_pipeline_backwards(status, q, current) -> None:
    lead = types.SimpleNamespace(status=status, qualification_status=current)
    before_status = lead.status
    apply_qualification(lead, q)
    if status not in (LeadStatus.NEW, LeadStatus.CONTACTED):
        assert lead.status == before_status, (status, q)  # a machine never changes a lead that has moved on (and never pulls one back)
    if q == "qualified":
        assert lead.qualification_status == QualificationStatus.QUALIFIED  # recorded for EVERY lead, booked / converted / lost included
    elif q == "needs_human_review":
        assert lead.qualification_status == QualificationStatus.REQUIRES_HUMAN
    elif q in (None, "unknown"):
        assert lead.qualification_status == current
    elif q == "not_qualified":
        already = status == LeadStatus.QUALIFIED or (status in (LeadStatus.BOOKED, LeadStatus.CONVERTED, LeadStatus.LOST) and current == QualificationStatus.QUALIFIED)
        assert lead.qualification_status == (current if already else QualificationStatus.UNQUALIFIED)


def _appointment(lead_id: str, days: int = 5):
    start_at = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()
    return {"klarosLeadId": lead_id, "appointment": {"id": "ap-order-1", "scheduled_time": start_at, "service": "Consultation"}}


EVENTS = ("call", "qualified", "appointment")


@pytest.mark.parametrize("order", list(itertools.permutations(EVENTS)))
async def test_every_arrival_order_of_call_qualified_and_appointment_ends_booked_and_qualified(client, halla, order) -> None:
    token, tid = await _connected(client, f"Order {'-'.join(order)}", f"order{'-'.join(order)}@example.com")
    lead = await _lead(client, token)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    stamp = {"call": t0, "qualified": t0 + timedelta(minutes=1), "appointment": t0 + timedelta(minutes=2)}  # the order Halla emitted them
    build = {
        "call": lambda: _body("call.completed", eid="evt-call", at=stamp["call"], data={"klarosLeadId": lead, "callId": "C1", "qualificationStatus": "qualified"}),
        "qualified": lambda: _body("lead.qualified", eid="evt-qual", at=stamp["qualified"], data={"klarosLeadId": lead, "callId": "C1", "qualificationStatus": "qualified"}),
        "appointment": lambda: _body("appointment.confirmed", eid="evt-appt", at=stamp["appointment"], data=_appointment(lead)),
    }
    for name in order:
        r = await _deliver(client, tid, build[name]())
        assert r.status_code == 200, (order, name, r.text)
    assert await _lead_state(client, token, lead) == ("BOOKED", "QUALIFIED"), order  # booked is never downgraded; qualification is never lost
    for name in order:  # and a redelivery of any of them changes nothing
        assert (await _deliver(client, tid, build[name]())).json() == {"status": "duplicate_ignored"}
    assert await _lead_state(client, token, lead) == ("BOOKED", "QUALIFIED")


async def test_appointment_first_then_qualification_is_no_longer_stuck_pending(client, halla) -> None:
    """The reported defect, exactly: appointment.confirmed arrives first, qualification second."""
    token, tid = await _connected(client, "Stuck Co", "stuckco@example.com")
    lead = await _lead(client, token)
    await _deliver(client, tid, _body("appointment.confirmed", data=_appointment(lead)))
    assert await _lead_state(client, token, lead) == ("BOOKED", "PENDING")
    await _deliver(client, tid, _body("lead.qualified", data={"klarosLeadId": lead, "qualificationStatus": "qualified"}))
    assert await _lead_state(client, token, lead) == ("BOOKED", "QUALIFIED")


async def test_a_late_not_qualified_cannot_undo_a_qualification_or_a_booking(client, halla) -> None:
    token, tid = await _connected(client, "Late NQ", "latenq@example.com")
    lead = await _lead(client, token)
    await _deliver(client, tid, _body("lead.qualified", data={"klarosLeadId": lead, "qualificationStatus": "qualified"}))
    await _deliver(client, tid, _body("appointment.confirmed", data=_appointment(lead)))
    await _deliver(client, tid, _body("call.completed", data={"klarosLeadId": lead, "callId": "C9", "qualificationStatus": "not_qualified"}))
    assert await _lead_state(client, token, lead) == ("BOOKED", "QUALIFIED")


async def test_an_escalation_after_booking_is_recorded_without_changing_the_booking(client, halla) -> None:
    token, tid = await _connected(client, "Esc After", "escafter@example.com")
    lead = await _lead(client, token)
    await _deliver(client, tid, _body("appointment.confirmed", data=_appointment(lead)))
    await _deliver(client, tid, _body("lead.escalated", data={"klarosLeadId": lead, "callId": "C2"}))
    assert await _lead_state(client, token, lead) == ("BOOKED", "REQUIRES_HUMAN")


async def test_a_lost_lead_keeps_its_status_but_records_what_halla_said(client, halla) -> None:
    token, tid = await _connected(client, "Lost Co", "lostco@example.com")
    lead = await _lead(client, token)
    assert (await client.patch(f"/api/v1/leads/{lead}", json={"status": "LOST"}, headers=_h(token))).status_code == 200
    await _deliver(client, tid, _body("lead.qualified", data={"klarosLeadId": lead, "qualificationStatus": "qualified"}))
    assert await _lead_state(client, token, lead) == ("LOST", "QUALIFIED")


# ============================================================================== 2. connect: UNVERIFIED and degraded-but-active
async def _status_of(tid):
    from app.api.tool_deps_business_builder import get_halla_integration_service

    conn = await get_halla_integration_service()._connections.get_connection(tid, "halla")
    return conn.status, conn.last_error


async def test_a_failed_health_check_leaves_error_but_the_receiver_stays_active(client, halla) -> None:
    import httpx

    token = await _register(client, "Err Conn", "errconn@example.com")
    tid = await _tenant_id(client, token)
    halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.Response(503, json={"error": "down"})
    from scripts.connect_halla_tenant import connect

    r = await connect(tid, HALLA_TENANT, {"HALLA_API_KEY": API_KEY, "HALLA_WEBHOOK_SECRET": SECRET})
    assert r["connection_status"] == "ERROR"
    lead = await _lead(client, token)
    ok = await _deliver(client, tid, _body("call.completed", data={"klarosLeadId": lead, "callId": "C", "qualificationStatus": "qualified"}))
    assert ok.status_code == 200 and await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED")  # signed events are still accepted


async def test_skip_verify_saves_the_credential_makes_no_halla_call_and_is_never_reported_connected(client, halla) -> None:
    token = await _register(client, "Skip Co", "skipco@example.com")
    tid = await _tenant_id(client, token)
    from scripts.connect_halla_tenant import connect

    r = await connect(tid, HALLA_TENANT, {"HALLA_API_KEY": API_KEY, "HALLA_WEBHOOK_SECRET": SECRET}, verify=False)
    assert r["connection_status"] == "UNVERIFIED" and r["status"] == "NEEDS_ATTENTION"
    assert halla.requests == []  # no request of any kind went to Halla
    status, err = await _status_of(tid)
    assert status == "UNVERIFIED" and "not verified" in err.lower() or "without verification" in err
    lead = await _lead(client, token)
    assert (await _deliver(client, tid, _body("call.started", data={"klarosLeadId": lead}))).status_code == 200  # receiver active
    ws = (await client.get("/api/v1/business-builder/workforce", headers=_h(token))).json()
    assert ws["status"] == "NEEDS_ATTENTION" and ws["status"] != "CONNECTED"
    health = await client.post("/api/v1/business-builder/workforce/halla/health", headers=_h(token))  # a later real check promotes it
    assert health.json()["status"] == "CONNECTED" and (await _status_of(tid))[0] == "CONNECTED"


def test_the_connect_tool_cli_prints_no_secret_and_skip_verify_exits_zero(capsys, monkeypatch) -> None:
    """The CLI's own behaviour (its flags, exit code, output). The connect() it calls is covered against the real database above; here it is
    stubbed so the CLI can run in its own event loop, exactly as it does as a process."""
    from scripts import connect_halla_tenant as cli

    seen = {}

    async def fake_connect(tenant, halla_tenant, environ, *, verify=True, **kw):
        seen.update(verify=verify, key=environ["HALLA_API_KEY"])
        return {"connection_status": "UNVERIFIED" if not verify else "ERROR", "status": "NEEDS_ATTENTION", "message": "stub", "halla_tenant_id": halla_tenant, "webhook_url": "https://klaros.test/x"}

    monkeypatch.setattr(cli, "connect", fake_connect)
    env = {"HALLA_API_KEY": API_KEY + MARK, "HALLA_WEBHOOK_SECRET": SECRET + MARK}
    tid = str(uuid.uuid4())
    assert cli.main(["--klaros-tenant-id", tid, "--halla-tenant-id", HALLA_TENANT, "--skip-verify"], env) == 0
    out = capsys.readouterr().out
    assert seen["verify"] is False and "UNVERIFIED" in out and MARK not in out and API_KEY not in out and SECRET not in out
    assert cli.main(["--klaros-tenant-id", tid, "--halla-tenant-id", HALLA_TENANT], env) == 1  # a failed health check still exits non-zero...
    assert "signed webhooks are accepted" in capsys.readouterr().out  # ...and says the credential is saved


# ============================================================================== secret rotation
async def test_resaving_a_connection_replaces_the_secret_old_signatures_stop_working(client, halla) -> None:
    token = await _register(client, "Rotate Co", "rotateco@example.com")
    tid = await _tenant_id(client, token)
    from scripts.connect_halla_tenant import connect

    await connect(tid, HALLA_TENANT, {"HALLA_API_KEY": API_KEY, "HALLA_WEBHOOK_SECRET": SECRET})
    assert (await _deliver(client, tid, _body("call.started"))).status_code == 200
    new_secret = "whsec-rotated-0123456789"
    await connect(tid, HALLA_TENANT, {"HALLA_API_KEY": API_KEY, "HALLA_WEBHOOK_SECRET": new_secret})
    assert (await _deliver(client, tid, _body("call.started"), secret=SECRET)).status_code == 401  # the old secret is dead
    assert (await _deliver(client, tid, _body("call.started"), secret=new_secret)).status_code == 200


# ============================================================================== 1. scripts/start.py
GOOD_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def _tenants_env(**over) -> dict:
    items = [
        {"slug": "pilot-a", "name": "Pilot A", "klaros_tenant_id": GOOD_ID, "halla_tenant_id": "halla-a-0001", "api_key_env": "KEY_A", "secret_env": "SEC_A", "safety_profile": "medical_tourism",
         "owner_email": "Owner@Example.com", "owner_password_env": "PW_A"},
        {"slug": "pilot-b", "name": "Pilot B", "klaros_tenant_id": OTHER_ID, "halla_tenant_id": "halla-b-0002", "api_key_env": "KEY_B", "secret_env": "SEC_B", "safety_profile": "dropshipping"},
    ]
    env = {"ENV": "production", "DATABASE_URL": "postgresql+asyncpg://u@h/db", "JWT_SECRET": "x" * 40, "INTEGRATION_CREDENTIAL_ENCRYPTION_KEY": "k" * 20,
           "WORKFORCE_ADAPTER": "halla", "HALLA_API_BASE_URL": "https://halla.test", "HALLA_API_KEY_HEADER": "X-Test-Key", "KLAROS_PUBLIC_API_URL": "https://klaros.test",
           "KLAROS_PILOT_DB_NAME": "db", "HALLA_PILOT_TENANTS": json.dumps(items), "KEY_A": API_KEY + "A", "SEC_A": SECRET + "A", "PW_A": "owner-pass-" + MARK, "KEY_B": API_KEY + "B", "SEC_B": SECRET + "B"}
    env.update(over)
    return env


def test_parse_tenants_accepts_the_two_pilot_tenants_and_normalises() -> None:
    ts = start.parse_tenants(_tenants_env())
    assert [t.slug for t in ts] == ["pilot-a", "pilot-b"] and ts[0].owner_email == "owner@example.com" and ts[1].owner_email is None and ts[0].trial_days == 14
    assert start.parse_tenants({}) == [] and start.parse_tenants({"HALLA_PILOT_TENANTS": "  "}) == []


@pytest.mark.parametrize(
    "mutate",
    [lambda i: i[0].update(api_key="sk_live_x"), lambda i: i[0].update(signing_secret="x"), lambda i: i[0].update(password="p"), lambda i: i[0].pop("halla_tenant_id"),
     lambda i: i[0].update(klaros_tenant_id="not-a-uuid"), lambda i: i[0].update(slug="Bad Slug!"), lambda i: i[1].update(slug="pilot-a"), lambda i: i[1].update(klaros_tenant_id=GOOD_ID),
     lambda i: i[0].pop("owner_password_env"), lambda i: i[0].pop("safety_profile"), lambda i: i[0].update(safety_profile="made_up"), lambda i: i[0].update(trial_days=0), lambda i: i[0].update(trial_days=True), lambda i: i.__setitem__(0, "nope")],
)
def test_parse_tenants_refuses_secrets_in_json_and_malformed_entries(mutate) -> None:
    items = json.loads(_tenants_env()["HALLA_PILOT_TENANTS"])
    mutate(items)
    with pytest.raises(start.ConfigError) as e:
        start.parse_tenants({"HALLA_PILOT_TENANTS": json.dumps(items)})
    assert "sk_live_x" not in str(e.value)


def test_parse_tenants_refuses_non_json_and_empty_lists() -> None:
    for bad in ("{", "[]", '{"a":1}'):
        with pytest.raises(start.ConfigError):
            start.parse_tenants({"HALLA_PILOT_TENANTS": bad})


def test_preflight_passes_a_complete_production_configuration() -> None:
    env = _tenants_env()
    assert start.preflight(env, start.parse_tenants(env)) == []


@pytest.mark.parametrize(
    "over,fragment",
    [({"JWT_SECRET": "change-me-in-production"}, "JWT_SECRET"), ({"JWT_SECRET": ""}, "JWT_SECRET"), ({"INTEGRATION_CREDENTIAL_ENCRYPTION_KEY": ""}, "INTEGRATION_CREDENTIAL_ENCRYPTION_KEY"),
     ({"DATABASE_URL": ""}, "DATABASE_URL is not set"), ({"DATABASE_URL": "postgresql://u@h/db"}, "postgresql+asyncpg"), ({"WORKFORCE_ADAPTER": "dev"}, "WORKFORCE_ADAPTER"),
     ({"HALLA_API_BASE_URL": ""}, "HALLA_API_BASE_URL"), ({"SEC_B": ""}, "SEC_B"), ({"KEY_A": ""}, "KEY_A"), ({"PW_A": ""}, "PW_A"), ({"KLAROS_PUBLIC_API_URL": ""}, "KLAROS_PUBLIC_API_URL")],
)
def test_preflight_names_exactly_what_is_wrong_and_never_prints_a_value(over, fragment) -> None:
    env = _tenants_env(**over)
    problems = start.preflight(env, start.parse_tenants(env))
    assert any(fragment in p for p in problems), problems
    blob = " ".join(problems)
    for v in ("x" * 40, "k" * 20, MARK, SECRET):
        assert v not in blob


def test_preflight_only_requires_the_real_secrets_where_the_environment_is_reachable() -> None:
    env = _tenants_env(ENV="development", JWT_SECRET="", INTEGRATION_CREDENTIAL_ENCRYPTION_KEY="")
    assert start.preflight(env, start.parse_tenants(env)) == []


def test_mask_never_reveals_more_than_a_prefix() -> None:
    assert start.mask("abcdefghij") == "abcd****" and start.mask("abc") == "****" and start.mask(None) == "****"
    assert start.mask(GOOD_ID, 8) == "11111111****"


async def test_a_prisma_database_and_an_unmanaged_database_are_refused_before_anything_runs(tmp_path) -> None:
    import sqlite3

    prisma = tmp_path / "prisma.db"
    c = sqlite3.connect(prisma)
    c.execute("CREATE TABLE _prisma_migrations (id TEXT)")
    c.execute("CREATE TABLE users (id TEXT)")
    c.commit()
    c.close()
    foreign = tmp_path / "foreign.db"
    c = sqlite3.connect(foreign)
    c.execute("CREATE TABLE orders (id TEXT)")
    c.commit()
    c.close()
    klaros = tmp_path / "klaros.db"
    c = sqlite3.connect(klaros)
    c.execute("CREATE TABLE alembic_version (version_num TEXT)")
    c.execute("CREATE TABLE users (id TEXT)")
    c.commit()
    c.close()
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(start.ConfigError, match="Prisma"):
        await start.refuse_foreign_database(f"sqlite+aiosqlite:///{prisma}")
    with pytest.raises(start.ConfigError, match="no Alembic history"):
        await start.refuse_foreign_database(f"sqlite+aiosqlite:///{foreign}")
    await start.refuse_foreign_database(f"sqlite+aiosqlite:///{klaros}")  # a Klaros database: allowed
    await start.refuse_foreign_database(f"sqlite+aiosqlite:///{empty}")  # a brand-new database: allowed


def test_setup_refuses_a_foreign_database_before_migrating_anything(tmp_path, monkeypatch, capsys) -> None:
    import sqlite3

    db = tmp_path / "p.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE _prisma_migrations (id TEXT)")
    c.commit()
    c.close()
    ran = []
    monkeypatch.setattr(start, "run_migrations", lambda env: ran.append(1))
    env = _tenants_env(ENV="development", KLAROS_PILOT_DB_NAME="")
    env["DATABASE_URL"] = f"postgresql+asyncpg://u@h/db"  # preflight needs the postgres scheme...
    env["DATABASE_MIGRATION_URL"] = f"sqlite+aiosqlite:///{db}"  # ...but the check runs against the URL the migration would use
    monkeypatch.setattr(start, "preflight", lambda e, t: [])  # (a sqlite file stands in for the foreign database here; the preflight rules have their own tests)
    code = start.main(["--no-serve"], env)
    assert code == 2 and ran == []
    out = capsys.readouterr().out
    assert "Prisma" in out and MARK not in out and SECRET not in out


async def test_ensure_tenant_is_idempotent_and_never_overwrites(client, halla) -> None:
    from sqlalchemy import func, select

    from app.db.session import async_session_maker
    from app.models.organization import Organization
    from app.models.user import User

    t = start.parse_tenants(_tenants_env())[0]
    env = _tenants_env()
    first = await start.ensure_tenant(t, env)
    assert first == {"slug": "pilot-a", "organization": "created", "owner": "created"}
    async with async_session_maker() as s:
        org = await s.get(Organization, t.klaros_tenant_id)
        assert org.name == "Pilot A" and org.slug == "pilot-a" and org.trial_ends_at is not None
        from app.db.session import set_tenant_context

        await set_tenant_context(s, t.klaros_tenant_id)
        user = (await s.execute(select(User).where(User.tenant_id == t.klaros_tenant_id))).scalar_one()
        digest = user.hashed_password
        assert user.email == "owner@example.com" and user.role == "OWNER" and digest != env["PW_A"]
    env["PW_A"] = "a-different-password-now"  # a second deploy with a changed password must NOT reset the owner
    second = await start.ensure_tenant(t, env)
    assert second == {"slug": "pilot-a", "organization": "exists", "owner": "exists (password unchanged)"}
    async with async_session_maker() as s:
        await set_tenant_context(s, t.klaros_tenant_id)
        assert (await s.execute(select(func.count()).select_from(User).where(User.tenant_id == t.klaros_tenant_id))).scalar_one() == 1
        assert (await s.execute(select(User.hashed_password).where(User.tenant_id == t.klaros_tenant_id))).scalar_one() == digest


async def test_ensure_tenant_refuses_a_slug_owned_by_another_organization(client, halla) -> None:
    await _register(client, "Pilot A", "slugowner@example.com")  # takes the slug "pilot-a"
    t = start.parse_tenants(_tenants_env())[0]
    with pytest.raises(start.ConfigError, match="different organization"):
        await start.ensure_tenant(t, _tenants_env())


async def test_setup_creates_two_tenants_connects_each_with_its_own_secret_and_is_repeatable(client, halla, capsys, monkeypatch) -> None:
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ENV", "development")
    halla.tenant_in_health = None  # the mocked health answer names no tenant (the tenant-mismatch case has its own tests)
    env = _tenants_env(ENV="development")
    monkeypatch.setattr(start, "refuse_foreign_database", _fake_check)
    monkeypatch.setattr(start, "run_migrations", lambda e: None)

    async def run():
        return await start.setup(env, skip_migrate=True, verify=True)

    assert await run() == 0
    assert await run() == 0  # second deploy: same result, nothing duplicated
    out = capsys.readouterr().out
    for secret in (SECRET + "A", SECRET + "B", API_KEY + "A", API_KEY + "B", "owner-pass-" + MARK, MARK):
        assert secret not in out
    assert "pilot-a" in out and "pilot-b" in out and "CONNECTED" in out
    a, b = uuid.UUID(GOOD_ID), uuid.UUID(OTHER_ID)
    # each tenant verifies ONLY its own secret: A's secret on B's URL and vice versa are refused
    assert (await _deliver(client, a, _body("call.started", tenant="halla-a-0001"), secret=SECRET + "A")).status_code == 200
    assert (await _deliver(client, b, _body("call.started", tenant="halla-b-0002"), secret=SECRET + "B")).status_code == 200
    assert (await _deliver(client, a, _body("call.started", tenant="halla-a-0001"), secret=SECRET + "B")).status_code == 401
    assert (await _deliver(client, b, _body("call.started", tenant="halla-b-0002"), secret=SECRET + "A")).status_code == 401
    assert (await _deliver(client, a, _body("call.started", tenant="halla-b-0002"), secret=SECRET + "A")).status_code == 403  # authentic, but the wrong Halla tenant
    # rotation: change the env var, redeploy
    env["SEC_A"] = "whsec-rotated-A-0123456789"
    assert await run() == 0
    assert (await _deliver(client, a, _body("call.started", tenant="halla-a-0001"), secret=SECRET + "A")).status_code == 401
    assert (await _deliver(client, a, _body("call.started", tenant="halla-a-0001"), secret="whsec-rotated-A-0123456789")).status_code == 200
    assert (await _deliver(client, b, _body("call.started", tenant="halla-b-0002"), secret=SECRET + "B")).status_code == 200  # B untouched


async def _noop():
    return None


async def _fake_check(url, expected_name=""):
    return {"database": expected_name or "test", "tables": 0, "vector_available": True, "vector_installed": False, "role_can_create_vector": True, "role_is_superuser": False, "alembic_managed": False}


async def test_a_halla_outage_during_setup_does_not_stop_the_boot_and_the_credential_is_still_saved(client, halla, capsys, monkeypatch) -> None:
    import httpx

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ENV", "development")
    halla.overrides[("GET", "/api/v1/integrations/klaros/health")] = httpx.Response(503, json={})
    env = _tenants_env(ENV="development")
    monkeypatch.setattr(start, "refuse_foreign_database", _fake_check)
    assert await start.setup(env, skip_migrate=True, verify=True) == 0
    out = capsys.readouterr().out
    assert "halla connection ERROR" in out
    assert (await _deliver(client, uuid.UUID(GOOD_ID), _body("call.started", tenant="halla-a-0001"), secret=SECRET + "A")).status_code == 200


async def test_skip_verify_setup_makes_no_halla_call(client, halla, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ENV", "development")
    env = _tenants_env(ENV="development")
    monkeypatch.setattr(start, "refuse_foreign_database", _fake_check)
    assert await start.setup(env, skip_migrate=True, verify=False) == 0
    assert halla.requests == []
    assert (await _status_of(uuid.UUID(GOOD_ID)))[0] == "UNVERIFIED"


def test_main_stops_with_exit_code_2_on_a_bad_configuration_and_serves_nothing(capsys, monkeypatch) -> None:
    monkeypatch.setattr(start.os, "execvp", lambda *a, **k: pytest.fail("must not start the server"))
    env = _tenants_env(JWT_SECRET="change-me-in-production", ENV="production")
    assert start.main(["--no-serve"], env) == 2
    assert start.main([], env) == 2
    out = capsys.readouterr().out
    assert "JWT_SECRET" in out and "x" * 40 not in out


def test_main_serves_with_uvicorn_on_the_platform_port_when_everything_passes(monkeypatch) -> None:
    calls = []

    async def fake_setup(env, *, skip_migrate, verify):
        return 0

    monkeypatch.setattr(start, "setup", fake_setup)
    monkeypatch.setattr(start.os, "execvp", lambda exe, argv: calls.append(argv))
    assert start.main([], {"PORT": "10000"}) == 0
    assert calls and calls[0][-4:] == ["--host", "0.0.0.0", "--port", "10000"] and "uvicorn" in calls[0] and "app.main:app" in calls[0]


# ============================================================================== Medical Tourism / Dropshipping safety on the Halla event path
async def _profiled(client, name, email, profile):
    """A connected tenant whose Halla connection carries the given safety profile (exactly what scripts/start.py records)."""
    token, tid = await _connected(client, name, email)
    from app.api.tool_deps_business_builder import get_halla_integration_service
    from app.db.session import async_session_maker, set_tenant_context

    conn = await get_halla_integration_service()._connections.get_connection(tid, "halla")
    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        row = await s.get(type(conn), conn.id)
        row.connection_metadata = {"safety_profile": profile}
        await s.commit()
    return token, tid


async def _events_of(tid, etype):
    from sqlalchemy import select

    from app.db.session import async_session_maker, set_tenant_context
    from app.models.event import Event

    async with async_session_maker() as s:
        await set_tenant_context(s, tid)
        return list((await s.execute(select(Event).where(Event.tenant_id == tid, Event.event_type == etype))).scalars())


@pytest.mark.parametrize(
    "profile,summary,category",
    [("medical_tourism", "The caller has chest pain and cannot breathe.", "EMERGENCY"), ("medical_tourism", "They asked me to diagnose their knee. Do I have arthritis?", "DIAGNOSIS_REQUEST"),
     ("medical_tourism", "Caller asked which medication to take and the dose.", "PRESCRIPTION_REQUEST"), ("medical_tourism", "Caller wants a guarantee the surgery works.", "OUTCOME_GUARANTEE_REQUEST"),
     ("dropshipping", "Caller threatened a chargeback with their bank.", "CHARGEBACK"), ("dropshipping", "Caller says they never got their refund and wants their money back.", "REFUND_DISPUTE"),
     ("dropshipping", "Caller asked us to promise delivery by Friday.", "DELIVERY_GUARANTEE_REQUEST"), ("dropshipping", "Caller says someone used their card without permission, a fraudulent order.", "FRAUD_INDICATOR"),
     ("dropshipping", "Caller was charged twice for the order.", "PAYMENT_DISPUTE"), ("dropshipping", "Caller asked where is my order and when it ships.", "ORDER_STATUS_REQUEST")],
)
async def test_a_conversation_that_needs_a_person_overrides_halla_qualified_and_triggers_no_qualified_workflow(client, halla, profile, summary, category) -> None:
    token, tid = await _profiled(client, f"Safe {category}", f"safe{category.lower()}@example.com", profile)
    lead = await _lead(client, token)
    raw = _body("call.completed", data={"klarosLeadId": lead, "callId": "C-S", "qualificationStatus": "qualified", "summary": summary})
    assert (await _deliver(client, tid, raw)).json() == {"status": "ok"}
    status, qual = await _lead_state(client, token, lead)
    assert qual == "REQUIRES_HUMAN" and status != "QUALIFIED"  # Halla said qualified; the safety profile wins
    esc = await _events_of(tid, "halla.lead.escalated")
    assert len(esc) == 1 and esc[0].payload["category"] == category and esc[0].payload["safety"] is True
    assert summary not in str(esc[0].payload) and "summary" not in esc[0].payload  # the category only, never the words
    assert await _events_of(tid, "halla.lead.qualified") == []
    assert (await _deliver(client, tid, raw)).json() == {"status": "duplicate_ignored"}  # replay: no second escalation
    assert len(await _events_of(tid, "halla.lead.escalated")) == 1


async def test_a_lead_qualified_event_with_unsafe_text_goes_to_a_person_not_to_qualified(client, halla) -> None:
    token, tid = await _profiled(client, "Safe LQ", "safelq@example.com", "dropshipping")
    lead = await _lead(client, token)
    await _deliver(client, tid, _body("lead.qualified", data={"klarosLeadId": lead, "qualificationStatus": "qualified", "summary": "I will do a chargeback"}))
    assert await _lead_state(client, token, lead) == ("NEW", "REQUIRES_HUMAN")
    assert await _events_of(tid, "halla.lead.qualified") == []


async def test_ordinary_business_conversations_still_qualify_under_a_profile(client, halla) -> None:
    for profile, summary in (("medical_tourism", "Interested in a knee consultation in Dubai in spring; prefers email."), ("dropshipping", "Asked how long delivery to Canada takes and the price of the blue backpack.")):
        token, tid = await _profiled(client, f"Norm {profile}", f"norm{profile}@example.com", profile)
        lead = await _lead(client, token)
        await _deliver(client, tid, _body("call.completed", data={"klarosLeadId": lead, "callId": "C", "qualificationStatus": "qualified", "summary": summary}))
        assert await _lead_state(client, token, lead) == ("QUALIFIED", "QUALIFIED"), profile
        assert await _events_of(tid, "halla.lead.escalated") == []


async def test_a_tenant_without_a_profile_and_other_tenants_are_unaffected(client, halla) -> None:
    plain_token, plain_tid = await _connected(client, "No Profile", "noprofile@example.com")
    prof_token, prof_tid = await _profiled(client, "With Profile", "withprofile@example.com", "medical_tourism")
    unsafe = "The caller has chest pain and cannot breathe."
    lead_plain = await _lead(client, plain_token)
    await _deliver(client, plain_tid, _body("call.completed", data={"klarosLeadId": lead_plain, "callId": "C", "qualificationStatus": "qualified", "summary": unsafe}))
    assert await _lead_state(client, plain_token, lead_plain) == ("QUALIFIED", "QUALIFIED")  # generic behaviour, unchanged
    lead_prof = await _lead(client, prof_token)
    await _deliver(client, prof_tid, _body("call.completed", data={"klarosLeadId": lead_prof, "callId": "C", "qualificationStatus": "qualified", "summary": unsafe}))
    assert (await _lead_state(client, prof_token, lead_prof))[1] == "REQUIRES_HUMAN"
    assert await _events_of(plain_tid, "halla.lead.escalated") == []  # the other tenant's profile never leaks across


async def test_safety_text_never_reaches_logs(client, halla, capsys, caplog) -> None:
    import logging

    token, tid = await _profiled(client, "Safe Logs", "safelogs@example.com", "medical_tourism")
    lead = await _lead(client, token)
    capsys.readouterr()
    with caplog.at_level(logging.INFO):
        await _deliver(client, tid, _body("call.completed", data={"klarosLeadId": lead, "callId": "C", "summary": f"chest pain {MARK}"}))
    assert MARK not in capsys.readouterr().out + caplog.text
    stored = ""
    for t in ("halla.interaction.completed", "halla.lead.escalated"):
        stored += " ".join(str(e.payload) for e in await _events_of(tid, t))
    assert MARK not in stored and "chest pain" not in stored  # an unsafe conversation's words are not stored in any event


async def test_the_workforce_configuration_sent_to_halla_carries_the_profiles_triggers_and_nothing_private(client, halla) -> None:
    for profile, must in (("medical_tourism", ("emergency", "diagnosis", "guaranteed outcome", "treatment")), ("dropshipping", ("chargeback", "refund", "fraud", "guarantee", "status of an order"))):
        token, tid = await _profiled(client, f"Cfg {profile}", f"cfg{profile}@example.com", profile)
        halla.requests.clear()
        r = await client.post("/api/v1/business-builder/workforce/halla/configure", headers=_h(token))
        assert r.status_code == 200, r.text
        sent = [q for q in halla.requests if q.method == "PUT" and q.url.path.endswith("/integrations/klaros/workforce")]
        assert len(sent) == 1
        body = json.loads(sent[0].content)
        triggers = " | ".join(body["escalationTriggers"]).lower()
        for word in must:
            assert word in triggers, (profile, word)
        assert body["autoTransferEnabled"] is True and body["servicesOffered"] == []  # no catalogue or service is invented
        blob = json.dumps(body).lower()
        for forbidden in ("api_key", "secret", "@example.com", "+1555", "sk_", "bearer "):
            assert forbidden not in blob, forbidden
        assert any("never ask for card numbers" in b.lower() or "never ask for diagnoses" in b.lower() or "do not" in b.lower() or "never" in b.lower() for b in body["bookingRules"])


async def test_start_records_each_tenants_profile_on_its_connection(client, halla, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.api.tool_deps_business_builder import get_halla_integration_service

    monkeypatch.setattr(get_settings(), "ENV", "development")
    halla.tenant_in_health = None
    env = _tenants_env(ENV="development")
    monkeypatch.setattr(start, "refuse_foreign_database", _fake_check)
    assert await start.setup(env, skip_migrate=True, verify=True) == 0
    conns = get_halla_integration_service()._connections
    assert (await conns.get_connection(uuid.UUID(GOOD_ID), "halla")).connection_metadata == {"safety_profile": "medical_tourism"}
    assert (await conns.get_connection(uuid.UUID(OTHER_ID), "halla")).connection_metadata == {"safety_profile": "dropshipping"}
    assert await start.setup(env, skip_migrate=True, verify=True) == 0  # a redeploy keeps it
    assert (await conns.get_connection(uuid.UUID(OTHER_ID), "halla")).connection_metadata == {"safety_profile": "dropshipping"}


# ============================================================================== which database can be selected
@pytest.mark.parametrize(
    "over,fragment",
    [({"KLAROS_PILOT_DB_NAME": ""}, "KLAROS_PILOT_DB_NAME is not set"), ({"KLAROS_PILOT_DB_NAME": "other"}, "not KLAROS_PILOT_DB_NAME"),
     ({"DATABASE_MIGRATION_URL": "postgresql+asyncpg://u@h/prisma_prod"}, "DATABASE_MIGRATION_URL points at a database that is not"),
     ({"KLAROS_FORBIDDEN_DB_NAMES": "DB"}, "forbidden database or host"), ({"KLAROS_FORBIDDEN_DB_HOSTS": "H"}, "forbidden database or host")],
)
def test_the_existing_production_database_cannot_be_selected(over, fragment) -> None:
    env = _tenants_env(**over)
    problems = start.preflight(env, start.parse_tenants(env))
    assert any(fragment in p for p in problems), problems
    assert code_main_refuses(env)


def code_main_refuses(env) -> bool:
    return start.main(["--no-serve", "--skip-migrate"], env) == 2


def test_preflight_prints_database_identity_only_never_credentials() -> None:
    env = _tenants_env(DATABASE_URL="postgresql+asyncpg://someuser:" + MARK + "@dbhost.example/db")
    assert start.url_identity(env["DATABASE_URL"]) == ("db", "dbhost.example")
    assert start.preflight(env, start.parse_tenants(env)) == []
    problems = start.preflight({**env, "KLAROS_PILOT_DB_NAME": "x"}, start.parse_tenants(env))
    assert MARK not in " ".join(problems) and "someuser" not in " ".join(problems)


async def test_the_live_database_name_must_match_and_a_missing_pgvector_is_refused(tmp_path, monkeypatch) -> None:
    import sqlite3

    db = tmp_path / "e.db"
    sqlite3.connect(db).close()
    info = await start.refuse_foreign_database(f"sqlite+aiosqlite:///{db}", "ignored-on-sqlite")
    assert info["alembic_managed"] is False and info["tables"] == 0


@pytest.mark.parametrize(
    "over,fragment",
    [({"DATABASE_MIGRATION_URL": "postgresql://u@h/db"}, "DATABASE_MIGRATION_URL must be a postgresql+asyncpg"), ({"DATABASE_MIGRATION_URL": "postgresql+asyncpg://u@other-host/db"}, "same database name and host"),
     ({"DATABASE_MIGRATION_URL": "postgresql+asyncpg://u@h/other"}, "same database name and host")],
)
def test_the_migration_url_must_use_asyncpg_and_name_the_same_database_and_host(over, fragment) -> None:
    env = _tenants_env(**over)
    assert any(fragment in p for p in start.preflight(env, start.parse_tenants(env)))
    assert start.preflight(_tenants_env(DATABASE_MIGRATION_URL="postgresql+asyncpg://owner:pw@h/db"), start.parse_tenants(_tenants_env())) == []


def test_check_mode_is_read_only_reports_identity_and_prints_no_secret(capsys, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(start, "run_migrations", lambda e: calls.append("migrate"))
    monkeypatch.setattr(start, "refuse_foreign_database", _fake_check)
    monkeypatch.setattr(start.os, "execvp", lambda *a, **k: calls.append("SERVER STARTED"))  # --check must never start the API
    env = _tenants_env(DATABASE_URL="postgresql+asyncpg://someuser:" + MARK + "@dbhost.example/db")
    assert start.main(["--check"], env) == 0
    out = capsys.readouterr().out
    assert calls == [] and "CHECK PASSED" in out and "dbhost.example" in out and "'db'" in out
    for forbidden in (MARK, "someuser", SECRET, API_KEY, "owner-pass"):
        assert forbidden not in out
    assert start.main(["--check"], {**env, "KLAROS_PILOT_DB_NAME": "wrong"}) == 2
