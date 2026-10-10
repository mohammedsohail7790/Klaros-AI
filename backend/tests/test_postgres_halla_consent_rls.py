"""REAL PostgreSQL: the policies that migration 0065_halla_consent_evidence creates, exercised as a NON-owner role (an owner bypasses RLS).

Skipped unless KLAROS_MIGRATED_PG_URL points at a DISPOSABLE database already migrated to head with `alembic upgrade head`
(e.g. postgresql+asyncpg://postgres@127.0.0.1:54329/klaros_mt_test). Everything runs inside one transaction that is rolled back.
"""
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ.get("KLAROS_MIGRATED_PG_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="needs KLAROS_MIGRATED_PG_URL (a disposable, migrated PostgreSQL)")]

A, B = uuid.uuid4(), uuid.uuid4()
INSERT = text(
    "INSERT INTO halla_consent_evidence (id, tenant_id, halla_event_id, event_type, halla_lead_id, granted, scopes, method, wording_version, recorded_at)"
    " VALUES (gen_random_uuid(), :t, :e, 'lead.created', 'h1', true, CAST('[\"store_personal_data\"]' AS json), 'voice_ai_verbal', 'v1', now())"
)


async def _as_tenant(conn, tenant: uuid.UUID | None):
    await conn.execute(text("SET LOCAL ROLE mt_rls_app"))
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant) if tenant else ""})


async def test_policies_isolate_tenants_forbid_delete_and_fail_closed_without_a_tenant() -> None:
    engine = create_async_engine(URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await conn.execute(text("DROP ROLE IF EXISTS mt_rls_app"))
                await conn.execute(text("CREATE ROLE mt_rls_app NOSUPERUSER NOBYPASSRLS NOLOGIN"))
                await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON halla_consent_evidence TO mt_rls_app"))

                rls = (await conn.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname='halla_consent_evidence'"))).scalar()
                pols = {r[0]: (r[1].decode() if isinstance(r[1], bytes) else r[1]) for r in (await conn.execute(text("SELECT polname, polcmd FROM pg_policy WHERE polrelid='halla_consent_evidence'::regclass")))}
                assert rls is True and pols == {"tenant_select": "r", "tenant_insert": "a", "tenant_update": "w"}  # no delete policy, by design

                await _as_tenant(conn, A)
                await conn.execute(INSERT, {"t": A, "e": "evt-a"})
                with pytest.raises(DBAPIError):  # WITH CHECK: cannot write another tenant's row
                    async with conn.begin_nested():
                        await conn.execute(INSERT, {"t": B, "e": "evt-b-forged"})
                assert (await conn.execute(text("SELECT count(*) FROM halla_consent_evidence"))).scalar() == 1

                await conn.execute(text("RESET ROLE"))
                await _as_tenant(conn, B)
                await conn.execute(INSERT, {"t": B, "e": "evt-b"})
                assert (await conn.execute(text("SELECT count(*) FROM halla_consent_evidence"))).scalar() == 1  # sees only its own
                assert (await conn.execute(text("UPDATE halla_consent_evidence SET lead_id = gen_random_uuid() WHERE halla_event_id = 'evt-a'"))).rowcount == 0

                await conn.execute(text("RESET ROLE"))
                await _as_tenant(conn, A)
                assert (await conn.execute(text("UPDATE halla_consent_evidence SET lead_id = gen_random_uuid() WHERE halla_event_id = 'evt-a'"))).rowcount == 1  # linking works
                assert (await conn.execute(text("DELETE FROM halla_consent_evidence"))).rowcount == 0  # append-only: no delete policy => nothing deleted
                with pytest.raises(DBAPIError):  # unique (tenant, event id)
                    async with conn.begin_nested():
                        await conn.execute(INSERT, {"t": A, "e": "evt-a"})

                await conn.execute(text("RESET ROLE"))
                await _as_tenant(conn, None)
                assert (await conn.execute(text("SELECT count(*) FROM halla_consent_evidence"))).scalar() == 0  # no tenant context => sees nothing
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()
