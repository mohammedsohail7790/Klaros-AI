"""The pilot-console switch: tells the UI whether this organisation is a consent-gated Medical Tourism pilot. Two booleans about the caller's own tenant, no personal data."""
from __future__ import annotations

from tests.test_consent_gate_intake import _gated, _plain
from tests.test_halla_integration import _h, halla  # noqa: F401


async def test_mode_is_true_for_a_consent_gated_tenant_and_false_for_a_plain_one(client, halla) -> None:  # noqa: F811
    gated_token, _ = await _gated(client, "Mode Gated", "mode.gated@example.com")
    plain_token, _ = await _plain(client, "Mode Plain", "mode.plain@example.com")

    gated = await client.get("/api/v1/medical-tourism/mode", headers=_h(gated_token))
    assert gated.status_code == 200
    assert gated.json() == {"pilot_console": True, "consent_gated": True}

    plain = await client.get("/api/v1/medical-tourism/mode", headers=_h(plain_token))
    assert plain.status_code == 200
    assert plain.json() == {"pilot_console": False, "consent_gated": False}


async def test_mode_needs_a_signed_in_user_and_returns_nothing_personal(client, halla) -> None:  # noqa: F811
    anonymous = await client.get("/api/v1/medical-tourism/mode")
    assert anonymous.status_code in (401, 403)

    token, _ = await _gated(client, "Mode Keys", "mode.keys@example.com")
    body = (await client.get("/api/v1/medical-tourism/mode", headers=_h(token))).json()
    assert set(body) == {"pilot_console", "consent_gated"}
