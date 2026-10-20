# Clinic Outreach Campaign, Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An owner can create a clinic outreach campaign from the Klaros dashboard, have Klaros find each clinic's business email from its own public pages, and press Start (shadow). **Nothing is sent in this phase.**

**Architecture:** A `Campaign` (channel `OUTBOUND`) gets a one-to-one `clinic_outreach_campaigns` configuration (term sheet, sender, limits, mode) and one `clinic_outreach` row per clinic with a stage. A deterministic service does discovery and lifecycle; there is no AI and no sending. The dashboard adds a wizard and a campaign panel inside Marketing > Campaigns.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Pydantic v2, pytest (SQLite in-process, `client` fixture); Next.js 15 / React / vitest. Reuses `mt_clinic_sourcing.SearchProvider`, `Provider`, `ProviderTerms`, `Campaign`, `AuditLog`.

**Spec:** `docs/superpowers/specs/2026-10-20-clinic-outreach-campaign-design.md`

## Global Constraints

- No email, SMS or message of any kind is sent in this phase. No code in this plan may call `get_clinic_channel()`, `get_communication_provider()` or any channel `deliver_*`.
- An email address is stored only if it appears in text returned for the clinic's own page and its domain equals that page's domain. Never guessed, never pattern-built, never bought. A person may add one by hand.
- Free-mail domains (gmail.com, yahoo.com, hotmail.com, outlook.com, live.com, icloud.com, proton.me, protonmail.com, aol.com) and local parts `noreply`, `no-reply`, `donotreply`, `privacy`, `careers`, `jobs`, `hr`, `press`, `media`, `legal`, `abuse`, `webmaster` are never accepted automatically. An MX record must exist for the domain.
- Defaults, from spec section 4 and 6: `daily_cap` 20, `ramp_start` 5, `max_emails_per_clinic` 3 (never configurable above 3), send window 09:00 to 17:00 weekdays.
- Money values are `Decimal`. Percentages are 0 to 100.
- Every table is tenant-scoped (`TenantScopedMixin`) with row-level security policies `tenant_select`, `tenant_insert`, `tenant_update` on PostgreSQL, like `alembic/versions/0076_inbound_email.py`.
- Permissions: reading needs `Permission.READ_MARKETING`; create, find contacts, start, pause, stop and edit need `Permission.MANAGE_CAMPAIGNS`.
- The migration's `down_revision` is the current Alembic head when the task runs (`0079_catalog_real_connectors` on the deploy line today; use the next free number, and re-chain if another migration merged first). `tests/test_alembic_single_head.py` must pass.
- Run backend tests from `backend/` with `/Users/mohammedsohail/Desktop/Klaros AI/backend/.venv/bin/python -m pytest <path> -q -p no:cacheprovider`. Run frontend tests from `frontend/` with `npx vitest run <path>`.
- Never `git add -A`. Commit only the files a task lists. End commits with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A clinic whose site publishes only a contact form (no email): becomes `NO_EMAIL` with a reason and is retried later; never an error. Tested in Task 3 and Task 4.
2. An email found on an aggregator page, on a different domain from the clinic's page, a free-mail address, or a `noreply`/`careers` style address: rejected. Tested in Task 3.
3. The same clinic selected in two active campaigns: the second campaign skips it, so it can never be contacted twice. Tested in Task 4.
4. A suspended or inactive clinic, or one that already has agreed commission terms: never selected. Tested in Task 4.
5. Term sheet nonsense (minimum above maximum, 150 percent, zero or negative payment days) and a missing search provider: refused with 422, and with status `not_configured` that changes no rows. Tested in Tasks 2, 4 and 5.

---

### Task 1: Data model and migration

**Files:**
- Create: `backend/app/models/clinic_outreach.py`, `backend/alembic/versions/0080_clinic_outreach.py` (renumber per Global Constraints)
- Modify: `backend/app/models/__init__.py` (one import line)
- Test: `backend/tests/test_clinic_outreach_models.py`

**Interfaces:**
- Produces: `OutreachMode` (`SHADOW`, `LIVE`), `OutreachStage` (the 14 values in spec section 5: `QUEUED, NO_EMAIL, READY, INTRO_SENT, FOLLOWUP_1, FOLLOWUP_2, NO_RESPONSE, REPLIED, TERMS_PROPOSED, NEEDS_PERSON, AGREED_PENDING, DECLINED, OPTED_OUT, HANDED_TO_VERIFICATION`), and models `ClinicOutreachCampaign`, `ClinicOutreach`, `OutreachSuppression`. Columns as in spec section 9, except `clinic_outreach_messages`, which is Phase 2. `ClinicOutreach` also has `contact_source` (`"owner"` or `"site"`) and `contact_checked_at` (nullable `DateTime`, set on every discovery attempt, found or not). `ClinicOutreachCampaign.campaign_id` is unique and a foreign key to `campaigns.id`; `ClinicOutreach` is unique on (`campaign_id`, `provider_id`); `OutreachSuppression` is unique on (`tenant_id`, `value`) with `kind` `address` or `domain`.

- [ ] **Step 1: Write the failing tests** in `test_clinic_outreach_models.py`: `test_a_clinic_can_appear_once_per_campaign` (second insert for the same campaign and provider raises `IntegrityError`); `test_stage_defaults_to_queued_and_mode_to_shadow`; `test_suppression_is_unique_per_tenant`.
- [ ] **Step 2: Run them to verify they fail** (`ImportError` for the module).
- [ ] **Step 3: Implement** the models following `app/models/mt_revenue.py` style, plus the migration with indexes on `tenant_id`, `campaign_id`, `stage` and RLS policies (Postgres only), with a working `downgrade`.
- [ ] **Step 4: Run** the new tests plus `tests/test_alembic_single_head.py` and `tests/test_migration_schema_matches_models.py`; all pass.
- [ ] **Step 5: Commit** `feat(outreach): data model for clinic outreach campaigns`.

### Task 2: Campaign settings and term sheet validation

**Files:**
- Create: `backend/app/services/outreach_settings.py`
- Test: `backend/tests/test_outreach_settings.py`

**Interfaces:**
- Produces: Pydantic models `TermSheet(basis: Literal["PERCENTAGE","FLAT"], min_value: Decimal, max_value: Decimal, payment_days: int, currency: str, qualifying_outcome: str)`, `Sender(display_name: str, signature: str, company_name: str, postal_address: str)`, `Limits(daily_cap: int = 20, ramp_start: int = 5, max_emails_per_clinic: int = 3, send_window_start: int = 9, send_window_end: int = 17, weekdays_only: bool = True)`, `OutreachConfig(term_sheet: TermSheet, sender: Sender, limits: Limits = Limits(), countries: list[str])`.

- [ ] **Step 1: Write the failing tests:** `test_defaults_match_the_spec` (a default `Limits()` has 20, 5, 3, 9, 17, True); `test_term_sheet_rejects_nonsense` parametrised over minimum above maximum, percentage 150, negative minimum, `payment_days` 0 and 121, a two-letter-only currency violation (`"US"`), each raising `ValidationError`; `test_percentage_term_sheet_accepts_10_to_15_and_30_days`; `test_limits_cannot_exceed_three_emails_per_clinic` (`max_emails_per_clinic=4` fails); `test_ramp_start_cannot_exceed_daily_cap`; `test_send_window_must_start_before_it_ends`; `test_sender_fields_cannot_be_blank_or_whitespace`; `test_countries_must_be_two_letter_codes_and_not_empty`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the models with Pydantic validators; strip strings; `currency` is exactly three letters, uppercased; countries uppercased.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** `feat(outreach): campaign settings and term sheet validation`.

### Task 3: Contact finder

**Files:**
- Create: `backend/app/services/clinic_contact_finder.py`
- Test: `backend/tests/test_clinic_contact_finder.py`

**Interfaces:**
- Consumes: `mt_clinic_sourcing.SearchProvider` and `SearchHit(title, url, content)`; `Provider` (`name`, `city`, `country`, `contact_email`).
- Produces: `usable_address(email: str) -> bool` (applies the Global Constraints address rules), `async def dns_mx_exists(domain: str) -> bool` (Cloudflare DNS-over-HTTPS MX lookup, `False` on any error), `@dataclass(frozen=True) ContactResult(email: str | None, source_url: str | None, source: Literal["owner","site"] | None, reason: str)`, and `async def find_contact(provider: Provider, search: SearchProvider | None, mx_exists: Callable[[str], Awaitable[bool]] = dns_mx_exists) -> ContactResult`. `reason` is one of `owner`, `site`, `not_configured`, `no_hits`, `no_email_on_clinic_site`, `mx_failed`.

- [ ] **Step 1: Write the failing tests** with a fake `SearchProvider` and a fake `mx_exists`: `test_a_person_added_email_is_used_without_searching` (provider has `contact_email`; search never called; `source == "owner"`); `test_finds_a_departmental_email_on_the_clinics_own_page` (hit titled with the clinic name, content containing `international@clinic.example`, url `https://www.clinic.example/contact`; result email equals it, `source == "site"`, `source_url` is the hit URL); `test_prefers_international_over_info` when both appear; `test_rejects_an_email_on_a_different_domain_than_the_page` (aggregator page listing `x@other.example`); `test_rejects_free_mail_and_role_addresses` over `a@gmail.com`, `noreply@clinic.example`, `careers@clinic.example`; `test_a_page_without_the_clinic_name_is_not_the_clinics_page`; `test_contact_form_only_gives_no_email_on_clinic_site`; `test_no_search_provider_gives_not_configured_and_makes_no_calls`; `test_mx_failure_rejects_the_address` (`reason == "mx_failed"`); `test_never_builds_an_address_from_a_pattern` (page with only a phone number returns no email).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** One query: `f"{name} {city} {country_name} international patients contact email"` using `COUNTRY_NAMES` from `mt_clinic_sourcing`. A hit counts as the clinic's page when the normalised clinic name (lowercased, punctuation removed) appears in its title or content. Extract with the existing `_EMAIL` regex; keep only addresses whose registered domain equals the hit URL's registered domain, that pass `usable_address`, and that pass `mx_exists`. Rank local parts `international`, `intl`, `patient`, `appointment`, `inquiry`, `contact`, `info` in that order.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** `feat(outreach): find a clinic's business email from its own public page`.

### Task 4: Outreach service

**Files:**
- Create: `backend/app/services/clinic_outreach_service.py`
- Test: `backend/tests/test_clinic_outreach_service.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 `OutreachConfig`, Task 3 `find_contact` and `usable_address`, `Campaign`, `CampaignChannel.OUTBOUND`, `CampaignStatus`, `Provider`, `ProviderStatus`, `ProviderTerms`, `AuditLog`, `search_provider_from_settings`.
- Produces: `class OutreachError(ValueError)`; `class ClinicOutreachService(session_factory, search_factory=search_provider_from_settings, mx_exists=dns_mx_exists)` with: `async eligible_clinics(tenant_id, countries: list[str]) -> list[dict]` (`id`, `name`, `country`, `city`, `has_email: bool`); `async create_campaign(tenant_id, user_id, *, name: str, config: OutreachConfig, provider_ids: list[UUID] | None) -> UUID` (creates a DRAFT `Campaign` and outreach rows in stage `QUEUED`); `async get(tenant_id, campaign_id) -> dict` (`campaign`, `config`, `mode`, `funnel: dict[stage, int]`, `clinics: list[dict]`); `async find_contacts(tenant_id, campaign_id, *, limit: int = 10) -> dict` (`status` `ok` or `not_configured`, `checked`, `found`, `no_email`); `async set_contact(tenant_id, campaign_id, provider_id, email: str) -> None`; `async start_shadow(tenant_id, user_id, campaign_id) -> None`; `async pause(...)`, `async stop(...)` (same signature); `async suppress(tenant_id, value: str, kind: str, reason: str) -> None`; `async is_suppressed(tenant_id, email: str) -> bool`.

- [ ] **Step 1: Write the failing tests:** `test_eligible_clinics_are_active_in_the_chosen_countries_with_no_terms` (excludes a SUSPENDED clinic, an INACTIVE one, a clinic with a `ProviderTerms` row, and a clinic in another country); `test_create_campaign_makes_a_draft_outbound_campaign_and_queued_rows`; `test_a_clinic_in_another_active_campaign_is_skipped` (second campaign has no row for it; after the first is `stop`ped it is selectable again); `test_find_contacts_moves_clinics_to_ready_or_no_email_and_records_the_source` (fake search); `test_find_contacts_is_bounded_by_limit`; `test_find_contacts_without_a_search_provider_returns_not_configured_and_changes_nothing`; `test_a_no_email_clinic_is_retried_only_after_14_days` (`NO_EMAIL` rows whose `contact_checked_at` is under 14 days old are skipped; backdate the column in the test); `test_set_contact_rejects_unusable_and_suppressed_addresses`; `test_start_shadow_sets_active_and_records_shadow_started_at_and_writes_an_audit_row`; `test_nothing_is_sent_by_any_outreach_call` (patch `get_clinic_channel` and `get_communication_provider` to raise; run every method; no raise); `test_pause_and_stop_set_status_and_never_resume_automatically`; `test_other_tenants_cannot_read_or_change_a_campaign`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the service. Selection excludes any provider that has a non-terminal outreach row (`stage` not in `DECLINED, OPTED_OUT, NO_RESPONSE, HANDED_TO_VERIFICATION`) in a campaign whose status is `ACTIVE` or `PAUSED` or `DRAFT`. Write `AuditLog` rows named `outreach.campaign_created`, `outreach.campaign_started`, `outreach.campaign_paused`, `outreach.campaign_stopped`, `outreach.contact_found`, with ids only. Start requires a complete config and at least one clinic.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** `feat(outreach): campaign lifecycle and contact discovery service`.

### Task 5: API

**Files:**
- Create: `backend/app/api/v1/outreach.py`
- Modify: `backend/app/api/v1/router.py` (import and include)
- Test: `backend/tests/test_outreach_api.py`

**Interfaces:**
- Consumes: `ClinicOutreachService`, `OutreachConfig`, `OutreachError`.
- Produces under `/api/v1/outreach`: `GET /eligible-clinics?countries=TR,TH` (READ_MARKETING); `POST /campaigns` body `{name, config, provider_ids?}` returning `{campaign_id}` with 201; `GET /campaigns/{id}`; `POST /campaigns/{id}/find-contacts`; `PUT /campaigns/{id}/contacts/{provider_id}` body `{email}`; `POST /campaigns/{id}/start`, `/pause`, `/stop`; `GET /suppressions` and `POST /suppressions`. An `OutreachError` becomes 409 with its message; unknown campaign 404.

- [ ] **Step 1: Write the failing tests** using the `client` fixture and an owner token: `test_create_get_and_find_contacts_round_trip`; `test_invalid_term_sheet_is_422` (percentage 150); `test_a_user_without_manage_campaigns_cannot_create_or_start` (403); `test_another_tenant_gets_404_for_the_campaign`; `test_start_pause_stop_change_the_campaign_status`; `test_set_contact_with_a_free_mail_address_is_409`; `test_unauthenticated_requests_are_401`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the router with `require_permission` dependencies like `app/api/v1/operating_crew.py`; build the service with `async_session_maker`.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** `feat(outreach): outreach API`.

### Task 6: Dashboard wizard and campaign panel

**Files:**
- Modify: `frontend/lib/api.ts` (types and functions for every endpoint in Task 5), `frontend/app/marketing/campaigns/page.tsx` (a "New clinic outreach campaign" button), `frontend/app/marketing/campaigns/[id]/page.tsx` (render the panel when `getOutreachCampaign` succeeds)
- Create: `frontend/components/marketing/ClinicOutreachWizard.tsx`, `frontend/components/marketing/ClinicOutreachPanel.tsx`
- Test: `frontend/components/marketing/__tests__/ClinicOutreach.test.tsx`

**Interfaces:**
- Consumes: Task 5 endpoints.
- Produces: `ClinicOutreachWizard({ token, onCreated(campaignId: string) })` with steps Basics (name, countries), Clinics (checkbox list from `eligible-clinics`, all ticked), Term sheet, Sender, Limits (defaults from spec), Review (plain-words rules summary; the intro-email preview is deferred to Phase 2 where the composer ships). `ClinicOutreachPanel({ token, campaignId })` showing status and mode badge, funnel counts, the clinic table with stage and contact, a Needs-you list for `NO_EMAIL` clinics with a manual email field, buttons **Find contacts**, **Start in shadow mode**, **Pause**, **Stop**, and a disabled **Go live** with the text "Available after the shadow week (a later release)". A visible line states "Nothing is sent in this release."

- [ ] **Step 1: Write the failing tests:** `test_the_wizard_blocks_next_until_required_fields_are_valid` (minimum above maximum shows an error and disables Next); `test_the_wizard_submits_the_chosen_clinics_and_calls_onCreated`; `test_the_panel_shows_funnel_counts_and_the_nothing_is_sent_notice`; `test_start_is_shadow_only_and_go_live_is_disabled`; `test_find_contacts_updates_the_list`; `test_a_no_email_clinic_accepts_a_manual_email_and_shows_the_api_error_for_free_mail`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** both components in the existing style (`Button`, `Badge`, `EmptyState` from `components/ui`).
- [ ] **Step 4: Run** the new test file, `npx tsc --noEmit -p .`, and the full `npx vitest run`; all pass.
- [ ] **Step 5: Commit** `feat(outreach): clinic outreach wizard and campaign panel`.

### Task 7: Whole-phase verification

**Files:**
- Test: `backend/tests/test_clinic_outreach_phase1_end_to_end.py`

- [ ] **Step 1: Write the end-to-end test** through the real app with a fake search provider: create two clinics (one with a findable departmental email, one with only a contact form), create a campaign through the API, find contacts, assert the funnel is one `READY` and one `NO_EMAIL`, add the second address by hand, start in shadow mode, and assert status `ACTIVE`, mode `SHADOW`, the audit rows exist, no channel was called, and a second tenant sees nothing.
- [ ] **Step 2: Run it and the whole backend suite** (`pytest tests -q`, about 17 minutes) and the whole frontend suite; all pass with no new failures.
- [ ] **Step 3: Commit** `test(outreach): phase 1 end to end`.

## Out of scope for this plan

Sending, scheduling, the engine, the composer, reply handling, Try a reply, shadow-week review marks, the Go live action, and the intro-email preview: Phases 2 to 4. Phase 2 must also coordinate with the existing Clinic Onboarder invitation (14-day rule) so a clinic is never emailed by both.
