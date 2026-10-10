"""The migration graph must have exactly one head: two heads (e.g. an unreleased 0065 colliding with another branch's) break `alembic upgrade head`,
which scripts/start.py runs on every boot of the pilot service."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_exactly_one_alembic_head() -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"multiple alembic heads: {heads}"
