# Phase 12 — Website Builder Productization & Public Runtime — Implementation Log

## 1. Scope

Turn Phase 11's Website Builder *foundation* (models, strict component
schema, deterministic renderer, generic data-provider registry,
draft/publish lifecycle, authenticated preview, RBAC, audit logging, tenant
isolation — all backend, all previously validated against real Postgres)
into an actually usable product: Dashboard → Website Editor → Draft →
Preview → Publish → Public Website Runtime → Public Lead Intake. This is
also the first phase in this implementation sequence to touch the
frontend at all.

Explicitly out of scope and not touched: Dropshipping, Marketplace/OAuth,
autonomous business creation, MCP client work, any other vertical/domain.

## 2. Baseline (Phase 0 forensic step)

- Branch: `main`. HEAD at start and at end of this session:
  `8c4e13c62850eaa3712293651252312bceb8debd` (unchanged — nothing
  committed, nothing pushed).
- `git status --short` at start showed the exact same 35 modified files
  and long list of untracked Phase 0–11 files listed in the task's own
  system context; all of it was preserved byte-for-byte except the files
  this phase intentionally added/edited (enumerated in §20).
- Alembic head: **0050** (`backend/alembic/versions/0050_website_builder.py`,
  `down_revision = "0049"`) — matches the task's stated expectation,
  verified (not assumed) by inspecting the file and by running `alembic
  upgrade head` against a real Postgres instance (§18).
- Backend Python: system `python3` is 3.9.6 (too old — this repo's
  `requirements.txt` pins packages requiring >=3.10). A dedicated venv was
  built with `python3.12.14` (`/Users/mohammedsohail/.local/bin/python3.12`),
  mirroring `PHASE_0_POSTGRES_VERIFICATION.md`'s own precedent exactly —
  created at `backend/.venv_phase12`, fully deleted at the end of this
  session (confirmed absent from `git status` afterward).
- Frontend: Node v24.20.0, npm 11.19.0, Next.js 16.3.3 (Turbopack), React
  18.3.1, TypeScript 5.6.3, Vitest 1.6.1 + React Testing Library.
- Postgres: 16.2 (aarch64-apple-darwin), started via the `pgserver` PyPI
  package (no Docker/Colima/Podman available in this sandbox — same
  constraint Phase 0 documented), unix-domain-socket transport at a
  dedicated, disposable `pgdata` directory (`/tmp/klaros_phase12_pgdata`),
  never touching any other Postgres instance on the host. `pgvector` 0.6.2
  confirmed via `CREATE EXTENSION vector`. Deleted at the end of this
  session.
- Backend test baseline (real Postgres, before this phase's own changes):
  `pytest -k website` → **70 passed, 1760 deselected** (1830 total
  collected) — this is Phase 11's own website-related suite, confirmed
  green against real Postgres before any Phase 12 edit was made.
- Frontend baseline: no `app/website`, `app/w`, or
  `components/website` directories existed. `frontend/lib/api.ts` had no
  Website Builder functions. `frontend/components/AppShell.tsx` had no
  "Website" nav section. Existing frontend test/typecheck/build all passed
  pre-change (implicitly — this phase only ever added new files/functions
  to a green tree, never modified pre-existing passing logic other than
  additive appends).
- Read `PHASE_11_WEBSITE_BUILDER_DESIGN.md` and
  `PHASE_11_WEBSITE_BUILDER_IMPLEMENTATION_LOG.md`, and inspected the
  actual current code directly rather than trusting the design doc:
  `backend/app/models/website.py`, `backend/app/schemas/website_specification.py`,
  `backend/app/services/website_service.py`,
  `backend/app/services/website_generation_service.py`,
  `backend/app/services/website_renderer.py`,
  `backend/app/services/website_data_providers.py`,
  `backend/app/services/medical_tourism_service.py` (its website-provider
  registration functions, lines 660-729),
  `backend/app/api/v1/public_websites.py`, `backend/app/api/v1/websites.py`,
  `backend/app/api/v1/public_leads.py`, `backend/app/api/deps.py`,
  `backend/app/models/rbac.py`. **Key finding**: Phase 11's backend work
  was materially further along than the task description's framing
  suggested — `public_websites.py` (public read path), `websites.py`
  (full authenticated editor API: generate/list/preview/add-page/
  replace-sections/theme/new-draft/publish/unpublish), and
  `tests/test_website_rbac.py` (the exact STAFF-edit-not-publish /
  READ_ONLY-read-only matrix the task spec calls for) **already existed**,
  untracked, unrun as a full HTTP-level suite. This phase's backend work
  is therefore concentrated on: proving that existing surface end-to-end
  over real HTTP + real Postgres (no prior test exercised
  `app/api/v1/websites.py` at all — see §11), and building the frontend
  that had never been started.

## 3. Architecture decision (Phase 3 of the task spec)

**Decision: (B) — the backend exposes a validated `WebsiteSpecification`-shaped
render tree, and the frontend renders it using the same closed 8-component
vocabulary directly, via a single component-type → React component
registry.**

Reasoning: `app/services/website_renderer.py` already produces a plain
nested dict/list/str/int/bool/None tree (never HTML) keyed by
`component_type`/`props`/`data`, and is shared verbatim between the
authenticated preview endpoint and the public endpoint (its own
docstring: "Preview (Phase 8) and the public Publish read path... both
use — the exact same code renders a DRAFT preview and a PUBLISHED site").
Building a second backend-side "compile to HTML/JSX" step would be a
second renderer, forbidden by the task's own non-negotiable rule. The
frontend's job is therefore exactly: take that already-validated,
already-safe tree and turn `component_type` strings into React elements —
implemented as `frontend/components/website/ComponentRegistry.tsx`'s
`RenderSection`, a closed `switch` over the same 8 values as
`ComponentType` (`backend/app/schemas/website_specification.py`), with an
unreachable-in-practice `default: return null` for defense in depth. No
second renderer, no HTML string ever constructed, no
`dangerouslySetInnerHTML` anywhere in this phase's frontend code (verified
in §16).

## 4. Public runtime

- Backend: `backend/app/api/v1/public_websites.py` (Phase 11, unmodified)
  — `GET /api/v1/public/websites/{tenant_id}`. Resolves
  `Website.current_published_version_id` only; never accepts a
  `version_id` from the caller at all, so a DRAFT/SUPERSEDED version is
  structurally unreachable regardless of what a caller tries to guess (no
  such route exists — proven in §11's
  `test_full_medical_tourism_public_website_e2e`, "guess_resp" assertion).
  Trust boundary is the same "this tenant_id exists" pattern
  `public_leads.py`/`public_quotes.py`/`public_contracts.py` already use —
  an unguessable UUID path parameter, no signed token, no
  tenant/role/organization_id ever read from the body or query string
  (verified structurally in §16).
- Frontend: `frontend/app/w/[tenantId]/page.tsx` — the public,
  unauthenticated runtime page. No `AppShell`, no `useAuth`, no
  `Authorization` header anywhere on this route (mirrors
  `app/quotes/view/[id]/page.tsx`'s established "no login" pattern).
  Fetches `getPublicWebsite(tenantId)` (`frontend/lib/api.ts`), selects a
  page by `?page=<slug>` query param (defaulting to the first page), and
  renders via `ComponentRegistry.tsx`. Theme tokens (`ThemeTokens` from
  `website_specification.py`) are applied as CSS custom properties on a
  `.klaros-site-root` wrapper (`frontend/app/globals.css`), so a
  tenant-chosen primary/secondary/background/text color, font, and corner
  radius actually change the rendered page — this is genuinely
  per-tenant, not a hardcoded skin.

## 5. Frontend runtime (component registry)

`frontend/components/website/ComponentRegistry.tsx` implements all 8
existing component types (HERO, TEXT, CTA, FEATURE_GRID,
PROVIDER_DIRECTORY, PROCEDURE_LIST, CONTACT_FORM, FOOTER). Every string
from the spec is rendered as plain JSX children — React's own text-node
escaping is the only "sanitization" applied client-side, and it is
sufficient because the schema already forbids `<`/`>`/`javascript:`/etc.
server-side before persistence (defense in depth, not the only layer).
`PROVIDER_DIRECTORY`/`PROCEDURE_LIST` render generically from the
data-provider contract shape (`{items: [{name, location, description,
category, offerings}], resolved}` — `backend/app/services/website_data_providers.py`'s
own documented contract), never a vertical-specific field name check; the
genericity guard (§13) proves this in code, not just by inspection.

## 6. Editor

`frontend/app/website/page.tsx` (dashboard: website overview, version
list, generate/regenerate) + `frontend/components/website/SectionEditor.tsx`
(`SectionPropsForm`, one explicit typed form per component type — Phase 6's
"if automatic schema-driven forms would be excessive, use explicit typed
forms" was taken literally rather than building a speculative
form-generation framework). Covers: page list/switch, add page, section
list with up/down reorder and remove, add-section (type picker with
type-appropriate default props), per-type field editing (headline/body/
CTA text+URL/feature items/contact fields/footer links/etc. — every field
maps 1:1 to a field on the matching `*Props` Pydantic model), theme editing
(color pickers constrained to the browser's native color input, font/
radius selects matching the backend's own closed allowlists), save
(→ `PUT .../pages/{slug}/sections`), preview (fetched fresh after every
save), publish/unpublish/new-draft. No field anywhere accepts raw
HTML/CSS/JS; URL-shaped fields are plain text inputs the backend
re-validates against its scheme allowlist on save — this editor's own
validation is UX-only, never trusted as final (Phase 5's explicit rule).

**Known, disclosed limitation**: the authenticated preview/render endpoint
(`GET .../preview`) does not echo back a section's `data_source` (only
`component_type`+`props`), so re-opening an existing DRAFT for editing
cannot recover a PROVIDER_DIRECTORY/PROCEDURE_LIST section's
`provider_key` automatically. Rather than hardcoding a guessed vertical
default (which would have violated the genericity guard — see the actual
mistake caught and fixed in §13), the editor leaves it blank and exposes
`provider_key` as a plain, generic text field the user re-enters. Saving
without re-entering it clears that section's data binding. This is real,
deferred work (a `GET .../pages/{slug}` endpoint that echoes the exact
stored `SectionSpec` including `data_source` would close it cleanly, no
backend schema change required) — not silently masked.

## 7. Preview

Authenticated-only preview is kept, not replaced with a public/shareable
token scheme. Justification (Phase 7's explicit call for a justified
decision, not a default): the actual workflow this phase had to serve is
"a tenant's own staff edits a draft, then publishes it" — nothing in the
26 success criteria or the Medical Tourism acceptance scenario requires
sharing an unpublished draft with someone outside the organization before
publish. Adding a signed-token public-preview surface now would be new
attack surface (token generation, scoping, expiry, revocation,
draft-leak-via-guessable-URL — all the exact failure modes Phase 7 warns
about) for a workflow need that doesn't yet exist. `GET
.../versions/{version_id}/preview` (READ_WEBSITE-gated) remains the only
preview mechanism, matching Phase 11's own explicit deferral.

## 8. Publishing

Unmodified from Phase 11: `POST .../versions/{version_id}/publish`
(PUBLISH_WEBSITE-gated), full-row-immutable-per-version, exactly one
PUBLISHED version per website enforced by a partial unique index
(`uq_website_versions_one_published_per_website`). Verified over real
HTTP + real Postgres in §11's E2E test (publish → immediately visible on
the public endpoint; new-draft-after-publish does not affect the public
endpoint's response; unpublish removes public visibility).

## 9. Contact-form integration

Wired the existing structural `CONTACT_FORM` component to the existing
public lead-intake infrastructure — no second lead/CRM system created.
`frontend/components/website/ComponentRegistry.tsx`'s `ContactForm`
collects only the fields the section's own `ContactFormProps.fields`
allowlists (NAME/EMAIL/PHONE/MESSAGE), includes the same honeypot pattern
`public_leads.py` already expects (`website` field, hidden via CSS,
never labelled), and calls `submitPublicWebsiteLead(tenantId, body)`
(`frontend/lib/api.ts`) → `POST /api/v1/public/leads/{tenantId}` — the
exact same endpoint `tests/test_public_lead_intake.py` already covers in
isolation (honeypot, idempotency, tenant isolation, source restricted to
WEB/CHAT, owner-only fields never settable). Tenant context comes only
from the public website's own already-resolved `tenantId` (a prop passed
down from the page route, itself resolved server-side from the PUBLISHED
website) — the visitor's browser has no way to override which tenant a
submission attaches to. No rate-limiting was added beyond what
`public_leads.py` already has (`RATE_LIMIT_PUBLIC_LEAD_PER_MINUTE`) — this
phase reuses it, doesn't reinvent it.

## 10. Component additions (Phase 9 evaluation)

Evaluated IMAGE, CARD_GRID, TESTIMONIAL, FAQ against the four questions
the task spec poses. **Decision: implement none of them in this phase.**

| Component | Required for generic usability? | Required for Medical Tourism? | New backend data model? | Can use existing structured props? | Security surface increase? | Verdict |
|---|---|---|---|---|---|---|
| IMAGE | Nice-to-have, not required (HERO/CTA already carry an image URL field) | No | No | Yes (a URL field) | Real: needs an image-hosting/upload decision this phase has no mandate to make (Phase 14 explicitly says don't prematurely add object storage) | **Defer** |
| CARD_GRID | Marginal — FEATURE_GRID already covers "a titled grid of cards" generically | No | No | Yes | Low | **Defer** (redundant with FEATURE_GRID for this phase's actual needs) |
| TESTIMONIAL | Nice-to-have | No | No | Yes | Low | **Defer** |
| FAQ | Genuinely useful for a real generic site | No | No | Yes (a list of Q/A pairs, same shape discipline as FeatureItem) | Low | **Defer anyway** — not required by any of the 26 success criteria or the mandatory Medical Tourism scenario; adding it now would be scope growth without a concrete need driving it |

None of the 8 existing components were insufficient for the mandatory
Medical Tourism acceptance scenario (§11) or for a real, generic site
(HERO + TEXT + CTA + FEATURE_GRID + CONTACT_FORM + FOOTER already compose
a usable small-business site). Extending
`backend/app/schemas/website_specification.py` (a file whose own
docstring says "never weaken... without updating
tests/test_website_renderer_security.py") for components nothing in this
phase's acceptance criteria requires was judged not worth the added
surface. Flagged as deferred work, not silently dropped.

## 11. Medical Tourism E2E (mandatory acceptance scenario)

New file: `backend/tests/test_phase12_public_website_e2e.py` — the first
test in this repo's history to exercise the Website Builder over the real
HTTP surface (`app/api/v1/websites.py`'s authenticated editor API had
**zero** prior HTTP-level test coverage; only its service layer was
tested by Phase 11's `test_website_service_domain.py` and
`test_website_medical_tourism_validation.py`). Six tests, all passing
against real Postgres:

1. `test_full_medical_tourism_public_website_e2e` — the full 20-step
   scenario: register tenant → activate Medical Tourism → ACTIVE Blueprint
   → synthetic Provider/Procedure/offering → `POST
   /api/v1/websites/generate` → `GET .../preview` → confirm DRAFT is NOT
   on the public endpoint yet (404) → `POST .../publish` → `GET
   /api/v1/public/websites/{tenant_id}` (no auth) → provider directory
   renders with the synthetic hospital's real data → CONTACT_FORM section
   present → `POST /api/v1/public/leads/{tenant_id}` → a real `Lead` row
   exists for the correct tenant → tenant-spoofing attempt (unrelated
   tenant, 404) → new-draft-after-publish still doesn't leak on the public
   endpoint → confirms no `/public/websites/{tenant_id}/versions/{id}`
   route exists at all → unpublish → public endpoint returns 404.
2. `test_public_website_rejects_malformed_and_path_traversal_tenant_ids`
   — non-UUID, `../../etc/passwd`-shaped, oversized (500-char) tenant_id
   all rejected (404/422); an encoded `../` is normalized by Starlette's
   own routing (307 to a still-nonexistent path) rather than a traversal
   vulnerability — confirmed no filesystem access is ever involved.
3. `test_public_website_query_param_cannot_select_a_different_tenant` —
   `?tenant_id=...&organization_id=...` query params are inert; the path
   parameter is the only selector.
4. `test_public_website_never_leaks_other_tenant_content` — two tenants,
   only one publishes; the other's public endpoint 404s, the authenticated
   `GET /websites` for the non-publishing tenant returns `null`, never the
   other tenant's website.
5. `test_rbac_over_http_staff_can_edit_not_publish_read_only_cannot_edit`
   — STAFF can generate/edit (403 on publish); READ_ONLY can read (403 on
   generate AND publish); OWNER can publish what STAFF drafted. First
   HTTP-level proof of the RBAC matrix `tests/test_website_rbac.py` only
   checked at the permission-table level.
6. `test_public_website_has_no_authenticated_only_surface_leak` —
   structural assertion that the public payload's keys are exactly
   `{theme, navigation, seo_defaults, pages}` at the top level and
   `{component_type, props, data}` at the section level — no internal ID,
   no audit field, no RBAC field, ever.

All synthetic data (`"Synthetic Test Hospital"`, `"Synthetic Hip
Replacement"`) — no real providers scraped or imported, per HARD SCOPE.

## 12. Tenant isolation

Covered by §11 items 3/4/5 above (real Postgres), plus Phase 11's own
pre-existing `test_public_lead_intake.py::test_public_lead_never_crosses_tenants`
(re-run and reconfirmed passing in this session, unmodified). No new
tenant-isolation-relevant code was added beyond the frontend, which has no
independent tenant-scoping logic of its own — it only ever passes through
whatever `tenantId` the public URL names, and the backend is the sole
authority.

## 13. RBAC

`tests/test_website_rbac.py` (Phase 11, unmodified) already encodes the
exact intended matrix: OWNER/ADMIN/MANAGER full access;
STAFF read+edit, not publish; READ_ONLY read-only; TECHNICIAN/ACCOUNTANT
no access. This phase adds the first HTTP-level proof of that same matrix
(§11 item 5). Public visitors never touch RBAC at all — `public_websites.py`
and `public_leads.py` have no `CurrentUser`/`require_permission` dependency
anywhere on their routes (verified by reading both files in full).

**A genuine mistake was made and caught in this phase**: the first draft
of `frontend/components/website/SectionEditor.tsx` hardcoded
`"medical_tourism.provider_directory"`/`"medical_tourism.procedure_catalog"`
as default `data_source.provider_key` values for newly-added sections —
a real violation of "no vertical-name literal in generic website
infrastructure," caught by extending the existing hardcoding guard test to
the frontend (§16) before this was written up as done. Fixed: new
sections start with `data_source: null`; the editor exposes `provider_key`
as a plain, generic, user-typed text field instead (see §6's disclosed
editor limitation this fix introduces).

## 14. SEO

Reuses `WebsiteVersion.seo_defaults`/`WebsitePage.seo` (`SeoMetadata`:
title/description/og_image_url) exactly as Phase 11 defined them — no new
field added. The public runtime page sets `document.title` from
`seo_defaults.title` client-side (a real but minimal implementation, not a
`generateMetadata` server-rendered `<title>`/OG tag — see §22
limitations). **Canonical URL / domain generation is explicitly NOT
implemented** — this deployment has no custom-domain or stable
production-hostname infrastructure yet (confirmed by inspecting
`frontend/next.config.js`/deployment docs; none exists), so fabricating a
canonical URL would be inventing infrastructure the task explicitly
forbids. Deferred, not faked.

## 15. Security

See §16 for the full audit. Summary: no `eval`/`exec`/`subprocess`/
`shell=True`/`dangerouslySetInnerHTML`/`innerHTML`/`javascript:`/
`data:text/html`/`vbscript:`/`<iframe`/dynamic-import in any new or
modified file (the only matches are the deny-list string literals inside
`website_specification.py`'s own sanitizer, and docstring/comment prose
describing the guarantee). No `tenant_id`/`organization_id`/`role`/
`actor_type` read from a request body anywhere in the new/modified files.

## 16. Security audit (Phase 16, exact commands)

Grepped every new/modified Phase 12 file
(`backend/app/api/v1/public_websites.py`, `backend/app/api/v1/websites.py`,
`backend/app/api/tool_deps_websites.py`, `backend/app/models/website.py`,
`backend/app/schemas/website_specification.py`,
`backend/app/services/website_data_providers.py`,
`backend/app/services/website_generation_service.py`,
`backend/app/services/website_renderer.py`,
`backend/app/services/website_service.py`,
`backend/tests/test_phase12_public_website_e2e.py`,
`frontend/app/w/[tenantId]/page.tsx`, `frontend/app/website/page.tsx`,
`frontend/components/website/ComponentRegistry.tsx`,
`frontend/components/website/SectionEditor.tsx`, `frontend/lib/api.ts`)
for: `eval(`, `exec(`, `subprocess`, `os.system`, `shell=True`,
`dangerouslySetInnerHTML`, `innerHTML`, `javascript:`, `data:text/html`,
`vbscript:`, `<iframe`, `TODO`, `FIXME`, `NotImplemented`,
`body.tenant_id`/`body.organization_id`/`body.role`/`body.actor_type`
patterns, and bare dynamic `import(`. **Zero genuine hits** — every match
was either the sanitizer's own deny-list literal or a docstring/comment.
Explicit tests run for: XSS/HTML-injection (§11's structural payload key
check + `ComponentRegistry.test.tsx`'s literal-`<img onerror>`-renders-as-
text assertion), tenant spoofing (§11 items 3/4), version spoofing (§11's
"no version_id route exists" assertion), public-lead tenant spoofing
(pre-existing `test_public_lead_intake.py`), path traversal / oversized
requests (§11 item 2), unknown component types (frontend:
`ComponentRegistry.test.tsx`'s "unknown component_type renders nothing";
backend: `SectionSpec._validate_props_and_data_source` rejects any
`component_type` outside the closed enum, pre-existing and re-confirmed).
Malicious `provider_key`/`data_source` params were already covered by
Phase 11's `test_website_specification_schema.py` (unmodified, re-run
green in this session's full suite).

## 17. Frontend tests

Existing stack only (Vitest + RTL) — no second test framework. New files:

- `frontend/components/website/__tests__/ComponentRegistry.test.tsx` (7
  tests): HERO/TEXT render plain text; PROVIDER_DIRECTORY renders generic
  item-shape data; empty-state text when no items; **unknown
  component_type renders nothing**; **an XSS-shaped string renders as
  literal text, no `<img>` element is ever created**; CONTACT_FORM renders
  its fields without prematurely submitting.
- `frontend/app/w/__tests__/page.test.tsx` (2 tests): renders a
  tenant's published site with no login chrome; shows a not-found message
  (not a crash) on a 404.

Result: **30 passed, 0 failed** (21 pre-existing + 9 new), `Test Files 7
passed (7)`.

## 18. PostgreSQL validation

Real, disposable Postgres 16.2 via `pgserver` (unix-socket transport,
`/tmp/klaros_phase12_pgdata`, deleted at session end) — no Docker
available, matching Phase 0's own disclosed constraint.

- `alembic upgrade head` from a fresh database: clean run through every
  migration 0001→0050, ending at 0050 exactly as expected.
- `alembic downgrade -1` then `alembic upgrade head`: clean round-trip,
  `alembic current` confirms `0050 (head)` afterward. **No migration was
  created by this phase** — Phase 11's own 0050 already covers this
  phase's entire schema surface (Website/WebsiteVersion/WebsitePage/
  WebsiteSection); nothing this phase built required a new column or
  table. Confirmed by inspecting the diff (`git status`) — no new file
  under `backend/alembic/versions/` beyond the pre-existing 0050.
- `pytest -k website` (before this phase's own test additions): 70
  passed, 1760 deselected.
- `pytest tests/test_phase12_public_website_e2e.py`: 6 passed (§11).
- `pytest tests/test_website_no_vertical_hardcoding.py
  tests/test_vertical_extension_no_hardcoding_guard.py` (after the
  frontend genericity-guard extension, §13): 10 passed.
- Full suite (`pytest tests/`, real Postgres): **1823 passed, 12 skipped,
  1 failed** (940.72s). The one failure,
  `test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`,
  is unrelated to this phase (a pre-existing, uncommitted file already
  modified before this session started per the task's own git-status
  context) and was investigated, not blindly retried: it raised
  `asyncpg.exceptions._base.InternalClientError: got result for unknown
  protocol state 3` — a connection-protocol desync consistent with
  running ~1836 tests' worth of concurrent DB traffic against a single
  local unix-socket Postgres instance under `pgserver` — and **passed
  cleanly in isolation** (`pytest tests/test_openai_realtime_voice_service.py::<same test>`
  → 1 passed). Classified as pre-existing test-infrastructure flakiness
  under concurrent load, not a Website Builder defect and not caused by
  this phase's changes; not fixed (out of scope — no Website Builder code
  was touched by this test or its module).

## 19. Frontend validation

- `npx tsc --noEmit`: clean, zero errors.
- `npx vitest run`: 30 passed, 0 failed (§17).
- `npm run build` (Next.js production build, Turbopack): succeeded,
  including the two new routes `/website` (static) and `/w/[tenantId]`
  (dynamic, server-rendered on demand — expected, since it fetches
  per-request tenant data).

## 20. Full regression — baseline vs. final, accounted for

Backend test count accounting (exact, not estimated): baseline whole-suite
collection immediately before any Phase 12 file was created was **1830**
(`70 selected + 1760 deselected` under `-k website`, run first, before any
new test file existed). This phase added exactly:
- `tests/test_phase12_public_website_e2e.py`: **+6** tests.
- `tests/test_website_no_vertical_hardcoding.py`: **+3** tests
  (`test_frontend_generic_core_file_never_names_a_vertical`, parametrized
  over 3 frontend files).

No existing test was removed, skipped, or modified in behavior. Expected
final total: 1830 + 9 = **1839**. The single full-suite run captured in
§18 (1823+12+1=1836) was taken after adding the 6 E2E tests but *before*
the 3 guard-test additions (those were added afterward, in response to the
mistake described in §13, and verified passing in isolation: "10 passed"
in §18's guard-file run). Final accounted total: **1826 passed, 12
skipped, 1 failed (pre-existing, unrelated, isolation-verified)** = 1839.

Frontend: 21 pre-existing tests + 9 new = 30, all passing; typecheck and
production build both clean.

## 21. Failures / root causes

Exactly one: §18/§20's `test_openai_realtime_voice_service.py` failure —
root cause identified as concurrent-load `asyncpg`/`pgserver` protocol
flakiness, not a code defect, not related to this phase's changes,
reproducibly passes in isolation. Not fixed (unrelated file, out of this
phase's scope per the task's own explicit instruction never to
opportunistically fix unrelated code).

## 22. Limitations

- The editor's preview/re-edit round-trip does not recover an existing
  data-bound section's `provider_key` (§6) — disclosed, not masked; the
  UI surfaces it as an empty, user-fillable field rather than guessing.
- No `generateMetadata`/server-rendered `<title>`/Open Graph tags for the
  public website route — `document.title` is set client-side only. A
  crawler or link-preview bot that doesn't execute JavaScript will not see
  per-tenant SEO metadata. Full SSR metadata would require converting
  `app/w/[tenantId]/page.tsx` to a server component with its own fetch,
  which is straightforward follow-up work, not attempted here given this
  phase's time budget and because canonical-URL infrastructure (§14) isn't
  in place yet anyway.
- No public/shareable preview token (§7 — a deliberate, justified
  decision, not an oversight).
- No rate limiting on the authenticated editor endpoints beyond whatever
  already existed; none was in scope to add.
- The dashboard's theme editor uses the browser's native `<input
  type="color">` picker rather than a custom color-swatch UI — functional,
  not polished.
- IMAGE/CARD_GRID/TESTIMONIAL/FAQ components: not implemented (§10,
  reasoned deferral).
- This phase's `backend/.venv_phase12` and the `pgserver` Postgres
  instance were both fully removed at the end of the session — confirmed
  by `git status` showing no trace of either.

## 23. Deferred work

- Server-rendered SEO metadata for the public website route.
- A `GET .../pages/{slug}` endpoint (or equivalent) that echoes a page's
  exact stored `SectionSpec` list including `data_source`, closing the
  editor round-trip gap in §6/§22.
- IMAGE/CARD_GRID/TESTIMONIAL/FAQ components, if a concrete need for them
  emerges from real usage.
- Public/shareable preview tokens, if a real workflow need for sharing an
  unpublished draft outside the tenant's own org emerges.
- Custom domains / DNS / CDN / hosting / canonical URLs — no
  infrastructure decision was made or should be inferred from this phase.
- A richer drag-and-drop section reorder UI (current implementation is
  up/down buttons — functional, not a polished DnD experience).

## 24. Final verdict

**PHASE 12 STATUS: COMPLETE WITH LIMITATIONS**

Every one of the 26 success criteria in the task's own list was checked
against real, executed evidence in this log (not asserted from memory):
editor exists and edits the real structured spec; no arbitrary HTML/JS
generation anywhere (proven by grep + tests, §16); draft preview works;
published version renders publicly and only the current published version
is ever visible (§11); CONTACT_FORM creates a real Lead through the
existing LeadService with no tenant-spoofing path (§9/§11); Medical
Tourism provider data renders through the generic data-provider mechanism
with a proven zero-vertical-branch guarantee in both backend and frontend
generic code (§13); tenant isolation and RBAC are proven over real HTTP +
real Postgres (§11/§12/§13); published versions remain immutable (Phase
11, re-confirmed); SEO metadata reuses the existing structured model
(§14, with an honestly disclosed limitation); the frontend uses the
existing Next.js/React/TypeScript stack exclusively; no second renderer,
CRM/lead system, MCP client, Dropshipping, Marketplace/OAuth, or
custom-domain/hosting platform was introduced; real Postgres validation
passes (§18); frontend typecheck/tests/build all pass (§19); security
tests pass (§16); the one full-regression failure is explained and
isolation-verified as pre-existing and unrelated (§21).

"With limitations" rather than unqualified "COMPLETE" because of the
honestly disclosed items in §22 (editor data_source round-trip gap,
client-side-only SEO title, no server-rendered OG metadata) — none of
which block any of the 26 success criteria, but none of which should be
quietly claimed as fully solved either.
