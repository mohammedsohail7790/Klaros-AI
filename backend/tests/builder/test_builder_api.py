"""The builder over its real HTTP surface: the full flow, the sandboxed serving, and the attack surface around it."""

import io
import time
import uuid
import zipfile

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.v1.builder import get_orchestrator
from app.builder import preview as pv
from app.db.session import async_session_maker
from app.models.crm import Lead

pytestmark = pytest.mark.asyncio

SAAS = "Build a premium AI SaaS marketing website with a homepage, features page, pricing page, blog listing, blog detail routes, about page, and contact page."
ESTATE = "Build a real-estate marketplace with a homepage, property search, filters, property details, saved properties, user accounts, and enquiry forms."


def H(t):
    return {"Authorization": f"Bearer {t}"}


async def register(client: AsyncClient, org: str, email: str):
    r = await client.post("/api/v1/auth/register", json={"organization_name": org, "full_name": "Owner", "email": email, "password": "supersecret1"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j["tokens"]["access_token"], uuid.UUID(j["user"]["tenant_id"])


async def build(client, token, prompt=SAAS, name="Acme"):
    r = await client.post("/api/v1/builder/projects", json={"prompt": prompt, "name": name}, headers=H(token))
    assert r.status_code == 202, r.text
    pid, rid = r.json()["project"]["id"], r.json()["run"]["id"]
    await get_orchestrator().wait(uuid.UUID(rid))
    return pid, rid


async def test_the_whole_flow_over_http(client: AsyncClient):
    token, tenant = await register(client, "Flow Co", "flow@example.com")
    pid, rid = await build(client, token)

    run = (await client.get(f"/api/v1/builder/projects/{pid}/runs/{rid}", headers=H(token))).json()
    assert run["state"] == "ready" and run["validation"]["ok"] is True
    proj = (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(token))).json()["project"]
    paths = {r["path"] for r in proj["routes"]}
    assert {"/", "/pricing", "/blog", "/blog/[slug]", "/contact"} <= paths and proj["status"] == "ready"

    files = (await client.get(f"/api/v1/builder/projects/{pid}/files", headers=H(token))).json()
    assert any(f["path"] == "pricing/index.html" for f in files["files"]) and files["validation"]["ok"]
    one = (await client.get(f"/api/v1/builder/projects/{pid}/files/pricing/index.html", headers=H(token))).json()
    assert "<h1>" in one["content"] and "Pricing" in one["content"]
    assert (await client.get(f"/api/v1/builder/projects/{pid}/messages", headers=H(token))).json()[0]["role"] == "user"

    # preview through the signed, sandboxed route
    tok = (await client.post(f"/api/v1/builder/projects/{pid}/preview-token", headers=H(token))).json()
    url = "/api/v1" + tok["url"].split("/api/v1", 1)[1]
    page = await client.get(url)
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "Acme" in page.text
    csp = page.headers["content-security-policy"]
    assert "sandbox allow-scripts allow-forms" in csp and "allow-same-origin" not in csp and "script-src 'self'" in csp and "default-src 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff" and page.headers["cache-control"] == "no-store"
    assert 'name="klaros-enquiry" content=""' in page.text  # preview forms are deliberately not connected
    js = await client.get(url + "assets/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert (await client.get(url + "pricing/")).status_code == 200
    missing = await client.get(url + "nope/")
    assert missing.status_code == 404 and "Page not found" in missing.text

    # edit by chat, then the version history shows both
    r = await client.post(f"/api/v1/builder/projects/{pid}/messages", json={"message": "add a faq page"}, headers=H(token))
    assert r.status_code == 202
    await get_orchestrator().wait(uuid.UUID(r.json()["run"]["id"]))
    versions = (await client.get(f"/api/v1/builder/projects/{pid}/versions", headers=H(token))).json()
    assert [v["number"] for v in versions] == [2, 1] and "faq/index.html" in versions[0]["changes"]["added"]
    old = versions[1]["id"]
    restored = await client.post(f"/api/v1/builder/projects/{pid}/versions/{old}/restore", headers=H(token))
    assert restored.status_code == 201 and restored.json()["number"] == 3

    # export
    z = await client.get(f"/api/v1/builder/projects/{pid}/export", headers=H(token))
    assert z.status_code == 200 and "index.html" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()


async def test_nothing_works_without_a_login(client: AsyncClient):
    for method, path in [("GET", "/api/v1/builder/projects"), ("POST", "/api/v1/builder/projects"), ("GET", f"/api/v1/builder/projects/{uuid.uuid4()}"), ("POST", f"/api/v1/builder/projects/{uuid.uuid4()}/messages")]:
        r = await client.request(method, path, json={"prompt": "x" * 20} if method == "POST" else None)
        assert r.status_code in (401, 403), (method, path, r.status_code)


async def test_validation_of_requests(client: AsyncClient):
    token, _ = await register(client, "V Co", "v@example.com")
    assert (await client.post("/api/v1/builder/projects", json={"prompt": "short"}, headers=H(token))).status_code == 422
    assert (await client.post("/api/v1/builder/projects", json={"prompt": "x" * 7000}, headers=H(token))).status_code == 422
    pid, _ = await build(client, token)
    assert (await client.post(f"/api/v1/builder/projects/{pid}/messages", json={"message": "x"}, headers=H(token))).status_code == 422
    assert (await client.put(f"/api/v1/builder/projects/{pid}/files/index.html", json={"content": "x" * 400_000}, headers=H(token))).status_code == 422


# ---------------------------------------------------------------- scenario F: isolation and path attacks
async def test_one_tenant_cannot_reach_another_tenants_project(client: AsyncClient):
    ta, _ = await register(client, "A Co", "a@example.com")
    tb, tenant_b = await register(client, "B Co", "b@example.com")
    pid, rid = await build(client, ta)
    proj = (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(ta))).json()["project"]
    vid = proj["current_version_id"]
    probes = [
        ("GET", f"/api/v1/builder/projects/{pid}"), ("DELETE", f"/api/v1/builder/projects/{pid}"), ("GET", f"/api/v1/builder/projects/{pid}/files"),
        ("GET", f"/api/v1/builder/projects/{pid}/files/index.html"), ("GET", f"/api/v1/builder/projects/{pid}/versions"), ("GET", f"/api/v1/builder/projects/{pid}/messages"),
        ("POST", f"/api/v1/builder/projects/{pid}/preview-token"), ("GET", f"/api/v1/builder/projects/{pid}/runs/{rid}"), ("POST", f"/api/v1/builder/projects/{pid}/runs/{rid}/cancel"),
        ("POST", f"/api/v1/builder/projects/{pid}/versions/{vid}/restore"), ("POST", f"/api/v1/builder/projects/{pid}/publish"), ("GET", f"/api/v1/builder/projects/{pid}/export"),
    ]
    for method, path in probes:
        r = await client.request(method, path, headers=H(tb), json={"message": "add a faq page", "content": "x", "acknowledge_examples": True} if method in ("POST", "PUT") else None)
        assert r.status_code in (404, 409, 422), (method, path, r.status_code)
    r = await client.put(f"/api/v1/builder/projects/{pid}/files/index.html", json={"content": "<p>pwned</p>"}, headers=H(tb))
    assert r.status_code in (404, 409, 422)
    r = await client.post(f"/api/v1/builder/projects/{pid}/messages", json={"message": "delete everything"}, headers=H(tb))
    assert r.status_code in (404, 409)
    assert (await client.get("/api/v1/builder/projects", headers=H(tb))).json() == []
    # A's project is untouched
    assert (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(ta))).json()["project"]["current_version_id"] == vid


async def test_a_forged_preview_token_cannot_reach_another_tenants_files(client: AsyncClient):
    ta, tenant_a = await register(client, "A2 Co", "a2@example.com")
    tb, tenant_b = await register(client, "B2 Co", "b2@example.com")
    pid, _ = await build(client, ta)
    vid = uuid.UUID((await client.get(f"/api/v1/builder/projects/{pid}", headers=H(ta))).json()["project"]["current_version_id"])
    good, _exp = pv.sign_preview(tenant_a, uuid.UUID(pid), vid)
    assert (await client.get(f"/api/v1/builder-preview/{good}/")).status_code == 200
    # tampering with any part, signing with the wrong key, dropping the signature, or expiry all fail
    body, sig = good.split(".")
    for bad in (f"{body}x.{sig}", f"{body}.{sig[:-2]}ab", body, f"{body}.", "x.y", "", "a" * 500):
        r = await client.get(f"/api/v1/builder-preview/{bad}/") if bad else await client.get("/api/v1/builder-preview//")
        assert r.status_code in (403, 404), bad
    expired, _ = pv.sign_preview(tenant_a, uuid.UUID(pid), vid, ttl=-5)
    assert (await client.get(f"/api/v1/builder-preview/{expired}/")).status_code == 403
    # a validly SIGNED token that names tenant B with A's project serves nothing: the files are not visible under B's tenant
    forged, _ = pv.sign_preview(tenant_b, uuid.UUID(pid), vid)
    r = await client.get(f"/api/v1/builder-preview/{forged}/")
    assert r.status_code == 404 and "Acme" not in r.text


@pytest.mark.parametrize("path", ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/index.html", "assets/../../x.html", "index.html%00.png", "\\windows\\win.ini", "/etc/passwd", ".env", "assets/.hidden.js"])
async def test_path_traversal_is_refused_everywhere(client: AsyncClient, path):
    token, tenant = await register(client, "P Co", f"p{uuid.uuid4().hex[:6]}@example.com")
    pid, _ = await build(client, token)
    r = await client.get(f"/api/v1/builder/projects/{pid}/files/{path}", headers=H(token))
    assert r.status_code in (404, 422)
    w = await client.put(f"/api/v1/builder/projects/{pid}/files/{path}", json={"content": "x"}, headers=H(token))
    assert w.status_code in (404, 405, 422)
    url = "/api/v1" + (await client.post(f"/api/v1/builder/projects/{pid}/preview-token", headers=H(token))).json()["url"].split("/api/v1", 1)[1]
    assert (await client.get(url)).status_code == 200  # the preview itself works, so the refusals below are real refusals
    p = await client.get(url + path)
    assert p.status_code in (404, 400, 422, 307) and "root:" not in p.text


async def test_editing_a_file_over_http_must_pass_the_checks(client: AsyncClient):
    token, _ = await register(client, "E Co", "e@example.com")
    pid, _ = await build(client, token)
    r = await client.put(f"/api/v1/builder/projects/{pid}/files/index.html", json={"content": "<!doctype html><html><body><script>alert(1)</script></body></html>"}, headers=H(token))
    assert r.status_code == 422 and "breaks the project" in r.json()["detail"]
    r = await client.put(f"/api/v1/builder/projects/{pid}/files/script.php", json={"content": "<?php"}, headers=H(token))
    assert r.status_code == 422
    cur = (await client.get(f"/api/v1/builder/projects/{pid}/files/about/index.html", headers=H(token))).json()["content"]
    ok = await client.put(f"/api/v1/builder/projects/{pid}/files/about/index.html", json={"content": cur.replace("</main>", "<p>Edited by hand.</p></main>")}, headers=H(token))
    assert ok.status_code == 200 and ok.json()["kind"] == "file_edit"


# ---------------------------------------------------------------- publishing and the public path
async def test_publishing_serves_only_the_published_version_and_wires_enquiries(client: AsyncClient):
    token, tenant = await register(client, "Pub Co", "pub@example.com")
    pid, _ = await build(client, token, ESTATE, "Hearth Homes")
    proj = (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(token))).json()["project"]
    slug = proj["slug"]
    assert (await client.get(f"/api/v1/public/sites/{tenant}/{slug}/")).status_code == 404  # nothing is public until published
    r = await client.post(f"/api/v1/builder/projects/{pid}/publish", json={"acknowledge_examples": False}, headers=H(token))
    assert r.status_code == 409 and "example content" in r.json()["detail"]
    r = await client.post(f"/api/v1/builder/projects/{pid}/publish", json={"acknowledge_examples": True}, headers=H(token))
    assert r.status_code == 200
    site = f"/api/v1/public/sites/{tenant}/{slug}/"
    home = await client.get(site)
    assert home.status_code == 200 and "Hearth Homes" in home.text
    assert f"/api/v1/public/leads/{tenant}" in home.text  # enquiries are connected once published
    assert "sandbox" not in home.headers["content-security-policy"] and "script-src 'self'" in home.headers["content-security-policy"]
    data = await client.get(site + "data/properties.json")
    assert data.status_code == 200 and len(data.json()) >= 6
    assert (await client.get(site + "properties/")).status_code == 200
    assert (await client.get(f"/api/v1/public/sites/{uuid.uuid4()}/{slug}/")).status_code == 404
    assert (await client.get(f"/api/v1/public/sites/{tenant}/not-a-slug/")).status_code == 404
    assert (await client.get(site + "../../../etc/passwd")).status_code in (404, 400)

    # a later edit does not change what is live
    r = await client.post(f"/api/v1/builder/projects/{pid}/messages", json={"message": "change the headline to Brand new headline"}, headers=H(token))
    await get_orchestrator().wait(uuid.UUID(r.json()["run"]["id"]))
    assert "Brand new headline" not in (await client.get(site)).text

    # the enquiry a published form sends is accepted by the real lead endpoint
    lead = await client.post(f"/api/v1/public/leads/{tenant}", json={"name": "Visitor", "source": "WEB", "email": "visitor@example.com", "description": "Is the Riverside loft still available?", "service_requested": "Enquiry about Riverside loft", "website": None})
    assert lead.status_code == 201
    async with async_session_maker() as s:
        assert (await s.execute(select(Lead).where(Lead.tenant_id == tenant, Lead.email == "visitor@example.com"))).scalar_one_or_none() is not None

    # unpublishing takes it down
    assert (await client.delete(f"/api/v1/builder/projects/{pid}/publish", headers=H(token))).status_code == 200
    assert (await client.get(site)).status_code == 404


async def test_cancel_endpoint_stops_a_running_build(client: AsyncClient, monkeypatch):
    import asyncio

    from app.builder import orchestrator as orch_mod

    started = asyncio.Event()

    async def slow(*a, **k):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(orch_mod, "plan_project", slow)
    token, _ = await register(client, "C Co", "c@example.com")
    r = await client.post("/api/v1/builder/projects", json={"prompt": SAAS}, headers=H(token))
    pid, rid = r.json()["project"]["id"], r.json()["run"]["id"]
    await started.wait()
    # a second build at the same time is refused
    assert (await client.post("/api/v1/builder/projects", json={"prompt": SAAS}, headers=H(token))).status_code == 409
    c = await client.post(f"/api/v1/builder/projects/{pid}/runs/{rid}/cancel", headers=H(token))
    assert c.status_code == 200 and c.json()["cancel_requested"] is True
    await get_orchestrator().wait(uuid.UUID(rid))
    assert (await client.get(f"/api/v1/builder/projects/{pid}/runs/{rid}", headers=H(token))).json()["state"] == "cancelled"
    assert (await client.get(f"/api/v1/builder/projects/{pid}", headers=H(token))).json()["project"]["current_version_id"] is None
