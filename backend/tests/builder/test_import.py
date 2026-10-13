"""Importing an existing static site: it gets exactly the checks a generated site gets, and a consent-gated business's form stays consent-gated."""

import uuid

import pytest
from httpx import AsyncClient

from app.builder import preview as pv
from app.builder.imports import build_workspace, normalise_import, routes_of
from app.builder.workspace import WorkspaceError
from tests.builder.test_builder_api import H, register
from tests.test_halla_integration import halla  # noqa: F401

pytestmark = pytest.mark.asyncio

PAGE = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Tiny</title><meta name="klaros-api" content=""><meta name="klaros-tenant" content=""><link rel="stylesheet" href="styles.css"></head><body><main><h1>Tiny</h1><a href="#x">x</a><span id="x"></span></main><script src="script.js" defer></script></body></html>'
FLAT = {"index.html": PAGE, "styles.css": "body{margin:0}", "script.js": "(function(){})();"}


def test_a_flat_site_is_moved_into_assets_and_its_links_follow():
    out = normalise_import(dict(FLAT))
    assert "assets/styles.css" in out and "assets/app.js" in out and "styles.css" not in out
    assert 'href="assets/styles.css"' in out["index.html"] and 'src="assets/app.js"' in out["index.html"]


def test_a_single_wrapping_folder_is_stripped_and_routes_are_listed():
    ws = build_workspace({f"my-site/{p}": c for p, c in FLAT.items()} | {"my-site/pricing/index.html": PAGE})
    assert "index.html" in ws.paths()
    assert {r["path"] for r in routes_of(ws)} == {"/", "/pricing"}


def test_an_import_without_an_index_page_is_refused():
    with pytest.raises(WorkspaceError):
        build_workspace({"about.html": PAGE})


def test_context_is_only_injected_for_published_pages():
    assert 'content="https://api.example"' in pv.inject_context(PAGE, api_base="https://api.example", tenant_id=uuid.uuid4())
    assert pv.inject_context(PAGE, api_base=None, tenant_id=uuid.uuid4()) == PAGE
    assert pv.inject_context(PAGE, api_base='"><script>', tenant_id=uuid.uuid4()).count("<script>") == 0


async def test_import_over_http_checks_previews_and_publishes(client: AsyncClient):
    token, tenant = await register(client, "Imp Co", "imp@example.com")
    r = await client.post("/api/v1/builder/projects/import", json={"name": "Tiny site", "files": FLAT}, headers=H(token))
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    assert r.json()["validation"]["ok"] is True
    tok = (await client.post(f"/api/v1/builder/projects/{pid}/preview-token", headers=H(token))).json()
    page = await client.get("/api/v1" + tok["url"].split("/api/v1", 1)[1])
    assert page.status_code == 200 and "Tiny" in page.text and 'name="klaros-api" content=""' in page.text
    slug = (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(token))).json()["project"]["slug"]
    pub = await client.post(f"/api/v1/builder/projects/{pid}/publish", json={"acknowledge_examples": True}, headers=H(token))
    assert pub.status_code == 200, pub.text
    live = await client.get(f"/api/v1/public/sites/{tenant}/{slug}/")
    assert live.status_code == 200 and f'name="klaros-tenant" content="{tenant}"' in live.text


@pytest.mark.parametrize("bad", [
    {"index.html": PAGE.replace("<h1>", '<h1 onclick="x()">')},
    {"index.html": PAGE.replace("<main>", '<main style="color:red">')},
    {"index.html": PAGE.replace('href="styles.css"', 'href="https://cdn.example/x.css"')},
    {"index.html": PAGE, "script.js": "eval('1')"},
    {"../evil.html": PAGE, "index.html": PAGE},
])
async def test_an_unsafe_import_is_rejected_with_a_reason(client: AsyncClient, bad):
    token, _ = await register(client, "Bad Co", f"bad{uuid.uuid4().hex[:6]}@example.com")
    files = {"styles.css": "body{}", "script.js": "(function(){})();"} | bad
    r = await client.post("/api/v1/builder/projects/import", json={"name": "Bad site", "files": files}, headers=H(token))
    assert r.status_code == 422 and r.json()["detail"]


async def test_imports_are_tenant_isolated(client: AsyncClient):
    a, _ = await register(client, "Iso A", "isoa@example.com")
    b, _ = await register(client, "Iso B", "isob@example.com")
    pid = (await client.post("/api/v1/builder/projects/import", json={"name": "A site", "files": FLAT}, headers=H(a))).json()["project"]["id"]
    assert (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(b))).status_code == 404
    assert (await client.post("/api/v1/builder/projects/import", json={"name": "No auth", "files": FLAT})).status_code in (401, 403)


async def test_the_public_consent_endpoint_says_nothing_is_required_for_an_ordinary_business(client: AsyncClient):
    _, tenant = await register(client, "Plain Co", "plain@example.com")
    r = await client.get(f"/api/v1/public/leads/{tenant}/consent")
    assert r.status_code == 200 and r.json() == {"required": False, "wording": None}


async def test_the_public_consent_endpoint_serves_only_the_owner_approved_wording(client: AsyncClient, halla):  # noqa: F811
    from tests.test_consent_gate_intake import _gated

    token, tid = await _gated(client, "Consent Co", "consent@example.com", wording=())
    r = await client.get(f"/api/v1/public/leads/{tid}/consent")
    assert r.json() == {"required": True, "wording": None}  # nothing approved yet: the form must stay closed
    text = "I agree that Consent Co may store my name and email address, and contact me about my enquiry, as described in its privacy policy."
    a = await client.post("/api/v1/consent-wordings", json={"text": text}, headers=H(token))
    assert a.status_code == 201, a.text
    w = (await client.get(f"/api/v1/public/leads/{tid}/consent")).json()["wording"]
    assert w["text"] == text and w["version"].startswith("WEB-v")
    ok = await client.post(f"/api/v1/public/leads/{tid}", json={"name": "Visitor One", "source": "WEB", "email": "v1@example.com", "consent": {"scopes": ["store_personal_data", "contact"], "wording_version": w["version"]}})
    assert ok.status_code == 201, ok.text
    no = await client.post(f"/api/v1/public/leads/{tid}", json={"name": "Visitor Two", "source": "WEB", "email": "v2@example.com"})
    assert no.status_code in (400, 403, 422)
