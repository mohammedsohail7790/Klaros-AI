"""Which build is running.

Render exposes the deployed commit to the running service (RENDER_GIT_COMMIT,
RENDER_GIT_BRANCH). Other hosts commonly set GIT_COMMIT or SOURCE_VERSION. Read
once per call from the environment so a deploy is verifiable from outside, with no
database or secret involved. Missing values say "unknown" rather than guessing.
"""

import os
from collections.abc import Mapping

_COMMIT_VARS = ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION")
_BRANCH_VARS = ("RENDER_GIT_BRANCH", "GIT_BRANCH")


def _first(env: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


def deployed_version(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    commit = _first(source, _COMMIT_VARS)
    return {
        "commit": commit or "unknown",
        "commit_short": commit[:8] if commit else "unknown",
        "branch": _first(source, _BRANCH_VARS) or "unknown",
    }
