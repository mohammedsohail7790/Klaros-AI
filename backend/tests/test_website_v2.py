"""Website v2: a real brand kit, and three blocks (steps, FAQ, trust bar) that only ever show what the business has stated.

The rule under test: a generated site looks designed, but never invents a fact. No steps in the Blueprint -> no steps section; no stated places -> no trust bar;
a FAQ needs at least two answers the business can honestly give; a button that goes nowhere is not generated. SQLite + in-process (not a deployed service)."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.business_blueprint import BlueprintSectionKey
from app.schemas.website_specification import (
    _ALLOWED_FONTS,
    ComponentType,
    FaqProps,
    HeroProps,
    SectionSpec,
    StepsProps,
    ThemeTokens,
    TrustBarProps,
    UnsafeContentError,
    WebsiteSpecification,
)
from app.services import website_generation_service as wgs
from app.services.website_brand import LOOKS, contrast_ratio, is_arabic_first, pick_look, split_steps, theme_for
from tests.test_halla_integration import _h, halla  # noqa: F401 - fixtures
from tests.test_website_generation_service import (  # noqa: F401 - fixtures
    _build_active_blueprint,
    blueprint_service,
    generation_service,
)

pytestmark = pytest.mark.asyncio

STEPS = [
    {"title": "Tell us what you need", "description": "Send an enquiry. Nothing is kept until you agree."},
    {"title": "We reply", "description": "A coordinator answers you directly."},
    {"title": "You decide", "description": "Nothing is shared without your yes."},
]


async def _with(blueprint_service, tenant_id, **sections):
    # Fill the sections on the draft first; _build_active_blueprint then adds its claims to the same draft and activates it.
    draft = await blueprint_service.get_or_create_draft(tenant_id, created_by=None)
    for key, data in sections.items():
        await blueprint_service.update_section(tenant_id, draft.id, section_key=BlueprintSectionKey[key].value, data=data, updated_by=None)
    return await _build_active_blueprint(blueprint_service, tenant_id)


def _home(result):
    return next(p for p in result.specification.pages if p.slug == "home")


def _types(page):
    return [s.component_type.value for s in page.sections]


# --- the brand kit ---------------------------------------------------------------------------------------------------------------------------------


def test_every_look_is_legible_and_uses_allowed_fonts() -> None:
    for look in LOOKS:
        assert contrast_ratio(look.text, "#FFFFFF") >= 7, look.key           # body text on the page
        assert contrast_ratio("#FFFFFF", look.primary) >= 7, look.key        # white button text on the primary colour
        assert contrast_ratio(look.text, look.surface) >= 7, look.key        # text on a tinted band
        assert look.heading_font in _ALLOWED_FONTS and look.body_font in _ALLOWED_FONTS, look.key
        ThemeTokens(primary_color=look.primary, secondary_color=look.accent, text_color=look.text, surface_color=look.surface, heading_font=look.heading_font, body_font=look.body_font)


def test_a_look_is_stable_for_a_business_and_the_set_is_actually_used() -> None:
    assert pick_look("Gate Clinic") is pick_look("  gate clinic ")
    chosen = {pick_look(f"Business {i}").key for i in range(60)}
    assert len(chosen) == len(LOOKS)


def test_arabic_first_text_reads_right_to_left_but_a_mixed_name_does_not() -> None:
    assert is_arabic_first("عيادة النور") is True
    assert is_arabic_first("Al Noor Clinic") is False
    assert is_arabic_first("Al Noor عيادة", "We help patients travel for care.") is False
    theme = theme_for("عيادة النور", texts=("نساعد المرضى",))
    assert theme["text_direction"] == "RTL" and theme["heading_font"] == "Cairo"
    ThemeTokens(**theme)


def test_a_stored_v1_theme_is_still_valid_and_gets_the_new_defaults() -> None:
    old = {"primary_color": "#1A1A2E", "secondary_color": "#E94560", "background_color": "#FFFFFF", "text_color": "#1A1A2E",
           "heading_font": "Georgia", "body_font": "system-ui", "spacing_scale": "COMFORTABLE", "radius_scale": "MD", "shadow_scale": "SM",
           "container_width_px": 1200, "button_variant": "SOLID"}
    t = ThemeTokens.model_validate(old)
    assert t.text_direction.value == "LTR" and t.surface_color == "#F4F7FA" and t.logo_text is None


@pytest.mark.parametrize("bad", [{"text_direction": "UP"}, {"surface_color": "red"}, {"logo_text": "<b>Acme</b>"}, {"heading_font": "Comic Sans"}])
def test_the_theme_still_rejects_anything_that_could_carry_markup_or_an_unknown_font(bad) -> None:
    with pytest.raises((ValidationError, UnsafeContentError)):
        ThemeTokens(**bad)


def test_split_steps_reads_numbered_lines_and_arrows_but_leaves_one_sentence_alone() -> None:
    assert split_steps("1. Enquire 2. Talk to us 3. Travel") == ["Enquire", "Talk to us", "Travel"]
    assert split_steps("Enquire\nTalk to us\nTravel") == ["Enquire", "Talk to us", "Travel"]
    assert split_steps("Enquire → Talk to us → Travel") == ["Enquire", "Talk to us", "Travel"]
    assert split_steps("We look after you from the first message to the flight home.") == ["We look after you from the first message to the flight home."]


# --- the new blocks reject what the rest of the schema rejects -------------------------------------------------------------------------------------


def test_new_blocks_validate_and_reject_markup_and_oversize() -> None:
    SectionSpec(component_type=ComponentType.STEPS, props=StepsProps(title="How it works", items=STEPS).model_dump())
    SectionSpec(component_type=ComponentType.FAQ, props=FaqProps(items=[{"question": "Q?", "answer": "A."}]).model_dump())
    SectionSpec(component_type=ComponentType.TRUST_BAR, props=TrustBarProps(items=["Patients from Qatar"]).model_dump())
    for model, props in [
        (StepsProps, {"items": [{"title": "<script>x</script>"}]}),
        (StepsProps, {"items": [{"title": "t"}] * 9}),
        (FaqProps, {"items": [{"question": "q", "answer": "see javascript:alert(1)"}]}),
        (TrustBarProps, {"items": ["<b>x</b>"]}),
        (TrustBarProps, {"items": ["x"] * 7}),
        (HeroProps, {"headline": "h", "highlights": ["a"] * 6}),
        (HeroProps, {"headline": "h", "secondary_cta_url": "javascript:alert(1)"}),
    ]:
        with pytest.raises((ValidationError, UnsafeContentError)):
            model(**props)
    with pytest.raises((ValidationError, UnsafeContentError)):
        SectionSpec(component_type=ComponentType.STEPS, props={"items": [], "onclick": "x"})


def test_only_data_bound_blocks_may_carry_a_data_source() -> None:
    with pytest.raises(Exception):
        SectionSpec(component_type=ComponentType.FAQ, props={"items": []}, data_source={"provider_key": "x.y"})


# --- generation: designed, but never invented ------------------------------------------------------------------------------------------------------


async def test_a_business_that_states_its_journey_and_places_gets_steps_trust_bar_faq_and_a_brand(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _with(blueprint_service, tid, CUSTOMER_JOURNEY={"steps": STEPS}, GEOGRAPHY={"highlights": ["Patients from Qatar", "Treatment in Turkey"]})
    result = await generation_service.generate_from_active_blueprint(tid, actor_id=None)
    home = _home(result)
    types = _types(home)
    assert types[0] == "HERO" and types[1] == "TRUST_BAR"
    assert "STEPS" in types and "FAQ" in types and types[-2:] == ["CTA", "FOOTER"]
    hero = home.sections[0].props
    assert hero["highlights"] == [s["title"] for s in STEPS] and hero["highlights_title"] == "How it works"
    assert next(s for s in home.sections if s.component_type.value == "TRUST_BAR").props["items"] == ["Patients from Qatar", "Treatment in Turkey"]
    steps = next(s for s in home.sections if s.component_type.value == "STEPS").props["items"]
    assert [i["title"] for i in steps] == [s["title"] for s in STEPS]
    faq = next(s for s in home.sections if s.component_type.value == "FAQ").props["items"]
    assert [q["question"] for q in faq] == ["How does it work?", "How can I get in touch?"]  # no consent answer: this tenant is not consent-gated
    # the How it works page is the same steps, not a paragraph
    page = next(p for p in result.specification.pages if p.slug == "how-it-works")
    assert _types(page) == ["STEPS", "FOOTER"]
    theme = result.specification.theme
    assert theme.logo_text == "Acme Clinic"
    assert theme.primary_color in {look.primary for look in LOOKS} and theme.surface_color in {look.surface for look in LOOKS}
    assert theme.spacing_scale.value == "SPACIOUS" and theme.text_direction.value == "LTR"
    assert result.provenance["how_it_works"] == "USER_STATED" and result.provenance["brand"] == "SYSTEM_DEFAULT"


async def test_nothing_stated_means_nothing_shown(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _with(blueprint_service, tid)
    result = await generation_service.generate_from_active_blueprint(tid, actor_id=None)
    home = _home(result)
    assert "STEPS" not in _types(home) and "TRUST_BAR" not in _types(home)
    assert "FAQ" not in _types(home)  # one honest answer (how to get in touch) is not a FAQ
    assert all(p.slug != "how-it-works" for p in result.specification.pages)
    assert home.sections[0].props["highlights"] == [] and home.sections[0].props["highlights_title"] is None
    assert result.provenance["how_it_works"] == "SYSTEM_DEFAULT"


async def test_the_details_answer_appears_only_for_a_business_that_must_collect_consent(blueprint_service, generation_service, monkeypatch) -> None:  # noqa: F811
    async def gated(*_a, **_k):
        return True

    monkeypatch.setattr(wgs, "tenant_requires_consent", gated)
    tid = uuid.uuid4()
    await _with(blueprint_service, tid)
    home = _home(await generation_service.generate_from_active_blueprint(tid, actor_id=None))
    faq = next(s for s in home.sections if s.component_type.value == "FAQ").props["items"]
    assert faq[0]["question"] == "What happens to the details I send?" and "Nothing is kept until you agree" in faq[0]["answer"]


async def test_unsafe_or_too_few_steps_are_dropped_never_repaired(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _with(blueprint_service, tid, CUSTOMER_JOURNEY={"steps": [{"title": "Fine step"}, {"title": "<script>alert(1)</script>"}]})
    result = await generation_service.generate_from_active_blueprint(tid, actor_id=None)
    assert "STEPS" not in _types(_home(result))  # one safe step left: not a process
    assert result.provenance["how_it_works"] == "SYSTEM_DEFAULT"


async def test_a_journey_written_as_one_block_is_split_into_steps(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _with(blueprint_service, tid, CUSTOMER_JOURNEY={"description": "1. Send an enquiry 2. We call you back 3. You choose a clinic"})
    result = await generation_service.generate_from_active_blueprint(tid, actor_id=None)
    steps = next(s for s in _home(result).sections if s.component_type.value == "STEPS").props["items"]
    assert [i["title"] for i in steps] == ["Send an enquiry", "We call you back", "You choose a clinic"]


async def test_an_arabic_business_gets_a_right_to_left_site(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _build_active_blueprint(blueprint_service, tid, business_name="عيادة النور", description="نساعد المرضى على السفر للعلاج.")
    result = await generation_service.generate_from_active_blueprint(tid, actor_id=None)
    assert result.specification.theme.text_direction.value == "RTL" and result.specification.theme.heading_font == "Cairo"


async def test_no_button_is_generated_that_goes_nowhere(blueprint_service, generation_service) -> None:  # noqa: F811
    from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, BlueprintSectionKey as K, ClaimProvenance, ClaimType

    tid = uuid.uuid4()
    blueprint = await blueprint_service.get_or_create_draft(tid, created_by=None)
    for key in MINIMUM_BAR_SECTIONS:  # no contact details at all
        claim = await blueprint_service.propose_claim(
            tid, blueprint.id, section_key=key.value, claim_type=ClaimType.FACT.value, key=f"{key.value.lower()}.v",
            value="ok" if key != K.REQUIRED_CAPABILITIES else ["x"], confidence=None, provenance=ClaimProvenance.USER_STATED.value,
            discovery_turn_id=None, evidence_ref=None,
        )
        await blueprint_service.confirm_claim(tid, claim.id, confirmed_by=None)
    await blueprint_service.activate(tid, blueprint.id, activated_by=None)
    result = await generation_service.generate_from_active_blueprint(tid, actor_id=None)
    for page in result.specification.pages:
        assert "CTA" not in _types(page)
        for s in page.sections:
            assert "0000000000" not in str(s.props)
    hero = _home(result).sections[0].props
    assert hero["cta_url"] is None and hero["cta_text"] is None


async def test_a_stated_phone_becomes_a_call_button_and_a_secondary_action(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _with(blueprint_service, tid, COMMUNICATIONS={"contact_email": "hello@acme.example", "contact_phone": "+966 50 123 4567"})
    hero = _home(await generation_service.generate_from_active_blueprint(tid, actor_id=None)).sections[0].props
    assert hero["cta_url"] == "mailto:hello@acme.example" and hero["secondary_cta_url"] == "tel:+966 50 123 4567" and hero["secondary_cta_text"] == "Call us"


async def test_a_phone_with_odd_characters_is_never_put_in_a_link(blueprint_service, generation_service) -> None:  # noqa: F811
    tid = uuid.uuid4()
    await _with(blueprint_service, tid, COMMUNICATIONS={"contact_email": "hello@acme.example", "contact_phone": "555 \"onclick=x"})
    hero = _home(await generation_service.generate_from_active_blueprint(tid, actor_id=None)).sections[0].props
    assert hero["secondary_cta_url"] is None


async def test_medical_tourism_setup_seeds_a_journey_and_places_the_site_can_show(client, halla, blueprint_service, generation_service) -> None:  # noqa: F811
    from tests.test_business_builder_api import _seed_registry
    from tests.test_consent_gate_intake import _gated
    from tests.test_mt_setup import FORM

    try:
        await _seed_registry()  # the test database has no migration seed; re-seeding is harmless
    except Exception:  # noqa: BLE001 - already seeded
        pass
    token, tid = await _gated(client, "Setup Co", "setupco@example.com", wording=())
    assert (await client.post("/api/v1/business-setup/medical-tourism", json=FORM, headers=_h(token))).status_code == 200
    result = await generation_service.generate_from_active_blueprint(uuid.UUID(str(tid)), actor_id=None)
    home = _home(result)
    assert "STEPS" in _types(home) and "TRUST_BAR" in _types(home)
    bar = next(s for s in home.sections if s.component_type.value == "TRUST_BAR").props["items"]
    assert len(bar) == 2 and bar[0].startswith("Patients from ") and bar[1].startswith("Treatment in ")
    titles = [i["title"] for i in next(s for s in home.sections if s.component_type.value == "STEPS").props["items"]]
    assert "You decide about a clinic" in titles
    WebsiteSpecification.model_validate(result.specification.model_dump())
