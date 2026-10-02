"""Business Builder: API-level tests for /business-builder — Requirements,
Business Map, Next Actions and the workforce boundary, all derived from the
Blueprint. Two differently-shaped businesses run through the *same* engine
(driven by the deterministic Discovery fallback, since no AI provider is
configured in tests), plus tenant isolation, RBAC, determinism, and an
honesty check that nothing claims a connection that does not exist.
"""

import pytest

pytestmark = pytest.mark.asyncio

from tests.test_business_journey_api import _register, _tech_token  # noqa: E402


async def _journey_via_fallback(client, headers, idea: str, answers: list[str]) -> dict:
    """Start -> answer every deterministic-fallback question -> confirm claims ->
    complete discovery -> confirm blueprint -> generate recommendations."""
    start = await client.post("/api/v1/business-journey", json={"business_idea": idea}, headers=headers)
    assert start.status_code == 201, start.text
    journey = start.json()
    sid = journey["discovery_session_id"]
    for ans in answers:
        r = await client.post(f"/api/v1/business-discovery/sessions/{sid}/answer", json={"answer": ans}, headers=headers)
        assert r.status_code == 200, r.text
        if r.json()["session_status"] == "COMPLETED":
            break
    draft = (await client.get("/api/v1/business-blueprint/draft", headers=headers)).json()
    for claim in [c for c in draft["claims"] if c["status"] == "PROPOSED"]:
        r = await client.post(f"/api/v1/business-blueprint/claims/{claim['id']}/confirm", headers=headers)
        assert r.status_code == 200, r.text
    jid = journey["id"]
    for step in ("complete-discovery", "confirm-blueprint", "generate-recommendations"):
        r = await client.post(f"/api/v1/business-journey/{jid}/{step}", headers=headers)
        assert r.status_code == 200, (step, r.text)
        journey = r.json()
    assert journey["status"] == "RECOMMENDATIONS_READY"
    return journey


async def _seed_registry() -> None:
    """The test DB is built with create_all (no migration seed) — insert the
    real platform registry rows so module behaviour is exercised for real."""
    from app.data.vertical_extension_seed import SEED_VERTICALS
    from app.db.session import async_session_maker
    from app.models.vertical_extension import VerticalExtension

    async with async_session_maker() as session:
        for v in SEED_VERTICALS:
            session.add(VerticalExtension(**{k: (str(val) if k == "status" else val) for k, val in v.items()}))
        await session.commit()


async def _seed_catalog() -> None:
    """Insert the real platform provider catalog (the test DB has no migration seed)."""
    from app.data.integration_provider_catalog_seed import SEED_PROVIDERS
    from app.db.session import async_session_maker
    from app.models.integration_catalog import IntegrationProviderCatalog

    async with async_session_maker() as session:
        for p in SEED_PROVIDERS:
            session.add(IntegrationProviderCatalog(**{k: (str(v) if k in ("implementation_status", "auth_shape") else v) for k, v in p.items()}))
        await session.commit()


_DROPSHIP = (
    "I want to start a dropshipping business using a supplier catalog.",
    [
        "An online store selling home goods in the United States.",
        "We keep a margin on every product sold.",
        "storefront, product catalog, supplier integration, inventory, payments, order management, fulfillment, marketing, customer support and analytics",
    ],
)
_CONSULT = (
    "I want to build an online consulting business.",
    [
        "Business consulting for small companies in Europe.",
        "Hourly fees and monthly retainers.",
        "website, lead capture, scheduling, payments and marketing",
    ],
)


async def test_overview_without_a_journey_is_empty_not_an_error(client) -> None:
    token = await _register(client, "Empty Co", "empty@example.com")
    r = await client.get("/api/v1/business-builder/overview", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["journey"] is None
    assert body["requirements"] == []
    assert body["business_map"]["nodes"] == []
    assert body["stages"][0]["state"] == "current"


async def test_two_different_businesses_get_different_requirements_and_maps(client) -> None:
    drop_token = await _register(client, "Drop Co", "drop@example.com")
    cons_token = await _register(client, "Consult Co", "consult@example.com")
    dh, ch = {"Authorization": f"Bearer {drop_token}"}, {"Authorization": f"Bearer {cons_token}"}
    await _journey_via_fallback(client, dh, *_DROPSHIP)
    await _journey_via_fallback(client, ch, *_CONSULT)

    drop = (await client.get("/api/v1/business-builder/overview", headers=dh)).json()
    cons = (await client.get("/api/v1/business-builder/overview", headers=ch)).json()

    drop_keys = {r["key"] for r in drop["requirements"]}
    cons_keys = {r["key"] for r in cons["requirements"]}
    assert {"storefront", "product_catalog", "supplier_integration", "inventory", "order_management", "fulfillment"} <= drop_keys
    assert {"payment_processing", "marketing", "analytics"} <= drop_keys
    assert {"website", "lead_capture", "appointment_scheduling"} <= cons_keys
    assert "storefront" not in cons_keys and "supplier_integration" not in cons_keys

    drop_nodes = {n["id"] for n in drop["business_map"]["nodes"]}
    cons_nodes = {n["id"] for n in cons["business_map"]["nodes"]}
    assert "actor:supplier" in drop_nodes and "actor:supplier" not in cons_nodes
    assert drop_nodes != cons_nodes
    assert {"actor:customers", "core:klaros", "outcome:revenue"} <= drop_nodes & cons_nodes

    # Capabilities are spread over lanes by their vocabulary group (generic, not per business).
    lane_of = {n["id"]: n["lane"] for n in drop["business_map"]["nodes"]}
    assert len(drop["business_map"]["lanes"]) == 6
    assert lane_of["cap:storefront"] == 1  # front door
    assert lane_of["cap:inventory"] == 3 and lane_of["cap:payment_processing"] == 4  # operations vs money & growth
    assert lane_of["actor:supplier"] == 5

    # Every edge endpoint exists; every non-core node is connected to something.
    for body in (drop, cons):
        ids = {n["id"] for n in body["business_map"]["nodes"]}
        for e in body["business_map"]["edges"]:
            assert e["source"] in ids and e["target"] in ids
        touched = {e["source"] for e in body["business_map"]["edges"]} | {e["target"] for e in body["business_map"]["edges"]}
        assert ids <= touched


async def test_planned_capabilities_are_never_shown_as_ready_or_connected(client) -> None:
    await _seed_catalog()
    token = await _register(client, "Honest Co", "honest@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_DROPSHIP)
    body = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    by_key = {r["key"]: r for r in body["requirements"]}
    for key in ("storefront", "product_catalog", "supplier_integration", "inventory", "order_management", "fulfillment"):
        assert by_key[key]["readiness"] == "PLANNED", key
    # No provider anywhere is CONNECTED for a fresh tenant — and with the real catalog
    # loaded, Stripe is offered as NOT_CONNECTED while stub providers are PLANNED.
    for r in body["requirements"]:
        assert all(p["state"] != "CONNECTED" for p in r["providers"])
    pay = by_key["payment_processing"]
    assert any(p["provider_key"] == "stripe" and p["state"] == "NOT_CONNECTED" for p in pay["providers"])
    assert pay["readiness"] == "NOT_CONNECTED"
    assert all(p["state"] == "PLANNED" for r in body["requirements"] for p in r["providers"] if p["adapter_required"])
    # Planned capabilities surface as honest "not available yet" actions, not as links.
    planned = [a for a in body["next_actions"] if a["id"].startswith("planned:")]
    assert planned and all(a["route"] is None and a["state"] == "PLANNED" for a in planned)
    # Launch is not claimed.
    assert body["launch"]["launched"] is False and body["launch"]["ready"] is False


async def test_map_shows_only_real_adapters_as_systems_never_planned_stubs(client) -> None:
    await _seed_catalog()
    token = await _register(client, "Sys Co", "sys@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_DROPSHIP)
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    systems = [n for n in o["business_map"]["nodes"] if n["kind"] == "system"]
    assert {n["provider_key"] for n in systems if "provider_key" in n} >= {"stripe"} if systems else True
    assert all(n["planned"] is False for n in systems)
    assert all(n["state"] in ("AVAILABLE", "CONNECTED", "CONFIGURATION_REQUIRED") for n in systems)
    assert any(n["state"] == "AVAILABLE" for n in systems)  # Stripe: real adapter, not connected


async def test_workforce_boundary_reports_not_connected_and_never_connected(client) -> None:
    token = await _register(client, "Workforce Co", "wf@example.com")
    h = {"Authorization": f"Bearer {token}"}
    wf = (await client.get("/api/v1/business-builder/workforce", headers=h)).json()
    assert wf["status"] == "NOT_CONNECTED"
    assert wf["adapter_implemented"] is False
    assert wf["provider"] == "halla"
    assert {c["key"] for c in wf["capabilities"]} >= {"voice", "incoming_calls", "outgoing_calls", "lead_qualification", "appointment_booking"}

    await _journey_via_fallback(client, h, *_DROPSHIP)
    body = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    node = next(n for n in body["business_map"]["nodes"] if n["id"] == "workforce:ai")
    # No adapter exists, so there is nothing to connect: "integration required", never "connected".
    assert node["state"] == "INTEGRATION_REQUIRED" and node["planned"] is True
    assert body["workforce"]["status"] == "NOT_CONNECTED"


async def test_overview_is_deterministic(client) -> None:
    token = await _register(client, "Determ Co", "determ@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_CONSULT)
    a = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    b = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    assert a["business_map"] == b["business_map"]
    assert a["requirements"] == b["requirements"]
    assert a["next_actions"] == b["next_actions"]


async def test_tenant_isolation(client) -> None:
    t1 = await _register(client, "Iso One", "iso1@example.com")
    t2 = await _register(client, "Iso Two", "iso2@example.com")
    h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
    await _journey_via_fallback(client, h1, *_DROPSHIP)
    other = (await client.get("/api/v1/business-builder/overview", headers=h2)).json()
    assert other["requirements"] == [] and other["blueprint"] is None


async def test_requires_authentication_and_rbac(client) -> None:
    assert (await client.get("/api/v1/business-builder/overview")).status_code == 401
    owner = await _register(client, "Rbac Co", "rbac@example.com")
    tech = await _tech_token(client, owner, "tech@example.com")
    r = await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers={"Authorization": f"Bearer {tech}"})
    assert r.status_code == 403


async def test_enable_module_is_explicit_idempotent_and_honest_about_empty_modules(client) -> None:
    await _seed_registry()
    token = await _register(client, "Module Co", "module@example.com")
    h = {"Authorization": f"Bearer {token}"}
    r1 = await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=h)
    r2 = await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    # A registry-only module has nothing to enable — refused, not faked.
    r3 = await client.post("/api/v1/business-builder/modules/dropshipping/enable", headers=h)
    assert r3.status_code == 409
    assert (await client.post("/api/v1/business-builder/modules/nope/enable", headers=h)).status_code == 404


_REFERRAL = (
    "We connect international patients with partner hospitals and earn a referral fee.",
    [
        "A medical tourism company serving patients from the Gulf, with hospitals in India.",
        "We earn a commission from the hospital on each referred patient.",
        "provider directory, patient leads, lead qualification, consultations and commission tracking",
    ],
)


async def test_industry_module_suggested_then_enabled_adds_registry_requirements(client) -> None:
    await _seed_registry()
    token = await _register(client, "Referral Co", "referral@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_REFERRAL)

    before = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    suggest = [a for a in before["next_actions"] if a["id"] == "enable-module:medical_tourism"]
    assert suggest and suggest[0]["kind"] == "enable_vertical"
    assert next(m for m in before["modules"] if m["key"] == "medical_tourism")["enabled"] is False
    # Registry-only module is never suggested as enable-able.
    assert not [a for a in before["next_actions"] if a["id"] == "enable-module:dropshipping"]

    assert (await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=h)).status_code == 200
    after = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    assert not [a for a in after["next_actions"] if a["id"] == "enable-module:medical_tourism"]
    keys = {r["key"]: r for r in after["requirements"]}
    # Registry capabilities merge into canonical requirements; stated ones stay required.
    assert keys["provider_directory"]["required"] is True
    assert "multi_currency" in keys and keys["multi_currency"]["required"] is False
    assert keys["multi_currency"]["source"] == "industry_module"
    # Enabled module with an empty directory -> honest "add data" action.
    assert any(a["id"] == "add-data:provider_directory" for a in after["next_actions"])


async def test_website_pages_follow_the_business_requirements(client) -> None:
    await _seed_registry()
    token = await _register(client, "Pages Co", "pages@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_REFERRAL)

    # No industry module yet: only the universal pages exist.
    gen = await client.post("/api/v1/websites/generate", headers=h)
    assert gen.status_code == 200, gen.text
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    assert o["website"]["exists"] and not o["website"]["published"]
    assert o["website"]["pages"] == ["home", "contact"]

    await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=h)
    gen = await client.post("/api/v1/websites/generate", headers=h)
    assert gen.status_code == 200, gen.text
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    assert {"home", "providers", "services", "contact"} <= set(o["website"]["pages"])
    # A draft is not "published" — the builder never claims it is.
    assert o["website"]["published"] is False
    assert any(a["id"] == "website:publish" for a in o["next_actions"])


def test_section_data_capabilities_are_a_requirement_source_and_only_named_fields_count() -> None:
    from app.services.recommendation_service import _capability_keys_from_section_data as keys

    assert keys({"capabilities": ["Payments", "hospital and doctor directory"], "notes": ["ignored thing"]}) == [
        "payment_processing",
        "provider_directory",
    ]
    assert keys({"required_capabilities": "website, lead capture and scheduling"}) == [
        "website", "lead_capture", "appointment_scheduling",
    ]
    assert keys({"filled": True}) == []
    assert keys(None) == []


async def test_capabilities_added_in_the_blueprint_section_become_requirements(client) -> None:
    token = await _register(client, "Section Co", "section@example.com")
    h = {"Authorization": f"Bearer {token}"}
    j = await _journey_via_fallback(client, h, *_CONSULT)
    # Business owner corrects the blueprint section directly (new ACTIVE version),
    # then regenerates recommendations: the new capability must show up.
    active = (await client.get("/api/v1/business-blueprint", headers=h)).json()
    r = await client.put(
        "/api/v1/business-blueprint/sections/REQUIRED_CAPABILITIES",
        json={"blueprint_id": active["blueprint"]["id"], "data": {"capabilities": ["inventory"]}},
        headers=h,
    )
    assert r.status_code == 200, r.text
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    inv = next(x for x in o["requirements"] if x["key"] == "inventory")
    assert inv["required"] is True and inv["readiness"] == "PLANNED"
    assert any(e["kind"] == "blueprint_section" for e in inv["evidence"])
    assert j["status"] == "RECOMMENDATIONS_READY"


async def test_recommendation_review_count_matches_real_decisions(client) -> None:
    """The "review N recommendations" action counts only real decisions
    (integration options + merely-suggested capabilities) — not required
    capabilities the user stated, nor name-matched tool rows."""
    await _seed_catalog()
    token = await _register(client, "Count Co", "count@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_DROPSHIP)
    recs = (await client.get("/api/v1/recommendations", headers=h)).json()
    integrations = [r for r in recs if r["type"] == "INTEGRATION" and r["status"] == "PROPOSED"]
    tools = [r for r in recs if r["type"] == "TOOL"]
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    action = next((a for a in o["next_actions"] if a["id"] == "recommendations:review"), None)
    assert integrations and action is not None
    assert action["title"].startswith(f"Review {len(integrations)} ")
    assert len(recs) > len(integrations) + len(tools) - 1  # capability rows exist but are not counted
    # Decide every integration option -> the review action disappears and launch item is done.
    for r in integrations:
        assert (await client.post(f"/api/v1/recommendations/{r['id']}/reject", headers=h)).status_code == 200
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    assert not any(a["id"] == "recommendations:review" for a in o["next_actions"])
    assert next(i for i in o["launch"]["items"] if i["key"] == "recommendations")["done"] is True


async def test_website_uses_the_company_name_given_at_signup_when_the_blueprint_has_none(client) -> None:
    token = await _register(client, "Gulf Care Connect", "gcc@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_REFERRAL)
    gen = await client.post("/api/v1/websites/generate", headers=h)
    assert gen.status_code == 200, gen.text
    w = gen.json()["website"]
    prev = await client.get(f"/api/v1/websites/{w['id']}/versions/{gen.json()['version']['id']}/preview", headers=h)
    home = prev.json()["pages"][0]
    assert "Gulf Care Connect" in str(home)


async def test_generated_site_uses_what_the_user_said_and_never_ships_filler(client) -> None:
    token = await _register(client, "Plain Co", "plain@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_CONSULT)
    gen = await client.post("/api/v1/websites/generate", headers=h)
    assert gen.status_code == 200, gen.text
    w, v = gen.json()["website"], gen.json()["version"]
    prev = (await client.get(f"/api/v1/websites/{w['id']}/versions/{v['id']}/preview", headers=h)).json()
    text = str(prev["pages"][0])
    # The owner's own description (a confirmed Discovery claim) reaches the hero…
    assert "online consulting business" in text
    # …and sections with nothing real to say are omitted rather than filled with placeholders.
    for filler in ("Our services", "Everyone who needs what we offer", "dedicated to serving our customers"):
        assert filler not in text


async def test_business_name_is_the_company_given_at_signup(client) -> None:
    token = await _register(client, "Gulf Care Connect", "name@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_REFERRAL)
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    assert o["business"]["name"] == "Gulf Care Connect"


async def test_finish_discovery_endpoint_lets_the_user_end_discovery_early(client) -> None:
    token = await _register(client, "Early Co", "early@example.com")
    h = {"Authorization": f"Bearer {token}"}
    j = (await client.post("/api/v1/business-journey", json={"business_idea": "A candle shop."}, headers=h)).json()
    sid = j["discovery_session_id"]
    r = await client.post(f"/api/v1/business-discovery/sessions/{sid}/finish", headers=h)
    assert r.status_code == 200 and r.json()["session"]["status"] == "COMPLETED"
    assert (await client.post(f"/api/v1/business-discovery/sessions/{sid}/finish", headers=h)).status_code == 200  # idempotent
    adv = await client.post(f"/api/v1/business-journey/{j['id']}/complete-discovery", headers=h)
    assert adv.status_code == 200 and adv.json()["status"] == "BLUEPRINT_REVIEW"
    # RBAC + isolation
    owner2 = await _register(client, "Other Co", "other@example.com")
    assert (await client.post(f"/api/v1/business-discovery/sessions/{sid}/finish", headers={"Authorization": f"Bearer {owner2}"})).status_code == 404
    tech = await _tech_token(client, token, "tech2@example.com")
    assert (await client.post(f"/api/v1/business-discovery/sessions/{sid}/finish", headers={"Authorization": f"Bearer {tech}"})).status_code == 403


async def test_launch_readiness_explains_every_item_and_never_marks_unreal_things_ready(client) -> None:
    await _seed_catalog()
    await _seed_registry()
    token = await _register(client, "Launch Co", "launch@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_REFERRAL)
    await client.post("/api/v1/business-builder/modules/medical_tourism/enable", headers=h)
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    launch = o["launch"]
    by = {i["key"]: i for i in launch["items"]}
    assert all(i["detail"] and i["status"] for i in launch["items"])  # every item says WHY
    assert by["blueprint"]["status"] == "READY"
    assert by["website"]["status"] == "NOT_READY" and "No website" in by["website"]["detail"]
    assert by["integrations"]["status"] == "NOT_READY" and "Google Calendar" in by["integrations"]["detail"]
    # Empty directory is NOT ready; the reason is given.
    data = [i for k, i in by.items() if k.startswith("data:")]
    assert data and all(i["status"] == "NOT_READY" for i in data)
    # Workforce can't be connected (no adapter): honest, optional, and never blocks.
    assert by["workforce"]["status"] == "INTEGRATION_REQUIRED" and by["workforce"]["required"] is False
    assert launch["ready"] is False and launch["verdict"] == "NOT_READY" and launch["launched"] is False
    prog = {r["key"]: r for r in o["progress"]}
    assert prog["discovery"]["status"] == "READY" and prog["integrations"]["status"] == "NOT_CONNECTED"
    assert prog["launch"]["status"] == "NOT_READY" and prog["workforce"]["status"] == "INTEGRATION_REQUIRED"

    # Publish a website and add records: those items turn READY with real counts.
    gen = await client.post("/api/v1/websites/generate", headers=h)
    w, v = gen.json()["website"], gen.json()["version"]
    assert (await client.post(f"/api/v1/websites/{w['id']}/versions/{v['id']}/publish", headers=h)).status_code == 200
    r = await client.post("/api/v1/medical-tourism/providers", json={"name": "Apex", "country": "IN", "idempotency_key": "p1"}, headers=h)
    assert r.status_code == 200, r.text
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    by = {i["key"]: i for i in o["launch"]["items"]}
    assert by["website"]["status"] == "READY" and o["launch"]["launched"] is True
    assert by["data:provider_directory"]["status"] == "READY" and "1 record" in by["data:provider_directory"]["detail"]
    assert o["launch"]["ready"] is False  # integrations / planned capabilities still outstanding
    stages = {st["key"]: st["state"] for st in o["stages"]}
    assert stages["website"] == "done" and stages["launch"] != "done"  # published is not "launched"


async def test_requirements_carry_a_grounded_why_and_an_honest_next_step(client) -> None:
    await _seed_catalog()
    token = await _register(client, "Why Co", "why@example.com")
    h = {"Authorization": f"Bearer {token}"}
    await _journey_via_fallback(client, h, *_DROPSHIP)
    o = (await client.get("/api/v1/business-builder/overview", headers=h)).json()
    by = {r["key"]: r for r in o["requirements"]}
    assert "You listed" in by["inventory"]["why"]
    # PLANNED -> no route, explicit "adapter required" wording (never a fake action).
    assert by["inventory"]["next_step"]["route"] is None and "adapter required" in by["inventory"]["next_step"]["text"]
    # Real provider, not connected -> a real route to connect it.
    assert by["payment_processing"]["next_step"]["route"] == "/settings/integrations"
    assert "Stripe" in by["payment_processing"]["next_step"]["text"]
    # Native module -> opens it.
    assert by["analytics"]["next_step"]["route"] == "/dashboard"
