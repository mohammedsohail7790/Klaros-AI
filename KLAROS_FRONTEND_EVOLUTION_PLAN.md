# Klaros — Frontend Evolution Plan

Status: proposal only. No frontend code was changed. Baseline (verified): Next.js 16 App Router, React 18.3.1, TypeScript 5.6.3, Tailwind 3.4.13, 57 route pages, single hand-written API client `frontend/lib/api.ts` (3,835 lines per prior audit), no state-management library, no form library, no test library (confirmed via direct read of `frontend/package.json`: scripts are `dev/build/start/lint` only; deps are `class-variance-authority`, `clsx`, `geist`, `lucide-react`, `next`, `react`/`react-dom`, `tailwind-merge`; devDeps are `@types/*`, `autoprefixer`, `postcss`, `tailwindcss`, `typescript` — nothing else).

## 1. Pages preserved as-is

All 57 existing pages remain: marketing (`/`, `/pricing`), auth (`/login`, `/register`, `/accept-invite`), dashboard, CRM (`/leads`, `/customers`, `/calendar`), operations, finance (7 sub-pages), quotes/contracts (incl. public no-login views), marketing (7 sub-pages), retention (7 sub-pages), AI/automation surfaces (`/ai-activity`, `/automations`, `/approvals`, `/morning-brief`, `/events`), settings (8 sub-pages). No redesign is required by this plan for any of them; several settings pages (`/settings/integrations`, `/settings/automation`) gain new content (§3) without losing existing functionality.

## 2. Onboarding — verified current behavior and evolution

`frontend/app/onboarding/page.tsx` (338 lines, verified real) is a functional 4-step wizard: plan selection → Stripe checkout (`createBillingCheckout`), business hours/timezone (`setAutomationTimezone`), free-text knowledge capture (`services`/`pricing`/`voice` fields → `setKnowledgeFile`/`indexKnowledgeFile` against canonical Knowledge paths), completion screen. This is **not** replaced — plan selection and timezone setup remain exactly as they are (they're billing/scheduling concerns, orthogonal to business discovery). The free-text knowledge step is superseded for *new* tenants by the richer `/discovery` flow (§3) once it ships, but remains available/functional for any tenant who already completed it — no forced re-onboarding, no data loss.

## 3. New pages/routes

| Route | Purpose | Backing API | Priority |
|---|---|---|---|
| `/discovery` | Conversational business-description intake + adaptive follow-ups | `/business-discovery/*` | Phase 2 |
| `/blueprint` | Business Map: section-by-section view/edit of the confirmed Blueprint, plus claim review/confirm UI | `/business-blueprint/*` | Phase 3 |
| `/recommendations` | Recommendation review, accept/dismiss | `/recommendations/*` | Phase 4 |
| `/settings/integrations` (extended, not new) | Marketplace catalog view layered onto the existing connect/OAuth UI, with the `REAL`/`STUB`/`WEBHOOK_NORMALIZER` badge from `IntegrationProviderCatalog` rendered explicitly (closing the original audit's P2 UI-disclosure gap) | `/integrations/catalog` + existing `/integrations/*` | Phase 5 |
| `/website` | Website Builder: site/page tree editor over Component Specifications, preview iframe, publish-request flow | `/websites/*` | Phase 6 |
| `/agents` | Agent list, agent builder (goal/instructions/tool-allowlist/autonomy-tier form), execution history, step-level trace viewer | `/agents/*` | Phase 7 |
| `/workflows` (new, distinct from existing `/automations`) | AI-assisted workflow generation UI proposing an `Automation` definition for human review before it's materialized — reuses the existing `/automations` list/detail views for the resulting object, adds only the generation/proposal step | `/automation`/`/automations` (resolve naming ambiguity first, per `KLAROS_API_EVOLUTION_PLAN.md` §3) + new generation endpoint | Phase 8 |
| `/providers`, `/procedures` | Medical Tourism domain management (tenants using that vertical extension only — conditionally shown based on `VerticalExtension` selection) | `/providers/*`, `/procedures/*` | Phase 9 |
| `/products`, `/orders`, `/suppliers` | Dropshipping domain management (conditionally shown) | `/products/*`, `/orders/*`, `/suppliers/*` | Phase 10 |

Vertical-specific pages are conditionally rendered based on the tenant's `VerticalExtension` selection (stored on `Organization` or `BusinessBlueprint`) — the navigation shell reads this once, not a per-page check scattered across the app, keeping the "don't hard-code around two businesses" principle visible in the UI layer too.

## 4. Navigation changes

Top-level nav gains three new sections once their features ship: **Business** (Discovery/Blueprint/Recommendations — grouped, since they share one underlying state per `KLAROS_TARGET_ARCHITECTURE.md` §12), **Website**, **Agents & Workflows**. Existing CRM/Operations/Finance/Marketing/Retention/Settings sections are unchanged in position and content. Vertical-specific sections (Providers/Procedures, Products/Orders/Suppliers) appear only when the relevant `VerticalExtension` is active for the tenant.

## 5. Dashboard changes

New summary tiles, following the existing `StatCard` component pattern (per recent commit history converting dashboard tiles to `StatCard` — real, in-repo convention to reuse, not invent a new tile component): Blueprint completion %, pending Recommendations count, active Agents / recent executions needing approval, Website publish status. These are additive tiles fetched via the same `~16 concurrent calls` retry-batched pattern `lib/api.ts` already documents for the dashboard (per audit §5/§6), not a new fetching mechanism.

## 6. Onboarding UX — Guided vs Business Map (target)

Both modes are views over the same `BusinessBlueprint` state (`KLAROS_TARGET_ARCHITECTURE.md` §12): **Guided** is a linear 10-step wizard reusing the existing onboarding page's step-indicator UI pattern (verified real, `STEPS` array pattern in `onboarding/page.tsx:31`) extended to cover Discovery/Blueprint-confirmation/Recommendation-review/Integration-connect/Website-review/Agent-setup in sequence; **Business Map** is the `/blueprint` page's non-linear section grid, letting a user jump to Customers/Products-Services/Marketing/Sales/Operations/Finance/Communication/Website/Integrations/Agents/Automation/Analytics directly. Both read/write `BlueprintSection` rows through the same API — no separate state store, no drift risk between the two entry points.

## 7. State management and forms — decision needed, not yet made

The existing app has zero state-management or form libraries (verified) and has scaled to 57 pages this way using local `useState`/`useEffect` and manual controlled inputs. The new surfaces (especially the Agent builder's multi-field form and the Website Builder's nested spec editor) are meaningfully more complex than existing forms (e.g. the onboarding wizard's 3 free-text fields). **Recommendation** (not yet implemented, a decision for the team at Phase 2 kickoff): introduce a form library (e.g. `react-hook-form`) scoped to only the new complex forms, rather than a blanket rewrite of all 57 existing pages' input handling — minimizes risk, matches the existing codebase's incremental-adoption pattern (e.g. `StatCard` rollout was also incremental per commit history) rather than a big-bang dependency change.

## 8. Testing (see `KLAROS_TESTING_STRATEGY.md` for full detail)

Frontend has zero automated tests today (confirmed: no test script, no test library dependency). This must be addressed starting Phase 0, not deferred until the new surfaces ship, because the new pages (Agent builder, Website Builder) are exactly the kind of high-consequence UI (approving autonomous actions, publishing a public website) where a UI bug has real safety/reputational impact — the testing strategy treats these as the first frontend test-coverage targets, not the last.

## Cross-references

`KLAROS_API_EVOLUTION_PLAN.md`, `KLAROS_TESTING_STRATEGY.md`, `KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §Guided/Business-Map reuse, `KLAROS_AI_AGENT_ARCHITECTURE.md` (Agent builder requirements), `KLAROS_WEBSITE_BUILDER_SPEC.md` (Website Builder requirements).
