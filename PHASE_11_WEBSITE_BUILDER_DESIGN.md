# Phase 11 — Website Builder Foundation: Architecture Design

Status: written before any Phase 11 code (Phase 0 forensic baseline complete, see
`PHASE_11_WEBSITE_BUILDER_IMPLEMENTATION_LOG.md` §2). Verified against the
current, real code in `backend/app/models/`, `backend/app/services/`,
`backend/app/api/v1/` — not against the older architecture markdown files' claims,
which describe an earlier, less-detailed target and are treated as background
context only.

This document answers the 18 mandated questions in order, then states what is
deliberately deferred.

## 1. What is the canonical Website entity?

`Website` (`app/models/website.py`) — one row per tenant "site container". It is
tenant-scoped (`TenantScopedMixin`, like every other Phase 2/3/10 model) and holds
identity/slug data plus a single pointer, `current_published_version_id`, at the
currently-live `WebsiteVersion` (nullable — a `Website` with no published version
yet has no live site). It optionally references the `BusinessBlueprint` it was
generated from (`blueprint_id`, nullable FK) — mirrors `BusinessBlueprint.
vertical_extension_id`'s "nullable FK to the thing this row is derived from"
shape. A tenant may have more than one `Website` row in principle (the schema
does not forbid it), but Phase 11 only ever creates/uses one per tenant — this
mirrors `BusinessBlueprint`'s "at most one ACTIVE row" partial-unique-index
pattern: a partial unique index enforces at most one `Website` per tenant for
now (`uq_websites_one_per_tenant`), because Phase 13's validation scenario and
every service method assume a single site per tenant, and multi-site-per-tenant
is explicitly deferred (§18).

## 2. What is the canonical WebsiteVersion?

`WebsiteVersion` — the versioned envelope, one row per version, full-row
immutable-per-version exactly like `BusinessBlueprint` (module docstring:
"whole-row, immutable-per-version"). It carries:
  - `website_id` FK, `version` int (unique per website)
  - `status`: `DRAFT` / `PUBLISHED` / `SUPERSEDED` (§9)
  - `theme` JSONB — validated design tokens (§4)
  - `navigation` JSONB — validated site-wide nav structure (§7)
  - `seo_defaults` JSONB — site-wide SEO fallback (§8)
  - `generation_provenance` JSONB — which fields were `USER_STATED` /
    `AI_GENERATED` / `SYSTEM_DEFAULT`, reusing Phase 2's exact
    `ClaimProvenance`-shaped vocabulary (§11 below), not a parallel one
  - `created_by`, `published_at`, `published_by`, `supersedes_id`

A `WebsitePage` (§3) and its `WebsiteSection` rows (§4) always belong to exactly
one `WebsiteVersion`. Editing any page/section/theme/navigation on a `DRAFT`
version mutates in place; there is nothing to preserve yet (mirrors Blueprint's
"DRAFT is mutated in place, ACTIVE is versioned" rule exactly). The moment a
version is `PUBLISHED`, the service layer refuses any further mutation to that
version's own rows or its children — verified by a real test attempting the
mutation (Phase 9, `test_website_publishing_lifecycle.py`). "Editing" a
published site means: create a new `DRAFT` version (cloned from the current
published version's pages/sections/theme/navigation), edit that, publish it —
the old `PUBLISHED` row flips to `SUPERSEDED` and is retained forever, never
mutated or deleted. This is the same mechanism `BusinessBlueprintService`
already uses for Blueprint versioning; Phase 11 does not invent a second one.

## 3. How are pages represented?

`WebsitePage` — one row per page within a `WebsiteVersion` (`website_version_id`
FK). Fields: `slug` (unique within the version), `title`, `seo` JSONB (`{title,
description, og_image_url}` — reuses the same URL/text sanitization as every
other field, §16), `order_index`. A page is a relational row, not a JSONB
array entry on the version, because pages need independent identity for
permission-free but query-friendly lookup (`GET /websites/{id}/pages/{slug}`),
stable ordering, and per-page SEO metadata that must be queryable/auditable —
the same reasoning `BlueprintSection` uses for being its own table rather than
a key inside one blueprint JSON blob.

## 4. How are sections represented?

`WebsiteSection` — one row per content block within a `WebsitePage`
(`page_id` FK). Fields: `component_type` (`String`, validated at the service/
schema layer against the fixed `ComponentType` enum, §Phase 3 vocabulary —
**never** validated only by the DB column, so an unknown value is rejected
before it ever reaches a row), `props` JSONB (validated per-component-type
against a strict Pydantic schema, §16), `data_source` JSONB nullable (the
generic vertical-data-binding pointer, §15), `order_index`.

Sections are their own table (not nested JSON on the page) for the same
reason pages are their own table: independent auditability, stable ordering,
and so the renderer can reject/skip one malformed section without discarding
the whole page (§7's "fail safely" requirement is much easier to satisfy at
row granularity than inside one big parsed blob).

## 5. Where are design tokens stored?

On `WebsiteVersion.theme`, one JSONB column, not a separate table. Themes are
small (roughly a dozen fixed keys — colors/typography/spacing/radius/shadow/
container width/button variant, §4), have no independent identity, permission
model, or lifecycle of their own (they only ever change alongside a version),
and are always read/written as a whole unit — exactly the profile
`BlueprintSection.data`'s module docstring uses to justify JSONB-inside-a-
relational-parent instead of a table-per-field. A separate `WebsiteTheme`
table was evaluated and rejected: it would add a join for no querying benefit,
since nothing ever searches "all versions with color X".

## 6. How is navigation represented?

`WebsiteVersion.navigation`, one JSONB column: `{"items": [{"label": str,
"page_slug": str}, ...]}`, validated against a strict Pydantic schema (only
`label`/`page_slug` string fields, no URL field at all — navigation only ever
points at a page already present in the same version, never an arbitrary
external/`javascript:` URL). A separate `WebsiteNavigation` table was
evaluated and rejected for the same reason as Theme (§5): navigation has no
independent lifecycle, is always read/written as a whole array bound to one
version, and is small and bounded (validation caps it at 50 entries, closing
the resource-exhaustion angle from Phase 12).

## 7. How are SEO settings represented?

Two layers, both JSONB, both schema-validated, no raw-HTML field anywhere:
`WebsiteVersion.seo_defaults` (site-wide fallback: default title suffix,
default description, default social image URL) and `WebsitePage.seo`
(per-page override of the same three fields). A page's effective SEO is
`page.seo` merged over `version.seo_defaults` at render time — computed, never
stored redundantly.

## 8. How is draft vs. published state represented?

Entirely on `WebsiteVersion.status` (§9) plus `Website.
current_published_version_id`. There is no separate "is this a draft" boolean
scattered across pages/sections — a page/section's own draft-vs-published
status is always inherited from whichever `WebsiteVersion` row it belongs to,
because it belongs to exactly one version and versions are never shared.

## 9. What exactly is the renderable specification?

A single in-memory Pydantic model, `WebsiteSpecification`
(`app/schemas/website_specification.py`): `{theme: ThemeTokens, navigation:
NavigationSpec, pages: [{slug, title, seo, sections: [{component_type, props,
data_source}]}]}`. This is assembled on demand from the relational rows above
(`WebsiteService.load_specification(tenant_id, version_id)`) — it is **not**
itself a stored column; the relational rows are the source of truth
(mirrors the whole design principle already established for Blueprint:
relational rows first, an assembled read-model second). The
`WebsiteGenerationService` (§10 below) produces one of these in memory,
validates it, and only then persists it into the relational rows —
`WebsiteSpecification.model_validate(...)` succeeding is the one gate a
generated (or hand-edited, via the API) website must pass before any row is
written.

## 10. How does Blueprint data feed the website?

`WebsiteGenerationService.generate_from_blueprint(tenant_id, blueprint)`
(`app/services/website_generation_service.py`) reads the tenant's `ACTIVE`
`BusinessBlueprint` (never `DRAFT`/`SUPERSEDED` — same rule
`RecommendationService` already enforces) via `BusinessBlueprintService`, plus
its `CONFIRMED` claims. It reads exactly the same generic fields
`RecommendationService`/Discovery already treat as canonical: `IDENTITY`
(business name/description), `PRODUCTS_SERVICES`, `CUSTOMERS` (target
market), `GEOGRAPHY`, `COMMUNICATIONS` (contact channels), and
`REQUIRED_CAPABILITIES`. It never invents a business fact the blueprint does
not contain as a `USER_STATED`/`AI_INFERRED` fact — a missing field produces a
`SYSTEM_DEFAULT` placeholder value (e.g. a generic "Contact us" CTA) with its
provenance recorded as `SYSTEM_DEFAULT` in `generation_provenance`, exactly
`BlueprintClaim`'s three-value `ClaimProvenance` vocabulary reused verbatim
(no parallel enum — see the module docstring cross-reference in
`app/schemas/website_specification.py`).

## 11. How does vertical-specific data feed it?

Never by branching on a vertical name. A section's `data_source` field (on
`WebsiteSection`, §4) is `{"provider_key": str, "params": dict}`. Generation
only ever populates `data_source.provider_key` from
`VerticalExtension.capabilities` entries the organization has enabled
(`OrganizationVerticalExtension`) — exactly the same registry lookup
`RecommendationService` already performs for `VERTICAL_EXTENSION_RULE`
recommendations. At render time (§13/§15), a small generic registry,
`WEBSITE_DATA_PROVIDERS: dict[str, Callable]`
(`app/services/website_data_providers.py`), maps a `provider_key` string to an
async function that returns already-structured, already-safe data for that
key. Medical Tourism's own service module
(`app/services/medical_tourism_service.py`) registers its own function under
the key `"medical_tourism.provider_directory"` at import time — the generic
website code only ever does `WEBSITE_DATA_PROVIDERS.get(provider_key)`, a
dict lookup, never an `if vertical == ...` branch. This is the same shape
`IntegrationProviderCatalog.capabilities` tags already use for
`RecommendationService`'s provider matching (§Phase 6).

## 12. Where are Medical Tourism providers surfaced (the key extensibility proof)?

Via the generic `PROVIDER_DIRECTORY` component type (§Phase 3 vocabulary) plus
the `data_source` mechanism above — see §11 and §15. The renderer
(`app/services/website_renderer.py`) contains no reference to "medical
tourism", "hospital", "procedure", or any vertical name anywhere in its
source; `tests/test_website_no_vertical_hardcoding.py` statically greps
`app/services/website_renderer.py` and `app/services/website_generation_
service.py` for the vertical-key literals, extending the existing
`tests/test_vertical_extension_no_hardcoding_guard.py` pattern (§Phase 6/18).

## 13. How are unsafe content types prevented?

Structurally, not by a blocklist bolted on afterward: the `WebsiteSpecification`
Pydantic schema has **no field, on any component type, that accepts raw
HTML/CSS/JS** — every text field is a plain string with a hard length cap and
a rejecting (not silently-stripping) validator that raises on `<`, `>`,
`javascript:`, or any of a short list of dangerous substrings
(`app/schemas/website_specification.py::_reject_unsafe_text`); every URL field
is validated against an explicit scheme allowlist (`http`, `https`, `mailto`,
`tel` only — `_validate_safe_url`). `component_type` is validated against a
closed `ComponentType` `StrEnum` — an unrecognized value is a
`pydantic.ValidationError`, not a silently-ignored/pass-through value (§Phase
3's explicit requirement). The renderer never calls `dangerouslySetInnerHTML`-
equivalent logic (it emits a plain nested-dict render tree, §14) and never
evaluates/imports anything based on spec content.

## 14. What is the publishing boundary?

`WebsiteService.publish_version(tenant_id, website_id, version_id, *,
published_by)`: a single DB transaction that (a) re-validates the version's
assembled `WebsiteSpecification` one final time (defense in depth — belt and
suspenders against a spec that was valid at generation time but has since been
hand-edited into something invalid via the section-edit API), (b) if another
`WebsiteVersion` for this website is currently `PUBLISHED`, flips it to
`SUPERSEDED`, (c) flips the target version to `PUBLISHED`, sets
`published_at`/`published_by`, (d) updates
`Website.current_published_version_id`, (e) writes one `AuditLog` row. Any
failure at (a) aborts the whole transaction — nothing is partially published
(§Phase 9 "publishing must be transactional"). A published version's rows
become immutable from that point on — `WebsiteService`'s every
page/section/theme/navigation-mutating method checks
`version.status == DRAFT` first and raises `WebsiteVersionImmutableError`
otherwise (verified by a real mutation-attempt test).

Unpublishing (explicitly designed, not ad hoc): `WebsiteService.
unpublish(tenant_id, website_id)` clears `Website.
current_published_version_id` to `None` (the public site goes offline) but
does **not** change the `WebsiteVersion.status` away from `PUBLISHED` — the
version's own history/immutability is preserved (it remains queryable/
re-publishable via a fresh `publish_version` call), only the "is this the
live site" pointer is cleared. This mirrors treating `PUBLISHED` as "this
version was, at some point, the site of record" rather than "this version is
currently being served," which is exactly what `Website.
current_published_version_id` already tracks separately.

## 15. What is deliberately deferred?

- Custom domains / DNS automation / CDN / production hosting — the public
  read path (§Phase 8/13 below) is a plain authenticated-free JSON API
  endpoint returning the rendered spec; there is no static-site export, no
  domain binding, and no hosting pipeline. Verified: `grep`ing the existing
  codebase found no DNS/CDN/custom-domain infrastructure anywhere to build on
  (`KLAROS_ENVIRONMENT_CURRENT.md`/`.env.example` confirm no object storage
  either — Phase 0 baseline's finding still holds), so per the phase's own
  "verify, don't assume" instruction this is a real absence, not an oversight.
- A visual drag-and-drop editor and a full CMS — the API is CRUD-shaped
  (create page, add/edit/reorder section, edit theme/navigation), matching
  every other Phase 2-10 domain's own API shape; nothing in the existing
  frontend architecture (`frontend/app/`) has a canvas/drag-and-drop
  primitive to build on, so building one from scratch would be new,
  non-generic infrastructure the phase's HARD SCOPE forbids introducing
  gratuitously.
- Actual Next.js page rendering / a public marketing site frontend (Phase 16):
  the backend (schema, generation, renderer, publish lifecycle, RBAC, audit)
  is fully self-standing and independently testable/verifiable without any
  frontend change — per this phase's own Phase 16 instruction ("if backend
  validation can stand alone without a full editor, keep UI minimal or skip
  it, document the decision"), this phase ships **no** frontend change. The
  renderer's output (§13, a plain nested-dict render tree keyed by
  `component_type`) is designed so that a future Next.js page can map it 1:1
  onto React components via a `component_type -> component` registry without
  any backend change — but building that mapping is explicitly out of this
  phase's scope, given the size of everything else the phase already
  requires and the explicit instruction not to let "frontend scope consume
  the phase."
- `CONTACT_FORM` component: structural only in Phase 11 (renders a described
  form: fields + labels + submit label) — actually wiring a submitted form to
  `POST /public/leads` (the existing public lead-intake endpoint,
  `app/api/v1/public_leads.py`) is a natural, small follow-up but is not
  built here, since it would require frontend form-submission code this phase
  is not building (see above).
- Preview via a random/expiring public token (Phase 8's "if a preview
  token/mechanism is used"): not built. Draft preview in Phase 11 is served
  by the same authenticated, tenant-scoped, RBAC-gated API
  (`GET /websites/{id}/versions/{version}/preview`, requires
  `READ_WEBSITE`) that reads/manages everything else — there is no separate
  token mechanism to get wrong. This sidesteps the entire "random, scoped,
  expiring, non-guessable" token-security surface by simply not introducing
  a second, weaker auth path; a shareable-without-login preview link is
  deferred.
- `IMAGE`, `CARD_GRID`, `TESTIMONIAL`, `FAQ` component types (§Phase 3) —
  the minimum set needed for a generic business site plus the Medical
  Tourism validation scenario is `HERO`, `TEXT`, `CTA`, `FEATURE_GRID`,
  `PROVIDER_DIRECTORY`, `PROCEDURE_LIST`, `CONTACT_FORM`, `FOOTER` (8 types);
  the other four are a straightforward additive follow-up (new
  `ComponentType` enum member + Pydantic props schema + renderer branch) and
  were left out only to keep the reviewable surface of this phase bounded,
  not because of any architectural blocker.
- Multi-website-per-tenant — `Website` has a partial-unique
  one-row-per-tenant index for now (§1); lifting it later is additive (drop
  the index, add a name/slug uniqueness constraint instead) and does not
  require touching `WebsiteVersion`/`WebsitePage`/`WebsiteSection` at all.
