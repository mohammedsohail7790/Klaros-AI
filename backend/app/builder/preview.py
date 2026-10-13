"""Serving a project's files safely.

Two read paths, both static and both unable to run anything on the server:
  * preview  - the owner's draft. Reached with a short-lived signed token in the URL (an iframe cannot send an
               Authorization header), shown in a sandboxed iframe whose document is an opaque origin: even a hostile
               generated script cannot touch Klaros' own storage, cookies or API session.
  * published - the version the owner chose to make live, addressed by tenant and project slug.
Both send a Content-Security-Policy that allows only same-project scripts, styles and https images.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import posixpath
import time
import uuid

from app.builder.workspace import WorkspaceError, normalise_path
from app.core.config import get_settings

_MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".xml": "application/xml; charset=utf-8", ".webmanifest": "application/manifest+json",
}


def _key() -> bytes:
    return hashlib.sha256(("klaros-builder-preview:" + get_settings().JWT_SECRET).encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_preview(tenant_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID, *, ttl: int | None = None) -> tuple[str, int]:
    exp = int(time.time()) + (ttl or get_settings().BUILDER_PREVIEW_TOKEN_TTL_SECONDS)
    body = _b64(json.dumps({"t": str(tenant_id), "p": str(project_id), "v": str(version_id), "e": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", exp


def verify_preview(token: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID] | None:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest()), sig):
            return None
        data = json.loads(_unb64(body))
        if int(data["e"]) < time.time():
            return None
        return uuid.UUID(data["t"]), uuid.UUID(data["p"]), uuid.UUID(data["v"])
    except (ValueError, KeyError, TypeError):
        return None


def resolve_request_path(raw: str) -> str | None:
    """URL path -> project file path, or None. Never a path outside the project."""
    p = (raw or "").lstrip("/")
    if p == "" or p.endswith("/"):
        p += "index.html"
    elif "." not in posixpath.basename(p):
        p += "/index.html"
    try:
        return normalise_path(p)
    except WorkspaceError:
        return None


def mime_for(path: str) -> str:
    return _MIME.get(posixpath.splitext(path)[1].lower(), "application/octet-stream")


def frame_ancestors() -> str:
    origins = [o for o in get_settings().CORS_ORIGINS if o.startswith(("http://", "https://"))]
    return " ".join(origins) if origins else "'none'"


def security_headers(*, preview: bool, enquiry_origin: str | None = None) -> dict[str, str]:
    connect = "'self'" + (f" {enquiry_origin}" if enquiry_origin else "")
    csp = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; font-src 'self'; "
        f"connect-src {connect}; form-action 'none'; base-uri 'none'; object-src 'none'; frame-ancestors {frame_ancestors() if preview else 'none'}"
    )
    if preview:
        csp += "; sandbox allow-scripts allow-forms"  # no allow-same-origin: an opaque origin
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Access-Control-Allow-Origin": "*",  # these are read-only static files; the sandboxed preview document has an opaque origin and needs it to load its JSON data
        "Cache-Control": "no-store" if preview else "public, max-age=60",
    }


def inject_enquiry(html: str, endpoint: str | None) -> str:
    """Point the generated forms at the lead endpoint (published sites only; in preview they stay disconnected on purpose)."""
    if not endpoint:
        return html
    from html import escape

    return html.replace('<meta name="klaros-enquiry" content="">', f'<meta name="klaros-enquiry" content="{escape(endpoint, quote=True)}">')
