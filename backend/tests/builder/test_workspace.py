import pytest

from app.builder.workspace import MAX_FILE_BYTES, Workspace, WorkspaceError, diff_manifests, normalise_path


@pytest.mark.parametrize(
    "bad",
    ["../x.html", "/etc/passwd.html", "a/../../b.html", "a\\b.html", "C:/x.html", ".env.json", "a/.hidden.html", "x.py", "x", "a\x00.html", "", "a/b/c/d/e/f/g/h/i.html", "sp ace.html"],
)
def test_unsafe_paths_are_rejected(bad):
    with pytest.raises(WorkspaceError):
        normalise_path(bad)


def test_safe_paths_are_normalised():
    assert normalise_path("pricing/index.html") == "pricing/index.html"
    assert normalise_path("assets/./app.js") == "assets/app.js"
    assert normalise_path("properties/[slug]/index.html") == "properties/[slug]/index.html"


def test_write_read_patch_delete_rename_search():
    w = Workspace()
    w.write("index.html", "<h1>Hello</h1>\n<p>World</p>")
    assert w.read("index.html").startswith("<h1>")
    w.patch("index.html", "Hello", "Hi")
    assert "Hi" in w.read("index.html")
    w.rename("index.html", "home.html")
    assert w.paths() == ["home.html"]
    assert w.search("world")[0][:2] == ("home.html", 2)
    w.delete("home.html")
    assert w.paths() == []
    assert [c.op for c in w.changes] == ["created", "modified", "renamed", "deleted"]


def test_patch_is_refused_when_ambiguous_or_missing():
    w = Workspace({"a.html": "x x"})
    with pytest.raises(WorkspaceError, match="2 times"):
        w.patch("a.html", "x", "y")
    with pytest.raises(WorkspaceError, match="not found"):
        w.patch("a.html", "zzz", "y")
    assert w.read("a.html") == "x x"


def test_limits_are_enforced():
    w = Workspace()
    with pytest.raises(WorkspaceError):
        w.write("big.html", "x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(WorkspaceError):
        w.write("a.html", b"bytes")  # type: ignore[arg-type]


def test_a_failing_batch_rolls_everything_back():
    w = Workspace({"a.html": "one"})
    with pytest.raises(WorkspaceError):
        w.apply_operations([{"op": "write", "path": "b.html", "content": "two"}, {"op": "patch", "path": "a.html", "find": "nope", "replace": "x"}])
    assert w.paths() == ["a.html"] and w.read("a.html") == "one"
    with pytest.raises(WorkspaceError, match="unknown operation"):
        w.apply_operations([{"op": "exec", "path": "a.html"}])
    with pytest.raises(WorkspaceError, match="missing a required field"):
        w.apply_operations([{"op": "write", "path": "c.html"}])


def test_search_is_bounded():
    w = Workspace({"a.html": "\n".join("hit" for _ in range(200))})
    assert len(w.search("hit", limit=10)) == 10
    with pytest.raises(WorkspaceError):
        w.search("x" * 300)


def test_manifest_diff():
    a, b = Workspace({"a.html": "1", "b.html": "2"}), Workspace({"a.html": "1", "b.html": "3", "c.html": "4"})
    assert diff_manifests(a.manifest(), b.manifest()) == {"added": ["c.html"], "removed": [], "changed": ["b.html"]}
