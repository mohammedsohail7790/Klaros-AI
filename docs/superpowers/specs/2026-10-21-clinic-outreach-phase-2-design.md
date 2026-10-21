# Clinic outreach campaign, phase 2: engine, composer and shadow review

Date: 2026-10-21. Status: draft for owner review. Nothing in this document is built.

Parent design: `2026-10-20-clinic-outreach-campaign-design.md` (sections 5, 6, 8, 9, 10, 11 and the phase list). Phase 1 (data model, term sheet, contact finder, dashboard wizard and panel, Start in shadow mode) is merged. This document says exactly what phase 2 adds and where it differs from the parent.

## 1. Purpose and what stays true

Phase 2 builds the part that decides what to say and when: the engine, the message composer, the checker, and the screen where the owner reads the shadow week. **It still sends nothing.** A shadow run stores each message as a draft the owner can read; the real send path is wired in phase 4 behind Go live, and in this phase it is never called. Go live stays disabled.

Decisions made with the owner:

- The engine is a **fixed-rule scheduled action**, not an AI agent. No model decides anything in phase 2.
- **One code path for shadow and live.** Only the last step differs (store or send). Shadow is therefore an honest rehearsal.
- Messages are **plain templates**. A model adapting wording belongs with reply handling in phase 3.
- **The assistant drafts the wording blocks, the owner edits and approves them** in the dashboard. The shadow week cannot start until they are approved. English only at launch.

## 2. Non-goals

Sending anything to a clinic. Reading, classifying or answering replies and the "Try a reply" test (phase 3). Go live, ramping, bounce-rate stops and the funnel notifications (phase 4). Model-written text. Translation. Public-holiday calendars (the parent says "when known"; no calendar exists, so phase 2 skips holidays and says so on the screen). Agreement generation.

## 3. What exists today and is reused

- Phase 1: `Campaign`, `ClinicOutreachCampaign` (mode SHADOW or LIVE, term sheet, sender, limits), `ClinicOutreach` (stage, contact, `next_action_at`, `emails_sent`), `OutreachSuppression`, `ClinicOutreachService` and the `/api/v1/outreach` routes, the wizard and panel.
- The crew framework: a crew member can carry a **direct scheduled action** (`schedule_action`, a tool called exactly as given, no model). Today these run daily; section 6 covers the hourly need.
- `get_clinic_channel()` for sending to a clinic's own address, used by `medical_tourism.invite_clinic`. Phase 2 never calls it.
- `tenant_settings` (platform default per key, tenant row overrides), the audit log, the AI kill switch, tool registry and approvals.
- `medical_tourism.invite_clinic` (the Clinic Onboarder invitation) with its rule of at most one invitation per clinic every 14 days, recorded in the audit log.

## 4. Data model changes

New table `clinic_outreach_messages` (tenant-scoped, row-level security, same policy pattern as the phase 1 tables, new migration chained after the phase 1 migration):

| Column | Meaning |
|---|---|
| `id`, `tenant_id`, `outreach_id` | key, tenant, the clinic's outreach row |
| `direction` | `OUT` (phase 2 writes only this; `IN` is phase 3) |
| `kind` | `intro`, `followup_1`, `followup_2` (phase 3 adds reply kinds) |
| `status` | `QUEUED_SHADOW`, `BLOCKED` (phase 4 adds `SENT`, `FAILED`) |
| `subject`, `body` | rendered text exactly as it would be sent |
| `blocks_version` | version of the approved wording used |
| `blocked_reason` | why the checker blocked it |
| `scheduled_for` | the simulated send time |
| `review_mark`, `review_reason`, `reviewed_by`, `reviewed_at` | owner's mark (`GOOD` or `FLAGGED`) and reason |
| `created_at` | |

Unique on (`outreach_id`, `kind`, date of `scheduled_for`) so a repeated or overlapping run can never create a duplicate. Indexes on (`outreach_id`) and (`tenant_id`, `status`, `scheduled_for`).

`ClinicOutreachCampaign` gains `engine_last_run_at` (for the screen) and nothing else. Mode, limits and term sheet already exist.

Wording blocks live in `tenant_settings` under a new key `outreach_blocks`, platform default `{"status": "DRAFT", "version": 0, "blocks": <the draft blocks in appendix A>}`. A tenant row holds `status` (`DRAFT` or `APPROVED`), `version`, `approved_by`, `approved_at` and `blocks` (a mapping of block name to text). **Editing any block returns the status to `DRAFT` and raises `version`**; approval records who and when.

## 5. The wording blocks

Blocks: `intro`, `followup_1`, `followup_2`, `how_referrals_work`, `faq_what_we_do`, `faq_what_we_need`, `faq_commission`, `opt_out_line`, plus a `subject` per kind. Only `intro`, `followup_1`, `followup_2` and `opt_out_line` are composed in phase 2; the other blocks are stored and approved now so phase 3 needs no new approval step.

Rules enforced when saving and again when approving a block set:

- Allowed placeholders only: `{clinic_name}`, `{business_name}`, `{sender_name}`, `{sender_title}`, `{postal_address}`, `{opt_out_line}`, `{commission}`, `{payment_days}`. Any other brace token is rejected.
- A block may not contain a percentage, a currency amount, a number of days or a calendar date written out. Figures appear only through `{commission}` and `{payment_days}`, which are filled from the term sheet.
- Every composed kind must include `{business_name}`, `{postal_address}` and `{opt_out_line}`; `opt_out_line` must be non-empty.
- Length limits: subject 120 characters, body 1,500 characters.
- Banned claims (case-insensitive phrases, a short list kept in code): "partner of", "official partner", "endorsed", "guarantee", "guaranteed", "exclusive", "we send you patients", "patients per month". A block containing one is rejected.

## 6. The engine

**Run.** A new tool `outreach.run_engine` (policy: AUTO, no approval, because in phase 2 it can only store drafts) is called hourly by a new crew member **Outreach Runner** through `schedule_action`. The crew's scheduler today supports daily actions only; phase 2 extends `schedule_action` with an optional interval in hours (default unchanged, so existing members behave as before) and gives Outreach Runner an interval of 1. The plan pins the exact change after reading the scheduler. If the extension proves invasive, the fallback is a single daily run that processes everything due within the next 24 hours; the rules below do not change.

**Per run, per tenant**, for each campaign with status ACTIVE:

1. Skip the whole campaign unless: the kill switch is off, mode is SHADOW (a LIVE campaign cannot exist before phase 4, and the engine refuses LIVE in this phase), and the wording blocks are `APPROVED`. If blocks are not approved the run records nothing and the screen shows "Approve the wording to begin".
2. Take a row lock on the campaign for the run so two overlapping runs cannot both process it.
3. For each clinic whose `next_action_at` is due, in `next_action_at` order, apply the **rules** below. Each clinic is processed in its own transaction; one clinic's failure is logged by id only and does not stop the others.
4. Write one `outreach.engine_run` audit row per campaign per run with counts only (composed, blocked, skipped by reason).

**Rules** (all checked every time, in this order):

- **Suppression.** If the address or its domain is on the suppression list, no message is made and the clinic moves to `NEEDS_PERSON` with the reason "address is on the suppression list". (Setting `OPTED_OUT` from a reply is phase 3.)
- **Stage and count.** Only `READY` (intro), `INTRO_SENT` (follow-up 1) and `FOLLOWUP_1` (follow-up 2) clinics are eligible. A clinic never gets more than 3 outbound messages in the campaign (`emails_sent` plus stored shadow messages).
- **Spacing.** Follow-up 1 is due 4 days after the intro's scheduled time, follow-up 2 five days after follow-up 1, `NO_RESPONSE` seven days after follow-up 2. In shadow these intervals use the simulated send times, not real replies (there are none).
- **Send window.** The simulated send time must fall inside the campaign's window (default weekdays 09:00 to 17:00) in the clinic's local time. Local time comes from a country-to-time-zone table in code (Turkey, Thailand, South Korea, China, plus the other countries the clinic directory contains); an unknown country is not processed and the clinic is moved to `NEEDS_PERSON` with a reason. No holiday calendar in phase 2.
- **Daily cap with warm-up.** The cap per day is the campaign's cap, but never more than the warm-up value for that campaign day (limits already store the ramp start of 5). Days are counted from `shadow_started_at`, so the shadow week reproduces the pacing a live run would have.
- **Onboarder coordination.** If an Onboarder invitation for this clinic exists in the last 14 days (audit action used by `invite_clinic`) the clinic is skipped for now and retried after the 14 days; the reason is shown on the clinic row.
- **Checker.** The composed message passes the checker (section 8) or is stored as `BLOCKED` and the clinic moves to `NEEDS_PERSON`.

A clinic that passes all rules gets a `QUEUED_SHADOW` message, `emails_sent` is **not** incremented for shadow messages (it counts real sends only), the stage advances as it would live (`READY` to `INTRO_SENT`, and so on), and `next_action_at` is set from the spacing rules. After follow-up 2 and the final seven days the stage becomes `NO_RESPONSE` and nothing more is made.

**Shadow week arithmetic.** With real spacing, a 7-day shadow week shows intros and follow-up 1 for every clinic but only some follow-up 2. To let the owner review the whole sequence, the clinic detail has **Preview full sequence**, which renders all three messages on demand through the same composer and checker, without storing them or advancing anything.

**Pause, stop, kill switch.** The run checks campaign status and the kill switch when it starts and again before each clinic. Nothing is composed in a paused, stopped or archived campaign.

## 7. Composer

A pure function `compose(kind, clinic, sender, term_sheet, blocks) -> (subject, body)`: fills the placeholders of the approved block, appends nothing else, no model call. `{commission}` renders from the term sheet (for example "10 percent of the treatment price" or "a flat 200 USD per qualifying patient"). The intro and both follow-ups state no commission at all. `{commission}` and `{payment_days}` exist now only because `faq_commission` and the phase 3 proposal use them. The composer is deterministic and unit-tested for every placeholder.

## 8. Checker

`check(message, term_sheet, sender, clinic) -> list[reason]`, run on every composed message and again on every block set at approval. A non-empty result blocks the message. It checks:

- No unresolved placeholder and no banned claim phrase.
- No percentage, currency amount, "within N days" or written-out date in the rendered text other than those the term sheet produces (the term sheet figures are computed, not parsed from the blocks). Digits inside `{postal_address}`, the sender block and the clinic name are ignored by construction: the scan runs on the text with those fields masked.
- `{business_name}`, a postal address and the opt-out line are present.
- Subject and body length limits.
- No text that looks like patient or lead data: any email address other than the clinic's own and the reply address, and any phone number, is rejected.

## 9. Onboarder coordination (both directions)

- **Engine side:** as in section 6, skip a clinic invited by the Onboarder in the last 14 days.
- **Onboarder side:** `medical_tourism.invite_clinic` returns `invited: false` with reason "this clinic is in an active outreach campaign" when the clinic has an outreach row in a campaign that is `LIVE` and ACTIVE or PAUSED, or has any stored `SENT` outreach message. In shadow mode the Onboarder is unaffected, so the existing flow keeps working during the shadow week; the engine's send-time check keeps the 14-day rule once live. This change is small and covered by a test in both directions.

## 10. API additions (all under `/api/v1/outreach`)

- `GET /blocks`, `PUT /blocks` (saves edits, status returns to `DRAFT`, validation errors are 422 with the reason per block), `POST /blocks/approve` (runs the full block-set validation, then sets `APPROVED`, version, who, when). Mutations need the campaign-management permission; reads need READ_MARKETING.
- `GET /campaigns/{id}/messages?stage=&status=&review=` (paged), `GET /campaigns/{id}/clinics/{provider_id}/preview-sequence`, `PUT /campaigns/{id}/messages/{message_id}/review` (`{mark: GOOD|FLAGGED|null, reason?}`).
- `GET /campaigns/{id}/shadow-progress`: days in shadow, shadow messages, reviewed count, flagged share, blocked count, next scheduled sends, engine last run. It reports phase 4's go-live checks as progress only; "Try a reply" tests show "available in a later release".
- `start_shadow` additionally requires `APPROVED` blocks and complete sender details (company name, postal address, signature); otherwise 409 with a message saying which. Already-ACTIVE phase 1 campaigns are not interrupted: the engine simply idles until blocks are approved.

## 11. Dashboard

- **Wording page** (Marketing > Clinic outreach wording): each block with its text, the allowed placeholders listed beside it, a live preview using a sample clinic and the campaign's sender, per-block validation errors, an **Approve wording** button with a confirmation, and a banner showing DRAFT or APPROVED with who and when. The assistant's draft blocks are shown pre-filled and clearly marked as drafts until approved.
- **Campaign panel:** a **Shadow review** tab with the message list (clinic, kind, simulated send time, subject, status), a message drawer showing the full text, **Good** and **Flag** buttons with a reason field, filters (unreviewed, flagged, blocked), the shadow progress counters, a **Preview full sequence** link on each clinic, and an "Approve the wording to begin" notice when blocks are not approved. The existing "Nothing is sent in this release." line stays.
- Needs you gains the reasons from section 6 (suppressed, unknown country, blocked by the checker).

## 12. Failure handling

A composer or checker error blocks that clinic's message and never aborts the run. An unexpected error for one clinic is logged by id and retried on the next run; three consecutive failures move the clinic to `NEEDS_PERSON`. The duplicate-prevention unique key means a retried run cannot double-store. A scheduler outage only delays drafts; nothing depends on exact timing in shadow.

## 13. Testing

- State machine and timing: each transition, spacing, the three-message limit, `NO_RESPONSE`, local-time window in each target country, warm-up from `shadow_started_at`, a daylight-saving boundary where one applies.
- Rules: suppressed address goes to `NEEDS_PERSON`, unknown country goes to `NEEDS_PERSON`, an Onboarder invitation in the last 14 days skips, paused or kill-switched campaigns make nothing, unapproved blocks make nothing, a LIVE campaign is refused.
- Idempotency: running the engine twice and concurrently produces one message per clinic and kind.
- Checker: each banned phrase, a stray percentage, currency amount, day count and date, a missing opt-out line, an unresolved placeholder, a stray email address and phone number, masking of address digits (must not false-positive), a block set that approves cleanly.
- Composer: every placeholder; deterministic output.
- **No send:** the e2e test patches every send path used in phase 1's end-to-end test, plus `get_clinic_channel`, and asserts zero calls across a full simulated shadow run.
- Tenant isolation and row-level security on real PostgreSQL for the new table and the blocks setting.
- API: permissions, 422 and 409 cases, review marks, paging, cross-tenant 404.
- Frontend: the wording page validation and approval, the shadow review tab (mark, filter, preview), the not-approved notice.
- End to end through the real app with a fake clock: approve blocks, start shadow, run the engine across simulated days, assert drafts, pacing, review marks and the audit trail.

## 14. Inputs needed from the owner

Review and edit the draft wording (appendix A); company name, postal address, sender name and signature (still needed from phase 1); the daily cap and which countries to start with; confirmation that the Onboarder should remain unaffected in shadow mode (section 9). Legal review of the wording is recommended before phase 4 and is not a gate in phase 2 because nothing is sent.

## 15. Risks and open questions

- **Scheduler granularity:** if the crew scheduler cannot take an hourly interval cheaply, the daily fallback in section 6 applies. Low risk.
- **False blocks by the checker** could park good clinics in Needs you. Mitigation: the masking rule and tests, and the shadow week itself is where this shows up.
- **Local-time table coverage:** a clinic in a country not in the table is parked, not guessed. The table is data and is extended by a small change.
- **Shadow does not test deliverability or real replies.** That remains true until phase 4's small first batch.
- **Open:** whether Onboarder invitations should also be blocked during shadow mode. Recommended: no, as in section 9.

## Appendix A: draft wording blocks (for the owner to edit and approve)

These are drafts. They claim nothing that does not exist, mention no patient, state no number, and name the sender and the reason for writing. Replace the bracketed items with the campaign sender's details through the placeholders.

**subject (intro):** `Introduction from {business_name}`

**intro:**
> Dear {clinic_name} team,
>
> I am {sender_name}, {sender_title} at {business_name}. We help international patients compare clinics abroad and arrange their travel, and we are building a list of clinics we can recommend. I am writing to introduce ourselves and to ask who in your team handles international patient enquiries.
>
> We are not asking you to commit to anything. If it is useful, we can explain how our referral arrangement works and what we would need from your clinic, such as licence and accreditation details, before we could consider listing you.
>
> {sender_name}
> {business_name}
> {postal_address}
>
> {opt_out_line}

**subject (follow-up 1):** `Following up: {business_name}`

**followup_1:**
> Dear {clinic_name} team,
>
> I wrote a few days ago to introduce {business_name}. I would be glad to know who handles international patient enquiries at your clinic, and I can send a short description of how our referral arrangement works if that helps.
>
> {sender_name}
> {business_name}
> {postal_address}
>
> {opt_out_line}

**subject (follow-up 2):** `A last note from {business_name}`

**followup_2:**
> Dear {clinic_name} team,
>
> This is my last message on this topic. If international patient enquiries are something your clinic would like to discuss, please reply and I will send details. If not, no action is needed and we will not write again.
>
> {sender_name}
> {business_name}
> {postal_address}
>
> {opt_out_line}

**opt_out_line:** `If you would rather not hear from us, reply "stop" and we will not contact you again.`

**how_referrals_work** (stored now, used in phase 3):
> We introduce patients who are looking for treatment abroad to clinics that we have checked. If a patient we introduced completes a qualifying treatment at your clinic, the clinic pays {business_name} a referral fee on the terms we agree in writing beforehand. We do not agree anything until your clinic's licence and accreditation have been checked by our team.

**faq_what_we_do:**
> {business_name} helps people compare clinics abroad and arranges their travel and aftercare. We do not provide medical care ourselves.

**faq_what_we_need:**
> To consider listing your clinic we need your current medical licence, any international accreditation, the name of a contact for international patients, and a list of the treatments you offer to patients from abroad.

**faq_commission:**
> Referral fees are agreed in writing and per clinic. Our standard arrangement is {commission}, payable within {payment_days} days of a qualifying treatment. We will confirm the final terms with you before anything is agreed.
