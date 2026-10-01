# Klaros Medical Tourism Completion — Living Progress Doc

Multi-round task. This doc is the resumption point for future rounds (per the
task's own instructions — do not rely on chat history surviving).

Starting HEAD at task start: `af4937e` (feat(klaros): complete platform
foundation through phase 15). Working tree already carries substantial
uncommitted work from phases 16A/16B/17A/17B1/17B2/17B2R/17B3/17B4 (271
modified/untracked paths per `git status --short`) — this is expected per the
task brief and must NOT be reverted, reset, or committed.

**FORCE RLS hard carve-out acknowledged and in effect: audit/recommend only,
never enable `ALTER TABLE ... FORCE ROW LEVEL SECURITY` on anything.**

## Round 1 (2026-09-30) — Phase 0 reconnaissance (partial)

### What already exists (backend, Medical Tourism domain)

- `backend/app/models/medical_tourism.py` — `Provider`, `ProviderCredential`,
  `Procedure`, `ProviderProcedure`, `PatientLead`, `Consultation`,
  `ReferralCommission`. All `TenantScopedMixin` (RLS-covered per Phase 17B-4;
  confirmed in PHASE_17B4 log as one of the 132 RLS tables / "all 7 Medical
  Tourism tables").
- `backend/app/services/medical_tourism_service.py` — service methods exist
  for: `create_provider`, `get_provider`, `list_providers`, `update_provider`,
  `add_provider_credential`, `verify_provider_credential`,
  `list_provider_credentials`, `create_procedure`, `get_procedure`,
  `list_procedures`, `create_provider_procedure`, `list_offerings`,
  `create_patient_lead`, `create_consultation`, `create_referral_commission`.
  Also two website-data-provider functions:
  `_provide_website_provider_directory`, `_provide_website_procedure_list`
  (wired into the Website Builder's dynamic-content system presumably —
  needs Phase 3 confirmation of the actual wiring call site).
- `backend/app/tools/builtin/medical_tourism_tools.py` — ToolRegistry tools:
  `SearchProviders`, `GetProvider`, `SearchProcedures`, `ListProviderOfferings`,
  `CreateProvider`, `CreateProcedure`, `CreateProviderOffering` (file has more
  content past line 336 not yet read — need to confirm whether lead/
  consultation/referral tools exist at all, since Phase 5 needs this).
- `backend/app/api/v1/medical_tourism.py` — **ONLY** exposes: `GET/POST
  /providers`, `GET /providers/{id}`, `GET/POST /providers/{id}/credentials`,
  `POST .../credentials/{id}/verify`, `GET/POST /procedures`, `GET
  /procedures/{id}`, `GET/POST /offerings`.

### CONFIRMED GAP #1 (blocks Phase 1 sign-off and all of Phase 2)

**No API endpoints exist for PatientLead, Consultation, or
ReferralCommission** despite the service layer already having
`create_patient_lead` / `create_consultation` / `create_referral_commission`.
No list/get/update endpoints for any of the three. This is the single
biggest concrete gap standing between "backend is source of truth" (as the
task instructs to treat it) and a working Medical Tourism frontend — Phase 2
literally cannot build `/medical-tourism/leads`, `/consultations`,
`/referrals`, `/commissions` screens without these existing first.

Do NOT confuse this with `backend/app/api/v1/retention_referrals.py` — that
is a **different, unrelated system**: a customer loyalty/referral-code/reward
program (`ReferralProgram`, `ReferralCode`, `Referral`, `ReferralReward` via
`referral_service.py`). It has nothing to do with Medical Tourism's
`ReferralCommission` (provider referral commissions). Do not conflate them
when building the Medical Tourism frontend's `/referrals` and `/commissions`
routes — those must hit new endpoints backed by
`medical_tourism_service.create_referral_commission` and friends, not
`retention_referrals.py`.

### CONFIRMED GAP #2 (blocks Phase 3 sign-off)

`backend/app/api/v1/public_leads.py` (`POST /public/leads/{tenant_id}`) is
the existing public/unauthenticated lead-intake endpoint used by the website
runtime. It writes into the **generic CRM `Lead` model** via `LeadService` /
`CreateLeadInput` — **not** the Medical Tourism `PatientLead` model. Per the
task's Phase 3 instructions ("use the existing public website runtime and
public lead endpoint... persist the lead into the tenant's Medical Tourism
PatientLead... Fix integration gaps rather than creating duplicate
pathways"), this needs a real fix: either (a) branch this endpoint's
persistence target by tenant vertical (Medical Tourism tenants → PatientLead,
others → generic Lead), reusing the vertical-registry mechanism Phase 4 also
depends on, or (b) some other minimal, non-duplicating integration — exact
approach needs a design decision in the next round after reading
`app/verticals/` / vertical-registry code and `website_data_providers.py`
more closely. This is NOT yet designed, just confirmed as a real gap.

### Frontend

`find frontend -iname "*medical*tourism*"` → **zero results**. Phase 2
(Medical Tourism operational frontend) has not been started at all. No
`/medical-tourism*` routes exist yet anywhere in `frontend/`.

### Not yet done this round (deferred to next round)

- Finish reading `medical_tourism_tools.py` past line 336 to confirm whether
  lead/consultation/referral tools exist (Phase 5 prerequisite).
- Read `medical_tourism_service.py` bodies for `create_patient_lead` /
  `create_consultation` / `create_referral_commission` in full (signatures,
  status-transition logic, validation) before designing the missing API
  endpoints.
- Locate and read the vertical-registry / vertical-extension architecture
  (`app/verticals/` or similar) referenced by Phase 4, to confirm how
  Medical Tourism is currently registered as a vertical and how Discovery/
  Blueprint/Recommendation already branch (or don't) for it.
- Read `frontend/lib/api.ts` and one existing operational frontend section
  (e.g. CRM or Jobs) end-to-end as the pattern to copy for Phase 2.
- Confirm current Phase 17B-4 regression baseline is still accurate by
  re-running it (task explicitly warns not to assume prior numbers hold) —
  not yet done this round; needs real Postgres (`pgserver`) which is slow to
  boot, deferred to a round with more budget.
- Have not touched Phase 6 (klaros_discovery live-wiring audit) or Phase 7
  (FORCE RLS audit) at all yet this round.

### Recommended next-round scope

1. Design + implement the 3 missing API surfaces (PatientLead, Consultation,
   ReferralCommission: list/get/create/update, RBAC/tenant-filtered, reusing
   `medical_tourism_service.py`) — smallest change, mirrors existing
   `providers`/`procedures` router patterns in the same file.
2. Fix the public-lead → PatientLead integration gap (Phase 3).
3. Only then start Phase 2 frontend routes, since they depend on #1.

## Git state at end of Round 1

No files were modified by this round — reconnaissance only (Read/grep/find/
Bash inspection). `git status --short` is unchanged from the snapshot at
task start (271 paths, all pre-existing from prior phases). Nothing staged.
Nothing committed. Nothing pushed.

## Round 2 (2026-09-30) — Gap #1 + Gap #2 fixed, Phase 2 frontend started

### Prerequisite reads completed this round

- `medical_tourism_tools.py` read in full (362 lines): **confirmed no
  lead/consultation/referral ToolRegistry tools exist, and this is
  deliberate**, not an oversight — the module's own docstring states the
  PatientLead/Consultation/ReferralCommission extension writes are
  reachable only via API + service layer directly, never as agent-callable
  tools in this phase (nothing in the validated walkthrough requires
  autonomous agent action for them). This resolved Round 1's open
  question: the new API endpoints below correctly do NOT route through
  `ToolRegistry.execute()` — they call `MedicalTourismService` directly
  with a manual RBAC check, exactly mirroring the pre-existing credential
  endpoints' own pattern in the same router file.
- `medical_tourism_service.py` read in full: confirmed `create_patient_lead`
  / `create_consultation` / `create_referral_commission` all validate their
  FK targets (Lead/Appointment/Provider/Referral) exist in-tenant, enforce
  1:1-extension uniqueness (`InvalidRelationshipError` on a second attempt),
  and (for commissions) compute `computed_amount` from
  `referral.revenue_amount` when basis=PERCENTAGE. No list/get methods
  existed for any of the three — added this round (see below).
- `app/models/vertical_extension.py` + `app/services/vertical_extension_service.py`
  read in full: the registry mechanism is
  `VerticalExtensionService.is_enabled_for_organization(tenant_id, key)` —
  exactly the "lookup mechanism core services are meant to use instead of
  branching on a vertical name" per that module's own docstring. This is
  the mechanism Gap #2's fix (below) now actually calls.
- `frontend/lib/api.ts` (4604 lines) + `frontend/app/jobs/page.tsx` +
  `frontend/components/AppShell.tsx` read as the copy pattern: `request<T>`/
  `authHeaders` fetch wrapper, plain `useState`/`useEffect` + a `load`
  `useCallback`, `PageHeader`/`Badge`/`EmptyState`/`Skeleton`/`Modal` UI
  components, `klaros-input`/`klaros-btn-primary`/`klaros-table` CSS
  classes, and `NAV_SECTIONS` in `AppShell.tsx` for sidebar nav.

### Gap #1 fixed — PatientLead/Consultation/ReferralCommission API surfaces

**`backend/app/services/medical_tourism_service.py`**: added
`get_patient_lead`, `get_patient_lead_by_lead_id`, `list_patient_leads`,
`get_consultation`, `list_consultations`, `update_consultation`,
`get_referral_commission`, `list_referral_commissions`,
`update_referral_commission_status` — all following the exact tenant-
filtered `async with self._session_factory()` + `set_tenant_context` +
`select(...).where(Model.tenant_id == tenant_id)` pattern every existing
method in this file already uses. `create_*` methods were left unchanged
(already existed, already correct).

**`backend/app/api/v1/medical_tourism.py`**: added three new route groups:
- `GET/POST /medical-tourism/patient-leads`, `GET /patient-leads/{id}`
- `GET/POST /medical-tourism/consultations`, `GET /consultations/{id}`,
  `PATCH /consultations/{id}` (status/notes)
- `GET/POST /medical-tourism/referral-commissions`,
  `GET /referral-commissions/{id}`,
  `PATCH /referral-commissions/{id}/status`

All GETs require `READ_MEDICAL_TOURISM` (via the pre-existing
`_require_read` helper); all POST/PATCH require `MANAGE_MEDICAL_TOURISM`
(via a new `_require_manage` helper, factored out of the same check the
credential endpoints already did inline). None of these 8 endpoints route
through `ToolRegistry` — confirmed correct per the tools-file docstring
finding above. 404 on not-found (via `NotFoundError`), 409 on a duplicate
1:1-extension attempt (via `InvalidRelationshipError`, newly imported).

### Gap #2 fixed — public lead intake now vertical-aware

**`backend/app/api/v1/public_leads.py`**: `PublicLeadRequest` gained 7
optional Medical-Tourism-specific fields (`procedure_id`,
`preferred_destination_country`, `medical_history_summary`,
`travel_start_date`, `travel_end_date`, `has_insurance`,
`insurance_notes`) and `PublicLeadResponse` gained `patient_lead_id`. After
the existing `LeadService.create_lead` call (unchanged), the handler now
calls `VerticalExtensionService.is_enabled_for_organization(tenant_id,
"medical_tourism")`; if `True` and the Lead wasn't a dedup hit, it also
calls `MedicalTourismService.create_patient_lead` to create the 1:1
extension row, carrying over whichever optional MT fields were submitted.
A non-Medical-Tourism tenant's submission never touches
`MedicalTourismService` at all. The vertical-name branch lives entirely in
this one public API handler (glue code), never injected into the generic
`LeadService`/`CreateLeadInput` core path — matching the extensibility
rule. `NotFoundError`/`InvalidRelationshipError` from the extension
attempt are swallowed (`patient_lead_id: null` in the response) rather
than failing the whole public submission, since the generic Lead is still
a valid, useful record on its own.

### Real-Postgres validation this round

Fresh `pgserver` instance bootstrapped (Docker unavailable, same
established methodology) at `/private/tmp/klaros_pg_mtround2`
(`DATABASE_URL=postgresql+asyncpg://postgres@/klaros?host=/private/tmp/klaros_pg_mtround2`).
**Left running in the background for the next round to reuse** (check
`ps aux | grep start_pg` — one `start_pg.py` process, pid may differ by
the time you read this; if gone, re-bootstrap the same way: `pgserver.get_server(path)`
then `CREATE DATABASE klaros` via `asyncpg` directly, NOT `db.psql()` —
that method's `subprocess.check_output(f'{executable} {uri}', shell=True)`
call breaks on this repo's own path containing a space ("Klaros AI"),
always exit 127; this cost real time to diagnose this round, documented
here so the next round doesn't repeat it).

`alembic upgrade head` ran clean from an empty database, 0001 through
0063, no errors.

New test file `backend/tests/test_medical_tourism_api_round2.py` (7 real-
Postgres-only tests, `requires_real_postgres` skipif guard matching this
codebase's established convention): patient-lead create/get/list over
HTTP, cross-tenant patient-lead 404 + empty list, consultation create/
PATCH + cross-tenant 404 on both GET and PATCH, referral-commission
create/PATCH-status + cross-tenant 404 on both, public-lead-creates-
patient-lead-for-MT-tenant, public-lead-stays-generic-for-non-MT-tenant.
**All 7 pass.**

Regression-checked against the same real Postgres instance: full
pre-existing Medical Tourism suite (`test_medical_tourism_domain.py`,
`test_medical_tourism_no_core_pollution.py`,
`test_medical_tourism_validation_scenario.py`,
`test_postgres_medical_tourism_domain.py`,
`test_tenant_context_medical_tourism_service_phase17b2r.py`,
`test_website_medical_tourism_validation.py`) — 43 passed, 0 failed.
`test_public_lead_intake.py` (pre-existing) — 8 passed, 0 failed (confirms
the Gap #2 fix didn't change behavior for the existing generic-lead test
cases, which don't enable the medical_tourism vertical).

Full suite re-run against sqlite (fast, catches import/collection errors
across all 2136 collected tests): `1743 passed, 393 skipped` (skips are
all the `requires_real_postgres`-guarded tests, expected under sqlite) —
**no regressions anywhere in the codebase** from either backend change.

This round did NOT re-run the full suite against real Postgres end to end
(that took ~13 minutes in Phase 10's own log; budget this round went to
the new work + the two most relevant existing suites + the full sqlite
pass instead) — a full real-Postgres regression pass is still a
reasonable thing for a future round to do before calling the whole task
complete, per the task's own "re-verify baseline, don't assume" warning.

### Phase 2 frontend started

`frontend/components/AppShell.tsx`: added a "Medical Tourism" nav section
(Providers/Procedures/Patient Leads) with a `Stethoscope` icon import.

`frontend/lib/api.ts`: added a full "Medical Tourism" section (~290 new
lines) — interfaces + list/get/create functions for Provider, Procedure,
Offering, ProviderCredential, PatientLead, Consultation, and
ReferralCommission (including the update/PATCH endpoints), all using the
existing `request<T>`/`authHeaders` pattern, never a new fetch mechanism.

Three new pages, each following `jobs/page.tsx`'s exact shape (status-tab
filter row, `klaros-table`, `EmptyState`/`Skeleton`/error-with-retry,
create `Modal`):
- `frontend/app/medical-tourism/providers/page.tsx` — full list + create.
- `frontend/app/medical-tourism/procedures/page.tsx` — full list + create.
- `frontend/app/medical-tourism/leads/page.tsx` — list of `PatientLead`
  extension rows + a "Extend a lead" modal that takes a Lead ID (pasted
  from the CRM Leads screen) plus the MT-specific fields. Deliberately
  does NOT build a full lead-picker/search UI this round (out of scope
  for the time budget) — a real next-round improvement would replace the
  raw Lead-ID text input with a searchable lead picker reusing
  `searchLeads`/`getLead` from `lib/api.ts`, mirroring how
  `jobs/page.tsx`'s `CreateJobModal` does customer search-as-you-type.

**Verification**: `npx tsc --noEmit` — zero errors across the whole
frontend. `npx next build` — succeeds, all three new routes
(`/medical-tourism/providers`, `/procedures`, `/leads`) statically
generated alongside the full existing route table, zero build errors.
ESLint was not run (no `eslint.config.*` present for a direct `npx eslint`
invocation outside `next lint`; not chased further this round — `next
lint`'s own config should be used by the next round if a lint pass is
wanted).

Not started this round: `/medical-tourism` consultations/referrals/
commissions frontend screens (backend now fully supports them — this is
the natural next slice), Website Builder's `PROVIDER_DIRECTORY`/procedure-
catalog component actually being placed on a real generated site page (the
two `_provide_website_provider_directory`/`_provide_website_procedure_list`
data providers were confirmed still wired via the registry but not
re-verified end-to-end through an actual rendered page this round),
Business Journey validation, Agent/ToolRegistry integration audit (beyond
confirming medical_tourism_tools.py's 7 existing tools), Phase 6
(klaros_discovery live-wiring), Phase 7 FORCE RLS audit (still untouched —
hard carve-out remains in effect, nothing was enabled).

### Recommended next-round scope

1. Build `/medical-tourism/consultations` and `/medical-tourism/referrals`
   (+ `/commissions`) frontend screens — backend fully supports them now,
   same copy-the-pattern approach as this round's providers/procedures
   pages. Consultations will need an appointment-picker (mirroring
   `jobs/page.tsx`'s customer search-as-you-type against `searchCustomers`
   — an equivalent `searchAppointments`/`listAppointments` lookup may not
   yet exist in `lib/api.ts`; check before assuming it does).
2. Replace the leads page's raw Lead-ID input with a real lead search
   picker (reuse `searchLeads` from `lib/api.ts`).
3. Verify the Website Builder provider-directory/procedure-catalog
   components render correctly end-to-end on an actual generated site
   (not just that the data-provider functions are registered).
4. Business Journey validation (Discovery/Blueprint/Recommendation
   surfacing Medical Tourism appropriately) — not started any round yet.
5. Agent/ToolRegistry integration audit beyond the 7 existing
   read/create-only tools — confirm this matches
   `KLAROS_MEDICAL_TOURISM_VALIDATION.md`'s intended scope, not a gap.
6. Phase 6 (klaros_discovery live-wiring) and Phase 7 (FORCE RLS audit —
   audit/recommend only, per the hard carve-out) still untouched.
7. A full real-Postgres regression pass (the complete suite, not just the
   Medical-Tourism-relevant subset) before any "COMPLETE" claim.
8. Security audit and the final `KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md`
   are both still fully pending — not in scope until the above functional
   work is further along.

## Git state at end of Round 2

Modified this round (all uncommitted, nothing staged, nothing committed,
nothing pushed): `backend/app/services/medical_tourism_service.py`,
`backend/app/api/v1/medical_tourism.py`, `backend/app/api/v1/public_leads.py`,
`frontend/components/AppShell.tsx`, `frontend/lib/api.ts`,
`KLAROS_MEDICAL_TOURISM_COMPLETION_PROGRESS.md` (this file). New files:
`backend/tests/test_medical_tourism_api_round2.py`,
`frontend/app/medical-tourism/providers/page.tsx`,
`frontend/app/medical-tourism/procedures/page.tsx`,
`frontend/app/medical-tourism/leads/page.tsx`. HEAD unchanged at
`af4937e403e47cdc141f2db349dfcc46a3df6c4b`. FORCE RLS was never touched.

## Round 3 (2026-09-30) — remaining frontend screens + full browser
## live-validation of Phases 2/3/4

### Frontend: consultations, referral commissions, lead picker

- `frontend/app/medical-tourism/consultations/page.tsx` (new): list +
  status-tab filter + quick-action status-transition buttons (SCHEDULED ->
  COMPLETED/CANCELLED/NO_SHOW, matching `ConsultationStatus`'s real
  lifecycle, terminal states get no buttons) + a create modal with
  provider/procedure dropdowns (from the existing list endpoints) and an
  appointment dropdown sourced from `listAppointments` (the closest
  existing primitive — no dedicated appointment-search endpoint exists in
  `lib/api.ts`, documented as a known limitation in the modal's own
  comment).
- `frontend/app/medical-tourism/referrals/page.tsx` (new): list + status
  tabs + quick-action buttons (PENDING -> CONFIRMED/CANCELLED, CONFIRMED ->
  CANCELLED) + a create modal with a referral dropdown (`listReferrals`,
  the **Retention** one) and provider dropdown, currency/basis/
  percentage-or-flat-amount fields. Page description explicitly restates
  the Round-1 disambiguation warning in-product (visible to whoever builds
  on this next) so nobody wires this route to
  `retention_referrals.py`/`referral_service.py` by mistake.
- `frontend/app/medical-tourism/leads/page.tsx` (extended): the Round-2
  "paste a raw Lead ID" stopgap replaced with a real search-as-you-type
  lead picker (`searchLeads`, 300ms debounce), mirroring
  `jobs/page.tsx`'s `CreateJobModal` customer-search pattern exactly. Also
  added a procedure dropdown to the same modal.
- `frontend/components/AppShell.tsx`: added Consultations/Referral
  Commissions nav items (`CalendarCheck`/`HandCoins` icons) under the
  existing Medical Tourism section.
- `frontend/lib/api.ts`: `submitPublicWebsiteLead`'s return type gained
  `patient_lead_id: string | null` (the field `public_leads.py` already
  returns since Round 2; the frontend type just hadn't been updated to
  expose it).

**A real bug was caught live** (see browser validation below, not from
static review): the consultations create-modal's appointment dropdown
computed its fetch window as `date_from = new Date()` (i.e. "now") through
"+90 days" — this silently excluded any appointment booked earlier the
*same day*, which is exactly the common case when a consultation is
created shortly after booking. Fixed to `date_from = 90 days ago` through
"+90 days" (`frontend/app/medical-tourism/consultations/page.tsx`).
Re-verified via the browser after the fix: the appointment then appeared
in the dropdown and the consultation was created successfully. `npx tsc
--noEmit` and `npx next build` both re-confirmed clean after the fix.

### Full real-browser live validation (not just build/typecheck)

Bootstrapped both halves of the stack for a real end-to-end session:
backend `uvicorn` against the same `pgserver` instance from Round 2
(`/private/tmp/klaros_pg_mtround2`, already `alembic upgrade head`-current
from Round 2 — reused, not rebuilt), frontend via the Browser pane's
`preview_start` (`.claude/launch.json`'s existing `frontend` config, port
3000). Registered a brand-new tenant ("Smoke Test Medical Co") through the
real `/register` -> onboarding flow — a genuine, not-pre-seeded account —
and drove every new screen by clicking through the actual rendered UI
(`browser_batch`/`computer`/`find`/`read_page`), falling back to
in-page `fetch()` via `javascript_tool` only for setup steps with no
minimal UI path yet (e.g. chaining Retention's program -> code -> referral
before a commission can be created).

Verified working, end to end, through the real UI -> real API -> real
Postgres:
- **Providers**: created "Istanbul Wellness Hospital" (TR) via the modal;
  appeared correctly in the table.
- **Procedures**: created "Hip Replacement" (Orthopedics); appeared
  correctly.
- **Patient Leads**: created a CRM Lead ("Jane Patient") via `/leads`,
  then used the new search-as-you-type picker on
  `/medical-tourism/leads` to extend it with `preferred_destination_country:
  TR` — the extension row appeared correctly, proving the picker (this
  round's replacement for the Round-2 stopgap) genuinely works against a
  real lead search.
- **Consultations**: booked a real Appointment via `/calendar` (which
  itself needed a Customer, created via `/customers`), then created a
  Consultation via `/medical-tourism/consultations` binding that
  appointment + the provider + the procedure. Caught and fixed the date-
  window bug above in the process. Verified the SCHEDULED -> COMPLETED
  quick-action transition works (status updated, actions column correctly
  went empty for the now-terminal state).
- **Referral Commissions**: created a Retention referral program -> code
  -> referral (via a mix of the existing `/retention/referrals` UI and
  direct API calls for the parts with no dedicated UI action yet, e.g.
  "create referral" itself has no button on that page currently — only
  program-create and get-or-create-code do), then created a
  ReferralCommission via `/medical-tourism/referrals`'s modal, correctly
  showing the real referral in its dropdown. Verified the basis toggle
  (PERCENTAGE <-> FLAT swaps the amount field) and the PENDING ->
  CONFIRMED quick-action transition, both working correctly.

### Phase 3 (Website Builder integration) — now genuinely validated end to end

Round 1 had confirmed the website-data-provider functions exist and are
registered but explicitly flagged this as unverified end-to-end. This
round closed that gap completely:

1. Ran the tenant through Discovery (see Phase 4 notes below for the real
   bug found there) to get an ACTIVE `BusinessBlueprint`.
2. **Found and fixed a live environment gap, not a code bug**: the
   `VerticalExtension` catalog table was completely empty in the live dev
   DB (0 rows) — not because the migration/seed code is wrong, but because
   `backend/tests/conftest.py`'s autouse `_reset_database` fixture (which
   every `pytest` run against this same `pgserver` instance triggers, per
   Round 2's already-documented caveat) rebuilds the schema via
   `Base.metadata.create_all()`, which recreates empty tables with no data
   — wiping out the migration-inserted `medical_tourism`/`dropshipping`
   seed rows from `app/data/vertical_extension_seed.py` without
   re-inserting them. Practical implication for future rounds: **after
   running `pytest` against a shared `pgserver` instance, re-seed
   `VerticalExtension` before any manual/browser session that depends on
   it** — either re-run the relevant migration's data step or insert
   `SEED_VERTICALS` directly (exactly what this round did: `from
   app.data.vertical_extension_seed import SEED_VERTICALS`, insert each as
   a `VerticalExtension` row). Then called
   `VerticalExtensionService.enable_for_organization(tenant_id,
   "medical_tourism")` for the test tenant.
3. Generated a real website via the actual `/website` UI's "Generate
   website" button (backed by `POST /api/v1/websites/generate`) —
   **confirmed the HERO section's headline/subheadline were populated
   directly from the Blueprint's IDENTITY section** ("Istanbul Care
   Bridge" / the medical tourism description typed into Discovery).
4. First generation attempt (before the vertical was enabled) correctly
   produced NO `PROVIDER_DIRECTORY`/`PROCEDURE_LIST` sections — confirmed
   this is the correct negative case, not a bug: a tenant without the
   vertical enabled must not get vertical-specific website components.
5. After enabling the vertical and clicking "Regenerate from blueprint",
   fetched the new draft version's raw specification via the API and
   confirmed `pages[0].sections` now included `PROVIDER_DIRECTORY` and
   `PROCEDURE_LIST`, with `data.resolved: true` and real data:
   `{"name": "Istanbul Wellness Hospital", "location": "Istanbul, TR",
   ...}`. (The in-editor form UI itself didn't visibly scroll to a spot
   showing these two new section types in one screenshot, but the
   underlying specification — the actual source of truth the public site
   renders from — was confirmed correct via direct API inspection; not
   read as a frontend-editor bug.)
6. **Published the version and loaded the real public site**
   (`http://localhost:3000/w/{tenantId}`, the actual unauthenticated
   runtime a real visitor would hit) — confirmed "Our partner network"
   renders "Istanbul Wellness Hospital · Istanbul, TR" and "What we offer"
   renders "Hip Replacement · Orthopedics", both sourced live from the
   Medical Tourism tables through `website_data_providers.py`'s registry,
   with zero vertical-name hardcoding anywhere in the render path (per the
   existing `test_website_no_vertical_hardcoding.py` guarantee, now also
   visually confirmed).
7. **Submitted the real public Contact form** on that live public page
   (`ContactForm` component -> `submitPublicWebsiteLead` ->
   `POST /api/v1/public/leads/{tenantId}`, the exact endpoint Round 2's
   Gap #2 fix touched) with a fabricated visitor name/email/message.
   Response came back `{"received": true, "lead_id": "9ea66130-...",
   "patient_lead_id": "8accc76f-..."}` — both ids populated, proving the
   Gap #2 fix works through the *actual public website UI*, not just the
   API-level pytest suite. Confirmed the new Lead + PatientLead extension
   immediately appeared in the operational `/medical-tourism/leads` screen
   (count went from 1 to 2, new row showed the correct lead id).

This is the strongest form of validation this task has had for Phase 3:
real browser, real anonymous-visitor path, real database, matching
exactly what a live customer interaction would do.

### Phase 4 (Business Journey validation) — partial, with one real bug found

Drove Discovery through the real `/business` -> `/business/discovery` UI
(typed a genuine medical tourism business description, answered all 8
deterministic fallback questions the no-AI-key dev environment asks —
confirms `AIProvider.is_connected` is correctly `False` here and the
documented deterministic-template fallback path is what's actually
exercised, matching `website_generation_service.py`'s own docstring
claim).

**Real bug found (NOT Medical-Tourism-specific — a Business Journey /
Discovery platform bug, filed as a separate background task so it doesn't
get lost, see below)**: after the final (8th) answer, the backend's
`DiscoverySession` correctly transitions to `status=COMPLETED` (confirmed
via a direct `GET /api/v1/business-discovery/sessions/{id}`), but the
`/business/discovery` frontend page never detects this and never calls
`POST /api/v1/business-journey/{journey_id}/complete-discovery` — it just
displays "Loading your next question..." forever. The `BusinessJourney`
record itself stays stuck at `status=DISCOVERY_ACTIVE`. This blocks
**every** tenant/vertical from reaching the Blueprint screen through the
UI, not just Medical Tourism — confirmed by reading
`frontend/app/business/discovery/page.tsx`'s behavior, not assumed.
Calling `complete-discovery` directly via `fetch()` immediately unblocks
everything (journey -> `BLUEPRINT_REVIEW`, Blueprint row appears). Flagged
via `spawn_task` (`task_f1392e87`, title "Fix Discovery page stuck on last
question") rather than fixed in this round, since it's out of this task's
Medical-Tourism-specific scope and deserves its own focused session —
noted here so it isn't lost and so a future round doesn't waste time
re-diagnosing it.

Worked around it for this round's own validation purposes by driving the
remaining Business Journey steps directly via API (`complete-discovery`,
then filling the 4 minimum-bar Blueprint sections the deterministic no-AI
extraction had left `EMPTY` — `IDENTITY`/`INDUSTRY`/`BUSINESS_MODEL`/
`REQUIRED_CAPABILITIES` — via `PUT /api/v1/business-blueprint/sections/
{key}`, then `POST /api/v1/business-blueprint/activate`, then
`confirm-blueprint`, then `generate-recommendations`). This is a second,
separate, real finding: **with no AI provider key configured (this dev
environment's actual state), the deterministic Discovery answer-extraction
does not populate the Blueprint's required minimum-bar sections from
generic conversational answers** — all sections showed `EMPTY` and the
Confirm-Blueprint gate correctly refused to activate
("Cannot activate — minimum-bar sections not COMPLETE: IDENTITY, INDUSTRY,
BUSINESS_MODEL, REQUIRED_CAPABILITIES") until manually filled. This is
very likely a platform-wide limitation of running without an AI key
(matches `website_generation_service.py`'s own documented AI-fallback
disclosure), not a Medical-Tourism-specific gap, but is worth a future
round confirming against a real AI provider key if one becomes available,
since it currently makes the full Discovery -> Blueprint pipeline
effectively non-functional for a real (non-manually-patched) user in this
environment.

After manually activating the Blueprint and generating recommendations,
`/business/recommendations` showed **zero** recommendations. Not
investigated further this round (ran low on remaining scope) — plausibly
explained by the sparse, manually-patched Blueprint (only 4 of 20 sections
filled) rather than a real bug, but **not confirmed either way** — flagged
as open for the next round rather than asserted as fine.

Business Journey validation is therefore **partial, not complete**:
Discovery -> Blueprint -> Website generation -> public site -> public
lead intake is now fully proven for Medical Tourism specifically (the
core ask), but Recommendations surfacing Medical-Tourism-specific content
was not reached/verified, and the Discovery-page and no-AI-key findings
above are real platform gaps a future round (or the spawned task) should
resolve.

### Not started / not reached this round

- Recommendations content verification (Medical Tourism-specific
  recommendations actually surfacing) — blocked on the empty-
  recommendations finding above, not yet root-caused.
- Agent/ToolRegistry integration audit (Round 1 already confirmed the 7
  existing tools' scope is deliberate; a deeper audit against
  `KLAROS_MEDICAL_TOURISM_VALIDATION.md` still pending).
- Phase 6 (klaros_discovery live-wiring) and Phase 7 (FORCE RLS audit) —
  still untouched. FORCE RLS hard carve-out remains in effect; nothing was
  enabled.
- Security audit and `KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md` —
  still fully pending.
- No new automated pytest tests were added this round (the round's testing
  effort went into real-browser live validation instead, which exercised
  more of the real stack than an API-level test would have, but doesn't
  leave a regression-preventing artifact behind the way a test does) —
  **a future round should consider adding a pytest regression test for the
  consultations-appointment-date-window bug fixed this round**, since nothing
  currently guards against that specific regression recurring.

### Recommended next-round scope

1. Pick up `task_f1392e87` (Discovery page stuck-on-completion bug) or
   confirm it's been picked up separately.
2. Investigate the empty-Recommendations finding: is it genuinely just
   sparse Blueprint data, or a real gap in Medical-Tourism-specific
   recommendation surfacing? `app/services/recommendation_service.py` is
   the place to start reading.
3. Add a pytest regression test for the appointment date-window fix
   (`frontend/app/medical-tourism/consultations/page.tsx`) if/when this
   project's frontend gets any test coverage, or at minimum leave a code
   comment trail (already done) so it isn't silently reverted.
4. Re-seed `VerticalExtension` in the shared `pgserver` instance before
   any further manual/browser work if another `pytest` run has happened
   in between (see the Phase 3 section above for the exact re-seed
   snippet).
5. Agent/ToolRegistry audit, Phase 6, Phase 7 (audit-only), security
   audit, final completion log — all still fully pending, per Round 2's
   own recommended scope, now carried forward again.

## Git state at end of Round 3

Modified this round (all uncommitted, nothing staged, nothing committed,
nothing pushed): `frontend/components/AppShell.tsx`, `frontend/lib/api.ts`,
`frontend/app/medical-tourism/leads/page.tsx`,
`KLAROS_MEDICAL_TOURISM_COMPLETION_PROGRESS.md` (this file). New files:
`frontend/app/medical-tourism/consultations/page.tsx`,
`frontend/app/medical-tourism/referrals/page.tsx`. HEAD unchanged at
`af4937e403e47cdc141f2db349dfcc46a3df6c4b`. FORCE RLS was never touched.
The `pgserver` instance at `/private/tmp/klaros_pg_mtround2` was left
running for the next round to reuse (same as Round 2); its
`VerticalExtension` table currently has the 2 seed rows re-inserted this
round (not wiped again since, as no further `pytest` run happened after
that point in this round) and the "Smoke Test Medical Co" test tenant's
data (providers, procedures, patient leads, a consultation, a referral
commission, a published website) is still live in it for inspection if
useful.

## Round 4 (2026-09-30) — Recommendations root-caused, Phase 4 completed
## for Medical Tourism's own scope, Phase 5 + Phase 6 audits done

No source files were changed this round — this was entirely investigation/
audit/live-validation, using the same `pgserver` instance
(`/private/tmp/klaros_pg_mtround2`) and a fresh `uvicorn` + frontend dev
server pair (both stopped again at the end of this round).

### 1. Empty-Recommendations finding from Round 3 — root-caused, and a
### correction to Round 3's own write-up

**Round 3's assumption that `AIProvider.is_connected` is `False` in this
dev environment was WRONG** — `backend/.env` actually has a real
`OPENAI_API_KEY` configured. The real, AI-connected Discovery extraction
path runs here, not the deterministic fallback. This is a correction to
the record, not a new finding on its own, but it matters because it
changes the diagnosis of everything downstream.

Traced the real root cause precisely, by reading the code (not
guessing): Round 3's Blueprint-activation workaround (`PUT
/api/v1/business-blueprint/sections/{key}` with hand-written JSON) was
**the wrong API** for populating a Blueprint from Discovery answers —
`update_section` just overwrites `BlueprintSection.data` directly and
creates zero `BlueprintClaim` rows, but `RecommendationService`'s
`BASELINE_RULE` source (a business's own stated requirements) only ever
reads **CONFIRMED** `BlueprintClaim` rows under `REQUIRED_CAPABILITIES` —
confirmed by reading `business_blueprint_service.py`'s own code comment:
*"`BlueprintSection.data` is recomputed from CONFIRMED claims only ...
never hand-mutated independently of this recomputation."* Round 3's
workaround violated that documented invariant, so of course
`RecommendationService` found nothing to work with.

**Fixed the workaround properly this round**: queried the 21 real
`BlueprintClaim` rows the actual AI extraction had already produced for
this tenant (all still `PROPOSED` — Round 3 never confirmed any of them),
confirmed all 21 via `POST /api/v1/business-blueprint/claims/{id}/confirm`
(the real per-claim confirmation flow a real user would click through in
the Blueprint review UI), which correctly recomputed all 4
`BlueprintSection.data` fields from the confirmed claims. Then called
`POST /api/v1/recommendations/generate` directly (bypassing
`business-journey`'s `generate-recommendations`, which is intentionally
idempotent once a journey reaches `RECOMMENDATIONS_READY` — confirmed by
reading `business_journey_service.py`'s `generate_recommendations`, not
assumed).

**Result: 28 real recommendations generated**, confirmed via the actual
`/business/recommendations` UI (not just the API) — all 7
`medical_tourism` vertical capabilities
(`provider_directory`/`procedure_catalog`/`provider_offerings`/
`compliance_verification`/`patient_leads`/`cross_border_commission`/
`multi_currency`) each produced one `CAPABILITY` recommendation
(`source: VERTICAL_EXTENSION_RULE`, `why: "The 'medical_tourism' vertical
(enabled for this organization) registers '...' as one of its
capabilities."`), plus correct `TOOL` recommendations matching the real
Tool Catalog (`medical_tourism.create_procedure`/`create_provider`/
`create_provider_offering` for the MT-specific capabilities; generic
`crm.bulk_import_leads`/`crm.search_leads`/`insights.get_marketing_snapshot`
for `patient_leads` via keyword overlap — proving the cross-vertical,
zero-hardcoding tool-matching mechanism genuinely works for Medical
Tourism-flavored capability keys). Accepted one recommendation via the
real UI button; status correctly flipped `PROPOSED` -> `ACCEPTED`.
**This is the strongest, most direct proof yet that Phase 4's actual ask
— "Medical Tourism represented through the vertical-extension
architecture end-to-end, Discovery -> Blueprint -> Recommendations" — is
genuinely true**, driven through the real UI for the final confirmation
step, not just inferred from reading code.

**A second, real, separate bug found in the process** (not fixed — flagged
per the coordinator's instruction, since it's a platform-wide Discovery/
Blueprint/Recommendation contract issue, not Medical-Tourism-specific):
of the 21 real (AI-generated) claims, 4 were `REQUIRED_CAPABILITIES`
claims shaped as `{key: "bilingual_patient_coordinators", value: true}` —
one boolean fact per capability, a fully valid shape per
`discovery_extraction_service.py`'s own AI prompt contract (nothing in
`_SYSTEM_INSTRUCTIONS` constrains what `value` must look like for a
`REQUIRED_CAPABILITIES` claim). But `recommendation_service.py`'s
`_collect_capability_requirements` only reads `claim.value` expecting it
to literally BE the capability-name string (or a list of such strings) —
never `claim.key`, where the AI actually put the capability name. The
result: **all 4 of the business's own confirmed "we need X" capability
claims were silently dropped** — 0 `BASELINE_RULE` recommendations out of
28 total, with no error or log signal. Confirmed this is genuinely
reachable in production (not a contrived edge case) since it's the real,
AI-connected extraction path, not a fallback. Flagged via `spawn_task`
(`task_441a4daa`, "Fix REQUIRED_CAPABILITIES claim shape vs Recommendation
Engine contract") with the full repro, root cause, and both candidate
fixes (tighten the AI prompt contract vs. make the consumer tolerant of
the boolean-flag shape) written into the task description — not fixed
in-scope here, since it touches Phase 3's `RecommendationService` (a
different phase's core service, with its own "why no AI in structural
decisions" invariant and test suite) and is not Medical-Tourism-specific.

Net assessment for this task's own purposes: **Phase 4 is now complete
for what this task actually needs** — Medical Tourism's vertical
extension correctly and fully drives Recommendations via the
`VERTICAL_EXTENSION_RULE` path (unaffected by the `BASELINE_RULE` bug
above, which is about a business's own free-text-stated requirements, a
separate and independent source). The `BASELINE_RULE` bug is real but
orthogonal to Medical Tourism specifically — it would affect this exact
same way for a plumbing business, a dropshipping business, or any other
vertical's tenant using the real AI path.

### 2. Phase 5 — Agent/ToolRegistry audit

Read `KLAROS_MEDICAL_TOURISM_VALIDATION.md`'s own Agents/Workflows table
(the original design brief for this vertical) to find the documented,
validated agent use case: exactly one — a "Provider Matching" agent at
**Recommend tier** ("every match is a suggestion, not an auto-send"),
proposing which providers best match a qualified lead's procedure/
geography preference, explicitly said to use "a read-only
`medical_tourism.match_providers` tool". No such tool exists by that
exact name, but `medical_tourism_tools.py`'s own docstring (read in Round
2) already documents the implementer's deliberate choice: the existing
read-only `search_providers`/`search_procedures`/`list_provider_offerings`
tools "already serve as read-only building blocks for" that one use case
— i.e. an agent or workflow composes the existing primitives rather than
a single bespoke `match_providers` tool. This is a legitimate, pre-
existing (Phase 10) design decision, not something Round 2's new API
endpoints changed or created a gap in.

Audited each of the 4 new API surfaces Round 2 added
(PatientLead/Consultation/ReferralCommission create + the status-
transition endpoints) as **candidate** new tools, per the coordinator's
explicit checklist (tenant isolation/RBAC/idempotency/audit/autonomy/
approval):

- **`CreatePatientLead`** (agent auto-extends a Lead with MT intake
  fields): technically straightforward to build safely (tenant isolation
  and RBAC come free from `ExecutionContext`/`required_permission`, same
  as every existing tool; idempotency is more awkward — `create_patient_lead`
  has no idempotency-key dedup mechanism today, only a hard 1:1-uniqueness
  constraint that raises on a second attempt, unlike
  `create_provider`/`create_procedure`'s idempotency-key pattern — so
  `supports_idempotency` would have to stay `False`, consistent with this
  file's own established convention for exactly this situation). **Not
  added**: no documented validated use case calls for an agent to
  autonomously decide a lead needs Medical Tourism enrichment; that
  decision plausibly belongs with qualification staff or a future,
  separately-designed qualification agent, not invented here.
- **`UpdateConsultationStatus`** (agent marks a consultation COMPLETED/
  CANCELLED/NO_SHOW): whether a patient actually showed up is a real-
  world fact only a human present can attest — the same reasoning
  `medical_tourism_tools.py` already applied to exclude credential
  verification from agent tooling ("credential management is not an
  agent-callable action in this phase"). **Not added** — an agent cannot
  safely or honestly assert this fact.
- **`CreateReferralCommission`** / **`UpdateReferralCommissionStatus`**
  (agent creates/confirms a financial commission record): commission
  terms are business-negotiated with a partner hospital, and confirming
  one implies a real payment obligation — squarely the kind of
  financially consequential action this codebase's broader tool-approval
  conventions reserve for human decision-makers. **Not added** — no
  validated use case, and the financial-consequence bar alone would argue
  against autonomous creation even if one existed.
- Considered, and rejected, adding new **read-only** list/get tools for
  the three new entities too (lower risk than the above, no approval-tier
  question) — but found no documented or plausible agent consumer for
  them: `app/services/ai_next_action_service.py` and
  `app/tools/builtin/morning_brief_tools.py` were grepped and confirmed to
  contain zero vertical-specific logic of any kind (matching this
  codebase's consistent "no vertical branching in core services"
  architecture) — there is no generic "surface this vertical's own
  operational status" mechanism for Morning Brief/AI-Next-Action to plug
  into yet for ANY vertical, so building MT-specific read tools now would
  have no real consumer and would pre-empt whatever that future generic
  mechanism ends up needing.

**Conclusion: the current scope (7 tools, all read-only or create-with-
idempotency-key, zero tools for PatientLead/Consultation/
ReferralCommission) remains the correct call**, even after Round 2's new
API surfaces. Nothing new was added. This matches
`medical_tourism_tools.py`'s own stated principle: "No tool is created
merely because an API endpoint exists."

### 3. Phase 6 — audit of the 4 deferred `klaros_discovery` live-wirings

Read `PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md`'s own table (§35) of the
5 real cross-tenant "discovery sweep" paths this refers to: EventBus
stuck-event discovery (`events`), Automation discovery
(`automations`/`automation_versions`), Agent discovery
(`agents`/`agent_versions`), Morning Brief discovery (`organizations`),
Agent Recovery discovery (`agent_executions`). All 5 have their
*database-level* `klaros_discovery` role grants done (since Round 4/5 of
that phase), but the *live application code* at each call site still
doesn't actually open a `klaros_discovery`-bound session — it's tracked
there as "§34 item 2," a known, deliberate deferral, not a bug.

Read `morning_brief_service.py`'s actual discovery-sweep code
(`check_and_generate_scheduled`) in full as a representative sample: it
reads only the `Organization` table with no tenant context (a genuine
`CROSS_TENANT_SYSTEM` scan, by the module's own docstring — `Organization`
itself is `AUTH_BOUNDARY`/no `tenant_id` at all, not a Medical-Tourism or
any other tenant-owned table), then does every subsequent per-tenant read/
write in its own separately-opened, properly `set_tenant_context`-scoped
session. Confirmed via grep that none of `automations`/`automation_
versions`/`agents`/`agent_versions`/`organizations`/`agent_executions`/
`events` — the 7 tables these 5 sweeps touch — are Medical Tourism tables,
and confirmed (consistent with every prior round's finding) that
`MedicalTourismService` has no cross-tenant sweep of its own anywhere —
every one of its methods takes an explicit `tenant_id` and scopes every
query to it.

**Audit conclusion: fixing these 4 (or 5) deferred live-wirings is
completely orthogonal to Medical Tourism's own completion criteria.**
This is general RLS-enforcement-readiness defense-in-depth
infrastructure (relevant only once/if the project ever moves from
audit-mode RLS to FORCE-mode enforcement — itself the permanently-out-of-
scope hard carve-out for this task) for a fixed set of platform-level
system tables that have no Medical Tourism data in them at all. Not
flagged as a new `spawn_task` (it was already a known, tracked item in
`PHASE_17B4_REAL_RLS_IMPLEMENTATION_LOG.md` before this task started —
re-flagging it here would be noise, not a new finding), and explicitly
NOT fixed in-scope, matching the audit-only instruction. Did not
exhaustively trace all 5 call sites' exact source (only
`morning_brief_service.py`, as a representative, sufficiently strong
sample given the architectural pattern is identical across all 5 per
the Phase 17B-4 table) — if a future round wants full certainty on all 5,
the other 4 files to check are wherever `automations`/`agents`/
`agent_executions`/`events` cross-tenant scans live (not located precisely
this round).

### Not started / still open

- Recommendations content has now been fully validated for Medical
  Tourism; the `BASELINE_RULE` bug (task_441a4daa) and the Discovery-page
  stuck-on-completion bug (task_f1392e87) remain open, tracked separately.
- Security audit and `KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md` —
  still fully pending.
- A full real-Postgres regression pass (the complete suite) — still not
  done since Round 2's targeted subset; still recommended before any
  "COMPLETE" claim.
- FORCE RLS — still never touched, hard carve-out fully respected.

### Recommended next-round scope

1. Security audit pass over the Medical Tourism surfaces built across
   Rounds 2-3 (API endpoints, public lead intake, frontend forms) —
   nothing dedicated has been done yet beyond the tenant-isolation tests
   already written.
2. A full real-Postgres regression run (not just the Medical-Tourism-
   relevant subset) before any "COMPLETE" claim — Round 2's own
   recommendation, still not done.
3. Start drafting `KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md` once the
   above are done.
4. Both spawned tasks (`task_f1392e87`, `task_441a4daa`) are independent
   of Medical Tourism's own remaining work and can proceed in parallel on
   their own track — no need to block this task's own completion on them,
   but worth checking their status before any final sign-off, since a
   truly complete Business Journey demo would currently still hit both.

## Git state at end of Round 4

No files modified this round (investigation/audit/live-validation only,
plus updating this progress doc). `git status --short` unchanged from end
of Round 3 except for this file. HEAD unchanged at
`af4937e403e47cdc141f2db349dfcc46a3df6c4b`. FORCE RLS never touched. Both
`uvicorn` and the frontend dev server were stopped at the end of this
round (unlike Round 2/3, nothing was deliberately left running this time
beyond the `pgserver` Postgres instance itself, which remains at
`/private/tmp/klaros_pg_mtround2` for the next round to reuse). The
"Smoke Test Medical Co" tenant's data — now including a fully-populated,
ACTIVE Blueprint with all 21 claims CONFIRMED, a real 28-recommendation
`RecommendationRun`, and one ACCEPTED recommendation — is still live in
that database for inspection.

## Round 5 (2026-09-30) — Security audit, full real-Postgres regression,
## final completion log

This round closed out the task's own remaining checklist: a security
audit scoped to everything this task added, a full real-Postgres
regression pass compared against the Phase 17B-4 baseline, and
`KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md` (the 21-section +
recommendation completion log, structured to mirror
`FINAL_KLAROS_PRE_COMMIT_AUDIT.md`'s own template — confirmed to be the
right template by finding it has exactly 21 numbered sections plus a
"Final Recommendation" section).

### Security audit (this task's own files only)

Swept every file this task modified or created (13 files: 5 modified
backend/frontend + 8 new) for: `BYPASSRLS`/superuser/`FORCE ROW LEVEL
SECURITY`, `is_system`/`system=True`/`SYSTEM_TENANT`/tenant-bypass
patterns, hardcoded/fabricated tenant UUIDs, duplicate tenant-context
mechanisms, secrets/API keys/passwords/credentials, frontend dangerous
patterns (`dangerouslySetInnerHTML`/`eval`/`console.log`/`debugger`),
backend dangerous patterns (`subprocess`/`eval`/`exec`/`os.system`), RBAC
coverage on every new endpoint, and raw-SQL-bypassing-the-ORM patterns.

**Result: zero findings across every category.** Specifically confirmed:
every `tenant_id` reference in `medical_tourism.py`'s router is
`current_user.tenant_id` (JWT-derived), never a request-body field; all
28 `set_tenant_context` call sites across `medical_tourism_service.py`
(19 pre-existing + 9 new this task) plus the 1 in `public_leads.py` use
the single canonical `app.db.session.set_tenant_context` import, no
duplicate/alternate mechanism anywhere; every one of the 8 new endpoints
has an explicit `_require_read`/`_require_manage` RBAC check (verified by
reading every route handler, not sampled); `KLAROS_MEDICAL_TOURISM_
COMPLETION_PROGRESS.md` itself mentions `OPENAI_API_KEY` by name only,
never reproducing the actual key value found in `backend/.env`; the
`public_leads.py` public trust boundary (tenant_id as an unauthenticated
URL path parameter) is confirmed unchanged from its pre-existing,
already-audited design — this task only added optional body fields and a
vertical-check branch after the existing tenant-existence check, never
widened what an anonymous caller controls.

### Full real-Postgres regression pass

Bootstrapped a **fresh** disposable `pgserver` instance
(`/private/tmp/klaros_pg_round5`, separate from Round 2-4's
`klaros_pg_mtround2`, specifically so this regression pass started from a
genuinely empty database rather than one with 4 rounds of manual test
data and ad-hoc DB surgery sitting in it) and ran `alembic upgrade head`
clean (0001→0063, exit 0) before running anything else.

Ran the complete suite (`pytest tests/ -q`, 2136 collected) against that
instance. **Result: 1 failed, 2123 passed, 12 skipped, in 1020.99s
(17:00).** The single failure is
`tests/test_openai_realtime_voice_service.py::test_voice_stream_route_dispatches_to_realtime_engine_when_configured`
— the exact same pre-existing WebSocket-test-harness quirk the Phase
17B-4 baseline (2117 passed, 1 known flake, 12 skipped) and
`FINAL_KLAROS_PRE_COMMIT_AUDIT.md` both already documented, not a new
regression and not touched by this task anywhere. The +6 passed-count
delta over the 2117 baseline is accounted for by this task's own 7 new
tests in `test_medical_tourism_api_round2.py`. Re-ran all 8
Medical-Tourism-relevant test files in isolation as a targeted
cross-check: **57 passed, 0 failed** — confirms they genuinely passed as
part of the full run, not just present in the aggregate count.

Frontend re-confirmed clean in the same round: `npx tsc --noEmit` exit 0;
`npx next build` exit 0, all routes including the 5 new Medical Tourism
routes; `npx vitest run` — 15 test files, 89/89 passed, exact match to
the pre-existing baseline.

The fresh `klaros_pg_round5` instance was stopped and torn down after
this regression pass (its only purpose was a clean-slate baseline
comparison) — `klaros_pg_mtround2` (with the live "Smoke Test Medical
Co" tenant data) remains running for a future round to reuse, per the
established pattern.

### `KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md` — written this round

Final classification: **COMPLETE WITH LIMITATIONS** — deliberately not
rounded up to a clean COMPLETE, matching the same standard every RLS
phase's own final log in this project has held itself to. Medical
Tourism's own completion criteria are all solidly met and independently
verified (not just asserted); the "WITH LIMITATIONS" qualifier exists
specifically because of the two spawned follow-up tasks
(`task_f1392e87`, `task_441a4daa`) that remain open — both genuinely
platform-wide, not Medical-Tourism-specific, and neither blocks this
task's own criteria, but an honest account can't omit them. Full detail,
all 21 sections plus the Final Recommendation, is in that document —
not restated here in full to avoid the two documents drifting out of
sync; this progress doc's own round-by-round record is what the final
log's §3 (Task Traceability) summarizes and cites.

### This task's own scope — assessment as of end of Round 5

All of this task's originally-assigned work is now done:
1. Backend API completeness — done (Round 2), tested (Round 2, re-verified Round 5).
2. Full operational frontend — done (Rounds 2-3), live-browser-validated (Round 3).
3. Website Builder integration — done (pre-existing) and fully validated end to end (Round 3).
4. Business Journey validation — validated for Medical Tourism's own content (Round 4), with two unrelated platform bugs found and correctly deferred.
5. Agent/ToolRegistry integration audit — done (Round 4), no changes needed.
6. Audit (not enablement) of Phase 17B-4's RLS limitations — done (Round 4), confirmed orthogonal.
7. Real-Postgres E2E validation — done throughout, plus a dedicated full-suite pass this round.
8. Frontend QA — done (Round 3 live validation, Round 5 re-confirmation).
9. Security audit — done this round.
10. Final documentation report — this round.

Nothing remains in this task's own scope. The two spawned follow-up
tasks are independent work items, not part of this task's own remaining
checklist.

## Git state at end of Round 5

No source files were modified this round beyond the two documentation
files (`KLAROS_MEDICAL_TOURISM_COMPLETION_PROGRESS.md`, this file, and
the new `KLAROS_FINAL_COMPLETION_IMPLEMENTATION_LOG.md`). HEAD unchanged
at `af4937e403e47cdc141f2db349dfcc46a3df6c4b`. Nothing staged, nothing
committed, nothing pushed, across all five rounds. FORCE RLS never
touched. `git status --short`: 277 lines (unchanged from end of Round 4
except for the one new final-log file).
