"""Phase 12: real token revocation. Closes the Phase 11 audit's P2-1/P2-2
findings — /auth/refresh and /auth/logout previously didn't exist despite
the app issuing (unusable) refresh tokens every login since Phase 1, and
there was no way to revoke an already-issued, still-valid access token."""

import pytest

pytestmark = pytest.mark.asyncio


async def _register(client, email="owner@refreshco.com"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Refresh Co",
            "full_name": "Owner",
            "email": email,
            "password": "supersecret1",
        },
    )
    return resp.json()


async def test_refresh_issues_a_new_working_access_token(client, tool_registry) -> None:
    reg = await _register(client)
    refresh_token = reg["tokens"]["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    new_access = resp.json()["access_token"]

    me = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_access_token_rejected_with_wrong_type(client, tool_registry) -> None:
    reg = await _register(client, "owner2@refreshco.com")
    access_token = reg["tokens"]["access_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


async def test_logout_revokes_the_access_token_immediately(client, tool_registry) -> None:
    reg = await _register(client, "owner3@refreshco.com")
    access_token = reg["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    before = await client.get("/api/v1/users/me", headers=headers)
    assert before.status_code == 200

    logout_resp = await client.post("/api/v1/auth/logout", headers=headers)
    assert logout_resp.status_code == 204

    after = await client.get("/api/v1/users/me", headers=headers)
    assert after.status_code == 401


async def test_logout_revokes_the_refresh_token_too(client, tool_registry) -> None:
    reg = await _register(client, "owner4@refreshco.com")
    access_token = reg["tokens"]["access_token"]
    refresh_token = reg["tokens"]["refresh_token"]

    await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 401


async def test_logging_in_again_after_logout_issues_a_working_token(client, tool_registry) -> None:
    await _register(client, "owner5@refreshco.com")

    login = await client.post(
        "/api/v1/auth/login",
        json={"organization_slug": "refresh-co", "email": "owner5@refreshco.com", "password": "supersecret1"},
    )
    access_token = login.json()["access_token"]
    await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    relogin = await client.post(
        "/api/v1/auth/login",
        json={"organization_slug": "refresh-co", "email": "owner5@refreshco.com", "password": "supersecret1"},
    )
    assert relogin.status_code == 200
    new_access = relogin.json()["access_token"]

    me = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200
