# Clinic Outreach Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A rule-based outreach engine that stores shadow drafts for each clinic from approved wording blocks, a checker that blocks any unapproved claim or figure, and a dashboard where the owner approves the wording and reviews the shadow week. **Nothing is sent.**

**Architecture:** Pure modules (wording-block validation, composer, checker, send-slot and warm-up arithmetic) feed one engine service that runs once a day per tenant through the existing crew `schedule_action` mechanism and writes `clinic_outreach_messages` rows with status `QUEUED_SHADOW` or `BLOCKED`. The API gains wording and review routes; the frontend gains a wording page and a Shadow review tab. The real send path is not touched.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, pydantic; Next.js, vitest, testing-library. Backend python: `/Users/mohammedsohail/Desktop/Klaros AI/backend/.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-10-21-clinic-outreach-phase-2-design.md` (parent: `2026-10-20-clinic-outreach-campaign-design.md`). Phase 1 code: `backend/app/models/clinic_outreach.py`, `services/outreach_settings.py`, `services/clinic_outreach_service.py`, `services/clinic_contact_finder.py`, `api/v1/outreach.py`, `frontend/components/marketing/ClinicOutreach{Wizard,Panel}.tsx`.

## Global Constraints

- No code path in this phase may send, queue for sending, or call `get_clinic_channel`, any `send_email`, SMS, WhatsApp or SMTP function. The engine is shadow only and refuses a `LIVE` campaign.
- Every service method sets tenant context (`set_tenant_context`) before its first query and filters on `tenant_id`; new tables use `TenantScopedMixin`, RLS policies `tenant_select`/`tenant_insert`/`tenant_update` (no FORCE, no grants, as migration 0080 does).
- Audit rows and logs hold ids and counts only: never an email address, message body or clinic name.
- Wording blocks contain **no digits at all**; every figure reaches a message only through a placeholder filled from the term sheet. Allowed placeholders exactly: `{clinic_name}`, `{business_name}`, `{sender_name}`, `{sender_title}`, `{postal_address}`, `{opt_out_line}`, `{commission}`, `{payment_days}`.
- Block limits: subject 120 characters, body 1,500. Banned phrases (case-insensitive): `partner of`, `official partner`, `endorsed`, `guarantee`, `guaranteed`, `exclusive`, `we send you patients`, `patients per month`.
- Warm-up cap on campaign day `n` (0 for the day shadow started): `min(limits.daily_cap, limits.ramp_start + 2 * n)`. At most 3 outbound drafts per clinic. Spacing: follow-up 1 four days after the intro's scheduled time, follow-up 2 five days after follow-up 1, `NO_RESPONSE` seven days after follow-up 2.
- New migration is `0084_clinic_outreach_messages`, `down_revision = "0082_merge_outreach_and_dropshipping"` (the merge revision that joined the outreach and dropshipping heads; main must keep a single head). The unmerged Xero/Google Business Profile branch also uses 0080; whichever merges second renumbers.
- Permissions: reads `READ_MARKETING`, every mutation the campaign-management permission used by `api/v1/outreach.py`.
- English only. No public-holiday calendar.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies but no single task's tests would otherwise cover, most likely first:

1. Engine run twice, or two runs at once, for the same day: exactly one draft per clinic and kind (Task 5 test).
2. A clinic whose address, sender postal address or name contains digits must not make the checker false-block, and a stray `%`, amount or "within N days" must always block (Task 3 tests).
3. A campaign paused, stopped, kill-switched, or with unapproved wording at any point during a run produces nothing, including mid-run (Task 5 tests).
4. A blocks edit after approval silently re-opens the draft state; shadow messages made from the older version stay marked with their `blocks_version` (Task 2 and Task 5 tests).
5. A clinic in an unknown country, a suppressed address, or an Onboarder invitation inside 14 days is parked or skipped with a visible reason, never composed (Task 5 test).

---

### Task 1: Message table and migration

**Files:**
- Create: `backend/alembic/versions/0084_clinic_outreach_messages.py`
- Modify: `backend/app/models/clinic_outreach.py` (add `ClinicOutreachMessage`, `engine_last_run_at` on `ClinicOutreachCampaign`), `backend/app/models/__init__.py` (export)
- Test: `backend/tests/test_clinic_outreach_messages_model.py`

**Interfaces:**
- Produces: `ClinicOutreachMessage(TenantScopedMixin, Base)`, table `clinic_outreach_messages`: `outreach_id` (FK `clinic_outreach.id`, indexed), `direction` String(3) default `"OUT"`, `kind` String(20), `status` String(20), `subject` String(255), `body` Text, `blocks_version` Integer, `blocked_reason` Text null, `scheduled_for` DateTime(tz), `review_mark` String(10) null (`GOOD`|`FLAGGED`), `review_reason` String(500) null, `reviewed_by` Uuid null, `reviewed_at` DateTime(tz) null. Unique constraint `uq_outreach_message_kind_day` on (`outreach_id`, `kind`, `schedule_day`) where `schedule_day` is a Date column set to the UTC date of `scheduled_for`. Index on (`tenant_id`, `status`, `scheduled_for`).
- Produces: `class OutreachMessageKind(StrEnum)`: `INTRO="intro"`, `FOLLOWUP_1="followup_1"`, `FOLLOWUP_2="followup_2"`; `class OutreachMessageStatus(StrEnum)`: `QUEUED_SHADOW`, `BLOCKED`.
- Produces: `ClinicOutreachCampaign.engine_last_run_at: DateTime(tz) null`.

- [ ] **Step 1: Write failing tests** `test_message_row_round_trips_with_defaults`, `test_duplicate_kind_for_the_same_day_is_rejected`, `test_same_kind_on_a_different_day_is_allowed`, `test_migration_creates_table_with_rls_policies` (follow the pattern in `tests/test_clinic_outreach_models.py` and the existing migration test style).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the model changes and migration 0084 mirroring 0080_clinic_outreach's RLS statements (ENABLE, three policies) with a working `downgrade`.
- [ ] **Step 4: Run the new tests and `tests/test_clinic_outreach_models.py`; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): shadow message table`.

### Task 2: Wording blocks (validation, defaults, settings, service)

**Files:**
- Create: `backend/app/services/outreach_blocks.py`
- Modify: `backend/app/services/tenant_settings_service.py` (key `OUTREACH_BLOCKS = "outreach_blocks"`, default `{"status": "DRAFT", "version": 0, "blocks": DEFAULT_BLOCKS}`), `backend/app/services/clinic_outreach_service.py` (blocks methods, gate in `start_shadow`)
- Test: `backend/tests/test_outreach_blocks.py`, additions to `backend/tests/test_clinic_outreach_service.py`

**Interfaces:**
- Produces in `outreach_blocks.py`: `DEFAULT_BLOCKS: dict[str, str]` holding exactly the appendix A text of the Phase 2 spec under keys `subject_intro`, `intro`, `subject_followup_1`, `followup_1`, `subject_followup_2`, `followup_2`, `opt_out_line`, `how_referrals_work`, `faq_what_we_do`, `faq_what_we_need`, `faq_commission`; `ALLOWED_PLACEHOLDERS: frozenset[str]`; `BANNED_PHRASES: tuple[str, ...]`; `validate_blocks(blocks: dict[str, str]) -> dict[str, list[str]]` (block name to list of problems; empty dict means valid). Rules: every key present and a string; only allowed `{placeholders}`; no digit characters; no banned phrase; length limits; `intro`, `followup_1`, `followup_2` each contain `{business_name}`, `{postal_address}` and `{opt_out_line}`; `opt_out_line` non-blank.
- Produces on `ClinicOutreachService`: `async get_blocks(tenant_id) -> dict` returning `{status, version, approved_by, approved_at, blocks}`; `async save_blocks(tenant_id, user_id, blocks) -> dict` (raises `OutreachError` carrying the per-block problems in `args[1]` when invalid; on success stores them, raises `version` by 1, sets status `DRAFT`, clears approval); `async approve_blocks(tenant_id, user_id) -> dict` (re-validates, raises `OutreachError` if invalid or already `APPROVED`, sets `APPROVED`, `approved_by`, `approved_at`; writes audit `outreach.blocks_approved` with ids and version only); `async blocks_approved(tenant_id) -> bool`.
- Changes: `start_shadow` raises `OutreachError("The clinic email wording has not been approved yet; approve it first.")` unless blocks are approved, and raises with a message naming the field when sender company name or postal address is blank (they are required by `Sender` already, so this is defence in depth).

- [ ] **Step 1: Write failing tests**: `test_default_blocks_are_valid`, `test_a_digit_in_any_block_is_rejected`, `test_a_percentage_or_amount_or_day_count_is_rejected` (as digits and as words like "ten percent", "within five days"), `test_unknown_placeholder_is_rejected`, `test_each_banned_phrase_is_rejected` (parametrised), `test_composed_kinds_require_business_name_address_and_opt_out`, `test_length_limits`, `test_save_resets_to_draft_and_bumps_version`, `test_approve_records_who_and_when_and_audits_ids_only`, `test_editing_after_approval_reopens_draft`, `test_start_shadow_refuses_until_blocks_are_approved`, `test_blocks_are_tenant_isolated`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** `outreach_blocks.py` and the service methods, reading and writing through `TenantSettingsService` (add the key to its `DEFAULTS`; update its docstring list of keys).
- [ ] **Step 4: Run the new and existing outreach service tests; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): approved wording blocks`.

### Task 3: Composer and checker (pure)

**Files:**
- Create: `backend/app/services/outreach_composer.py`
- Test: `backend/tests/test_outreach_composer.py`

**Interfaces:**
- Consumes: `OutreachMessageKind` (Task 1), `DEFAULT_BLOCKS`/`ALLOWED_PLACEHOLDERS` (Task 2), `TermSheet`, `Sender` (Phase 1).
- Produces: `@dataclass(frozen=True) class ClinicRef: name: str` ; `compose(kind: OutreachMessageKind, clinic: ClinicRef, sender: Sender, term_sheet: TermSheet, blocks: dict[str, str]) -> tuple[str, str]` returning `(subject, body)` by substituting placeholders (`{sender_name}`=`sender.display_name`, `{sender_title}`=`sender.signature`, `{business_name}`=`sender.company_name`, `{postal_address}`=`sender.postal_address`, `{opt_out_line}`=`blocks["opt_out_line"]`, `{commission}` rendered as `"<n> percent of the treatment price"` for PERCENTAGE or `"a flat <n> <CCY> per qualifying patient"` for FLAT using `term_sheet.max_value` normalised without trailing zeros, `{payment_days}`=`str(term_sheet.payment_days)`); `check(subject: str, body: str, *, clinic: ClinicRef, sender: Sender, allowed_figures: frozenset[str] = frozenset()) -> list[str]` returning human-readable problems (empty means pass).
- `check` rules: mask `clinic.name`, `sender.display_name`, `sender.signature`, `sender.company_name`, `sender.postal_address` out of the text first, then reject: any unresolved `{token}`; any banned phrase; any `%`, the word "percent"; any currency symbol or three-letter currency code adjacent to a number; any digit-bearing figure, day/week/month count (digits or words one to thirty and "fifty", "sixty", "ninety") not in `allowed_figures`; any email address (regex) or phone number (seven or more digits with separators); a missing postal address, business name or opt-out line; subject longer than 120 or body longer than 1,500.

- [ ] **Step 1: Write failing tests**: `test_compose_fills_every_placeholder_and_is_deterministic`, `test_commission_rendering_for_percentage_and_flat`, `test_default_blocks_compose_and_pass_the_checker_for_all_three_kinds`, `test_address_digits_and_clinic_name_digits_do_not_false_block` (clinic named "Clinic 24", address "12 Main St 34000"), `test_a_stray_percentage_amount_or_day_count_blocks` (parametrised incl. "within five days", "10%"), `test_allowed_figures_pass`, `test_unresolved_placeholder_blocks`, `test_each_banned_phrase_blocks`, `test_stray_email_and_phone_block`, `test_missing_opt_out_or_address_blocks`, `test_length_limits_block`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** both functions as pure code with no I/O.
- [ ] **Step 4: Run the new tests; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): message composer and checker`.

### Task 4: Send-slot and warm-up arithmetic (pure)

**Files:**
- Create: `backend/app/services/outreach_schedule.py`
- Test: `backend/tests/test_outreach_schedule.py`

**Interfaces:**
- Consumes: `Limits` (Phase 1).
- Produces: `COUNTRY_TIMEZONES: dict[str, str]` (at least TR `Europe/Istanbul`, TH `Asia/Bangkok`, KR `Asia/Seoul`, CN `Asia/Shanghai`, and every other distinct `country` value found in the seeded provider data in `backend/app/data/vertical_extension_seed.py` and `mt_clinic_sourcing` with its correct IANA zone); `timezone_for(country: str | None) -> ZoneInfo | None` (None when unknown); `warmup_cap(limits: Limits, shadow_started_at: datetime, now: datetime) -> int`; `next_send_slot(earliest: datetime, country: str | None, limits: Limits, *, horizon_end: datetime) -> datetime | None` returning the first instant at or after `earliest` that lies inside `[send_window_start, send_window_end)` local time on an allowed weekday (Monday to Friday when `weekdays_only`), or `None` when the zone is unknown or that instant is at or after `horizon_end`; `FOLLOWUP_GAPS: dict[OutreachMessageKind, timedelta]` (`followup_1`: 4 days after intro, `followup_2`: 5 days after follow-up 1) and `NO_RESPONSE_AFTER = timedelta(days=7)`.

- [ ] **Step 1: Write failing tests**: `test_timezone_lookup_known_and_unknown`, `test_warmup_cap_day_zero_one_and_capped`, `test_slot_inside_window_is_kept`, `test_slot_before_window_moves_to_window_start`, `test_slot_after_window_moves_to_next_allowed_day`, `test_weekend_is_skipped_when_weekdays_only_and_kept_when_not`, `test_slot_uses_the_clinics_local_time_across_zones` (Istanbul vs Seoul same UTC instant), `test_a_dst_boundary_uses_local_wall_time` (use a zone with DST present in the table, e.g. a European one), `test_slot_beyond_horizon_is_none`, `test_unknown_country_is_none`, `test_every_seeded_provider_country_has_a_timezone` (reads the distinct countries from the seed data).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** with `zoneinfo` and plain datetime arithmetic; no I/O.
- [ ] **Step 4: Run the new tests; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): send windows and warm-up`.

### Task 5: Engine service

**Files:**
- Create: `backend/app/services/clinic_outreach_engine.py`
- Test: `backend/tests/test_clinic_outreach_engine.py`

**Interfaces:**
- Consumes: Tasks 1 to 4, `ClinicOutreachService.blocks_approved`/`get_blocks`/`is_suppressed`, `Campaign`/`CampaignStatus`, `ClinicOutreach`, `Provider` (country, name), `AuditLog` with the `INVITE_ACTION` constant from `app/tools/builtin/mt_referral_tools.py`, and the kill-switch check the tool registry uses (`app/tools/registry.py`, look near the `ai_kill_switch_active` branch; reuse its helper, do not copy its logic).
- Produces: `class ClinicOutreachEngine: __init__(self, session_factory)`; `async run(self, tenant_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, int]` returning counts `{"campaigns": n, "composed": n, "blocked": n, "skipped": n, "parked": n}`.
- Behaviour for each campaign with status `ACTIVE` in the tenant, in order: skip unless kill switch off, mode `SHADOW` (a `LIVE` campaign is skipped and counted), and wording `APPROVED`; take a row lock on the campaign (`with_for_update`) plus a per-campaign in-process `asyncio.Lock`; `horizon_end = now + 24h`; remaining cap = `warmup_cap` minus the number of stored messages with `scheduled_for` in `[now, horizon_end)`; iterate clinics whose stage is `READY` (intro; `next_action_at` null counts as due), `INTRO_SENT` (follow-up 1) or `FOLLOWUP_1` (follow-up 2) with `next_action_at <= horizon_end`, ordered by `next_action_at` then id; before each clinic re-read campaign status and kill switch and stop if either changed. Per clinic, in its own transaction, apply in order: suppression (park to `NEEDS_PERSON`, reason "address is on the suppression list"); unknown country (park, "clinic country has no known time zone"); Onboarder invitation audit row for this provider within 14 days (skip: set `next_action_at` to that invitation time plus 14 days and count it as `skipped`; the clinic then shows that later date in the shadow-progress `next_sends` list); outbound draft count at or above `limits.max_emails_per_clinic` (stop, set `NO_RESPONSE`); slot via `next_send_slot` (none: leave for a later run); cap exhausted (stop the loop); `compose` then `check` (problems: store a `BLOCKED` message with `blocked_reason`, park to `NEEDS_PERSON`); otherwise store a `QUEUED_SHADOW` message with the slot as `scheduled_for`, `blocks_version`, advance the stage (`READY` to `INTRO_SENT`, `INTRO_SENT` to `FOLLOWUP_1`, `FOLLOWUP_1` to `FOLLOWUP_2`), set `next_action_at` to the slot plus the gap (`FOLLOWUP_2` uses `NO_RESPONSE_AFTER`), and **do not change `emails_sent` or `last_sent_at`**. A `FOLLOWUP_2` clinic whose `next_action_at` has arrived becomes `NO_RESPONSE` with no message. Insert uses the unique key; a unique violation is treated as already done. One clinic raising any other exception is logged by id only and counted `skipped`; three consecutive runs failing for the same clinic park it with reason "the outreach engine could not process this clinic". After the loop set `engine_last_run_at` and write one audit row `outreach.engine_run` with the counts only.

- [ ] **Step 1: Write failing tests** (real service rows through the session factory, fixed `now`, no network): `test_a_ready_clinic_gets_one_intro_draft_and_advances`, `test_the_whole_sequence_over_simulated_days_ends_in_no_response` (intro, follow-up 1, follow-up 2, then `NO_RESPONSE`, never more than three drafts), `test_emails_sent_and_last_sent_at_are_never_changed`, `test_running_twice_makes_one_draft_per_clinic_and_kind`, `test_concurrent_runs_make_one_draft_per_clinic_and_kind` (gather), `test_the_daily_cap_and_warmup_limit_drafts`, `test_paused_stopped_or_unapproved_campaigns_make_nothing`, `test_a_status_change_during_the_run_stops_it` (patch status between clinics), `test_kill_switch_makes_nothing`, `test_a_live_campaign_is_refused`, `test_suppressed_address_parks_with_a_reason`, `test_unknown_country_parks_with_a_reason`, `test_an_onboarder_invitation_in_the_last_14_days_skips_and_defers`, `test_checker_failure_stores_a_blocked_message_and_parks`, `test_a_failing_clinic_does_not_stop_the_others`, `test_audit_run_row_has_counts_and_no_personal_data`, `test_tenant_isolation`, `test_no_send_path_is_called` (patch `get_clinic_channel` and every send function listed in `tests/test_clinic_outreach_phase1_end_to_end.py`, each asserted to exist, and assert zero calls).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** `ClinicOutreachEngine` exactly as above.
- [ ] **Step 4: Run the new tests and the Phase 1 outreach tests; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): shadow engine`.

### Task 6: Engine tool, Outreach Runner, Onboarder rule

**Files:**
- Create: `backend/app/tools/builtin/outreach_tools.py`
- Modify: `backend/app/tools/factory.py` (register `RunOutreachEngine`), `backend/app/tools/policy.py` (`"outreach.run_engine": ActionPolicy.AUTO` with a comment that it stores drafts only), `backend/app/services/operating_crew.py` (new `CrewMember` in the medical-tourism crew), `backend/app/tools/builtin/mt_referral_tools.py` (Onboarder rule)
- Test: `backend/tests/test_outreach_tools.py`, additions to `backend/tests/test_operating_crew.py` and the existing `invite_clinic` tests

**Interfaces:**
- Consumes: `ClinicOutreachEngine` (Task 5).
- Produces: tool `outreach.run_engine` (input model empty, output `{campaigns, composed, blocked, skipped, parked}`), required permission the campaign-management permission, executes `ClinicOutreachEngine(async_session_maker).run(context.tenant_id)`.
- Produces: crew member `key="outreach-runner"`, name "Outreach Runner", `tools=("outreach.run_engine",)`, `schedule_time="06:00"`, `schedule_action=("outreach.run_engine", {})`, purpose and goal text saying it prepares clinic outreach drafts for review and sends nothing, `max_tool_chain_depth=2`. It joins the medical-tourism playbook only. Scheduling stays **daily** (the scheduler supports only DAILY/WEEKLY); the hourly extension is deferred to Phase 4 where live timing needs it.
- Changes `InviteClinic.execute`: before sending, if the clinic has a `ClinicOutreach` row in a campaign with `ClinicOutreachCampaign.mode == LIVE` and campaign status ACTIVE or PAUSED, or any `ClinicOutreachMessage` with status `SENT`, return `InviteClinicOutput(invited=False, reason="this clinic is in an active outreach campaign")`. Shadow mode does not affect it.

- [ ] **Step 1: Write failing tests**: `test_the_tool_runs_the_engine_for_the_calling_tenant`, `test_the_tool_is_blocked_by_the_kill_switch_through_the_registry`, `test_the_tool_has_an_auto_policy_and_the_right_permission`, `test_outreach_runner_is_in_the_mt_crew_with_a_direct_action_and_no_other_tools`, existing crew tests updated for the new member (counts derive from `crew_for`; fix any hard-coded count), `test_invite_clinic_refuses_a_clinic_in_a_live_campaign`, `test_invite_clinic_is_unaffected_in_shadow_mode`, `test_invite_clinic_refuses_a_clinic_with_a_sent_outreach_message`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the new tests, `tests/test_operating_crew.py`, the invite_clinic tests, and any test that enumerates tools or policies (grep for `policy` coverage tests); all pass.**
- [ ] **Step 5: Commit** `feat(outreach): engine tool, Outreach Runner and Onboarder rule`.

### Task 7: API additions

**Files:**
- Modify: `backend/app/api/v1/outreach.py`, `backend/app/services/clinic_outreach_service.py` (messages, preview, review, progress methods)
- Test: `backend/tests/test_outreach_api_phase2.py`

**Interfaces:**
- Consumes: Tasks 1 to 5, `compose`/`check`.
- Produces service methods: `async list_messages(tenant_id, campaign_id, *, stage=None, status=None, review=None, limit=50, offset=0) -> dict` (`{items: [...], total}` each item: id, provider_id, clinic name, kind, status, subject, scheduled_for, review_mark, review_reason, blocked_reason; body only via `get_message`); `async get_message(tenant_id, campaign_id, message_id) -> dict` (adds `body`); `async review_message(tenant_id, user_id, campaign_id, message_id, mark: str | None, reason: str | None) -> None` (mark must be `GOOD`, `FLAGGED` or `None`; a flag requires a reason; sets reviewer and time; audit `outreach.message_reviewed` ids and mark only); `async preview_sequence(tenant_id, campaign_id, provider_id) -> list[dict]` (three `{kind, subject, body, problems}` rendered through `compose` and `check` with the approved blocks, not stored, nothing advanced; refuses with `OutreachError` if blocks are not approved); `async shadow_progress(tenant_id, campaign_id) -> dict` (`days_in_shadow`, `shadow_messages`, `reviewed`, `flagged`, `flagged_share`, `blocked`, `engine_last_run_at`, `blocks_status`, `needs_you` list of `{provider_id, clinic, reason}` for `NEEDS_PERSON` and `NO_EMAIL` clinics, `next_sends` list of up to 10 `{provider_id, clinic, kind, next_action_at}`; `try_a_reply_tests` fixed at 0 with `try_a_reply_available: false`).
- Produces routes under `/api/v1/outreach`: `GET /blocks`; `PUT /blocks` (body `{blocks: {name: text}}`, 422 with `{block: [problems]}` detail when invalid); `POST /blocks/approve`; `GET /campaigns/{id}/messages` (query `stage`, `status`, `review`, `limit` 1 to 200, `offset`); `GET /campaigns/{id}/messages/{message_id}`; `PUT /campaigns/{id}/messages/{message_id}/review` (body `{mark: "GOOD"|"FLAGGED"|null, reason?: str}`); `GET /campaigns/{id}/clinics/{provider_id}/preview-sequence`; `GET /campaigns/{id}/shadow-progress`. `OutreachError` stays 409 and `OutreachNotFound` 404; `UUID` path types give 422 on bad ids.

- [ ] **Step 1: Write failing tests** with the `client` fixture and owner token as in `tests/test_outreach_api.py`: `test_blocks_default_save_approve_round_trip`, `test_invalid_blocks_are_422_with_per_block_reasons`, `test_blocks_mutations_need_manage_permission_and_reads_need_read`, `test_messages_list_filters_and_pages`, `test_message_detail_includes_the_body_only_there`, `test_review_good_and_flagged_and_clear`, `test_a_flag_without_a_reason_is_422`, `test_preview_sequence_returns_three_messages_without_storing`, `test_preview_refuses_when_blocks_are_not_approved`, `test_shadow_progress_counts_and_needs_you`, `test_another_tenant_gets_404_everywhere`, `test_unauthenticated_requests_are_401`, `test_bad_uuid_is_422`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the service methods and routes in the existing style.
- [ ] **Step 4: Run the new tests and `tests/test_outreach_api.py`; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): wording and shadow review API`.

### Task 8: Frontend wording page and Shadow review

**Files:**
- Modify: `frontend/lib/api.ts` (types and one function per Task 7 route), `frontend/components/marketing/ClinicOutreachPanel.tsx` (tabs: Overview, Shadow review; "Approve the wording to begin" notice linking to the wording page; Start disabled with that notice when blocks are not approved), `frontend/app/marketing/campaigns/[id]/page.tsx` only if needed
- Create: `frontend/components/marketing/OutreachWording.tsx`, `frontend/components/marketing/ShadowReview.tsx`, `frontend/app/marketing/outreach-wording/page.tsx`
- Test: `frontend/components/marketing/__tests__/OutreachPhase2.test.tsx`

**Interfaces:**
- Consumes: Task 7 routes.
- Produces: `OutreachWording({ token })` with a text area per block, the allowed placeholders listed beside each, per-block validation errors from the 422, a live preview filled with a sample clinic ("Example Clinic") and a sample sender, a banner "Draft" or "Approved by … on …", **Save** and **Approve wording** (confirmation required), and the text "Drafts written by the assistant. Nothing is used until you approve." `ShadowReview({ token, campaignId })` with the progress counters, filters (All, Unreviewed, Flagged, Blocked), a message list, a drawer showing the full body with **Good** and **Flag** (flag asks for a reason) and **Clear mark**, **Preview full sequence** per clinic, the "Needs you" reasons, and the line "Nothing is sent in this release." The panel's existing texts and button labels from Phase 1 stay.

- [ ] **Step 1: Write failing tests:** `test_wording_page_shows_drafts_and_the_placeholder_help`, `test_wording_save_shows_per_block_errors_from_the_api`, `test_approve_needs_confirmation_and_shows_the_approved_banner`, `test_shadow_review_lists_messages_and_filters_them`, `test_marking_good_and_flagging_with_a_reason_call_the_api`, `test_flag_without_a_reason_is_blocked_in_the_form`, `test_preview_full_sequence_shows_three_messages`, `test_panel_shows_the_approve_wording_notice_and_disables_start_until_approved`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** in the existing style (`Button`, `Badge`, `EmptyState`); API errors shown as plain text.
- [ ] **Step 4: Run the new test file, `npx tsc --noEmit -p .`, and the full `npx vitest run`; all pass.**
- [ ] **Step 5: Commit** `feat(outreach): wording page and shadow review`.

### Task 9: Whole-phase verification

**Files:**
- Test: `backend/tests/test_clinic_outreach_phase2_end_to_end.py`

- [ ] **Step 1: Write the end-to-end test** through the real app with a controllable clock and a fake search provider: create a campaign with three clinics (one with a findable address, two with hand-entered addresses, one of those in a country with a different time zone), approve the default wording through the API, start shadow, run `outreach.run_engine` through the tool registry day by day for 12 simulated days, and assert: drafts and pacing match the rules (intro within each clinic's local window, follow-up 1 four days later, follow-up 2 five days after that, then `NO_RESPONSE`), the daily cap and warm-up limited the first days, the review screen data (list, detail, marks, progress) is correct, a suppressed fourth clinic is parked with its reason, the audit trail has `outreach.blocks_approved`, `outreach.engine_run` and `outreach.message_reviewed` rows with no `@` in summaries, **zero send calls** (patch every send path, each asserted to exist), `emails_sent` is 0 everywhere, a second tenant sees nothing, and a paused campaign makes no drafts on the next run.
- [ ] **Step 2: Run it and the whole backend suite** (`pytest tests -q`, about 20 minutes) and the whole frontend suite; all pass with no new failures.
- [ ] **Step 3: Commit** `test(outreach): phase 2 end to end`.

## Out of scope for this plan

Sending, hourly scheduling, the live send path, reply handling and "Try a reply", the funnel numbers and notifications, Go live and ramping, model-written text, translation, holiday calendars: Phases 3 and 4.
