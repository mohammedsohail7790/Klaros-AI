"""Importing an existing static site (HTML, CSS, JS, data, SVG) into the builder.

The builder's rules do not bend for imported files: they go through the same path checks and the same validator as generated
ones, so an import is accepted only if it is safe to serve (no inline scripts, no `eval`/`innerHTML =`, no external scripts or
stylesheets, no secrets...). What this module adds is convenience, not leniency: a flat upload (`index.html`, `styles.css`,
`script.js`) is arranged into the layout the builder expects, and the references are rewritten to match.
"""

from __future__ import annotations

import posixpath
import re

from app.builder.workspace import MAX_FILES, Workspace, WorkspaceError

_TEXT_EXT = {".html", ".css", ".js", ".json", ".svg", ".txt", ".md", ".xml", ".webmanifest"}


def normalise_import(files: dict[str, str]) -> dict[str, str]:
    """Strip a common top-level folder, and move a flat styles.css / script.js into assets/ (rewriting the pages that reference them)."""
    cleaned: dict[str, str] = {}
    for raw, content in files.items():
        path = raw.replace("\\", "/")
        if ".." in path.split("/") or path.startswith("/"):
            raise WorkspaceError(f"'{raw}' is not a safe file path.")
        path = path.lstrip("./")
        cleaned[path] = content
    tops = {p.split("/", 1)[0] for p in cleaned if "/" in p}
    if cleaned and len(tops) == 1 and all("/" in p for p in cleaned):
        prefix = next(iter(tops)) + "/"
        cleaned = {p[len(prefix):]: c for p, c in cleaned.items()}
    moves: dict[str, str] = {}
    if "assets/styles.css" not in cleaned and "styles.css" in cleaned:
        moves["styles.css"] = "assets/styles.css"
    if "assets/app.js" not in cleaned and "script.js" in cleaned:
        moves["script.js"] = "assets/app.js"
    for src, dst in moves.items():
        cleaned[dst] = cleaned.pop(src)
    if moves:
        for p in [p for p in cleaned if p.endswith(".html")]:
            base = posixpath.dirname(p)
            html = cleaned[p]
            for src, dst in moves.items():
                new = posixpath.relpath(dst, base or ".")
                html = re.sub(rf'''((?:href|src)=["'])(?:\./)?{re.escape(src)}(["'])''', lambda m, new=new: f"{m.group(1)}{new}{m.group(2)}", html)
            cleaned[p] = html
    return cleaned


def build_workspace(files: dict[str, str]) -> Workspace:
    if not files:
        raise WorkspaceError("No files were provided.")
    if len(files) > MAX_FILES:
        raise WorkspaceError(f"An import may have at most {MAX_FILES} files.")
    ws = Workspace()
    for path, content in normalise_import(files).items():
        ws.write(path, content)  # path safety, type allow-list and size limits live in Workspace
    if "index.html" not in ws.paths():
        raise WorkspaceError("An import needs an index.html at its top level.")
    return ws


def routes_of(ws: Workspace) -> list[dict]:
    """What pages the imported site has (index.html -> '/', pricing/index.html -> '/pricing'); shown in the preview's page picker."""
    out = []
    for p in ws.paths():
        if p == "404.html" or not p.endswith("index.html"):
            continue
        d = posixpath.dirname(p)
        title = re.search(r"<title>(.*?)</title>", ws.read(p), re.S | re.I)
        out.append({"path": "/" + d if d else "/", "title": (title.group(1).strip()[:60] if title else (d or "Home")), "nav": True, "detail": False})
    return out or [{"path": "/", "title": "Home", "nav": True, "detail": False}]
