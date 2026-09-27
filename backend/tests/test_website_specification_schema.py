"""Phase 11 (Phase 3/4/12/13 of PHASE_11_WEBSITE_BUILDER_DESIGN.md /
KLAROS the task prompt): schema-level validation coverage for
app/schemas/website_specification.py — the one gate a generated or
hand-edited website must pass.
"""

import pytest
from pydantic import ValidationError

from app.schemas.website_specification import (
    ComponentType,
    DataSourceRef,
    FooterProps,
    HeroProps,
    NavigationItem,
    PageSpec,
    SectionSpec,
    ThemeTokens,
    UnsafeContentError,
    WebsiteSpecification,
)


def _hero_section(**overrides) -> SectionSpec:
    props = {"headline": "Welcome"} | overrides
    return SectionSpec(component_type=ComponentType.HERO, props=props)


def test_valid_specification_round_trips() -> None:
    spec = WebsiteSpecification(
        navigation={"items": [{"label": "Home", "page_slug": "home"}]},
        pages=[PageSpec(slug="home", title="Home", sections=[_hero_section()])],
    )
    assert spec.pages[0].sections[0].component_type == ComponentType.HERO


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "hello <img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        "click onclick=alert(1)",
    ],
)
def test_script_and_html_markers_rejected_in_text_fields(payload: str) -> None:
    with pytest.raises(ValidationError):
        HeroProps(headline=payload)


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "ftp://evil.example"],
)
def test_dangerous_url_schemes_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        HeroProps(headline="ok", cta_url=url)


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com/page", "mailto:a@example.com", "tel:+15551234567"])
def test_allowed_url_schemes_accepted(url: str) -> None:
    HeroProps(headline="ok", cta_url=url)


def test_unknown_component_type_rejected() -> None:
    with pytest.raises(ValidationError):
        SectionSpec(component_type="RAW_HTML", props={})


def test_unknown_prop_field_rejected() -> None:
    with pytest.raises(ValidationError):
        SectionSpec(component_type=ComponentType.HERO, props={"headline": "ok", "raw_html": "<b>x</b>"})


def test_no_component_props_schema_accepts_raw_html_or_script_field() -> None:
    """Structural proof of Phase 11 §13's core security property: not one
    field, across every component's props schema, is named anything that
    would suggest raw markup/script content."""
    from app.schemas.website_specification import _PROPS_BY_TYPE

    forbidden_field_name_fragments = ("html", "script", "raw", "markup", "css")
    for component_type, schema in _PROPS_BY_TYPE.items():
        for field_name in schema.model_fields:
            lowered = field_name.lower()
            assert not any(frag in lowered for frag in forbidden_field_name_fragments), (
                f"{component_type}: field {field_name!r} looks like a raw-content escape hatch"
            )


def test_data_source_rejected_on_non_data_bound_component() -> None:
    with pytest.raises(ValidationError):
        SectionSpec(
            component_type=ComponentType.HERO,
            props={"headline": "ok"},
            data_source=DataSourceRef(provider_key="medical_tourism.provider_directory"),
        )


def test_data_source_accepted_on_provider_directory() -> None:
    section = SectionSpec(
        component_type=ComponentType.PROVIDER_DIRECTORY,
        props={},
        data_source=DataSourceRef(provider_key="medical_tourism.provider_directory", params={"limit": 5}),
    )
    assert section.data_source.provider_key == "medical_tourism.provider_directory"


def test_invalid_provider_key_shape_rejected() -> None:
    with pytest.raises(ValidationError):
        DataSourceRef(provider_key="not-namespaced")


def test_navigation_pointing_at_unknown_page_rejected() -> None:
    with pytest.raises(ValidationError):
        WebsiteSpecification(
            navigation={"items": [{"label": "Ghost", "page_slug": "does-not-exist"}]},
            pages=[PageSpec(slug="home", title="Home", sections=[])],
        )


def test_duplicate_page_slugs_rejected() -> None:
    with pytest.raises(ValidationError):
        WebsiteSpecification(
            pages=[
                PageSpec(slug="home", title="Home", sections=[]),
                PageSpec(slug="home", title="Home Again", sections=[]),
            ]
        )


def test_invalid_slug_rejected() -> None:
    with pytest.raises(ValidationError):
        PageSpec(slug="Not A Slug!", title="x", sections=[])
    with pytest.raises(ValidationError):
        NavigationItem(label="x", page_slug="UPPER_CASE")


def test_theme_hex_color_validated() -> None:
    with pytest.raises(ValidationError):
        ThemeTokens(primary_color="red")
    with pytest.raises(ValidationError):
        ThemeTokens(primary_color="javascript:alert(1)")
    ThemeTokens(primary_color="#123ABC")


def test_theme_font_allowlist_enforced() -> None:
    with pytest.raises(ValidationError):
        ThemeTokens(heading_font="Comic Sans MS")
    ThemeTokens(heading_font="Inter")


def test_theme_container_width_bounded() -> None:
    with pytest.raises(ValidationError):
        ThemeTokens(container_width_px=10)
    with pytest.raises(ValidationError):
        ThemeTokens(container_width_px=99999)


def test_oversized_text_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HeroProps(headline="x" * 500)


def test_deeply_nested_or_oversized_page_list_bounded() -> None:
    """Phase 12: resource-exhaustion via an enormous specification is
    bounded structurally (max_length on every list field), not left to
    the caller's good behavior."""
    with pytest.raises(ValidationError):
        WebsiteSpecification(pages=[PageSpec(slug=f"p{i}", title="x", sections=[]) for i in range(51)])
    with pytest.raises(ValidationError):
        PageSpec(slug="home", title="x", sections=[_hero_section() for _ in range(41)])


def test_footer_email_validated() -> None:
    with pytest.raises(ValidationError):
        FooterProps(business_name="Acme", contact_email="not-an-email")
    FooterProps(business_name="Acme", contact_email="hello@acme.example")


def test_reject_unsafe_text_is_rejecting_not_stripping() -> None:
    """Phase 3/12's explicit rule: unsafe content is REJECTED, never
    silently stripped down to something that looks safe."""
    from app.schemas.website_specification import reject_unsafe_text

    with pytest.raises(UnsafeContentError):
        reject_unsafe_text("Hello <script>alert(1)</script> world")
