from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import clinic_contact_finder as ccf
from app.services.clinic_contact_finder import find_contact, usable_address
from app.services.mt_clinic_sourcing import SearchHit


class FakeSearch:
    name = "fake"

    def __init__(self, hits):
        self.hits = hits
        self.queries: list[str] = []

    async def search(self, query, max_results=5):
        self.queries.append(query)
        return self.hits


async def _mx_ok(domain: str) -> bool:
    return True


async def _mx_no(domain: str) -> bool:
    return False


def _clinic(email=None):
    return SimpleNamespace(name="Acme Dental Clinic", city="Istanbul", country="TR", contact_email=email)


def _hit(content, url="https://www.clinic.example/contact", title="Acme Dental Clinic - Contact"):
    return SearchHit(title=title, url=url, content=content)


async def test_a_person_added_email_is_used_without_searching():
    s = FakeSearch([])
    r = await find_contact(_clinic("owner@clinic.example"), s, _mx_ok)
    assert (r.email, r.source, r.reason) == ("owner@clinic.example", "owner", "owner")
    assert s.queries == []


async def test_finds_a_departmental_email_on_the_clinics_own_page():
    s = FakeSearch([_hit("Write to international@clinic.example today")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email == "international@clinic.example"
    assert r.source == "site" and r.reason == "site"
    assert r.source_url == "https://www.clinic.example/contact"
    assert s.queries == ["Acme Dental Clinic Istanbul Turkey international patients contact email"]


async def test_prefers_international_over_info():
    s = FakeSearch([_hit("info@clinic.example and international@clinic.example")])
    assert (await find_contact(_clinic(), s, _mx_ok)).email == "international@clinic.example"


async def test_accepts_a_subdomain_of_the_pages_registered_domain():
    s = FakeSearch([_hit("info@mail.clinic.example")])
    assert (await find_contact(_clinic(), s, _mx_ok)).email == "info@mail.clinic.example"


async def test_two_part_public_suffix_domains_compare_correctly():
    s = FakeSearch([_hit("info@clinic.co.uk x@other.co.uk", url="https://www.clinic.co.uk/")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email == "info@clinic.co.uk"


async def test_rejects_an_email_on_a_different_domain_than_the_page():
    s = FakeSearch([_hit("Listing contact: x@other.example")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email is None and r.reason == "no_email_on_clinic_site"


@pytest.mark.parametrize("addr", ["a@gmail.com", "noreply@clinic.example", "careers@clinic.example"])
async def test_rejects_free_mail_and_role_addresses(addr):
    s = FakeSearch([_hit(f"contact {addr}")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email is None and r.reason == "no_email_on_clinic_site"


def test_usable_address_rules():
    assert usable_address("info@clinic.example")
    for bad in ("a@yahoo.com", "a@proton.me", "no-reply@clinic.example", "Abuse@clinic.example", "hr@clinic.example", "nonsense"):
        assert not usable_address(bad)


async def test_a_page_without_the_clinic_name_is_not_the_clinics_page():
    s = FakeSearch([_hit("info@clinic.example", title="Best dentists in Turkey")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email is None and r.reason == "no_email_on_clinic_site"


async def test_clinic_name_match_ignores_case_and_punctuation():
    s = FakeSearch([_hit("info@clinic.example", title="ACME  dental, clinic")])
    assert (await find_contact(_clinic(), s, _mx_ok)).email == "info@clinic.example"


async def test_contact_form_only_gives_no_email_on_clinic_site():
    s = FakeSearch([_hit("Use our contact form to reach Acme Dental Clinic")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert (r.email, r.reason) == (None, "no_email_on_clinic_site")


async def test_no_hits():
    r = await find_contact(_clinic(), FakeSearch([]), _mx_ok)
    assert (r.email, r.reason) == (None, "no_hits")


async def test_no_search_provider_gives_not_configured_and_makes_no_calls():
    calls = []

    async def mx(d):
        calls.append(d)
        return True

    r = await find_contact(_clinic(), None, mx)
    assert (r.email, r.reason) == (None, "not_configured")
    assert calls == []


async def test_mx_failure_rejects_the_address():
    s = FakeSearch([_hit("info@clinic.example")])
    r = await find_contact(_clinic(), s, _mx_no)
    assert (r.email, r.reason) == (None, "mx_failed")


async def test_never_builds_an_address_from_a_pattern():
    s = FakeSearch([_hit("Call Acme Dental Clinic on +90 212 555 0100")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email is None


def _patch_http(monkeypatch, handler):
    monkeypatch.setattr(ccf, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_dns_mx_exists_true_when_mx_answer_present(monkeypatch):
    seen = {}

    def handler(req):
        seen["q"] = dict(req.url.params)
        return httpx.Response(200, json={"Answer": [{"type": 15, "data": "10 mx.clinic.example."}]})

    _patch_http(monkeypatch, handler)
    assert await ccf.dns_mx_exists("clinic.example") is True
    assert seen["q"] == {"name": "clinic.example", "type": "MX"}


async def test_dns_mx_exists_false_without_mx_answer(monkeypatch):
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"Status": 3}))
    assert await ccf.dns_mx_exists("clinic.example") is False


async def test_dns_mx_exists_false_on_http_error_and_exceptions(monkeypatch):
    _patch_http(monkeypatch, lambda req: httpx.Response(500))
    assert await ccf.dns_mx_exists("clinic.example") is False

    def boom(req):
        raise httpx.ConnectError("down")

    _patch_http(monkeypatch, boom)
    assert await ccf.dns_mx_exists("clinic.example") is False


@pytest.mark.parametrize("bad", ["noreply+x@clinic.example", "careers+intl@clinic.example", "hr+1@clinic.example", "No-Reply@clinic.example", "NOREPLY@clinic.example"])
def test_plus_addressing_and_case_do_not_bypass_the_blocked_list(bad):
    assert not usable_address(bad)


async def test_registry_shared_suffix_is_not_one_domain():
    s = FakeSearch([_hit("info@other-clinic.or.jp", url="https://clinic-a.or.jp/")])
    assert (await find_contact(_clinic(), s, _mx_ok)).email is None
    s = FakeSearch([_hit("info@clinic-a.or.jp", url="https://clinic-a.or.jp/")])
    assert (await find_contact(_clinic(), s, _mx_ok)).email == "info@clinic-a.or.jp"


async def test_hosting_platform_addresses_are_refused():
    s = FakeSearch([_hit("info@wixsite.com", url="https://clinic.wixsite.com/home")])
    r = await find_contact(_clinic(), s, _mx_ok)
    assert r.email is None and r.reason == "no_email_on_clinic_site"


class RaisingSearch:
    name = "boom"

    async def search(self, query, max_results=5):
        raise RuntimeError("rate limited")


async def test_a_search_failure_returns_search_failed():
    r = await find_contact(_clinic(), RaisingSearch(), _mx_ok)
    assert (r.email, r.source, r.reason) == (None, None, "search_failed")


async def test_null_mx_means_no_mail(monkeypatch):
    _patch_http(monkeypatch, lambda req: httpx.Response(200, json={"Answer": [{"type": 15, "data": "0 ."}]}))
    assert await ccf.dns_mx_exists("clinic.example") is False


async def test_mx_checked_once_per_distinct_domain():
    calls = []

    async def mx(d):
        calls.append(d)
        return False

    s = FakeSearch([_hit("info@clinic.example international@clinic.example")])
    r = await find_contact(_clinic(), s, mx)
    assert r.reason == "mx_failed" and calls == ["clinic.example"]


async def test_clinic_name_must_match_whole_words():
    s = FakeSearch([_hit("info@clinic.example", title="Acme Dental Clinics Group")])
    assert (await find_contact(_clinic(), s, _mx_ok)).email is None
    c = SimpleNamespace(name="Dent", city="Istanbul", country="TR", contact_email=None)
    s = FakeSearch([_hit("info@clinic.example", title="Best dentist")])
    assert (await find_contact(c, s, _mx_ok)).email is None
