import json

import pytest

from app.builder.generator import generate_project, route_file
from app.builder.planner import deterministic_plan
from app.builder.validator import validate_project
from app.builder.workspace import Workspace

PROMPTS = [
    "Build a premium AI SaaS marketing website with a homepage, features page, pricing page, blog listing, blog detail routes, about page, and contact page.",
    "Build a real-estate marketplace with property search, filters, property details, saved properties, user accounts, and enquiry forms.",
    "Create a restaurant website called Olive & Ash with a menu, reservations, location and opening hours.",
    "Create a modern architecture studio portfolio with project galleries, project detail pages and a contact form.",
    "Build a project management application with a dashboard, tasks and a contact page.",
    "Build an online store selling desk mats, monitor risers and laptop stands with a cart and checkout.",
]


@pytest.mark.parametrize("prompt", PROMPTS)
def test_generated_projects_validate_with_no_errors(prompt):
    spec = deterministic_plan(prompt)
    ws = generate_project(spec)
    rep = validate_project(ws, spec)
    assert rep.ok, [i.as_dict() for i in rep.errors]
    assert all(rep.checks.values()), rep.checks
    assert rep.stats["pages"] >= len(spec.routes)


def test_one_page_per_route_and_per_record():
    spec = deterministic_plan(PROMPTS[1])
    ws = generate_project(spec)
    ent = spec.entity("properties")
    for rec in ent.records:
        assert route_file("/properties/[slug]", rec["slug"]) in ws.paths()
    assert "properties/index.html" in ws.paths() and "saved/index.html" in ws.paths() and "404.html" in ws.paths()


def test_generation_is_deterministic_so_edits_only_change_what_changed():
    spec = deterministic_plan(PROMPTS[2])
    a, b = generate_project(spec), generate_project(spec)
    assert a.manifest() == b.manifest()
    spec2 = spec.model_copy(deep=True)
    spec2.routes[0].sections[0].heading = "A different headline"
    c = generate_project(spec2)
    changed = {m["path"] for m in c.manifest()} - {m["path"] for m in a.manifest() if any(m2["path"] == m["path"] and m2["sha"] == m["sha"] for m2 in c.manifest())}
    assert changed == {"index.html"}


def test_hand_edited_files_are_not_overwritten_by_regeneration():
    spec = deterministic_plan(PROMPTS[2])
    first = generate_project(spec)
    first.write("about/index.html", first.read("about/index.html") + "<!-- manual -->")
    spec.routes[0].sections[0].heading = "New"
    again = generate_project(spec, existing=first, locked={"about/index.html"})
    assert "<!-- manual -->" in again.read("about/index.html")
    assert "New" in again.read("index.html")


def test_content_is_escaped():
    spec = deterministic_plan(PROMPTS[2])
    spec.name = "Fish & Chips"
    ws = generate_project(spec)
    assert "Fish &amp; Chips" in ws.read("index.html") and "Fish & Chips<" not in ws.read("index.html")


def test_data_files_and_artwork_exist_for_every_record():
    spec = deterministic_plan(PROMPTS[1])
    ws = generate_project(spec)
    data = json.loads(ws.read("data/properties.json"))
    assert len(data) == len(spec.entity("properties").records)
    for rec in data:
        assert rec["image"] in ws.paths()


# ---- the validator must actually catch problems ----
def _good():
    spec = deterministic_plan(PROMPTS[0])
    return spec, generate_project(spec)


def test_validator_catches_a_broken_link():
    spec, ws = _good()
    ws.write("index.html", ws.read("index.html").replace('href="pricing/"', 'href="nowhere/"'))
    rep = validate_project(ws, spec)
    assert not rep.ok and any(i.code == "broken_link" for i in rep.errors)


def test_validator_catches_a_missing_page_for_a_planned_route():
    spec, ws = _good()
    ws.delete("pricing/index.html")
    rep = validate_project(ws, spec)
    assert any(i.code == "route_missing" and "pricing" in i.path for i in rep.errors)


def test_validator_catches_missing_asset_alt_title_label_and_h1():
    spec, ws = _good()
    ws.write("bad/index.html", '<!doctype html><html><head></head><body><main><img src="../missing.png"><input name="q"></main></body></html>')
    rep = validate_project(ws)
    codes = {i.code for i in rep.errors}
    assert {"no_lang", "no_title", "no_viewport", "h1_count", "img_alt", "missing_asset", "input_label"} <= codes


def test_validator_blocks_inline_and_external_scripts_and_dangerous_js():
    spec, ws = _good()
    ws.patch("index.html", "</body>", '<script>alert(1)</script><script src="https://evil.example/x.js"></script></body>')
    ws.patch("assets/app.js", "'use strict';", "'use strict'; eval('1');")
    rep = validate_project(ws, spec)
    codes = {i.code for i in rep.errors}
    assert {"inline_script", "external_script", "dangerous_js"} <= codes and not rep.ok


def test_validator_flags_secrets_and_bad_syntax_and_bad_json():
    spec, ws = _good()
    ws.write("data/leak.json", json.dumps({"k": "sk_live_" + "a1" * 12}))
    ws.write("assets/broken.js", "function (")
    ws.write("data/bad.json", "{nope")
    rep = validate_project(ws, spec)
    codes = {i.code for i in rep.errors}
    assert "secret_in_file" in codes and "json_invalid" in codes
    import shutil

    if shutil.which("node"):
        assert "js_syntax" in codes


def test_example_content_is_surfaced_as_a_warning_not_hidden():
    spec, ws = _good()
    rep = validate_project(ws, spec)
    assert any(i.code == "example_content" for i in rep.warnings)
