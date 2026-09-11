"""Regression test for a real, previously-undetected defect: migration
0009's docstring claimed it added
`morning_brief_recommendations.approval_request_id`, and the ORM model
(app/models/morning_brief.py) has declared that column ever since, but
the actual `op.add_column` call was missing from 0009's `upgrade()`
body. Every other test in this suite builds its schema via
`Base.metadata.create_all()` (see conftest.py), which reads the ORM
models directly and so never exercises the real Alembic migration
chain — this gap was invisible to 669 passing tests until the migration
chain was actually run against a real database. Fixed forward in
migration 0026 rather than editing the already-applied 0009.

This test runs the actual `alembic upgrade head` against a fresh,
disposable SQLite file (not the ORM's create_all) and inspects the
resulting schema directly, so it would have caught the original gap and
will catch any future migration whose body drifts from what its models
(or its own docstring) require.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_migration_chain_produces_columns_the_orm_models_declare(tmp_path):
    db_path = tmp_path / "migration_schema_check.db"
    env = {
        **__import__("os").environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
    }

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(morning_brief_recommendations)")}
    finally:
        conn.close()

    assert "approval_request_id" in columns, (
        "morning_brief_recommendations.approval_request_id is declared on the ORM model "
        "(app/models/morning_brief.py) but missing from the real migrated schema — "
        "this is the exact defect migration 0026 fixes; it must not regress."
    )
