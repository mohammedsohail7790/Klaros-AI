"""Hosting a site the business built itself, on its own domain.

Upload: one HTML file, or a zip of a finished static site (HTML, CSS, JS, images, fonts). Nothing is executed on the server and nothing is rewritten: the files are stored and served exactly
as uploaded. Only a fixed list of static file types is accepted (no server-side scripts, no executables), paths are normalised so nothing can escape the site, and size and count limits
stop a zip bomb. A new upload is a DRAFT; publishing it makes it the live site (the previous one is archived, never deleted, so unpublishing/rolling back is possible).

Domains: a business adds a hostname it owns, proves control by adding one DNS TXT record, and only then does Klaros answer for that hostname. Until verified a domain serves nothing.
"""

from __future__ import annotations

import io
import mimetypes
import re
import secrets
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.site_hosting import (
    DOMAIN_PENDING, DOMAIN_VERIFIED, UPLOAD_ARCHIVED, UPLOAD_DRAFT, UPLOAD_PUBLISHED, TenantDomain, UploadedSite, UploadedSiteFile,
)

logger = structlog.get_logger(__name__)

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_UNPACKED_BYTES = 25 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 300
ALLOWED_EXTENSIONS = {
    "html", "htm", "css", "js", "mjs", "json", "txt", "xml", "svg", "png", "jpg", "jpeg", "gif", "webp", "avif", "ico", "woff", "woff2", "ttf", "otf", "map", "webmanifest", "mp4", "webm", "pdf",
}
VERIFY_PREFIX = "_klaros-verify"
# Where a business points its domain once it is verified: the frontend host that answers for custom domains.
DNS_TARGET = {"a_record": "76.76.21.21", "cname": "cname.vercel-dns.com"}
_RESERVED_SUFFIXES = ("meetklaros.com", "onrender.com", "vercel.app", "localhost")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

DnsTxtLookup = Callable[[str], Awaitable[list[str]]]


class SiteUploadError(ValueError):
    """The upload cannot be accepted; the message says why in words the owner can act on."""


class DomainError(ValueError):
    pass


def normalise_hostname(raw: str) -> str:
    host = (raw or "").strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].rstrip(".")
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels) or labels[-1].isdigit():
        raise DomainError("Enter a domain like aesthetictourism.com or www.aesthetictourism.com.")
    if any(host == s or host.endswith("." + s) for s in _RESERVED_SUFFIXES):
        raise DomainError("That domain belongs to the platform and cannot be attached.")
    return host


def _clean_path(name: str) -> str | None:
    path = name.replace("\\", "/").lstrip("/")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts:
        return None
    if any(p == ".." for p in parts):
        raise SiteUploadError("The zip contains a path that leaves the site folder, so it was rejected.")
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return None  # hidden files and macOS metadata are skipped, not served
    return "/".join(parts)


def _content_type(path: str) -> str:
    ct = mimetypes.guess_type(path)[0]
    if path.endswith((".js", ".mjs")):
        return "text/javascript; charset=utf-8"
    if path.endswith(".webmanifest"):
        return "application/manifest+json"
    if ct and (ct.startswith("text/") or ct in ("application/json", "image/svg+xml")) and "charset" not in ct and not ct.startswith("image/"):
        return ct + "; charset=utf-8"
    return ct or "application/octet-stream"


def unpack(filename: str, data: bytes) -> dict[str, bytes]:
    """The files of an upload as {path: bytes}. Raises SiteUploadError with a plain reason."""
    if not data:
        raise SiteUploadError("The file is empty.")
    lower = (filename or "").lower()
    if lower.endswith((".html", ".htm")):
        if len(data) > MAX_FILE_BYTES:
            raise SiteUploadError("That HTML file is larger than 8 MB.")
        return {"index.html": data}
    if not lower.endswith(".zip"):
        raise SiteUploadError("Upload an .html file or a .zip of your site.")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SiteUploadError("That zip is larger than 10 MB. Compress your images and try again.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SiteUploadError("That file is not a valid zip.") from exc
    entries = [i for i in archive.infolist() if not i.is_dir()]
    if len(entries) > MAX_FILES * 3:
        raise SiteUploadError(f"The zip has too many files (the limit is {MAX_FILES}).")
    if sum(i.file_size for i in entries) > MAX_UNPACKED_BYTES:
        raise SiteUploadError("The zip is larger than 25 MB once unpacked.")
    files: dict[str, bytes] = {}
    for info in entries:
        path = _clean_path(info.filename)
        if path is None:
            continue
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise SiteUploadError(f"“{path}” is not a type that can be hosted (only static site files are accepted: HTML, CSS, JavaScript, images, fonts).")
        if info.file_size > MAX_FILE_BYTES:
            raise SiteUploadError(f"“{path}” is larger than 8 MB.")
        with archive.open(info) as fh:
            body = fh.read(MAX_FILE_BYTES + 1)
        if len(body) > MAX_FILE_BYTES:
            raise SiteUploadError(f"“{path}” is larger than 8 MB.")
        files[path] = body
    if not files:
        raise SiteUploadError("The zip has no site files in it.")
    tops = {p.split("/", 1)[0] for p in files}
    if len(tops) == 1 and all("/" in p for p in files) and "index.html" not in files:  # a zip made by zipping the folder: drop the one wrapping folder
        files = {p.split("/", 1)[1]: b for p, b in files.items()}
    if len(files) > MAX_FILES:
        raise SiteUploadError(f"The site has too many files (the limit is {MAX_FILES}).")
    if "index.html" not in files:
        raise SiteUploadError("The site needs an index.html at its top level: that is the home page.")
    return files


async def doh_txt_lookup(name: str) -> list[str]:
    """TXT records for `name`, read through a public DNS-over-HTTPS resolver."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get("https://cloudflare-dns.com/dns-query", params={"name": name, "type": "TXT"}, headers={"accept": "application/dns-json"})
        r.raise_for_status()
    return [str(a.get("data", "")).strip('"') for a in r.json().get("Answer", []) if a.get("type") == 16]


def site_summary(s: UploadedSite) -> dict:
    return {
        "id": str(s.id), "version": s.version, "status": s.status, "source_name": s.source_name, "file_count": s.file_count, "total_bytes": s.total_bytes,
        "created_at": s.created_at.isoformat() if s.created_at else None, "published_at": s.published_at.isoformat() if s.published_at else None,
    }


def domain_summary(d: TenantDomain) -> dict:
    return {
        "id": str(d.id), "hostname": d.hostname, "status": d.status, "verified_at": d.verified_at.isoformat() if d.verified_at else None,
        "txt_name": f"{VERIFY_PREFIX}.{d.hostname}", "txt_value": d.verification_token, "dns_target": DNS_TARGET,
    }


class SiteHostingService:
    def __init__(self, session_factory: async_sessionmaker, dns_lookup: DnsTxtLookup = doh_txt_lookup) -> None:
        self._sf = session_factory
        self._dns = dns_lookup

    # ---- uploaded sites -------------------------------------------------------------------------------------------------------------------------------

    async def upload(self, tenant_id: uuid.UUID, filename: str, data: bytes, *, user_id: uuid.UUID | None) -> UploadedSite:
        files = unpack(filename, data)
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            last = (await session.execute(select(func.max(UploadedSite.version)).where(UploadedSite.tenant_id == tenant_id))).scalar() or 0
            site = UploadedSite(tenant_id=tenant_id, version=last + 1, status=UPLOAD_DRAFT, source_name=(filename or "upload")[:255], file_count=len(files),
                                total_bytes=sum(len(b) for b in files.values()), created_by=user_id)
            session.add(site)
            await session.flush()
            for path, body in sorted(files.items()):
                session.add(UploadedSiteFile(tenant_id=tenant_id, site_id=site.id, path=path, content_type=_content_type(path), content=body))
            await session.commit()
            await session.refresh(site)
            return site

    async def list_sites(self, tenant_id: uuid.UUID) -> list[UploadedSite]:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            return list((await session.execute(select(UploadedSite).where(UploadedSite.tenant_id == tenant_id).order_by(UploadedSite.version.desc()))).scalars())

    async def publish(self, tenant_id: uuid.UUID, site_id: uuid.UUID) -> UploadedSite | None:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            site = (await session.execute(select(UploadedSite).where(UploadedSite.tenant_id == tenant_id, UploadedSite.id == site_id))).scalar_one_or_none()
            if site is None:
                return None
            await session.execute(update(UploadedSite).where(UploadedSite.tenant_id == tenant_id, UploadedSite.status == UPLOAD_PUBLISHED, UploadedSite.id != site_id).values(status=UPLOAD_ARCHIVED))
            site.status, site.published_at = UPLOAD_PUBLISHED, datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(site)
            return site

    async def unpublish(self, tenant_id: uuid.UUID) -> int:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            res = await session.execute(update(UploadedSite).where(UploadedSite.tenant_id == tenant_id, UploadedSite.status == UPLOAD_PUBLISHED).values(status=UPLOAD_ARCHIVED))
            await session.commit()
            return res.rowcount or 0

    async def published_file(self, tenant_id: uuid.UUID, path: str) -> tuple[bytes, str] | None:
        """One file of the tenant's PUBLISHED uploaded site, or None. A draft is never reachable here."""
        wanted = (path or "").strip("/") or "index.html"
        candidates = [wanted] if "." in wanted.rsplit("/", 1)[-1] else [wanted + "/index.html", wanted + ".html", wanted]
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            site_id = (await session.execute(select(UploadedSite.id).where(UploadedSite.tenant_id == tenant_id, UploadedSite.status == UPLOAD_PUBLISHED))).scalar_one_or_none()
            if site_id is None:
                return None
            for candidate in candidates:
                row = (await session.execute(select(UploadedSiteFile).where(UploadedSiteFile.tenant_id == tenant_id, UploadedSiteFile.site_id == site_id, UploadedSiteFile.path == candidate))).scalar_one_or_none()
                if row is not None:
                    return row.content, row.content_type
        return None

    async def has_published_upload(self, tenant_id: uuid.UUID) -> bool:
        async with self._sf() as session:
            await set_tenant_context(session, tenant_id)
            return (await session.execute(select(UploadedSite.id).where(UploadedSite.tenant_id == tenant_id, UploadedSite.status == UPLOAD_PUBLISHED))).first() is not None

    # ---- domains --------------------------------------------------------------------------------------------------------------------------------------

    async def add_domain(self, tenant_id: uuid.UUID, raw_hostname: str) -> TenantDomain:
        host = normalise_hostname(raw_hostname)
        async with self._sf() as session:
            existing = (await session.execute(select(TenantDomain).where(TenantDomain.hostname == host))).scalar_one_or_none()
            if existing is not None:
                if existing.tenant_id == tenant_id:
                    return existing
                raise DomainError("That domain is already connected to another business.")
            domain = TenantDomain(tenant_id=tenant_id, hostname=host, verification_token="klaros-" + secrets.token_hex(12), status=DOMAIN_PENDING)
            session.add(domain)
            await session.commit()
            await session.refresh(domain)
            return domain

    async def list_domains(self, tenant_id: uuid.UUID) -> list[TenantDomain]:
        async with self._sf() as session:
            return list((await session.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id).order_by(TenantDomain.created_at))).scalars())

    async def verify_domain(self, tenant_id: uuid.UUID, domain_id: uuid.UUID) -> tuple[TenantDomain | None, bool]:
        """(domain, verified now). A lookup failure leaves the domain pending; it never marks it verified."""
        async with self._sf() as session:
            domain = (await session.execute(select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == tenant_id))).scalar_one_or_none()
            if domain is None:
                return None, False
            if domain.status == DOMAIN_VERIFIED:
                return domain, True
            try:
                records = await self._dns(f"{VERIFY_PREFIX}.{domain.hostname}")
            except Exception as exc:  # network or resolver trouble must not look like "verified" or crash the page
                logger.warning("domain_dns_lookup_failed", hostname=domain.hostname, error=type(exc).__name__)
                return domain, False
            if domain.verification_token not in records:
                return domain, False
            domain.status, domain.verified_at = DOMAIN_VERIFIED, datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(domain)
            return domain, True

    async def remove_domain(self, tenant_id: uuid.UUID, domain_id: uuid.UUID) -> bool:
        async with self._sf() as session:
            domain = (await session.execute(select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == tenant_id))).scalar_one_or_none()
            if domain is None:
                return False
            await session.delete(domain)
            await session.commit()
            return True

    async def tenant_for_host(self, hostname: str) -> uuid.UUID | None:
        """The tenant that owns a VERIFIED domain. A pending domain answers nothing."""
        try:
            host = normalise_hostname(hostname)
        except DomainError:
            return None
        async with self._sf() as session:
            return (await session.execute(select(TenantDomain.tenant_id).where(TenantDomain.hostname == host, TenantDomain.status == DOMAIN_VERIFIED))).scalar_one_or_none()
