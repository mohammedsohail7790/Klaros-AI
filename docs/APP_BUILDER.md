# Klaros App Builder

Requirements-driven, multi-page project generation with a live sandboxed preview, chat edits, versions, checks and publishing.
Code: `backend/app/builder/`, `backend/app/api/v1/builder.py`, `backend/app/models/builder.py`, migration `0071_app_builder`, UI `frontend/app/builder`, `frontend/components/builder`.

## What it is (and is not)
Generated projects are **static**: HTML, CSS, one vanilla JS file, JSON data and SVG art. The API never executes generated code. Anything
that needs a server (sign-in, payments, a database, maps) is recorded as a feature with status `unsupported` and the reason, never faked.
This is a deliberate first milestone: the production host (Render, Python, no container runtime) cannot safely run untrusted apps.
Supporting React/Next/Node projects needs an isolated runtime (container sandbox per project) — see "Not done".

## Why the old generator produced ~3 pages (root cause)
`website_generation_service.py` is a deterministic template builder over a closed vocabulary of 11 block types. The page set was whatever
its code paths produced (home + contact + a few conditional pages), not what the request needed. The AI was only asked for a hero
headline. `AIProvider.generate_structured` also used `OPENAI_MAX_OUTPUT_TOKENS=1024` and a 20 s timeout, so any richer answer was truncated
into invalid JSON and silently dropped. There was no project filesystem, versioning, build step or real preview.

## Pipeline
`prompt -> plan (spec) -> generate files -> validate -> repair (bounded) -> save version -> preview`
- `spec.py` ProjectSpec: routes (any number up to `MAX_ROUTES=60`), sections, entities (+sample records), design brief, features. Pydantic-validated; no markup allowed.
- `planner.py` AI planning (patch-tolerant `sanitize_plan`) or deterministic requirement extraction. Sitemap is composed from detected capabilities.
- `design.py` ten visual directions as tokens; contrast is enforced (text >= 7:1, muted >= 4.5:1).
- `generator.py` deterministic spec -> files. Same spec, same bytes, so edits change only the files they should. Hand-edited files are `locked`.
- `validator.py` routes exist, links/assets resolve, accessibility basics, no inline/external scripts, no `eval`/`innerHTML=`, no secrets, JSON/CSS/JS syntax (`node --check` when present).
- `orchestrator.py` states `queued -> planning -> generating -> validating -> (repairing -> validating)* -> ready | failed | cancelled`; atomic version save; heartbeat; cancellation; timeout.
- `edits.py` chat edit -> spec patch (AI) or rule (no model): add/remove page, sticky header, dark/light, colour, typography, spacing, hero layout, rename, contact email.
- `store.py` tenant-scoped persistence, quotas, version pruning. `preview.py` signed preview tokens + response security headers.

## Security model
- Generated code runs only in the visitor's browser. Preview: `sandbox allow-scripts allow-forms` (no `allow-same-origin`) => opaque origin, cannot read Klaros' storage, cookies or session.
  CSP: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors <CORS_ORIGINS>`.
- Verified in Chrome: injected inline script, external script, inline style, http image and iframe were all blocked by CSP.
- Preview URL = HMAC-signed token (tenant, project, version, expiry; default 1 h), derived from `JWT_SECRET`. Tampered/expired/forged-tenant tokens are refused.
- All file paths pass `workspace.normalise_path` (no traversal, absolute, hidden, backslash or NUL; extension allow-list; <=8 segments). Files never touch the host filesystem.
- Limits: 400 files, 300 KB/file, 3 MB/project, 60 routes, 25 projects/tenant, 1 active build/tenant, 20 builds/hour, 6 model calls/run, 420 s/run.
- Prompt injection: user text is fenced as data in prompts; model output is parsed and re-validated; unsafe pieces are dropped, never repaired. The consent boundary (`bound_ai_provider`) applies to every model call.
- Tenant isolation: every store call stamps the tenant on its session; tables have RLS policies (0071). **RLS enforcement was not executed here (no PostgreSQL available); the SQLite tests prove the explicit tenant filters only.**
- Published projects are served from the API origin (`/api/v1/public/sites/{tenant}/{slug}/`) under CSP (no sandbox, so localStorage works). Because that origin hosts the API, a dedicated domain
  for published sites (`BUILDER_PUBLIC_BASE_URL`) is recommended before public launch.
- Enquiry forms post to the existing rate-limited, honeypot- and consent-gated `POST /public/leads/{tenant}`. Consent-gated (medical) tenants should keep using the Website Builder, whose form collects consent.

## Configuration (`backend/app/core/config.py`)
`BUILDER_PLAN_MAX_OUTPUT_TOKENS` (9000), `BUILDER_EDIT_MAX_OUTPUT_TOKENS` (6000), `BUILDER_LLM_TIMEOUT_SECONDS` (120), `BUILDER_PLAN_MODEL` (optional override),
`BUILDER_MAX_REPAIR_ATTEMPTS` (2), `BUILDER_RUN_TIMEOUT_SECONDS` (420), `BUILDER_MAX_LLM_CALLS_PER_RUN` (6), `BUILDER_MAX_ACTIVE_RUNS_PER_TENANT` (1),
`BUILDER_MAX_RUNS_PER_TENANT_PER_HOUR` (20), `BUILDER_MAX_PROJECTS_PER_TENANT` (25), `BUILDER_VERSIONS_KEPT` (30), `BUILDER_RUN_STALE_AFTER_SECONDS` (120),
`BUILDER_PREVIEW_TOKEN_TTL_SECONDS` (3600), `BUILDER_PUBLIC_BASE_URL`. AI provider keys are the existing `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `AI_PROVIDER`.
Real planning needs a configured provider whose output limit allows `BUILDER_PLAN_MAX_OUTPUT_TOKENS`; truncated answers are detected (`finish_reason=length`) and fall back to the requirement-based plan.
Provision the DB app role grants for the five new tables if your role setup is table-by-table (`scripts/db/provision_app_role.py`).

## Database
Migration `0071_app_builder` adds `builder_projects`, `builder_versions`, `builder_files`, `builder_runs`, `builder_messages` (+ RLS on Postgres). Additive only.
Rollback: `alembic downgrade 0070_consent_wordings` (drops the five tables and policies; export projects first via `GET /builder/projects/{id}/export`).
Files are stored as text rows because the only object store wired is a local disk on an ephemeral host; versions beyond `BUILDER_VERSIONS_KEPT` are pruned (current and published are never pruned).

## API
`POST /builder/projects` · `GET /builder/projects[/{id}]` · `DELETE /builder/projects/{id}` · `GET .../runs/{run}` · `POST .../runs/{run}/cancel` · `POST .../messages` ·
`GET .../messages|versions|files[/{path}]` · `PUT .../files/{path}` · `POST .../versions/{v}/restore` · `POST .../preview-token` · `POST|DELETE .../publish` · `GET .../export`
(READ_WEBSITE / MANAGE_WEBSITE / PUBLISH_WEBSITE). Unauthenticated static serving: `GET /builder-preview/{token}/{path}`, `GET /public/sites/{tenant}/{slug}/{path}`.

## Not done (be honest about these)
- React/Next/Node project generation, `npm install`/build, a container sandbox, server-side app features (auth, protected routes, persistent per-user records, payments). Scenario D is **not implemented**.
- Real photography or AI-generated images: projects get designed SVG art; owners can paste https image URLs into data files.
- Click-to-select visual editing; chat edits and the file editor exist.
- Deployment beyond "served by Klaros" and ".zip export": no custom domains, no Vercel/Netlify deploy.
- Automated browser tests (Playwright) in CI; browser behaviour was verified by hand in Chrome (see the report).
- A live model run: no provider key was available while building; the AI path is covered by fake-provider tests only.
- Cost in currency: tokens and models are recorded per run; no price table.
- Run execution is in-process (asyncio). A crash marks runs failed (heartbeat/stale sweep) and the last version is intact, but runs are not resumed. Temporal exists in the repo but needs a server.

## Importing an existing static site

`POST /api/v1/builder/projects/import` (`{name, files: {path: text}}`) loads a site you already have. A wrapping folder is stripped, a flat `styles.css` / `script.js` is moved to `assets/` with the page links rewritten, and an unsafe path (`..`, absolute) is refused. The files then get exactly the checks a generated site gets (no inline script/style/handlers, no external CSS/JS, no `eval`/`innerHTML`, links, assets, accessibility, JS syntax), and a rejection names the file and the reason. Nothing is rewritten for you beyond the move above.

- Imported projects can be previewed, hand-edited file by file, published and exported. Chat edits need an AI provider (there is no spec to regenerate from).
- Published pages get two `<meta>` values filled in when they contain the empty placeholders `klaros-api` and `klaros-tenant`, so a form can reach the right business. Previews never get them.
- **Consent-gated businesses (Medical Tourism).** `GET /api/v1/public/leads/{tenant}/consent` returns `{required, wording}`: the newest wording the owner approved, or `wording: null` while none is approved (the form must stay closed). The lead endpoint still enforces the consent itself; the procedure and notes count as health information, so a form that sends them must send the `store_medical_information` scope and show wording that covers it.
- Klaros checks an import for safety. It does not review the content: prices, savings, accreditation counts and named people on an imported site are the owner's claims.
