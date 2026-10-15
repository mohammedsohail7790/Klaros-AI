"""A business can upload its own finished site and serve it on its own domain.

The rules under test: only static files are accepted and paths cannot escape; a draft is never served; a domain serves nothing until its owner has proved control with a DNS record;
a domain belongs to one business; and one business can never see or serve another's files.
"""

import base64
import io
import zipfile

import pytest

from app.api.v1 import site_hosting as api
from app.services.site_hosting_service import SiteUploadError, normalise_hostname, unpack
from tests.test_halla_integration import _register
from tests.test_mt_referrals import _h

pytestmark = pytest.mark.asyncio


def _zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


SITE = {"index.html": "<h1>Hello</h1>", "css/site.css": "body{color:red}", "app.js": "console.log(1)", "logo.png": b"\x89PNG"}


async def test_a_zip_is_unpacked_and_a_wrapping_folder_is_dropped() -> None:
    files = unpack("site.zip", _zip({f"mysite/{k}": v for k, v in SITE.items()} | {"__MACOSX/._x": "junk", "mysite/.DS_Store": "junk"}))
    assert set(files) == set(SITE)


async def test_a_single_html_file_becomes_the_home_page() -> None:
    assert unpack("page.html", b"<p>hi</p>") == {"index.html": b"<p>hi</p>"}


@pytest.mark.parametrize("name,data,why", [
    ("site.zip", _zip({"index.html": "x", "run.php": "<?php"}), "not a type"),
    ("site.zip", _zip({"index.html": "x", "bin.exe": "MZ"}), "not a type"),
    ("site.zip", _zip({"about.html": "x"}), "index.html"),
    ("site.zip", _zip({"../evil.html": "x", "index.html": "x"}), "leaves the site folder"),
    ("site.zip", b"not a zip", "not a valid zip"),
    ("site.txt", b"hello", "Upload an .html"),
    ("site.zip", b"", "empty"),
])
async def test_unusable_uploads_are_refused_with_a_reason(name, data, why) -> None:
    with pytest.raises(SiteUploadError, match=why):
        unpack(name, data)


async def test_a_zip_that_unpacks_too_large_is_refused() -> None:
    big = _zip({"index.html": "x", "a.txt": b"0" * (9 * 1024 * 1024), "b.txt": b"0" * (9 * 1024 * 1024), "c.txt": b"0" * (9 * 1024 * 1024)})
    assert len(big) < 10 * 1024 * 1024
    with pytest.raises(SiteUploadError, match="25 MB"):
        unpack("site.zip", big)


@pytest.mark.parametrize("raw,expected", [("https://WWW.Example.com/path", "www.example.com"), ("example.com.", "example.com")])
async def test_hostnames_are_normalised(raw, expected) -> None:
    assert normalise_hostname(raw) == expected


@pytest.mark.parametrize("raw", ["localhost", "example", "1.2.3.4", "meetklaros.com", "x.meetklaros.com", "foo.onrender.com", "bad_host.com", ""])
async def test_hostnames_that_are_not_ours_to_attach_are_refused(raw) -> None:
    from app.services.site_hosting_service import DomainError

    with pytest.raises(DomainError):
        normalise_hostname(raw)


async def _upload(client, token, files=SITE, name="site.zip"):
    return await client.post("/api/v1/site-hosting/uploads", json={"filename": name, "content_base64": _b64(_zip(files))}, headers=_h(token))


async def test_upload_publish_domain_verify_and_serve(client, monkeypatch) -> None:
    token = await _register(client, "Own Site Co", "ownsite@example.com")
    up = await _upload(client, token)
    assert up.status_code == 201 and up.json()["status"] == "DRAFT" and up.json()["file_count"] == 4
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "www.ownsite-test.com"}, headers=_h(token))).json()
    assert dom["status"] == "PENDING" and dom["txt_name"] == "_klaros-verify.www.ownsite-test.com"

    # nothing is served yet: not verified, and not published
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/")).status_code == 404

    async def no_record(name):
        return []

    monkeypatch.setattr(api._service, "_dns", no_record)
    not_yet = (await client.post(f"/api/v1/site-hosting/domains/{dom['id']}/verify", headers=_h(token))).json()
    assert not_yet["verified"] is False and not_yet["domain"]["status"] == "PENDING"

    async def with_record(name):
        assert name == dom["txt_name"]
        return ["unrelated", dom["txt_value"]]

    monkeypatch.setattr(api._service, "_dns", with_record)
    assert (await client.post(f"/api/v1/site-hosting/domains/{dom['id']}/verify", headers=_h(token))).json()["verified"] is True

    # verified but nothing published: still no site files, and the host resolves to the generated Klaros site
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/")).status_code == 404
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com")).json()["kind"] == "klaros"

    pub = await client.post(f"/api/v1/site-hosting/uploads/{up.json()['id']}/publish", headers=_h(token))
    assert pub.status_code == 200 and pub.json()["status"] == "PUBLISHED"
    home = await client.get("/api/v1/public/domains/www.ownsite-test.com/")
    assert home.status_code == 200 and home.text == "<h1>Hello</h1>" and home.headers["content-type"].startswith("text/html")
    assert home.headers["x-content-type-options"] == "nosniff" and "object-src 'none'" in home.headers["content-security-policy"]
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/css/site.css")).headers["content-type"].startswith("text/css")
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/app.js")).headers["content-type"].startswith("text/javascript")
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/logo.png")).headers["content-type"] == "image/png"
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/missing.html")).status_code == 404
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com")).json()["kind"] == "upload"

    await client.post("/api/v1/site-hosting/unpublish", headers=_h(token))
    assert (await client.get("/api/v1/public/domains/www.ownsite-test.com/")).status_code == 404


async def test_a_new_publish_replaces_the_live_site_and_keeps_the_old_one(client, monkeypatch) -> None:
    token = await _register(client, "Republish Co", "republish@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "republish-test.com"}, headers=_h(token))).json()

    async def ok(name):
        return [dom["txt_value"]]

    monkeypatch.setattr(api._service, "_dns", ok)
    await client.post(f"/api/v1/site-hosting/domains/{dom['id']}/verify", headers=_h(token))
    first = (await _upload(client, token, {"index.html": "one"})).json()
    second = (await _upload(client, token, {"index.html": "two"})).json()
    await client.post(f"/api/v1/site-hosting/uploads/{first['id']}/publish", headers=_h(token))
    assert (await client.get("/api/v1/public/domains/republish-test.com/")).text == "one"
    await client.post(f"/api/v1/site-hosting/uploads/{second['id']}/publish", headers=_h(token))
    assert (await client.get("/api/v1/public/domains/republish-test.com/")).text == "two"
    statuses = {u["version"]: u["status"] for u in (await client.get("/api/v1/site-hosting", headers=_h(token))).json()["uploads"]}
    assert statuses == {first["version"]: "ARCHIVED", second["version"]: "PUBLISHED"}


async def test_a_draft_is_never_served(client, monkeypatch) -> None:
    token = await _register(client, "Draft Co", "draftco@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "draft-test.com"}, headers=_h(token))).json()

    async def ok(name):
        return [dom["txt_value"]]

    monkeypatch.setattr(api._service, "_dns", ok)
    await client.post(f"/api/v1/site-hosting/domains/{dom['id']}/verify", headers=_h(token))
    await _upload(client, token, {"index.html": "secret draft"})
    assert (await client.get("/api/v1/public/domains/draft-test.com/")).status_code == 404


async def test_a_domain_belongs_to_one_business_and_the_other_cannot_serve_or_remove_it(client, monkeypatch) -> None:
    a = await _register(client, "Dom A Co", "doma@example.com")
    b = await _register(client, "Dom B Co", "domb@example.com")
    mine = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "contested-test.com"}, headers=_h(a))).json()
    taken = await client.post("/api/v1/site-hosting/domains", json={"hostname": "contested-test.com"}, headers=_h(b))
    assert taken.status_code == 422 and "another business" in taken.json()["detail"]
    assert (await client.post(f"/api/v1/site-hosting/domains/{mine['id']}/verify", headers=_h(b))).status_code == 404
    assert (await client.delete(f"/api/v1/site-hosting/domains/{mine['id']}", headers=_h(b))).status_code == 404
    assert (await client.get("/api/v1/site-hosting", headers=_h(b))).json()["domains"] == []
    assert (await client.delete(f"/api/v1/site-hosting/domains/{mine['id']}", headers=_h(a))).status_code == 204


async def test_a_dns_lookup_failure_never_verifies(client, monkeypatch) -> None:
    token = await _register(client, "Dns Fail Co", "dnsfail@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "dnsfail-test.com"}, headers=_h(token))).json()

    async def boom(name):
        raise TimeoutError

    monkeypatch.setattr(api._service, "_dns", boom)
    res = await client.post(f"/api/v1/site-hosting/domains/{dom['id']}/verify", headers=_h(token))
    assert res.status_code == 200 and res.json()["verified"] is False


async def test_a_bad_upload_is_a_clear_422_and_stores_nothing(client) -> None:
    token = await _register(client, "Bad Up Co", "badup@example.com")
    res = await _upload(client, token, {"index.html": "x", "evil.php": "<?php"})
    assert res.status_code == 422 and "evil.php" in res.json()["detail"]
    assert (await client.get("/api/v1/site-hosting", headers=_h(token))).json()["uploads"] == []
    assert (await client.post("/api/v1/site-hosting/uploads", json={"filename": "a.zip", "content_base64": "!!!notbase64!!!"}, headers=_h(token))).status_code == 422


async def test_a_site_on_its_own_domain_can_send_an_enquiry_but_other_endpoints_stay_closed(client) -> None:
    origin = {"Origin": "https://aesthetictourism.example"}
    pre = await client.options("/api/v1/public/leads/00000000-0000-0000-0000-000000000000", headers={**origin, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "*"
    got = await client.get("/api/v1/public/domains/unknown-test.com", headers=origin)
    assert got.status_code == 404 and got.headers["access-control-allow-origin"] == "*"
    private = await client.options("/api/v1/leads", headers={**origin, "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in private.headers
    assert "access-control-allow-origin" not in (await client.get("/api/v1/site-hosting", headers=origin)).headers


# ---- automatic upkeep ------------------------------------------------------------------------------------------------------------------------------------

class FakeEdge:
    """Stands in for the frontend host's domain API."""

    def __init__(self, configured=True, live=False, error=None):
        self._configured, self.live, self.error = configured, live, error
        self.added: list[str] = []
        self.removed: list[str] = []
        self.registered: set[str] = set()

    def configured(self):
        return self._configured

    async def add(self, host):
        from app.services.vercel_domains import EdgeState

        self.added.append(host)
        if self.error:
            return EdgeState(registered=False, error=self.error)
        self.registered.add(host)
        return EdgeState(registered=True)

    async def state(self, host):
        from app.services.vercel_domains import EdgeState

        return EdgeState(registered=host in self.registered, live=self.live and host in self.registered)

    async def remove(self, host):
        self.removed.append(host)
        self.registered.discard(host)


def _swap_edge(monkeypatch, edge):
    monkeypatch.setattr(api._service, "_edge", edge)


async def test_adding_a_domain_registers_it_with_the_host_without_the_owner_touching_it(client, monkeypatch) -> None:
    edge = FakeEdge()
    _swap_edge(monkeypatch, edge)
    token = await _register(client, "Auto Dom Co", "autodom@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "auto-test.com"}, headers=_h(token))).json()
    assert edge.added == ["auto-test.com"] and dom["automated"] is True and dom["edge_status"] == "WAITING_FOR_DNS"


async def test_without_host_credentials_the_domain_says_it_is_manual(client, monkeypatch) -> None:
    _swap_edge(monkeypatch, FakeEdge(configured=False))
    token = await _register(client, "Manual Dom Co", "manualdom@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "manual-test.com"}, headers=_h(token))).json()
    assert dom["automated"] is False and dom["edge_status"] == "NOT_AUTOMATED"


async def test_a_host_refusal_is_shown_and_retried_not_hidden(client, monkeypatch) -> None:
    edge = FakeEdge(error="The host refused the domain (400).")
    _swap_edge(monkeypatch, edge)
    token = await _register(client, "Refused Co", "refused@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "refused-test.com"}, headers=_h(token))).json()
    assert dom["edge_status"] == "ERROR" and "refused" in dom["edge_detail"]
    edge.error = None
    await api._service.sweep()
    assert (await client.get("/api/v1/site-hosting", headers=_h(token))).json()["domains"][0]["edge_status"] == "WAITING_FOR_DNS"


async def test_the_sweep_verifies_ownership_and_marks_the_domain_live_on_its_own(client, monkeypatch) -> None:
    edge = FakeEdge()
    _swap_edge(monkeypatch, edge)
    token = await _register(client, "Sweep Co", "sweepco@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "sweep-test.com"}, headers=_h(token))).json()

    async def nothing(name):
        return []

    monkeypatch.setattr(api._service, "_dns", nothing)
    await api._service.sweep()
    assert (await client.get("/api/v1/site-hosting", headers=_h(token))).json()["domains"][0]["status"] == "PENDING"

    async def record(name):
        return [dom["txt_value"]]

    monkeypatch.setattr(api._service, "_dns", record)
    edge.live = True
    result = await api._service.sweep()
    shown = (await client.get("/api/v1/site-hosting", headers=_h(token))).json()["domains"][0]
    assert result["verified"] >= 1 and shown["status"] == "VERIFIED" and shown["edge_status"] == "LIVE" and shown["edge_detail"] is None


async def test_removing_a_domain_removes_it_from_the_host_and_one_bad_domain_does_not_stop_the_sweep(client, monkeypatch) -> None:
    edge = FakeEdge()
    _swap_edge(monkeypatch, edge)
    token = await _register(client, "Rm Dom Co", "rmdom@example.com")
    dom = (await client.post("/api/v1/site-hosting/domains", json={"hostname": "rm-test.com"}, headers=_h(token))).json()
    await client.delete(f"/api/v1/site-hosting/domains/{dom['id']}", headers=_h(token))
    assert edge.removed == ["rm-test.com"]

    async def boom(name):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(api._service, "_dns", boom)
    assert isinstance(await api._service.sweep(), dict)


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body


async def test_the_vercel_client_adds_reads_and_never_raises(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.vercel_domains import VercelDomains

    s = get_settings()
    monkeypatch.setattr(s, "VERCEL_API_TOKEN", "tok")
    monkeypatch.setattr(s, "VERCEL_PROJECT_ID", "prj")
    v = VercelDomains()
    assert v.configured()
    calls = []

    async def ok(method, path, **kw):
        calls.append((method, path))
        if method == "POST":
            return _Resp(200, {"name": "x.com"})
        if "/v6/domains/" in path:
            return _Resp(200, {"misconfigured": False})
        return _Resp(200, {"verified": True})

    monkeypatch.setattr(v, "_call", ok)
    assert (await v.add("x.com")).registered
    live = await v.state("x.com")
    assert live.registered and live.live and ("POST", "/v10/projects/prj/domains") in calls

    async def misconfigured(method, path, **kw):
        return _Resp(200, {"misconfigured": True, "verified": True})

    monkeypatch.setattr(v, "_call", misconfigured)
    assert (await v.state("x.com")).live is False

    async def taken(method, path, **kw):
        return _Resp(409, {"error": {"code": "domain_already_in_use"}}) if method == "POST" else _Resp(404)

    monkeypatch.setattr(v, "_call", taken)
    refused = await v.add("x.com")
    assert refused.registered is False and "another" in refused.error

    async def down(method, path, **kw):
        raise TimeoutError

    monkeypatch.setattr(v, "_call", down)
    assert (await v.add("x.com")).error and (await v.state("x.com")).error
    await v.remove("x.com")  # does not raise
    monkeypatch.setattr(s, "VERCEL_API_TOKEN", None)
    assert not VercelDomains().configured()
