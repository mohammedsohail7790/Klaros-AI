"""Static build checks on a generated project. Nothing here executes the project.

The report is the evidence behind "ready": a project is only called ready when `report.ok` is true, and every failed
check names the file and the problem so a repair step (or a person) can act on it. Errors block a version from being
saved; warnings are shown but do not.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from app.builder.design import contrast_ratio
from app.builder.spec import ProjectSpec
from app.builder.generator import route_file
from app.builder.workspace import Workspace

_ALLOWED_SCRIPT = re.compile(r"^(?:\.{1,2}/)*assets/[A-Za-z0-9._\-]+\.js$")
_SECRET = re.compile(r"(sk_live_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abp]-[A-Za-z0-9-]{10,}|whsec_[A-Za-z0-9]{10,})")
_DANGEROUS_JS = re.compile(r"\b(eval|Function)\s*\(|document\.write\s*\(|\.innerHTML\s*=|new\s+Function\b|importScripts\s*\(")


@dataclass
class Issue:
    severity: str  # error | warning
    code: str
    path: str
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "path": self.path, "message": self.message}


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, code: str, path: str, message: str) -> None:
        self.issues.append(Issue(severity, code, path, message))

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": len(self.errors), "warnings": len(self.warnings), "checks": self.checks, "stats": self.stats, "issues": [i.as_dict() for i in self.issues[:200]]}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.h1 = 0
        self.lang = ""
        self.viewport = False
        self.links: list[str] = []
        self.assets: list[tuple[str, str]] = []  # (tag, url)
        self.scripts: list[tuple[str, bool]] = []  # (src, inline)
        self.images_without_alt: int = 0
        self.inputs: list[dict] = []
        self.labels_for: set[str] = set()
        self.ids: set[str] = set()
        self.main = 0
        self.headings: list[int] = []
        self.inline_handlers = 0
        self.depth_stack: list[str] = []
        self.errors: list[str] = []
        self.has_description = False

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang", "") or ""
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self.h1 += 1
        if tag in ("h1", "h2", "h3", "h4"):
            self.headings.append(int(tag[1]))
        if tag == "main":
            self.main += 1
        if tag == "meta":
            if a.get("name") == "viewport":
                self.viewport = True
            if a.get("name") == "description" and a.get("content"):
                self.has_description = True
        if tag == "a" and a.get("href") is not None:
            self.links.append(a["href"])
        if tag == "link" and a.get("href"):
            self.assets.append(("link", a["href"]))
        if tag == "img":
            if a.get("src"):
                self.assets.append(("img", a["src"]))
            if "alt" not in a:
                self.images_without_alt += 1
        if tag == "script":
            self.scripts.append((a.get("src") or "", not a.get("src")))
        if tag in ("input", "textarea", "select"):
            self.inputs.append(a)
        if tag == "label" and a.get("for"):
            self.labels_for.add(a["for"])
        if a.get("id"):
            self.ids.add(a["id"])
        if any(k.lower().startswith("on") for k in a):
            self.inline_handlers += 1
        if tag not in self._VOID:
            self.depth_stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        if not self.depth_stack:
            self.errors.append(f"stray </{tag}>")
            return
        if self.depth_stack[-1] == tag:
            self.depth_stack.pop()
        elif tag in self.depth_stack:
            # tolerate omitted optional end tags only for p/li; otherwise it is a real nesting error
            while self.depth_stack and self.depth_stack[-1] != tag:
                top = self.depth_stack.pop()
                if top not in ("p", "li", "dt", "dd", "tr", "td", "th"):
                    self.errors.append(f"<{top}> closed implicitly by </{tag}>")
            if self.depth_stack:
                self.depth_stack.pop()
        else:
            self.errors.append(f"stray </{tag}>")
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _resolve(from_file: str, href: str) -> str | None:
    """Resolve a relative link to a project path, or None if it is external / not a project link."""
    if not href or href.startswith(("#", "mailto:", "tel:")):
        return None
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return None
    target = unquote(parts.path)
    if not target:
        return None
    base = posixpath.dirname(from_file)
    joined = posixpath.normpath(posixpath.join(base, target))
    if joined.startswith(".."):
        return "../"
    if target.endswith("/") or "." not in posixpath.basename(joined):
        joined = posixpath.join(joined, "index.html")
    return joined.lstrip("./") if joined != "." else "index.html"


def validate_project(ws: Workspace, spec: ProjectSpec | None = None, *, check_js_syntax: bool = True) -> Report:
    rep = Report()
    files = ws.snapshot()
    pages = {p: c for p, c in files.items() if p.endswith(".html")}
    rep.stats = {"files": len(files), "pages": len(pages), "bytes": ws.total_bytes()}

    # 1. required files and every planned route has a file
    rep.checks["entry_point"] = "index.html" in files
    if not rep.checks["entry_point"]:
        rep.add("error", "missing_index", "index.html", "the project has no home page")
    for need in ("assets/styles.css", "assets/app.js"):
        if need not in files:
            rep.add("error", "missing_asset", need, f"{need} is missing")
    routes_ok = True
    if spec is not None:
        for r in spec.routes:
            if r.is_detail:
                ent = spec.entity(r.entity or "")
                expected = [route_file(r.path, str(rec["slug"])) for rec in (ent.records if ent else [])]
            else:
                expected = [route_file(r.path)]
            for f in expected:
                if f not in files:
                    routes_ok = False
                    rep.add("error", "route_missing", f, f"route {r.path} has no generated page ({f})")
        rep.stats["routes"] = len(spec.routes)
    rep.checks["routes_generated"] = routes_ok

    # 2. per-page structure, accessibility, links, assets, scripts
    links_ok = assets_ok = a11y_ok = scripts_ok = html_ok = True
    for path, html in pages.items():
        p = _Page()
        try:
            p.feed(html)
            p.close()
        except Exception as exc:  # noqa: BLE001
            rep.add("error", "html_parse", path, f"could not parse the page: {exc}")
            html_ok = False
            continue
        for msg in p.errors[:3]:
            html_ok = False
            rep.add("warning", "html_nesting", path, msg)
        if p.depth_stack:
            html_ok = False
            rep.add("warning", "html_unclosed", path, f"unclosed elements: {', '.join(p.depth_stack[:4])}")
        if not p.lang:
            a11y_ok = False
            rep.add("error", "no_lang", path, "the page has no lang attribute")
        if not p.title.strip():
            a11y_ok = False
            rep.add("error", "no_title", path, "the page has no title")
        if not p.viewport:
            a11y_ok = False
            rep.add("error", "no_viewport", path, "the page has no viewport meta tag, so it will not be responsive on phones")
        if p.main != 1:
            a11y_ok = False
            rep.add("error", "main_landmark", path, f"expected one <main> landmark, found {p.main}")
        if p.h1 != 1:
            a11y_ok = False
            rep.add("error" if p.h1 == 0 else "warning", "h1_count", path, f"expected exactly one <h1>, found {p.h1}")
        if p.images_without_alt:
            a11y_ok = False
            rep.add("error", "img_alt", path, f"{p.images_without_alt} image(s) have no alt attribute")
        for a in p.inputs:
            t = (a.get("type") or "text").lower()
            if t in ("hidden", "submit", "button") or a.get("aria-hidden") == "true":
                continue
            if not (a.get("id") in p.labels_for or a.get("aria-label")):
                a11y_ok = False
                rep.add("error", "input_label", path, f"form field {a.get('name') or a.get('id')!r} has no label")
        if p.headings and p.headings[0] != 1 and 1 in p.headings:
            rep.add("warning", "heading_order", path, "the first heading is not the h1")
        if p.inline_handlers:
            scripts_ok = False
            rep.add("error", "inline_handler", path, "inline event handlers (onclick=...) are not allowed; use assets/app.js")
        for src, inline in p.scripts:
            if inline:
                scripts_ok = False
                rep.add("error", "inline_script", path, "inline <script> blocks are not allowed")
            elif not _ALLOWED_SCRIPT.match(src):
                scripts_ok = False
                rep.add("error", "external_script", path, f"script {src!r} is not a local file in assets/")
        for href in p.links:
            target = _resolve(path, href)
            if href.startswith("javascript:"):
                links_ok = False
                rep.add("error", "unsafe_link", path, "javascript: links are not allowed")
            elif target is not None and target not in files:
                links_ok = False
                rep.add("error", "broken_link", path, f"link {href!r} does not lead to a page in this project")
        for tag, url in p.assets:
            if url.startswith("https://"):
                continue
            if url.startswith(("http:", "//", "data:")):
                assets_ok = False
                rep.add("error", "insecure_asset", path, f"{url[:60]!r} must be a project file or an https:// address")
                continue
            target = _resolve(path, url)
            if target is not None and target not in files:
                assets_ok = False
                rep.add("error", "missing_asset", path, f"{tag} {url!r} does not exist in the project")
        for fragment in re.findall(r'href="#([^"]+)"', html):
            if fragment not in p.ids:
                links_ok = False
                rep.add("warning", "fragment_missing", path, f"link to #{fragment} has no matching element")
        if not p.has_description:
            rep.add("warning", "no_description", path, "the page has no meta description")
    rep.checks.update(html_wellformed=html_ok, links_resolve=links_ok, assets_exist=assets_ok, accessibility_basics=a11y_ok, no_inline_or_external_scripts=scripts_ok)

    # 3. javascript: dangerous constructs, secrets, syntax
    js_ok = secrets_ok = True
    for path, content in files.items():
        if _SECRET.search(content):
            secrets_ok = False
            rep.add("error", "secret_in_file", path, "the file contains something that looks like a secret or API key")
        if path.endswith(".js"):
            if _DANGEROUS_JS.search(content):
                js_ok = False
                rep.add("error", "dangerous_js", path, "uses eval, Function(), document.write or innerHTML assignment")
            if check_js_syntax and shutil.which("node"):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
                    tmp.write(content)
                try:
                    r = subprocess.run(["node", "--check", tmp.name], capture_output=True, text=True, timeout=10)  # noqa: S603,S607 - fixed argv, our own temp file
                    if r.returncode != 0:
                        js_ok = False
                        rep.add("error", "js_syntax", path, (r.stderr.strip().splitlines() or ["syntax error"])[-1][:200])
                except (subprocess.TimeoutExpired, OSError):
                    pass
                finally:
                    import os
                    os.unlink(tmp.name)
        if path.endswith(".json"):
            import json

            try:
                json.loads(content)
            except ValueError as exc:
                js_ok = False
                rep.add("error", "json_invalid", path, f"invalid JSON: {exc}")
        if path.endswith(".css") and content.count("{") != content.count("}"):
            js_ok = False
            rep.add("error", "css_unbalanced", path, "unbalanced braces in the stylesheet")
    rep.checks.update(scripts_safe=js_ok, no_secrets=secrets_ok)

    # 4. design contrast on the resolved palette (the generator corrects it; this confirms)
    if spec is not None:
        from app.builder.design import resolve_design

        pal = resolve_design(spec.design).palette
        good = contrast_ratio(pal.text, pal.background) >= 7 and contrast_ratio(pal.muted, pal.background) >= 4.5
        rep.checks["colour_contrast"] = good
        if not good:
            rep.add("error", "contrast", "assets/styles.css", "text colours do not reach readable contrast on the background")
        if any(s.example for r in spec.routes for s in r.sections):
            n = sum(1 for r in spec.routes for s in r.sections if s.example)
            rep.add("warning", "example_content", "", f"{n} section(s) contain example content that should be reviewed before publishing")
        rep.stats["example_sections"] = sum(1 for r in spec.routes for s in r.sections if s.example)
    return rep
