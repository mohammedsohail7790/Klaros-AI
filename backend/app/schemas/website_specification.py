"""Phase 11 (PHASE_11_WEBSITE_BUILDER_DESIGN.md §9/§13): the canonical,
strict, versionable `WebsiteSpecification` schema — the one gate every
generated or hand-edited website must pass before any row is written
(`app/services/website_service.py`).

CORE SECURITY PROPERTY (never weaken this without updating
tests/test_website_renderer_security.py and tests/test_website_ai_safety.py):
there is NO field anywhere in this module that accepts raw HTML, CSS, or
JavaScript. Every text field is a plain string, length-capped, and rejected
(never silently stripped — silent stripping can turn "reject this" into
"partially render this," which is its own failure mode) if it contains any
HTML/script/URL-injection marker. Every URL field is restricted to an
explicit scheme allowlist. `component_type` is a closed enum — an unknown
value is a validation error, never silently ignored or passed through
(HARD SCOPE / Phase 3's explicit requirement).

This schema is untrusted-input-shaped even when its source is our own AI
generation path (`app/services/website_generation_service.py`): AI output is
validated identically to a human's hand-edit via the API — see
`app/services/website_generation_service.py`'s module docstring and
tests/test_website_ai_safety.py.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared sanitization primitives
# ---------------------------------------------------------------------------

# Deliberately broad and rejecting (not stripping): any of these substrings
# anywhere in a plain-text field is treated as an attempted injection, not
# formatting to clean up. `<`/`>` alone (not just "<script") are banned
# because these fields are plain text — a business's real name/description
# legitimately never needs an angle bracket, and allowing them "just in
# case" is exactly the crack an XSS payload needs.
_UNSAFE_TEXT_MARKERS = (
    "<",
    ">",
    "javascript:",
    "data:text/html",
    "vbscript:",
    "onerror=",
    "onload=",
    "onclick=",
    "onmouseover=",
)

_ALLOWED_URL_SCHEMES = ("http://", "https://", "mailto:", "tel:")

_MAX_SHORT_TEXT = 200
_MAX_LONG_TEXT = 4000


class UnsafeContentError(ValueError):
    """Raised (as a pydantic ValidationError wrapper) when generated or
    user-supplied content contains a structurally unsafe marker. Callers
    (website_generation_service, the section-edit API) must treat this as a
    hard rejection, never a "try to fix it up" signal — see Phase 12/14."""


def reject_unsafe_text(value: str, *, max_length: int = _MAX_LONG_TEXT) -> str:
    if not isinstance(value, str):
        raise UnsafeContentError("Text content must be a string")
    if len(value) > max_length:
        raise UnsafeContentError(f"Text content exceeds max length {max_length}")
    lowered = value.lower()
    for marker in _UNSAFE_TEXT_MARKERS:
        if marker in lowered:
            raise UnsafeContentError(f"Text content contains a disallowed marker: {marker!r}")
    return value


def reject_unsafe_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnsafeContentError("URL must be a non-empty string")
    if len(value) > _MAX_SHORT_TEXT:
        raise UnsafeContentError("URL exceeds max length")
    lowered = value.strip().lower()
    if not lowered.startswith(_ALLOWED_URL_SCHEMES):
        raise UnsafeContentError(
            f"URL scheme not allowed (only {_ALLOWED_URL_SCHEMES}): {value!r}"
        )
    # Defense in depth: even inside an allowed scheme, no embedded markup.
    for marker in ("<", ">", "javascript:"):
        if marker in lowered:
            raise UnsafeContentError(f"URL contains a disallowed marker: {marker!r}")
    return value


_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def reject_unsafe_slug(value: str) -> str:
    if not isinstance(value, str) or not _SLUG_RE.match(value) or len(value) > 100:
        raise UnsafeContentError(f"Invalid slug: {value!r}")
    return value


ShortText = Annotated[str, Field(max_length=_MAX_SHORT_TEXT)]
LongText = Annotated[str, Field(max_length=_MAX_LONG_TEXT)]


class _SafeModel(BaseModel):
    """Base for every content model in this module: forbids unknown fields
    (an unknown prop is rejected, never silently ignored — Phase 3) and
    disallows arbitrary extra config."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Theme tokens (design doc §5) — structured design tokens only, no raw CSS.
# ---------------------------------------------------------------------------

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Small, closed allowlist — never an arbitrary font-family string (which
# could otherwise be used to smuggle a CSS `url()` or `@import`).
_ALLOWED_FONTS = (
    "system-ui",
    "Inter",
    "Georgia",
    "Roboto",
    "Merriweather",
    "Poppins",
)


class SpacingScale(StrEnum):
    COMPACT = "COMPACT"
    COMFORTABLE = "COMFORTABLE"
    SPACIOUS = "SPACIOUS"


class RadiusScale(StrEnum):
    NONE = "NONE"
    SM = "SM"
    MD = "MD"
    LG = "LG"


class ShadowScale(StrEnum):
    NONE = "NONE"
    SM = "SM"
    MD = "MD"


class ButtonVariant(StrEnum):
    SOLID = "SOLID"
    OUTLINE = "OUTLINE"
    GHOST = "GHOST"


class ThemeTokens(_SafeModel):
    primary_color: str = "#1A1A2E"
    secondary_color: str = "#E94560"
    background_color: str = "#FFFFFF"
    text_color: str = "#1A1A2E"
    heading_font: str = "system-ui"
    body_font: str = "system-ui"
    spacing_scale: SpacingScale = SpacingScale.COMFORTABLE
    radius_scale: RadiusScale = RadiusScale.MD
    shadow_scale: ShadowScale = ShadowScale.SM
    container_width_px: int = Field(default=1200, ge=320, le=1600)
    button_variant: ButtonVariant = ButtonVariant.SOLID

    @field_validator("primary_color", "secondary_color", "background_color", "text_color")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        if not _HEX_COLOR_RE.match(v):
            raise UnsafeContentError(f"Not a valid #RRGGBB hex color: {v!r}")
        return v

    @field_validator("heading_font", "body_font")
    @classmethod
    def _validate_font(cls, v: str) -> str:
        if v not in _ALLOWED_FONTS:
            raise UnsafeContentError(f"Font {v!r} not in allowlist {_ALLOWED_FONTS}")
        return v


# ---------------------------------------------------------------------------
# Navigation (design doc §6) — points only at pages in the same version,
# never an arbitrary URL.
# ---------------------------------------------------------------------------


class NavigationItem(_SafeModel):
    label: ShortText
    page_slug: str

    @field_validator("label")
    @classmethod
    def _validate_label(cls, v: str) -> str:
        return reject_unsafe_text(v, max_length=_MAX_SHORT_TEXT)

    @field_validator("page_slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        return reject_unsafe_slug(v)


class NavigationSpec(_SafeModel):
    items: list[NavigationItem] = Field(default_factory=list, max_length=50)


# ---------------------------------------------------------------------------
# SEO (design doc §7)
# ---------------------------------------------------------------------------


class SeoMetadata(_SafeModel):
    title: ShortText | None = None
    description: LongText | None = None
    og_image_url: ShortText | None = None

    @field_validator("title", "description")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v, max_length=_MAX_LONG_TEXT)

    @field_validator("og_image_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_url(v)


# ---------------------------------------------------------------------------
# Component vocabulary (design doc §Phase 3) — the minimum set for a
# generic business site + the Medical Tourism validation scenario. See
# PHASE_11_WEBSITE_BUILDER_DESIGN.md §18 for the deliberately-deferred
# component types (IMAGE, CARD_GRID, TESTIMONIAL, FAQ).
# ---------------------------------------------------------------------------


class ComponentType(StrEnum):
    HERO = "HERO"
    TEXT = "TEXT"
    CTA = "CTA"
    FEATURE_GRID = "FEATURE_GRID"
    PROVIDER_DIRECTORY = "PROVIDER_DIRECTORY"
    PROCEDURE_LIST = "PROCEDURE_LIST"
    CONTACT_FORM = "CONTACT_FORM"
    FOOTER = "FOOTER"


class DataSourceRef(_SafeModel):
    """The generic vertical-data-binding pointer (design doc §11/§12).
    `provider_key` is looked up in `app/services/website_data_providers.py`'s
    registry at render time — never a vertical-name branch."""

    provider_key: ShortText
    params: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=20)

    @field_validator("provider_key")
    @classmethod
    def _validate_provider_key(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_]+\.[a-z0-9_]+$", v):
            raise UnsafeContentError(f"provider_key must be '<namespace>.<key>': {v!r}")
        return v


class HeroProps(_SafeModel):
    headline: ShortText
    subheadline: LongText | None = None
    cta_text: ShortText | None = None
    cta_url: ShortText | None = None
    background_image_url: ShortText | None = None

    @field_validator("headline", "subheadline", "cta_text")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)

    @field_validator("cta_url", "background_image_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_url(v)


class TextProps(_SafeModel):
    heading: ShortText | None = None
    body: LongText

    @field_validator("heading", "body")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)


class CtaProps(_SafeModel):
    heading: ShortText
    body: LongText | None = None
    button_text: ShortText
    button_url: ShortText

    @field_validator("heading", "body", "button_text")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)

    @field_validator("button_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return reject_unsafe_url(v)


class FeatureItem(_SafeModel):
    title: ShortText
    description: LongText | None = None
    icon_key: Literal[
        "check", "star", "shield", "heart", "globe", "clock", "phone", "map-pin", "none"
    ] = "none"

    @field_validator("title", "description")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)


class FeatureGridProps(_SafeModel):
    title: ShortText | None = None
    items: list[FeatureItem] = Field(default_factory=list, max_length=12)

    @field_validator("title")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)


class ProviderDirectoryProps(_SafeModel):
    """Generic — no Medical-Tourism-specific field name anywhere. Actual
    provider rows are supplied at render time by whatever
    `data_source.provider_key` resolves to (design doc §11)."""

    title: ShortText | None = None
    empty_state_text: ShortText | None = None

    @field_validator("title", "empty_state_text")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)


class ProcedureListProps(_SafeModel):
    title: ShortText | None = None
    empty_state_text: ShortText | None = None

    @field_validator("title", "empty_state_text")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)


class ContactFormField(StrEnum):
    NAME = "NAME"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    MESSAGE = "MESSAGE"


class ContactFormProps(_SafeModel):
    title: ShortText | None = None
    fields: list[ContactFormField] = Field(default_factory=lambda: [ContactFormField.NAME, ContactFormField.EMAIL, ContactFormField.MESSAGE])
    submit_label: ShortText = "Send"

    @field_validator("title", "submit_label")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v)


class FooterLink(_SafeModel):
    label: ShortText
    url: ShortText

    @field_validator("label")
    @classmethod
    def _validate_label(cls, v: str) -> str:
        return reject_unsafe_text(v, max_length=_MAX_SHORT_TEXT)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return reject_unsafe_url(v)


class FooterProps(_SafeModel):
    business_name: ShortText
    contact_email: ShortText | None = None
    contact_phone: ShortText | None = None
    links: list[FooterLink] = Field(default_factory=list, max_length=20)

    @field_validator("business_name", "contact_phone")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return reject_unsafe_text(v, max_length=_MAX_SHORT_TEXT)

    @field_validator("contact_email")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > _MAX_SHORT_TEXT or "@" not in v:
            raise UnsafeContentError(f"Invalid email: {v!r}")
        return reject_unsafe_text(v, max_length=_MAX_SHORT_TEXT)


_PROPS_BY_TYPE: dict[ComponentType, type[_SafeModel]] = {
    ComponentType.HERO: HeroProps,
    ComponentType.TEXT: TextProps,
    ComponentType.CTA: CtaProps,
    ComponentType.FEATURE_GRID: FeatureGridProps,
    ComponentType.PROVIDER_DIRECTORY: ProviderDirectoryProps,
    ComponentType.PROCEDURE_LIST: ProcedureListProps,
    ComponentType.CONTACT_FORM: ContactFormProps,
    ComponentType.FOOTER: FooterProps,
}

# Only these two component types are permitted to carry a data_source
# pointer — a HERO/TEXT/CTA/FEATURE_GRID/CONTACT_FORM/FOOTER section with a
# data_source is rejected (there is no generic meaning for it there).
_DATA_BOUND_TYPES = {ComponentType.PROVIDER_DIRECTORY, ComponentType.PROCEDURE_LIST}


class SectionSpec(_SafeModel):
    component_type: ComponentType
    props: dict
    data_source: DataSourceRef | None = None
    order_index: int = 0

    @model_validator(mode="after")
    def _validate_props_and_data_source(self) -> "SectionSpec":
        schema = _PROPS_BY_TYPE.get(self.component_type)
        if schema is None:
            # Unreachable given ComponentType is a closed enum Pydantic
            # already validated `component_type` against, but fail loud
            # rather than silently pass through (Phase 3's explicit rule).
            raise UnsafeContentError(f"Unknown component_type: {self.component_type!r}")
        # Re-validate props strictly against the per-type schema — raises
        # (via pydantic) on any unknown/malformed prop.
        validated = schema.model_validate(self.props)
        object.__setattr__(self, "props", validated.model_dump())
        if self.data_source is not None and self.component_type not in _DATA_BOUND_TYPES:
            raise UnsafeContentError(
                f"component_type {self.component_type!r} does not accept a data_source"
            )
        return self


class PageSpec(_SafeModel):
    slug: str
    title: ShortText
    seo: SeoMetadata = Field(default_factory=SeoMetadata)
    sections: list[SectionSpec] = Field(default_factory=list, max_length=40)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        return reject_unsafe_slug(v)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        return reject_unsafe_text(v, max_length=_MAX_SHORT_TEXT)


class WebsiteSpecification(_SafeModel):
    """The full renderable specification (design doc §9) — assembled from
    (or, before persistence, destined for) the relational
    Website/WebsiteVersion/WebsitePage/WebsiteSection rows. This model, not
    any JSON column, is the one validation gate every generated or
    hand-edited website must pass (module docstring)."""

    theme: ThemeTokens = Field(default_factory=ThemeTokens)
    navigation: NavigationSpec = Field(default_factory=NavigationSpec)
    seo_defaults: SeoMetadata = Field(default_factory=SeoMetadata)
    pages: list[PageSpec] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _validate_navigation_targets_exist(self) -> "WebsiteSpecification":
        page_slugs = {p.slug for p in self.pages}
        for item in self.navigation.items:
            if item.page_slug not in page_slugs:
                raise UnsafeContentError(
                    f"Navigation item points at unknown page slug: {item.page_slug!r}"
                )
        slugs_seen: set[str] = set()
        for p in self.pages:
            if p.slug in slugs_seen:
                raise UnsafeContentError(f"Duplicate page slug: {p.slug!r}")
            slugs_seen.add(p.slug)
        return self
