"""Sign in with just an email, forgot/reset password, change password. SQLite + in-process HTTP."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.account import AccountDirectory, PasswordResetToken

PW = "supersecret1"


async def _register(client, org, email, password=PW):
    r = await client.post("/api/v1/auth/register", json={"organization_name": org, "full_name": "Owner", "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def outbox(monkeypatch):
    sent: list[dict] = []

    async def fake_send(*, to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr("app.services.account_service.send_system_email", fake_send)
    return sent


def _token_from(message: dict) -> str:
    return message["body"].split("reset-password?token=")[1].split()[0]


async def test_a_new_account_can_sign_in_with_just_email_and_password(client) -> None:
    await _register(client, "Solo Clinic Co", "solo@example.com")
    ok = await client.post("/api/v1/auth/login", json={"email": "SOLO@example.com", "password": PW})
    assert ok.status_code == 200 and ok.json()["access_token"]
    # the workspace slug still works when given
    assert (await client.post("/api/v1/auth/login", json={"organization_slug": "solo-clinic-co", "email": "solo@example.com", "password": PW})).status_code == 200
    # a wrong password and an unknown email look identical
    bad = await client.post("/api/v1/auth/login", json={"email": "solo@example.com", "password": "nope-nope-nope"})
    unknown = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PW})
    assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()


async def test_an_email_in_two_workspaces_must_pick_one_and_only_after_the_password_matches(client) -> None:
    await _register(client, "First Co", "both@example.com")
    await _register(client, "Second Co", "both@example.com")
    clash = await client.post("/api/v1/auth/login", json={"email": "both@example.com", "password": PW})
    assert clash.status_code == 409
    detail = clash.json()["detail"]
    assert detail["error"] == "multiple_workspaces" and sorted(w["slug"] for w in detail["workspaces"]) == ["first-co", "second-co"]
    # workspace names are not revealed to someone who does not hold the password
    wrong = await client.post("/api/v1/auth/login", json={"email": "both@example.com", "password": "wrong-password"})
    assert wrong.status_code == 401 and "workspaces" not in wrong.text
    chosen = await client.post("/api/v1/auth/login", json={"organization_slug": "second-co", "email": "both@example.com", "password": PW})
    assert chosen.status_code == 200


async def test_registration_records_the_directory_entry_once(client) -> None:
    await _register(client, "Dir Co", "dir@example.com")
    async with async_session_maker() as s:
        rows = (await s.execute(select(AccountDirectory).where(AccountDirectory.email == "dir@example.com"))).scalars().all()
    assert len(rows) == 1 and rows[0].organization_slug == "dir-co"


async def test_forgot_password_answers_the_same_for_known_and_unknown_emails_and_sends_one_link(client, outbox) -> None:
    await _register(client, "Reset Co", "reset@example.com")
    known = await client.post("/api/v1/auth/forgot-password", json={"email": "reset@example.com"})
    unknown = await client.post("/api/v1/auth/forgot-password", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert len(outbox) == 1 and outbox[0]["to"] == "reset@example.com" and "Reset Co" in outbox[0]["body"]
    # only a hash of the secret is stored, never the secret
    secret = _token_from(outbox[0])
    async with async_session_maker() as s:
        rows = (await s.execute(select(PasswordResetToken))).scalars().all()
    assert len(rows) == 1 and rows[0].token_hash != secret and secret not in rows[0].token_hash


async def test_a_reset_link_sets_a_new_password_once_and_signs_every_old_session_out(client, outbox) -> None:
    reg = await _register(client, "Flow Co", "flow@example.com")
    old_access = reg["tokens"]["access_token"]
    old_refresh = reg["tokens"]["refresh_token"]
    await client.post("/api/v1/auth/forgot-password", json={"email": "flow@example.com"})
    secret = _token_from(outbox[0])
    done = await client.post("/api/v1/auth/reset-password", json={"token": secret, "new_password": "brand-new-pass-9"})
    assert done.status_code == 204
    assert (await client.post("/api/v1/auth/login", json={"email": "flow@example.com", "password": "brand-new-pass-9"})).status_code == 200
    assert (await client.post("/api/v1/auth/login", json={"email": "flow@example.com", "password": PW})).status_code == 401
    # the session that existed before the reset no longer works
    assert (await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {old_access}"})).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401
    # single use
    again = await client.post("/api/v1/auth/reset-password", json={"token": secret, "new_password": "another-pass-77"})
    assert again.status_code == 400


async def test_a_newer_reset_request_retires_the_older_link_and_an_expired_link_is_refused(client, outbox) -> None:
    await _register(client, "Newer Co", "newer@example.com")
    await client.post("/api/v1/auth/forgot-password", json={"email": "newer@example.com"})
    await client.post("/api/v1/auth/forgot-password", json={"email": "newer@example.com"})
    first, second = _token_from(outbox[0]), _token_from(outbox[1])
    assert (await client.post("/api/v1/auth/reset-password", json={"token": first, "new_password": "should-not-work-1"})).status_code == 400
    async with async_session_maker() as s:
        row = (await s.execute(select(PasswordResetToken).where(PasswordResetToken.used_at.is_(None)))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    assert (await client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "should-not-work-2"})).status_code == 400
    assert (await client.post("/api/v1/auth/reset-password", json={"token": "x" * 40, "new_password": "should-not-work-3"})).status_code == 400


async def test_a_weak_new_password_is_refused_and_the_link_survives(client, outbox) -> None:
    await _register(client, "Weak Co", "weak@example.com")
    await client.post("/api/v1/auth/forgot-password", json={"email": "weak@example.com"})
    secret = _token_from(outbox[0])
    assert (await client.post("/api/v1/auth/reset-password", json={"token": secret, "new_password": "short"})).status_code == 422
    assert (await client.post("/api/v1/auth/reset-password", json={"token": secret, "new_password": "long-enough-now-1"})).status_code == 204


async def test_change_password_needs_the_current_password_and_keeps_this_session_alive(client) -> None:
    reg = await _register(client, "Change Co", "change@example.com")
    h = {"Authorization": f"Bearer {reg['tokens']['access_token']}"}
    wrong = await client.post("/api/v1/auth/change-password", json={"current_password": "not-it", "new_password": "fresh-password-3"}, headers=h)
    assert wrong.status_code == 400
    ok = await client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "fresh-password-3"}, headers=h)
    assert ok.status_code == 200
    new_h = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert (await client.get("/api/v1/users/me", headers=new_h)).status_code == 200  # this session stays signed in
    assert (await client.get("/api/v1/users/me", headers=h)).status_code == 401  # the old token is revoked
    assert (await client.post("/api/v1/auth/login", json={"email": "change@example.com", "password": "fresh-password-3"})).status_code == 200
    assert (await client.post("/api/v1/auth/change-password", json={"current_password": "x", "new_password": "fresh-password-4"})).status_code == 401  # needs a session


async def test_without_an_email_provider_the_mailer_reports_not_sent(monkeypatch) -> None:
    from app.core import config
    from app.services import system_mailer

    monkeypatch.setattr(config.get_settings(), "SENDGRID_API_KEY", None, raising=False)
    assert system_mailer.email_is_configured() is False
    assert await system_mailer.send_system_email(to="a@example.com", subject="s", body="b") is False


async def test_contact_form_stores_the_message_ignores_bots_and_emails_the_inbox_when_configured(client, monkeypatch) -> None:
    from app.core import config
    from app.models.account import ContactMessage

    sent: list[dict] = []

    async def fake_send(*, to, subject, body):
        sent.append({"to": to, "body": body})
        return True

    monkeypatch.setattr("app.api.v1.public_contact.send_system_email", fake_send)
    monkeypatch.setattr(config.get_settings(), "SUPPORT_INBOX_EMAIL", "inbox@example.com", raising=False)
    ok = await client.post("/api/v1/public/contact", json={"name": "Pat", "email": "pat@example.com", "message": "I would like a demo please."})
    assert ok.status_code == 202
    bot = await client.post("/api/v1/public/contact", json={"name": "Bot", "email": "bot@example.com", "message": "buy cheap stuff now!!", "website": "http://spam.test"})
    assert bot.status_code == 202 and bot.json() == ok.json()  # a bot cannot tell it was ignored
    short = await client.post("/api/v1/public/contact", json={"name": "Pat", "email": "pat@example.com", "message": "hi"})
    assert short.status_code == 422
    async with async_session_maker() as s:
        rows = (await s.execute(select(ContactMessage))).scalars().all()
    assert [r.email for r in rows] == ["pat@example.com"]
    assert len(sent) == 1 and sent[0]["to"] == "inbox@example.com" and "demo" in sent[0]["body"]
