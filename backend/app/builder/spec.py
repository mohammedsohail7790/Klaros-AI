"""The ProjectSpec: what a generated project is, as validated data.

The planner (LLM or deterministic) proposes one; `ProjectSpec.model_validate` is the only gate. Nothing downstream
trusts model output that has not passed through here. Routes, not a fixed page count, define the site: the number of
pages is whatever the requirements need, up to `MAX_ROUTES` (a resource limit, not a design assumption).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ROUTES = 60
MAX_ENTITIES = 12
MAX_RECORDS_PER_ENTITY = 24

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ROUTE = re.compile(r"^/(?:[a-z0-9]+(?:-[a-z0-9]+)*|\[slug\])?(?:/(?:[a-z0-9]+(?:-[a-z0-9]+)*|\[slug\]))*$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_MARKUP = re.compile(r"<\s*/?\s*(script|iframe|object|embed|style|link|meta|base|form)\b|javascript:|on\w+\s*=", re.I)


class UnsafeSpecError(ValueError):
    pass


def clean_text(value: str, *, max_length: int = 400) -> str:
    """Plain text only: no markup or script-ish content ever enters a spec."""
    if not isinstance(value, str):
        raise UnsafeSpecError("text must be a string")
    v = " ".join(value.split())
    if len(v) > max_length:
        raise UnsafeSpecError(f"text longer than {max_length} characters")
    if _MARKUP.search(v) or "<" in v or ">" in v:
        raise UnsafeSpecError("markup is not allowed in project content")
    return v


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Direction(StrEnum):
    MINIMAL_EDITORIAL = "minimal_editorial"
    PREMIUM_SAAS = "premium_saas"
    ENTERPRISE_TECH = "enterprise_tech"
    ECOMMERCE = "ecommerce"
    REAL_ESTATE = "real_estate"
    CREATIVE_PORTFOLIO = "creative_portfolio"
    ARCHITECTURE = "architecture"
    CONSUMER_APP = "consumer_app"
    DARK_DASHBOARD = "dark_dashboard"
    PLAYFUL_STARTUP = "playful_startup"


class FontStack(StrEnum):
    """System font stacks only: no third-party font request, no tracking, no layout shift from late fonts."""

    SANS = "sans"
    GEOMETRIC = "geometric"
    SERIF = "serif"
    MONO = "mono"
    ROUNDED = "rounded"


class Palette(_Model):
    primary: str
    secondary: str
    accent: str
    background: str
    surface: str
    text: str
    muted: str

    @field_validator("*")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX.match(v):
            raise UnsafeSpecError(f"not a #rrggbb colour: {v!r}")
        return v.lower()


class DesignBrief(_Model):
    """Design choices. `None` means "not chosen: use the direction's default" - stored explicitly so a spec that is saved and loaded again
    (or patched by an edit) keeps meaning exactly the same thing."""

    direction: Direction = Direction.PREMIUM_SAAS
    personality: str = Field(default="Clear, confident, modern", max_length=200)
    palette: Palette | None = None
    heading_font: FontStack | None = None
    body_font: FontStack | None = None
    radius: Literal["none", "sm", "md", "lg", "xl"] | None = None
    density: Literal["compact", "comfortable", "spacious"] | None = None
    hero_layout: Literal["split", "centered", "editorial", "stacked"] | None = None
    card_style: Literal["flat", "bordered", "elevated"] | None = None
    dark: bool | None = None
    sticky_header: bool = True

    @field_validator("personality")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v, max_length=200)


class FieldSpec(_Model):
    name: str
    type: Literal["text", "number", "price", "image", "tags", "date", "boolean", "longtext"] = "text"
    filterable: bool = False
    sortable: bool = False
    searchable: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _FIELD.match(v):
            raise UnsafeSpecError(f"invalid field name: {v!r}")
        return v


class EntitySpec(_Model):
    """A kind of record the site lists (properties, products, posts, menu items...). Sample records are labelled example content."""

    name: str  # plural, URL-safe: "properties"
    singular: str
    fields: list[FieldSpec] = Field(default_factory=list, max_length=14)
    records: list[dict[str, str | int | float | bool | list[str]]] = Field(default_factory=list, max_length=MAX_RECORDS_PER_ENTITY)

    @field_validator("name")
    @classmethod
    def _plural(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise UnsafeSpecError(f"entity names must be url-safe: {v!r}")
        return v

    @field_validator("singular")
    @classmethod
    def _singular(cls, v: str) -> str:
        return clean_text(v, max_length=60)

    @model_validator(mode="after")
    def _records_use_declared_fields(self) -> "EntitySpec":
        declared = {f.name for f in self.fields} | {"slug", "title"}
        for r in self.records:
            for k, val in r.items():
                if k not in declared:
                    raise UnsafeSpecError(f"record uses undeclared field {k!r}")
                if isinstance(val, str):
                    clean_text(val, max_length=1200)
                if isinstance(val, list):
                    for item in val:
                        clean_text(item, max_length=80)
            slug = r.get("slug")
            if slug is not None and not _SLUG.match(str(slug)):
                raise UnsafeSpecError(f"record slug is not url-safe: {slug!r}")
        return self


class SectionKind(StrEnum):
    HERO = "hero"
    LOGOS = "facts"  # a plain strip of stated facts - never invented logos or counts
    FEATURES = "features"
    SPLIT = "split"
    STEPS = "steps"
    PRICING = "pricing"
    FAQ = "faq"
    CTA = "cta"
    CONTENT = "content"
    COLLECTION = "collection"  # listing with search / filters / sort driven by an entity
    DETAIL = "detail"  # one record, on a detail route
    FORM = "form"  # enquiry / contact form
    ARTICLE_LIST = "article_list"
    ARTICLE = "article"
    GALLERY = "gallery"
    MENU = "menu"
    HOURS = "hours"
    TEAM = "team"
    DASHBOARD = "dashboard"  # client-side data views over the entity records

    # kept for the validator's benefit
    @classmethod
    def requires_entity(cls) -> set["SectionKind"]:
        return {cls.COLLECTION, cls.DETAIL, cls.ARTICLE_LIST, cls.ARTICLE, cls.GALLERY, cls.MENU, cls.DASHBOARD}


class Block(_Model):
    """One titled piece inside a section: a feature, plan, step, question, fact."""

    title: str
    body: str | None = None
    meta: str | None = None  # e.g. a price for a plan, an icon key for a feature
    items: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("title")
    @classmethod
    def _t(cls, v: str) -> str:
        return clean_text(v, max_length=160)

    @field_validator("body")
    @classmethod
    def _b(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, max_length=900)

    @field_validator("meta")
    @classmethod
    def _m(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, max_length=80)

    @field_validator("items")
    @classmethod
    def _i(cls, v: list[str]) -> list[str]:
        return [clean_text(x, max_length=160) for x in v]


class SectionPlan(_Model):
    kind: SectionKind
    heading: str | None = None
    eyebrow: str | None = None
    body: str | None = None
    blocks: list[Block] = Field(default_factory=list, max_length=12)
    entity: str | None = None
    cta_label: str | None = None
    cta_href: str | None = None  # a route path ("/pricing") or an https/mailto/tel URL
    example: bool = False  # True = stand-in content the owner must review before publishing

    @field_validator("heading", "eyebrow", "cta_label")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, max_length=160)

    @field_validator("body")
    @classmethod
    def _body(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, max_length=1600)

    @field_validator("cta_href")
    @classmethod
    def _href(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.startswith("/") or re.match(r"^(https://|mailto:|tel:)", v, re.I):
            if "<" in v or " " in v or "javascript:" in v.lower():
                raise UnsafeSpecError("unsafe link")
            return v
        raise UnsafeSpecError("links must be a /route, https://, mailto: or tel:")


class RoutePlan(_Model):
    path: str
    title: str
    purpose: str = ""
    nav: bool = True
    nav_label: str | None = None
    requires_auth: bool = False
    sections: list[SectionPlan] = Field(default_factory=list, max_length=20)
    entity: str | None = None  # set on list and detail routes

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if len(v) > 120 or not _ROUTE.match(v):
            raise UnsafeSpecError(f"invalid route path: {v!r}")
        return v

    @field_validator("title", "nav_label")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, max_length=100)

    @field_validator("purpose")
    @classmethod
    def _p(cls, v: str) -> str:
        return clean_text(v, max_length=300)

    @property
    def is_detail(self) -> bool:
        return self.path.endswith("/[slug]")


class FeatureStatus(StrEnum):
    IMPLEMENTED = "implemented"  # works in the generated static project (verified by validation)
    CLIENT_SIDE = "client_side"  # works in the visitor's browser only (localStorage), stated plainly
    NEEDS_SETUP = "needs_setup"  # real, but needs a configured integration (e.g. the enquiry endpoint)
    UNSUPPORTED = "unsupported"  # cannot be done in a static project; not faked


class FeatureReq(_Model):
    key: str
    label: str
    status: FeatureStatus = FeatureStatus.IMPLEMENTED
    note: str = ""

    @field_validator("label", "note")
    @classmethod
    def _t(cls, v: str) -> str:
        return clean_text(v, max_length=300)


class ProjectSpec(_Model):
    name: str
    tagline: str = ""
    summary: str = ""
    industry: str = ""
    audience: str = ""
    contact_email: str | None = None
    design: DesignBrief = Field(default_factory=DesignBrief)
    routes: list[RoutePlan] = Field(default_factory=list, max_length=MAX_ROUTES)
    entities: list[EntitySpec] = Field(default_factory=list, max_length=MAX_ENTITIES)
    features: list[FeatureReq] = Field(default_factory=list, max_length=40)
    assumptions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = clean_text(v, max_length=80)
        if not v:
            raise UnsafeSpecError("a project needs a name")
        return v

    @field_validator("tagline", "summary", "industry", "audience")
    @classmethod
    def _text(cls, v: str) -> str:
        return clean_text(v, max_length=600)

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not re.match(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$", v):
            raise UnsafeSpecError("invalid contact email")
        return v

    @field_validator("assumptions")
    @classmethod
    def _assume(cls, v: list[str]) -> list[str]:
        return [clean_text(x, max_length=300) for x in v]

    @model_validator(mode="after")
    def _consistent(self) -> "ProjectSpec":
        if not self.routes:
            raise UnsafeSpecError("a project needs at least one route")
        paths = [r.path for r in self.routes]
        if len(set(paths)) != len(paths):
            raise UnsafeSpecError("duplicate route paths")
        if "/" not in paths:
            raise UnsafeSpecError("a project needs a home route '/'")
        entities = {e.name for e in self.entities}
        for r in self.routes:
            if r.entity and r.entity not in entities:
                raise UnsafeSpecError(f"route {r.path} uses unknown entity {r.entity!r}")
            if r.is_detail and not r.entity:
                raise UnsafeSpecError(f"detail route {r.path} must name its entity")
            for s in r.sections:
                if s.kind in SectionKind.requires_entity():
                    ent = s.entity or r.entity
                    if not ent or ent not in entities:
                        raise UnsafeSpecError(f"section {s.kind} on {r.path} needs a known entity")
        # every internal link must point at a real route (detail routes are linked per record)
        known = set(paths)
        for r in self.routes:
            for s in r.sections:
                if s.cta_href and s.cta_href.startswith("/") and s.cta_href not in known:
                    raise UnsafeSpecError(f"{r.path}: link to unknown route {s.cta_href}")
        return self

    # ---- convenience ----
    def route(self, path: str) -> RoutePlan | None:
        return next((r for r in self.routes if r.path == path), None)

    def entity(self, name: str) -> EntitySpec | None:
        return next((e for e in self.entities if e.name == name), None)

    def nav_routes(self) -> list[RoutePlan]:
        return [r for r in self.routes if r.nav and not r.is_detail]
