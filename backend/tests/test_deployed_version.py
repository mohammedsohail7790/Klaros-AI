from app.core.version import deployed_version


def test_reads_the_render_commit_and_branch() -> None:
    v = deployed_version({"RENDER_GIT_COMMIT": "a822ad2c1f0e9b7d", "RENDER_GIT_BRANCH": "feat/klaros-redesign"})
    assert v == {"commit": "a822ad2c1f0e9b7d", "commit_short": "a822ad2c", "branch": "feat/klaros-redesign"}


def test_falls_back_to_other_hosts_variables() -> None:
    assert deployed_version({"GIT_COMMIT": "abc123def456"})["commit"] == "abc123def456"
    assert deployed_version({"SOURCE_VERSION": "deadbeefcafe"})["commit_short"] == "deadbeef"


def test_prefers_render_over_the_fallbacks_and_ignores_blank_values() -> None:
    v = deployed_version({"RENDER_GIT_COMMIT": "  ", "GIT_COMMIT": "1234567890ab", "RENDER_GIT_BRANCH": ""})
    assert v["commit"] == "1234567890ab" and v["branch"] == "unknown"


def test_says_unknown_instead_of_guessing() -> None:
    assert deployed_version({}) == {"commit": "unknown", "commit_short": "unknown", "branch": "unknown"}
