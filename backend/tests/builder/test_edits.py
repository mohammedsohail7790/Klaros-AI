import pytest

from app.builder.edits import apply_patch, deterministic_edit
from app.builder.generator import generate_project
from app.builder.planner import deterministic_plan
from app.builder.validator import validate_project

BASE = deterministic_plan("Create a restaurant website called Olive & Ash with a menu, reservations, location and opening hours.")


def paths(spec):
    return [r.path for r in spec.routes]


def test_add_a_page_adds_a_route_and_keeps_everything_else():
    r = deterministic_edit(BASE, "Add a pricing page")
    assert r.changed and r.intent == "add_page" and "/pricing" in paths(r.spec)
    assert set(paths(BASE)) <= set(paths(r.spec))
    assert validate_project(generate_project(r.spec), r.spec).ok


def test_add_blog_brings_its_data_and_detail_routes():
    r = deterministic_edit(BASE, "please add a blog page")
    assert {"/blog", "/blog/[slug]"} <= set(paths(r.spec)) and r.spec.entity("posts")
    assert validate_project(generate_project(r.spec), r.spec).ok


def test_a_request_that_is_already_true_is_reported_as_no_change():
    r = deterministic_edit(BASE, "make the header sticky")
    assert not r.changed and "already" in r.summary


def test_adding_a_page_that_exists_changes_nothing():
    r = deterministic_edit(BASE, "add a contact page")
    assert not r.changed and "already" in r.summary


def test_remove_page_and_its_detail_routes_but_never_the_home_page():
    r = deterministic_edit(BASE, "remove the menu page")
    assert "/menu" not in paths(r.spec) and "/menu/[slug]" not in paths(r.spec)
    assert validate_project(generate_project(r.spec), r.spec).ok
    assert not deterministic_edit(BASE, "remove the home page").changed


@pytest.mark.parametrize(
    "msg,check",
    [
        ("the header should not be sticky", lambda s: s.design.sticky_header is False),
        ("switch to a dark theme", lambda s: s.design.dark is True),
        ("use a serif typeface", lambda s: s.design.heading_font.value == "serif"),
        ("give it more spacing", lambda s: s.design.density == "spacious"),
        ("make it more premium", lambda s: s.design.card_style == "elevated" and s.design.radius == "lg"),
        ("make it green", lambda s: s.design.palette is not None),
        ("use a centered hero", lambda s: s.design.hero_layout == "centered"),
        ("change the headline to Dinner is served", lambda s: s.routes[0].sections[0].heading == "Dinner is served"),
        ("set the contact email to hello@oliveandash.example", lambda s: s.contact_email == "hello@oliveandash.example"),
    ],
)
def test_common_edits_work_without_a_model(msg, check):
    r = deterministic_edit(BASE, msg)
    assert r.changed, msg
    assert check(r.spec)
    assert validate_project(generate_project(r.spec), r.spec).ok


def test_a_dark_theme_still_has_readable_contrast():
    r = deterministic_edit(BASE, "switch to a dark theme")
    from app.builder.design import contrast_ratio, resolve_design

    p = resolve_design(r.spec.design).palette
    assert contrast_ratio(p.text, p.background) >= 7 and p.background == "#0b0f1a"


def test_requests_a_static_site_cannot_meet_are_recorded_not_faked():
    r = deterministic_edit(BASE, "add authentication and user accounts")
    assert r.intent == "feature" and "not possible" in r.summary
    assert {f.key: f.status.value for f in r.spec.features}["auth"] == "unsupported"
    assert paths(r.spec) == paths(BASE)


def test_unknown_requests_are_reported_honestly():
    r = deterministic_edit(BASE, "make the logo spin when I hover over it")
    assert r.intent == "none" and not r.changed


def test_an_incremental_edit_only_changes_the_files_it_should():
    a = generate_project(BASE)
    r = deterministic_edit(BASE, "the header should not be sticky")
    b = generate_project(r.spec)
    changed = {m["path"] for m in b.manifest()} - {m["path"] for m in a.manifest() if any(x["path"] == m["path"] and x["sha"] == m["sha"] for x in b.manifest())}
    assert changed == {"assets/styles.css"}  # no page was rewritten for a header-style change
    hero = deterministic_edit(BASE, "change the headline to Dinner is served")
    c = generate_project(hero.spec)
    changed = {m["path"] for m in c.manifest()} - {m["path"] for m in a.manifest() if any(x["path"] == m["path"] and x["sha"] == m["sha"] for x in c.manifest())}
    assert changed == {"index.html"}


def test_ai_patch_is_merged_not_trusted():
    patch = {
        "summary": "Added a careers page and a warmer palette.", "intent": "add_page",
        "routes": [{"path": "/careers", "title": "Careers", "sections": [{"kind": "content", "heading": "Join us", "blocks": [{"title": "Open roles", "body": "None right now."}]}, {"kind": "cta", "cta_href": "/does-not-exist", "cta_label": "x"}]},
                   {"path": "/members", "title": "Members", "requires_auth": True, "sections": [{"kind": "content", "heading": "<script>x</script>"}]}],
        "remove_routes": ["/", "/ghost"],
        "design": {"radius": "xl", "heading_font": "not-a-font"},
        "entities": [{"name": "ghosts", "singular": "Ghost", "records": [{"slug": "a b", "title": "bad slug"}]}],
    }
    r = apply_patch(BASE, patch)
    assert r is not None and r.changed
    assert "/careers" in paths(r.spec) and "/" in paths(r.spec) and "/members" in paths(r.spec)
    careers = r.spec.route("/careers")
    assert [s.kind.value for s in careers.sections] == ["content", "cta"] and careers.sections[1].cta_href is None
    assert r.spec.route("/members").sections == [] and not r.spec.route("/members").requires_auth
    assert r.spec.design.radius == BASE.design.radius  # the whole design change was invalid, so none of it applied
    assert r.spec.entity("ghosts") is None
    assert set(paths(BASE)) <= set(paths(r.spec))
    assert validate_project(generate_project(r.spec), r.spec).ok
