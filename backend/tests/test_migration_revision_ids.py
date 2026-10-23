"""Alembic keeps the current revision in a VARCHAR(32) column. SQLite does not enforce that length, real PostgreSQL does, so a longer revision id passes every local test and then fails the
migration in CI. This checks the length of every revision id up front."""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_every_revision_id_fits_the_32_character_column() -> None:
    too_long = []
    for path in sorted(VERSIONS.glob("*.py")):
        match = re.search(r'^revision\s*(?::\s*[^=]+)?=\s*["\']([^"\']+)["\']', path.read_text(encoding="utf-8"), re.M)
        assert match, f"{path.name} has no revision id"
        if len(match.group(1)) > 32:
            too_long.append((path.name, len(match.group(1))))
    assert too_long == [], too_long
