"""Team management — the previously-missing "invite a second person"
flow. register_organization has always created exactly one OWNER with no
path to a second (confirmed: users.py only had GET /users/me before this).
Covers the full real flow over HTTP: invite -> preview -> accept -> login
as the new member, plus the safety invariants (can't invite a duplicate,
can't strip the last owner, a revoked link stops working)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, org_name: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org_name, "full_name": "Owner Test", "email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_owner_can_invite_and_invitee_can_accept_and_login(client: AsyncClient) -> None:
    owner = await _register(client, "Invite Test Co", "owner@invitetest.com")
    owner_token = owner["tokens"]["access_token"]

    invite_resp = await client.post(
        "/api/v1/users/invites", json={"email": "newmember@invitetest.com", "role": "STAFF"},
        headers=_auth_headers(owner_token),
    )
    assert invite_resp.status_code == 201, invite_resp.text
    invite_body = invite_resp.json()
    assert invite_body["invite"]["email"] == "newmember@invitetest.com"
    assert invite_body["invite"]["role"] == "STAFF"
    assert invite_body["invite"]["status"] == "PENDING"
    token = invite_body["invite_url_path"].split("token=")[1]

    preview_resp = await client.get(f"/api/v1/public/invites/{token}")
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["organization_name"] == "Invite Test Co"
    assert preview["email"] == "newmember@invitetest.com"
    assert preview["role"] == "STAFF"

    accept_resp = await client.post(
        f"/api/v1/public/invites/{token}/accept", json={"full_name": "New Member", "password": "memberpass1"}
    )
    assert accept_resp.status_code == 200, accept_resp.text
    accept_body = accept_resp.json()
    assert accept_body["user"]["role"] == "STAFF"
    assert accept_body["user"]["email"] == "newmember@invitetest.com"
    assert "access_token" in accept_body["tokens"]

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"organization_slug": "invite-test-co", "email": "newmember@invitetest.com", "password": "memberpass1"},
    )
    assert login_resp.status_code == 200


async def test_member_list_shows_both_owner_and_accepted_invitee(client: AsyncClient) -> None:
    owner = await _register(client, "List Members Co", "owner@listmembers.com")
    owner_token = owner["tokens"]["access_token"]

    invite_resp = await client.post(
        "/api/v1/users/invites", json={"email": "member2@listmembers.com", "role": "TECHNICIAN"},
        headers=_auth_headers(owner_token),
    )
    token = invite_resp.json()["invite_url_path"].split("token=")[1]
    await client.post(f"/api/v1/public/invites/{token}/accept", json={"full_name": "Member Two", "password": "pw12345678"})

    members_resp = await client.get("/api/v1/users", headers=_auth_headers(owner_token))
    assert members_resp.status_code == 200
    emails = {m["email"] for m in members_resp.json()["members"]}
    assert emails == {"owner@listmembers.com", "member2@listmembers.com"}


async def test_cannot_invite_the_same_email_twice(client: AsyncClient) -> None:
    owner = await _register(client, "Dup Invite Co", "owner@dupinvite.com")
    owner_token = owner["tokens"]["access_token"]

    r1 = await client.post(
        "/api/v1/users/invites", json={"email": "dup@dupinvite.com", "role": "STAFF"}, headers=_auth_headers(owner_token)
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/api/v1/users/invites", json={"email": "dup@dupinvite.com", "role": "MANAGER"}, headers=_auth_headers(owner_token)
    )
    assert r2.status_code == 404  # ValueError -> raise_http_for_tool_error maps to 404


async def test_cannot_invite_an_email_already_a_member(client: AsyncClient) -> None:
    owner = await _register(client, "Existing Member Co", "owner@existingmember.com")
    owner_token = owner["tokens"]["access_token"]

    resp = await client.post(
        "/api/v1/users/invites", json={"email": "owner@existingmember.com", "role": "STAFF"},
        headers=_auth_headers(owner_token),
    )
    assert resp.status_code == 404


async def test_revoked_invite_link_no_longer_accepts(client: AsyncClient) -> None:
    owner = await _register(client, "Revoke Co", "owner@revokeco.com")
    owner_token = owner["tokens"]["access_token"]

    invite_resp = await client.post(
        "/api/v1/users/invites", json={"email": "revoked@revokeco.com", "role": "STAFF"}, headers=_auth_headers(owner_token)
    )
    invite_id = invite_resp.json()["invite"]["id"]
    token = invite_resp.json()["invite_url_path"].split("token=")[1]

    revoke_resp = await client.post(f"/api/v1/users/invites/{invite_id}/revoke", headers=_auth_headers(owner_token))
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["invite"]["status"] == "REVOKED"

    accept_resp = await client.post(
        f"/api/v1/public/invites/{token}/accept", json={"full_name": "Too Late", "password": "pw12345678"}
    )
    assert accept_resp.status_code == 400


async def test_cannot_demote_the_last_owner(client: AsyncClient) -> None:
    owner = await _register(client, "Last Owner Co", "owner@lastowner.com")
    owner_token = owner["tokens"]["access_token"]
    owner_id = owner["user"]["id"]

    resp = await client.patch(
        f"/api/v1/users/{owner_id}", json={"role": "STAFF"}, headers=_auth_headers(owner_token)
    )
    assert resp.status_code == 404  # LastOwnerError -> ValueError -> 404, same mapping as other ValueError cases


async def test_can_demote_owner_when_a_second_owner_exists(client: AsyncClient) -> None:
    owner = await _register(client, "Second Owner Co", "owner@secondowner.com")
    owner_token = owner["tokens"]["access_token"]
    owner_id = owner["user"]["id"]

    invite_resp = await client.post(
        "/api/v1/users/invites", json={"email": "coowner@secondowner.com", "role": "OWNER"},
        headers=_auth_headers(owner_token),
    )
    token = invite_resp.json()["invite_url_path"].split("token=")[1]
    await client.post(f"/api/v1/public/invites/{token}/accept", json={"full_name": "Co Owner", "password": "pw12345678"})

    resp = await client.patch(
        f"/api/v1/users/{owner_id}", json={"role": "STAFF"}, headers=_auth_headers(owner_token)
    )
    assert resp.status_code == 200
    assert resp.json()["member"]["role"] == "STAFF"


async def test_deactivating_a_member_bumps_token_version_and_revokes_their_session(client: AsyncClient) -> None:
    owner = await _register(client, "Deactivate Co", "owner@deactivateco.com")
    owner_token = owner["tokens"]["access_token"]

    invite_resp = await client.post(
        "/api/v1/users/invites", json={"email": "staffer@deactivateco.com", "role": "STAFF"},
        headers=_auth_headers(owner_token),
    )
    token = invite_resp.json()["invite_url_path"].split("token=")[1]
    accept_resp = await client.post(
        f"/api/v1/public/invites/{token}/accept", json={"full_name": "Staffer", "password": "pw12345678"}
    )
    staffer_id = accept_resp.json()["user"]["id"]
    staffer_token = accept_resp.json()["tokens"]["access_token"]

    me_before = await client.get("/api/v1/users/me", headers=_auth_headers(staffer_token))
    assert me_before.status_code == 200

    deactivate_resp = await client.patch(
        f"/api/v1/users/{staffer_id}", json={"is_active": False}, headers=_auth_headers(owner_token)
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["member"]["is_active"] is False

    me_after = await client.get("/api/v1/users/me", headers=_auth_headers(staffer_token))
    assert me_after.status_code == 401  # token_version bump invalidates the old token


async def test_non_owner_admin_cannot_manage_team(client: AsyncClient) -> None:
    owner = await _register(client, "Staff Cannot Invite Co", "owner@staffcannot.com")
    owner_token = owner["tokens"]["access_token"]

    invite_resp = await client.post(
        "/api/v1/users/invites", json={"email": "staffer2@staffcannot.com", "role": "STAFF"},
        headers=_auth_headers(owner_token),
    )
    token = invite_resp.json()["invite_url_path"].split("token=")[1]
    accept_resp = await client.post(
        f"/api/v1/public/invites/{token}/accept", json={"full_name": "Staffer Two", "password": "pw12345678"}
    )
    staffer_token = accept_resp.json()["tokens"]["access_token"]

    resp = await client.post(
        "/api/v1/users/invites", json={"email": "someoneelse@staffcannot.com", "role": "STAFF"},
        headers=_auth_headers(staffer_token),
    )
    assert resp.status_code == 403


async def test_invalid_invite_token_preview_is_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/public/invites/not-a-real-token")
    assert resp.status_code == 404
