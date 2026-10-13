import pytest

from app.builder.planner import detect_requirements, deterministic_plan, sanitize_plan
from app.builder.spec import FeatureStatus, ProjectSpec, UnsafeSpecError


def paths(spec):
    return [r.path for r in spec.routes]


SAAS = "Build a premium AI SaaS marketing website with a homepage, features page, pricing page, blog listing, blog detail routes, about page, and contact page."
ESTATE = "Build a real-estate marketplace with a homepage, property search, filters, property details, saved properties, user accounts, and enquiry forms."
RESTAURANT = "Create a restaurant website called Olive & Ash with a menu, reservations, location and opening hours."
PORTFOLIO = "Create a modern architecture studio portfolio with project galleries, project detail pages and a contact form."
PM_APP = "Build a project management application with authentication, workspaces, tasks, assignments, comments, notifications, and role-based access."


def test_the_sitemap_follows_the_requirements_not_a_fixed_page_count():
    saas, estate, rest = deterministic_plan(SAAS), deterministic_plan(ESTATE), deterministic_plan(RESTAURANT)
    assert {"/pricing", "/features", "/blog", "/blog/[slug]", "/about", "/contact"} <= set(paths(saas))
    assert "/properties" not in paths(saas)  # "blog listing" must not be mistaken for a property catalogue
    assert {"/properties", "/properties/[slug]", "/saved"} <= set(paths(estate))
    assert "/pricing" not in paths(estate)
    assert {"/menu", "/menu/[slug]", "/reservations"} <= set(paths(rest))
    assert len({tuple(paths(x)) for x in (saas, estate, rest)}) == 3
    assert max(len(x.routes) for x in (saas, estate, rest)) > 3


def test_name_is_taken_from_the_prompt_and_gives_a_distinct_identity():
    a, b = deterministic_plan(RESTAURANT), deterministic_plan(RESTAURANT.replace("Olive & Ash", "Fig Tree"))
    assert a.name == "Olive & Ash" and b.name == "Fig Tree"
    assert a.design.palette != b.design.palette  # same direction, different business, different colour


def test_what_a_static_project_cannot_do_is_reported_not_faked():
    spec = deterministic_plan(ESTATE)
    st = {f.key: f.status for f in spec.features}
    assert st["auth"] is FeatureStatus.UNSUPPORTED
    assert st["saved_items"] is FeatureStatus.CLIENT_SIDE
    assert not any(r.requires_auth for r in spec.routes)
    assert any("Sign-in" in a for a in spec.assumptions)
    pm = deterministic_plan(PM_APP)
    assert {f.key: f.status for f in pm.features}["auth"] is FeatureStatus.UNSUPPORTED
    assert "/dashboard" in paths(pm)


def test_requirement_detection():
    r = detect_requirements(PORTFOLIO)
    assert r.entity == "projects" and r.direction.value in ("architecture", "creative_portfolio")
    assert detect_requirements("an online store with a cart and checkout").payments


def test_every_link_in_a_plan_resolves():
    for p in (SAAS, ESTATE, RESTAURANT, PORTFOLIO, PM_APP):
        spec = deterministic_plan(p)
        known = set(paths(spec))
        for r in spec.routes:
            for s in r.sections:
                if s.cta_href and s.cta_href.startswith("/"):
                    assert s.cta_href in known, (p, r.path, s.cta_href)


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "x", "routes": [{"path": "/", "title": "Home"}, {"path": "/", "title": "dup"}]},
        {"name": "x", "routes": [{"path": "/pricing", "title": "No home"}]},
        {"name": "x", "routes": [{"path": "/", "title": "Home"}, {"path": "/a b", "title": "bad path"}]},
        {"name": "x", "routes": [{"path": "/", "title": "<script>alert(1)</script>"}]},
        {"name": "x", "routes": [{"path": "/", "title": "H", "sections": [{"kind": "cta", "cta_href": "/nope", "cta_label": "x"}]}]},
        {"name": "x", "routes": [{"path": "/", "title": "H", "sections": [{"kind": "cta", "cta_href": "javascript:alert(1)", "cta_label": "x"}]}]},
    ],
)
def test_the_spec_rejects_unsafe_or_inconsistent_plans(bad):
    with pytest.raises((UnsafeSpecError, ValueError)):
        ProjectSpec.model_validate(bad)


def test_sanitize_keeps_the_good_pieces_of_a_flawed_ai_plan():
    fallback = deterministic_plan(RESTAURANT)
    raw = {
        "name": "Bistro Nine",
        "routes": [
            {"path": "/", "title": "Home", "sections": [{"kind": "hero", "heading": "Welcome"}, {"kind": "hero", "heading": "<script>x</script>"}, {"kind": "cta", "heading": "Book", "cta_label": "Go", "cta_href": "/gone"}]},
            {"path": "/members", "title": "Members", "requires_auth": True, "sections": [{"kind": "content", "heading": "Secret"}]},
            {"path": "/gallery", "title": "Gallery", "sections": [{"kind": "gallery", "entity": "ghost"}]},
            {"path": "/bad path", "title": "x"},
        ],
    }
    spec, notes = sanitize_plan(raw, fallback=fallback)
    assert spec.name == "Bistro Nine"
    assert paths(spec) == ["/", "/members", "/gallery"]
    assert [s.kind.value for s in spec.routes[0].sections] == ["hero", "cta"]
    assert spec.routes[0].sections[1].cta_href is None  # link to a missing route removed
    assert spec.routes[1].requires_auth is False
    assert spec.routes[2].sections == []  # the gallery of an unknown entity was dropped
    assert any("static project cannot enforce" in n for n in notes)
    # unsupported capabilities from requirements are never lost, whatever the model says
    assert "reservations" in {f.key for f in spec.features}


def test_sanitize_falls_back_when_nothing_usable():
    fallback = deterministic_plan(RESTAURANT)
    spec, notes = sanitize_plan({"routes": [{"path": "/x", "title": "no home"}]}, fallback=fallback)
    assert spec is fallback and notes
