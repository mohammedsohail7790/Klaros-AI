# Klaros Business Builder — product architecture, flow and local development

Klaros is the AI Business Operating Platform: the business brain. A person arrives with an
idea and Klaros turns it, step by step, into a structured, running business. This document
describes what is **actually built**, how the pieces fit, and what is deliberately not.

> Status words used everywhere in the product and in this document
> (`frontend/components/business/StatusPill.tsx`, `backend/app/services/business_builder_service.py`):
> **READY**, **CONFIGURATION REQUIRED**, **CONNECTED**, **NOT CONNECTED**, **PLANNED**, plus
> **AVAILABLE** (a real adapter exists; not connected), **INTEGRATION REQUIRED** (no adapter exists, so
> there is nothing to connect) and **NOT READY** (launch readiness).
> Nothing is ever shown as *Connected* unless a live connection exists, and nothing is shown
> as *Published* unless a website version is published.

## 1. The journey

```
Landing ─► Sign up / sign in ─► What are you building? ─► Discovery ─► Blueprint
        ─► Requirements ─► Recommendations ─► Business Map (+ Next actions)
        ─► Website ─► AI workforce ─► Launch ─► Operate (Business Home)
```

| Stage | Route | Backed by | State |
|---|---|---|---|
| Landing (marketing) | `/` | static + `BuildCta` / `HeroIdeaForm` | **Built** — "Build My Business" goes to sign-up, or straight into the app when signed in. An idea typed on the landing page is carried (sessionStorage only) into the app. |
| What are you building? | `/business` | `POST /business-journey` | **Built** |
| Discovery | `/business/discovery` | existing Discovery engine (`business_discovery_service.py`) | **Built** (engine reused; UX, progress, answer review, "that's enough" added) |
| Blueprint | `/business/blueprint` | existing Blueprint API | **Built** (grouped by business headings, correctable, names exactly what is missing) |
| Requirements | `/business/requirements` | `GET /business-builder/overview` | **Built** — derived, read-only |
| Recommendations | `/business/recommendations` | existing Recommendation engine | **Built** (grouped by requirement; honest availability) |
| Business Map + Next actions | `/business/map` | `GET /business-builder/overview` | **Built** — derived graph |
| Website | `/website` | existing Website Builder | **Built** (generation now requirement-driven; preview, publish, public site, lead intake all existing + verified) |
| AI workforce | `/workforce` | Klaros-side contract only | **Boundary only** — see `AI_WORKFORCE_INTEGRATION_BOUNDARY.md` |
| Launch / Operate | `/business/home` | `GET /business-builder/overview` | **Built** — checklist + operating cards |

The authoritative position in the journey is `BusinessJourney.status`
(`frontend/lib/businessJourneyController.ts` is the single status→route mapping). Requirements
and the Business Map are *views*, not new journey states; the backend state machine is unchanged.

## 2. What was reused, what is new

Reused, unmodified in behaviour: tenant isolation / RLS, RBAC, Discovery, Blueprint, Recommendation
engine, BusinessJourney orchestration, Website Builder + public runtime, Medical Tourism module,
agent runtime, ToolRegistry, integration catalog.

New backend (`backend/app/`):

- `services/capability_vocabulary.py` — shared, data-only capability vocabulary (label, group,
  free-text aliases, `klaros_support` NATIVE/INTEGRATION/PLANNED, depends-on). Pure reference data.
- `services/business_builder_service.py` — **pure derivations** over existing rows:
  Requirements, Business Map graph, Next actions, launch checklist, stage tracker. Nothing is
  persisted; the same inputs always produce the same output (tested).
- `api/v1/business_builder.py` — `GET /business-builder/overview`, `GET /business-builder/workforce`,
  `POST /business-builder/modules/{key}/enable` (explicit opt-in to an industry module).
- `integrations/workforce/` — the AI-workforce adapter *contract* and an honest "pending" adapter.
- Small extensions to existing code: free-text capability parsing in `recommendation_service.py`
  (split + canonicalise; provider matching by canonical key; `collect_capability_requirements`
  public wrapper; section-data capabilities as a requirement source), `business_discovery_service.py`
  (coverage-based gap logic, no-follow-up completion, `finish_session`), `discovery_extraction_service.py`
  (section guide in the prompt), `website_generation_service.py` (requirement-driven pages,
  claim fallbacks, no filler sections), `POST /business-discovery/sessions/{id}/finish`.

**No database migration was needed.** Every new view is derived from existing tables.

## 3. Genericity

Core code never branches on a business type. Vertical behaviour arrives only as data:

- a vertical's registry `capabilities` (`VerticalExtension`), enabled per tenant by an explicit action;
- the shared capability vocabulary;
- website data providers registered by vertical modules (used to bind directory/catalog sections
  and to count records);
- the integration catalog's provider capability tags.

`backend/tests/test_capability_vocabulary.py::test_business_builder_modules_never_name_a_vertical`
fails if any Business Builder module names a vertical, and the pre-existing
`test_vertical_extension_no_hardcoding_guard.py` still passes.

## 4. How each view is derived (traceability)

- **Requirements** = confirmed `REQUIRED_CAPABILITIES` claims ∪ capabilities listed in the (editable)
  section data ∪ capabilities of any enabled industry module, merged by canonical key. Each carries
  its evidence ("you said…" quote or "listed in your Blueprint") and a confidence.
- **Readiness** of a requirement: NATIVE module → READY; real provider connected → CONNECTED; real
  provider available but not connected → NOT CONNECTED; stub/webhook-only/none → PLANNED.
- **Business Map** nodes: customers, Klaros core, revenue (from the Blueprint's business model), one
  node per requirement (lane by its group), outside parties (e.g. a supplier) implied by a capability,
  **only providers with a real adapter**, and the AI workforce when any requirement could use one.
  Edges: reaches-you-via, sends data, routes to, depends on, fulfilled by, triggers, earns.
- **Next actions** come from live state: enable an industry module, generate/publish the website,
  connect a real provider for a required capability, add records to an empty directory, decide
  recommendations, connect the workforce, and honest "not available yet" notes for PLANNED capabilities.

## 5. Local development

Backend (Python 3.12, `backend/.venv`), real PostgreSQL recommended for anything touching RLS:

```bash
cd backend
.venv/bin/alembic upgrade head                 # owner role
.venv/bin/python -m scripts.db.provision_app_role   # APP_DB_USER / APP_DB_PASSWORD
.venv/bin/python -m uvicorn app.main:app --port 8000
.venv/bin/python -m pytest tests/test_business_builder_api.py tests/test_capability_vocabulary.py
```

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
npm run test && npx tsc --noEmit && npm run build
```

With no `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, Discovery uses its documented deterministic fallback
(a short fixed question sequence); with a key it is adaptive. The Blueprint screen and every
downstream view work identically either way.

## 6. Known limitations

- AI question quality: with a connected provider Discovery can still ask a question the user has
  effectively answered, and may file an answer under a neighbouring section. The user can end
  Discovery at any time ("That's enough"), and the Blueprint screen names any required section that
  is still empty and lets the user correct it.
- Recommendation tool matching is keyword-based (pre-existing) and noisy; the Business Builder shows
  those tool rows only inside a collapsed, caveated disclosure.
- Only Stripe, QuickBooks and Google Calendar have real adapters. Everything else is PLANNED.
- Storefront, supplier connection, product catalog, inventory, pricing, orders and fulfilment have
  no Klaros module — they are mapped and shown as PLANNED.
- The Medical Tourism provider API returns a 500 (not a 422) for a country value longer than two
  characters (pre-existing).


## 7. Launch readiness and Business Home (productization pass)

`GET /business-builder/overview` also returns:

- `launch.items[]` — each with `status`, a plain-language `detail` (the *why*), `required`, and a `route`.
  Items: Blueprint, Requirements, Recommendations reviewed, Website, one item per industry-module record set
  that a required capability depends on (e.g. "2 records configured" / "Nothing has been added yet"),
  Integrations (real providers a required capability needs), Required capabilities (those Klaros cannot serve
  yet), and AI workforce. `launch.verdict` is `READY` only when no *required* item is outstanding; the AI
  workforce item never blocks the website launch and says why it is not ready
  ("Connect Halla to enable AI customer communication — the integration isn't available yet").
- `progress[]` — the compact ladder on Business Home (Discovery → … → Architecture → Website → AI workforce →
  Integrations → Launch).
- Requirements now carry a grounded `why` (the user's own words) and an honest `next_step`
  (a real route, or "Integration adapter required" with no link).

`/business/home` is organised as Business (what am I building) → Launch progress and readiness (what is ready)
→ Next actions → Business activity (leads, website, workforce, integrations, workflows). The website call to
action follows real state: none → *Generate Website*; draft → *Publish Website* / *Open Website*; published →
*View Live Website* (the public site) / *Open Website*.

## 8. Discovery quality changes

The extraction prompt now receives a **KNOWN FACTS** block (what the user already said) and a fuller section
guide, and is told to ask exactly one question about one concept, in gap-priority order, and to emit a claim for
*every* section an answer supports. Server-side guards: a question identical to one already asked is
suppressed (Discovery ends instead of looping), and Discovery completes when the provider has nothing more to
ask. The existing question cap is unchanged. Result in a live run: one rich answer produced identity, model,
industry, customers and eight capability claims; Discovery finished in two answers.

## 9. Accessibility notes

Design-token contrast was raised to meet WCAG AA (muted hint text 3.1→4.7:1, gold-button label 2.4→7.2:1 by using
dark text on gold, text-safe accent and warning colours); a global `:focus-visible` ring was added; `Field`
now associates its label with its control; `Alert` announces (`role=alert`/`status`); loading skeletons are
`role=status`; generic "Open" links carry an invisible context name.
