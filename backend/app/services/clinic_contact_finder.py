"""Find a clinic's own business email from text a web search returned.

Pure lookup: nothing is sent to anyone and nothing is written to the database. An address is
returned only if it appears in the text of a page that is the clinic's own (its name is in the
page) and its domain belongs to that page's registered domain. Addresses are never guessed or
built from a pattern.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from app.models.medical_tourism import Provider
from app.services.mt_clinic_sourcing import COUNTRY_NAMES, _EMAIL, SearchProvider

FREE_MAIL_DOMAINS = frozenset({"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "proton.me", "protonmail.com", "aol.com"})
BLOCKED_LOCAL_PARTS = frozenset({"noreply", "no-reply", "donotreply", "privacy", "careers", "jobs", "hr", "press", "media", "legal", "abuse", "webmaster"})
LOCAL_PART_RANK = ("international", "intl", "patient", "appointment", "inquiry", "contact", "info")
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go", "in", "med", "gen", "k12", "nhs"})
PLATFORM_DOMAINS = frozenset({"wixsite.com", "wordpress.com", "blogspot.com", "squarespace.com", "weebly.com", "github.io", "netlify.app", "vercel.app", "business.site", "godaddysites.com", "webflow.io", "linktr.ee", "facebook.com", "instagram.com", "linkedin.com"})


@dataclass(frozen=True)
class ContactResult:
    # reason: owner | site | not_configured | no_hits | no_email_on_clinic_site | mx_failed | search_failed
    email: str | None
    source_url: str | None
    source: Literal["owner", "site"] | None
    reason: str


def usable_address(email: str) -> bool:
    local, sep, domain = email.strip().lower().partition("@")
    if not sep or not local or not domain or "@" in domain:
        return False
    base = local.split("+", 1)[0]
    return domain not in FREE_MAIL_DOMAINS and base not in BLOCKED_LOCAL_PARTS and _registered_domain(domain) not in PLATFORM_DOMAINS


def _registered_domain(host: str) -> str:
    labels = host.lower().strip(".").removeprefix("www.").split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _has_name(name: str, text: str) -> bool:
    n = _normalise(name)
    return bool(n) and f" {n} " in f" {_normalise(text)} "


def _rank(email: str) -> int:
    local = email.split("@", 1)[0].lower()
    return LOCAL_PART_RANK.index(local) if local in LOCAL_PART_RANK else len(LOCAL_PART_RANK)


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=8.0)


async def dns_mx_exists(domain: str) -> bool:
    """True when `domain` has an MX record, read through Cloudflare DNS-over-HTTPS. False on any error."""
    try:
        async with _http_client() as client:
            r = await client.get("https://cloudflare-dns.com/dns-query", params={"name": domain, "type": "MX"}, headers={"accept": "application/dns-json"})
            r.raise_for_status()
            return any(a.get("type") == 15 and str(a.get("data", "")).strip() != "0 ." for a in r.json().get("Answer", []))
    except Exception:
        return False


async def find_contact(provider: Provider, search: SearchProvider | None, mx_exists: Callable[[str], Awaitable[bool]] = dns_mx_exists) -> ContactResult:
    if (provider.contact_email or "").strip():
        return ContactResult(provider.contact_email.strip(), None, "owner", "owner")
    if search is None:
        return ContactResult(None, None, None, "not_configured")
    country = COUNTRY_NAMES.get(provider.country, provider.country)
    try:
        hits = await search.search(f"{provider.name} {provider.city} {country} international patients contact email")
    except Exception:
        return ContactResult(None, None, None, "search_failed")
    if not hits:
        return ContactResult(None, None, None, "no_hits")
    candidates: list[tuple[int, int, str, str]] = []
    for hit in hits:
        if not _has_name(provider.name, f"{hit.title} {hit.content}"):
            continue
        host = urlparse(hit.url).hostname or ""
        if not host:
            continue
        page_domain = _registered_domain(host)
        for found in _EMAIL.findall(hit.content or ""):
            email = found.strip(".").lower()
            if usable_address(email) and _registered_domain(email.split("@", 1)[1]) == page_domain:
                candidates.append((_rank(email), len(candidates), email, hit.url))
    if not candidates:
        return ContactResult(None, None, None, "no_email_on_clinic_site")
    checked: dict[str, bool] = {}
    for _, _, email, url in sorted(candidates):
        domain = email.split("@", 1)[1]
        if domain not in checked:
            checked[domain] = await mx_exists(domain)
        if checked[domain]:
            return ContactResult(email, url, "site", "site")
    return ContactResult(None, None, None, "mx_failed")
