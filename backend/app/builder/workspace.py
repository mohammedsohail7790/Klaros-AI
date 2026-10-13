"""A project's files as a bounded, path-safe virtual filesystem.

Nothing here touches the host filesystem: a workspace is a mapping of normalised relative paths to text. That makes
"directory traversal" and "edits outside the project" impossible by construction rather than by careful joining, and
it is why generated code can be stored, versioned and served without ever being written to disk on the API host.

Every mutation (create, patch, delete, rename) goes through this class, whether it comes from the generator, the
repair step, the model's edit operations or the in-app code editor. One set of rules, one set of tests.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass

MAX_FILES = 400
MAX_FILE_BYTES = 300_000
MAX_TOTAL_BYTES = 3_000_000
ALLOWED_EXTENSIONS = frozenset({".html", ".css", ".js", ".json", ".svg", ".txt", ".md", ".xml", ".webmanifest"})
_SEGMENT = re.compile(r"^[A-Za-z0-9._\-\[\]]+$")


class WorkspaceError(ValueError):
    """A rejected file operation. The message is safe to show to the person who asked for it."""


def normalise_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise WorkspaceError("invalid file path")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise WorkspaceError("absolute paths are not allowed")
    norm = posixpath.normpath(path)
    if norm in (".", "") or norm.startswith("..") or "/../" in f"/{norm}/":
        raise WorkspaceError("paths must stay inside the project")
    parts = norm.split("/")
    if len(norm) > 200 or len(parts) > 8:
        raise WorkspaceError("path too long or too deep")
    for seg in parts:
        if seg in (".", "..") or seg.startswith(".") or not _SEGMENT.match(seg):
            raise WorkspaceError(f"unsupported file name: {seg!r}")
    ext = posixpath.splitext(norm)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise WorkspaceError(f"files of type {ext or '(none)'} are not allowed in a project")
    return norm


def sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Change:
    op: str  # created | modified | deleted | renamed
    path: str
    detail: str = ""


class Workspace:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files: dict[str, str] = {}
        self.changes: list[Change] = []
        for p, c in (files or {}).items():
            self._set(p, c, record=False)

    # ---- read ----
    def paths(self) -> list[str]:
        return sorted(self._files)

    def exists(self, path: str) -> bool:
        return normalise_path(path) in self._files

    def read(self, path: str) -> str:
        p = normalise_path(path)
        try:
            return self._files[p]
        except KeyError:
            raise WorkspaceError(f"{p} does not exist") from None

    def snapshot(self) -> dict[str, str]:
        return dict(self._files)

    def manifest(self) -> list[dict[str, str | int]]:
        return [{"path": p, "sha": sha(c), "size": len(c.encode("utf-8"))} for p, c in sorted(self._files.items())]

    def total_bytes(self) -> int:
        return sum(len(c.encode("utf-8")) for c in self._files.values())

    def search(self, needle: str, *, regex: bool = False, limit: int = 50) -> list[tuple[str, int, str]]:
        """(path, line number, line) for matching lines. Bounded so a model-supplied pattern cannot be used to burn CPU."""
        if not needle or len(needle) > 200:
            raise WorkspaceError("search text must be 1-200 characters")
        pattern = re.compile(needle if regex else re.escape(needle), re.I)
        hits: list[tuple[str, int, str]] = []
        for p, c in sorted(self._files.items()):
            for i, line in enumerate(c.splitlines(), 1):
                if len(line) > 2000:
                    continue
                if pattern.search(line):
                    hits.append((p, i, line.strip()[:200]))
                    if len(hits) >= limit:
                        return hits
        return hits

    # ---- write ----
    def _set(self, path: str, content: str, *, record: bool = True) -> None:
        p = normalise_path(path)
        if not isinstance(content, str):
            raise WorkspaceError("file content must be text")
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise WorkspaceError(f"{p} is larger than {MAX_FILE_BYTES // 1000} KB")
        existed = p in self._files
        if not existed and len(self._files) >= MAX_FILES:
            raise WorkspaceError(f"a project may have at most {MAX_FILES} files")
        projected = self.total_bytes() - (len(self._files[p].encode("utf-8")) if existed else 0) + size
        if projected > MAX_TOTAL_BYTES:
            raise WorkspaceError("the project would exceed its total size limit")
        if existed and self._files[p] == content:
            return
        self._files[p] = content
        if record:
            self.changes.append(Change("modified" if existed else "created", p))

    def write(self, path: str, content: str) -> None:
        self._set(path, content)

    def patch(self, path: str, find: str, replace: str) -> None:
        """Replace exactly one occurrence. Zero or several matches is an error, so a vague edit can never silently rewrite the wrong place."""
        p = normalise_path(path)
        text = self.read(p)
        if not find:
            raise WorkspaceError("the text to replace cannot be empty")
        n = text.count(find)
        if n == 0:
            raise WorkspaceError(f"the text to replace was not found in {p}")
        if n > 1:
            raise WorkspaceError(f"the text to replace appears {n} times in {p}; include more surrounding text")
        self._set(p, text.replace(find, replace, 1))

    def delete(self, path: str) -> None:
        p = normalise_path(path)
        if p not in self._files:
            raise WorkspaceError(f"{p} does not exist")
        del self._files[p]
        self.changes.append(Change("deleted", p))

    def rename(self, old: str, new: str) -> None:
        o, n = normalise_path(old), normalise_path(new)
        if o not in self._files:
            raise WorkspaceError(f"{o} does not exist")
        if n in self._files:
            raise WorkspaceError(f"{n} already exists")
        self._files[n] = self._files.pop(o)
        self.changes.append(Change("renamed", n, f"from {o}"))

    # ---- model-supplied operations ----
    def apply_operations(self, ops: list[dict], *, max_ops: int = 40) -> list[str]:
        """Apply a model's edit list. Unknown operations are refused; the first failure aborts and rolls the whole batch back."""
        if len(ops) > max_ops:
            raise WorkspaceError(f"too many operations in one edit (limit {max_ops})")
        backup, backup_changes = dict(self._files), list(self.changes)
        done: list[str] = []
        try:
            for op in ops:
                kind = op.get("op")
                if kind == "write":
                    self.write(op["path"], op["content"])
                elif kind == "patch":
                    self.patch(op["path"], op["find"], op["replace"])
                elif kind == "delete":
                    self.delete(op["path"])
                elif kind == "rename":
                    self.rename(op["from"], op["to"])
                else:
                    raise WorkspaceError(f"unknown operation {kind!r}")
                done.append(f"{kind} {op.get('path') or op.get('from')}")
        except (KeyError, TypeError) as exc:
            self._files, self.changes = backup, backup_changes
            raise WorkspaceError("an edit operation was missing a required field") from exc
        except WorkspaceError:
            self._files, self.changes = backup, backup_changes
            raise
        return done


def diff_manifests(old: list[dict], new: list[dict]) -> dict[str, list[str]]:
    o = {m["path"]: m["sha"] for m in old}
    n = {m["path"]: m["sha"] for m in new}
    return {
        "added": sorted(set(n) - set(o)),
        "removed": sorted(set(o) - set(n)),
        "changed": sorted(p for p in set(o) & set(n) if o[p] != n[p]),
    }
