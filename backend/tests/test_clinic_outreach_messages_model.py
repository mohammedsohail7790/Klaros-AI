"""Shadow outreach message table: defaults, one message per (clinic, kind, UTC day), and the 0083 migration."""

import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.session import async_session_maker
from app.models.clinic_outreach import (
    ClinicOutreachCampaign,
    ClinicOutreachMessage,
    OutreachMessageKind,
    OutreachMessageStatus,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DAY1 = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def _msg(tenant, outreach, when=DAY1, kind=OutreachMessageKind.INTRO) -> ClinicOutreachMessage:
    return ClinicOutreachMessage(
        tenant_id=tenant, outreach_id=outreach, kind=kind, status=OutreachMessageStatus.QUEUED_SHADOW,
        subject="Hello", body="Body", blocks_version=1, scheduled_for=when, schedule_day=when.astimezone(UTC).date(),
    )


@pytest.mark.asyncio
async def test_message_row_round_trips_with_defaults() -> None:
    tenant, outreach = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as db:
        camp = ClinicOutreachCampaign(tenant_id=tenant, campaign_id=uuid.uuid4())
        row = _msg(tenant, outreach)
        db.add_all([camp, row])
        await db.commit()
        await db.refresh(row)
        await db.refresh(camp)
        assert row.direction == "OUT"
        assert row.review_mark is None and row.reviewed_by is None and row.reviewed_at is None and row.blocked_reason is None
        assert row.schedule_day == date(2026, 10, 21)
        assert camp.engine_last_run_at is None
    assert [k.value for k in OutreachMessageKind] == ["intro", "followup_1", "followup_2"]
    assert {s.name for s in OutreachMessageStatus} == {"QUEUED_SHADOW", "BLOCKED", "QUEUED_LIVE", "SENT", "FAILED", "CANCELLED", "SHADOW_ARCHIVED"}


@pytest.mark.asyncio
async def test_duplicate_kind_for_the_same_day_is_rejected() -> None:
    tenant, outreach = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as db:
        db.add(_msg(tenant, outreach))
        await db.commit()
        db.add(_msg(tenant, outreach))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_same_kind_on_a_different_day_is_allowed() -> None:
    tenant, outreach = uuid.uuid4(), uuid.uuid4()
    async with async_session_maker() as db:
        db.add(_msg(tenant, outreach))
        await db.commit()
        db.add(_msg(tenant, outreach, when=DAY2))
        await db.commit()
        db.add(_msg(tenant, outreach, kind=OutreachMessageKind.FOLLOWUP_1))
        await db.commit()  # a different kind on the same day is fine too


def test_migration_creates_table_with_rls_policies(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    env = {**os.environ, "DATABASE_URL": url, "DATABASE_MIGRATION_URL": url}
    run = lambda *a: subprocess.run([sys.executable, "-m", "alembic", *a], cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=180)  # noqa: E731
    up = run("upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(clinic_outreach_messages)")}
        assert {"outreach_id", "direction", "kind", "status", "subject", "body", "blocks_version", "blocked_reason",
                "scheduled_for", "schedule_day", "review_mark", "review_reason", "reviewed_by", "reviewed_at", "tenant_id", "sent_at", "attempts", "failure_reason"} <= cols
        assert "engine_last_run_at" in {r[1] for r in conn.execute("PRAGMA table_info(clinic_outreach_campaigns)")}
    finally:
        conn.close()
    down = run("downgrade", "0083_dropshipping_supplier_sync")  # through 0085 and 0084
    assert down.returncode == 0, down.stdout + down.stderr
    conn = sqlite3.connect(db_path)
    try:
        assert not list(conn.execute("SELECT name FROM sqlite_master WHERE name='clinic_outreach_messages'"))
        assert "engine_last_run_at" not in {r[1] for r in conn.execute("PRAGMA table_info(clinic_outreach_campaigns)")}
    finally:
        conn.close()
    src = (BACKEND_ROOT / "alembic/versions/0084_clinic_outreach_messages.py").read_text()
    for stmt in ("ENABLE ROW LEVEL SECURITY", "tenant_select", "tenant_insert", "tenant_update", "current_tenant_id()"):
        assert stmt in src
