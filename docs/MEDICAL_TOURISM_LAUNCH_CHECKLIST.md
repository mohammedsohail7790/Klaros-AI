# Medical Tourism pilot — consolidated launch checklist

**Status: NOT LIVE.** Nothing below has been merged, deployed, migrated or configured. V = verified (ran it / read it / looked at the live system); A = assumption to confirm before acting.

## 0. State
| Item | Value | |
|---|---|---|
| Klaros feature branch `feat/mt-halla-contract-hardening` | `adb8ee7a0c57d04fefd18ebc55d81c17875afdb3` on the remote (later local commits are unpushed until approved) | V |
| Klaros release branch `release/pilot-prod` (deployed to `klaros-halla-pilot`, Auto-Deploy off) | `8c18afe5c10b3ced81478f9c5c484bb83dda8b44` | V |
| Halla `feat/medical-tourism-consent-evidence` | `49bdbc74baff2db2ec6faaaaf0a226585b3a09de` (on `10bce7ada2bf2dbfbfd88a04eddb8755df21d963`, on `3df71b127ba5778214283104ad0b75c6847af26e`) | V |
| Production gateway | `gateway.hallaai.com` → CNAME `middle-east-05u0.onrender.com` (Render service `middle-east`), `/health` 200; Halla `main` config, CI and docs all name this host | V |
| `middle-east` is **not** in the Render workspace the pilot lives in (that workspace has only `klaros-halla-pilot`, `halla-ai-gateway`, `klaros-staging`, `klaros-staging-db`, `halla-ai-gateway-staging`) | its commit, branch and Auto-Deploy setting are unknown | V |
| `halla-ai-gateway` (blueprint, `onrender.com`) | a *different* service, Auto-Deploy on, `main@64c2821` — not the production host | V |

## 1. Prioritised checklist
**P0 — nothing else can start without these (blockers)**
| # | Action | Who |
|---|---|---|
| 1 | Name the genuine business owner (legal name, verified email) and have them create their own Halla account/tenant — a separate tenant, never Call IQ or Halla AI | **Business owner** |
| 2 | Give access to, or perform on your behalf, deploys on the Render account that owns `middle-east`; confirm its deployed commit, tracked branch and Auto-Deploy | **Render owner of `middle-east`** |
| 3 | Give access to, or perform, applying Halla migrations `075` then `076` on the production Supabase database (backup first, §4) | **Supabase owner** |
| 4 | Decide: approve merging `feat/mt-halla-contract-hardening` → `release/pilot-prod` and a manual deploy of `klaros-halla-pilot`; approve pushing the unpushed local commits | **You** |
| 5 | Approved consent wording (per scope: contact / store personal data / store medical information) and its label (`wording_version`); counsel sign-off is the owner's responsibility — this repo makes no legal claim | **Business owner + counsel** |

**P1 — configuration (secrets are entered only by you / the owner, never by me)**
| # | Action | Who |
|---|---|---|
| 6 | Halla tenant id and the Klaros tenant id; confirm `c14d42d1-4c63-46c1-bdc3-89d4dc2b7b7b` is unused in the pilot DB (A: I cannot read the DB) | Business owner / You |
| 7 | Dedicated least-privilege Halla API key with an expiry (scopes: only health + workforce read/write as the contract needs) and Halla's API-key header name | Business owner creates; **You** enter into Render |
| 8 | A unique webhook signing secret; register the webhook at `https://klaros-halla-pilot.onrender.com/api/v1/webhooks/halla/{klaros_tenant_id}` for the 9 supported events | Business owner / You |
| 9 | Pilot service environment (names only): `DATABASE_URL`, `HALLA_API_BASE_URL=https://gateway.hallaai.com`, `HALLA_ALLOWED_HOSTS=gateway.hallaai.com`, `HALLA_API_KEY_HEADER`, `KLAROS_PUBLIC_API_URL`, `HALLA_PILOT_TENANTS` (one Medical Tourism entry, `safety_profile: "medical_tourism"`, optional `approved_wording_versions`), the key/secret variables it names. **No Dropshipping entry** | **You** (Render) |
| 10 | Halla tenant: set `voice_tenants.metadata.consent_capture.wording_version` (this is what turns `record_consent` on) | Business owner / Halla operator |
| 11 | Verified human escalation destination, hours, response time | Business owner |
| 12 | Approved clinic/provider facts (services, prices, accreditation claims) — nothing invented | Business owner |

**P2 — business/legal decisions (fail-closed defaults apply until decided)**
| # | Decision | Default until decided |
|---|---|---|
| 13 | Retention periods (lead, consent history, event text) and whether erasure becomes automatic | nothing is erased automatically; erasure is operator-initiated; history kept |
| 14 | What happens to customers/invoices/quotes/jobs referencing an erased lead (financial records have their own rules) | customer kept if referenced, reported |
| 15 | Keep the public web form closed, or approve wording labels for it | **closed** |
| 16 | Are transactional messages (appointment confirmations, invoices) exempt from `contact` consent? | **no exemption: every patient-facing message needs `contact`** |
| 17 | Operator-authored free text on appointments/jobs/quotes/contracts (not intake) | not gated (§3 residual) |

**P3 — execution once P0/P1 are done:** deploy (§4) → synthetic end-to-end test (§6) → only then may the integration be called verified.

## 2. Who must do what
| Task | Render owner (`middle-east`) | Render owner (pilot) | Supabase owner | Business owner | You |
|---|---|---|---|---|---|
| Deploy Halla consent code to `middle-east` | ✔ | | | | approve |
| Apply Halla `075`/`076` | | | ✔ (+ backup) | | approve |
| Merge + deploy Klaros pilot | | ✔ (you: in your workspace) | | | **approve + click Manual Deploy / or authorise me** |
| Create Halla tenant, API key, webhook secret | | | | ✔ | enter secrets |
| Set pilot env vars | | ✔ | | | ✔ |
| Consent wording, retention, escalation, clinic facts | | | | ✔ | decide with them |
| Synthetic E2E execution | | | | provides synthetic number/inbox | ✔ (or me with a short-lived authorised session) |

## 3. Consent-gate coverage (V: tests in `tests/test_consent_gate_intake.py` and `tests/test_consent_gate_channels.py`; each was mutation-checked)
A tenant is gated when its Halla connection has the Medical Tourism profile **or** the `medical_tourism` vertical is enabled; all other tenants are unchanged (tested).
| Path | Gated tenant behaviour |
|---|---|
| Halla `lead.created`/`lead.updated` | stored only with `store_personal_data`; text with `store_medical_information`; newest evidence governs existing leads (freeze on withdrawal) |
| `POST /leads`, agent tool `crm.create_lead` | consent attestation required; agents can never attest |
| `POST /leads/import`, `crm.bulk_import_leads` | disabled |
| `PATCH /leads/{id}` | description needs medical scope |
| `POST /public/leads/{tenant}` | closed until owner-approved wording; explicit scopes required |
| `POST /medical-tourism/patient-leads` | lead evidence required (legacy leads refused) |
| `POST /leads/{id}/consent` | person records/withdraws; audited |
| `POST /customers`, `/customers/import`, invoice-import customers, QuickBooks customer import, calendar-attendee customers | disabled |
| `POST /customers/{id}/notes` | needs medical scope on every linked lead |
| `POST /marketing/outbound/contacts` (list building) | disabled |
| Twilio inbound SMS / voice, Klaros voice receptionist | neutral acknowledgement, nothing stored (not even the raw payload), no promise |
| Marketplace lead webhook | acknowledged, nothing stored |
| Referral → lead | refused (a referred friend has not consented) |
| Lead → customer/job conversion | needs `store_personal_data` on the lead |
| **All outbound email/SMS** (one choke point: `get_communication_provider` returns the consent guard) | sent only if the recipient resolves to a lead whose newest evidence grants `contact`; unknown recipient not sent; staff invites exempt |
| Halla outbound call / sync-to-Halla | need `contact` |
| Tool audit log | personal fields replaced by `***PII***` (lead, customer, outbound-contact, referral tools) |
**Residual (A / decisions):** operator-authored free text on appointments, jobs, quotes, contracts and consultations is not gated (it describes existing records); `crm.update_customer` edits existing customers; AI lead qualification sends lead text to the configured AI provider — the pilot's `AI_PROVIDER`/keys must be confirmed to be the internal deterministic provider (A); the invoice-delivery adapter is an internal recorder only (V). The pilot configures no Twilio/SendGrid credentials (A: confirm on the live service) — the guard protects it if they are ever added.

## 4. Migration plans, backup, rollback (nothing applied)
**Klaros** (pilot DB `klaros_halla_pilot`, Neon): revision graph `… → 0064 → 0065_halla_consent_evidence` (single head, V; no Dropshipping migration in this tree, V). Offline SQL for `0064→head` is only `CREATE TABLE halla_consent_evidence` (+ `source`, `actor_user_id`), 3 indexes, RLS + select/insert/update policies, `UPDATE alembic_version` (V). It is applied by the service at boot (`scripts.start` → `alembic upgrade head`) after the identity checks; do **not** apply by hand. The main tree's unreleased `0065`–`0067` must be renumbered after this one when they merge (a test fails on two heads).
1. Confirm DB revision = `0064` (A). 2. **Backup:** create a Neon branch of `klaros_halla_pilot` (copy-on-write restore point) or export, and record its name. 3. Merge + Manual Deploy. 4. Verify revision `0065_halla_consent_evidence`, table + 3 policies.
**Rollback:** Render → Rollback to the `8c18afe` deploy (old code ignores the table); then, if required, export the table and `alembic downgrade 0064` (V on PostgreSQL 16); restore the Neon branch only for corruption.
**Halla** (production Supabase): migrations `075_lead_consents.sql` then `076_lead_consent_outbox.sql`, both additive/`IF NOT EXISTS`/RLS-on (V on PostgreSQL 16 with stub parent tables; A: real schema has `voice_tenants`, `leads`, and prod is at `074`). Before applying: list `supabase_migrations.schema_migrations` — if earlier migrations are missing, a CLI push would apply them too (stop and report). Halla's documented routes are `supabase db push` or `node run-migration.js` (docs differ; confirm with the owner). **Backup:** Supabase backup/PITR availability depends on the plan (A) — take a manual `pg_dump` of at least `leads`/`voice_tenants` if PITR is absent. **Rollback:** drop the two new tables (they hold only consent records); the previous gateway commit ignores them.
**Deploy order:** (1) Halla `075`/`076`; (2) Halla gateway with the consent code; (3) Klaros migration + pilot deploy; (4) connect the Halla tenant.

## 5. The two reported test failures — can they affect the pilot?
| Test | Root cause (V) | Pilot impact |
|---|---|---|
| `test_five_way_duplicate_execution_request_one_logical_execution` (PostgreSQL, intermittent under load) | In `AgentExecutionService.run_action` the **concurrency-ceiling check runs before the duplicate-key check**, so when five identical requests race, a late one can get "concurrent ceiling reached" instead of "duplicate". Still exactly one execution; the test asserts the other message | **None.** The Halla receiver and consent path never use agent executions (V: grep); no agents are configured on the pilot (A). Suggested fix (not applied, outside this task): check the idempotency key first |
| `test_phase9_e2e` | Test defects: it relied on handler registration done by importing the full app, and it wrote an invoice status directly **outside the lock** the running worker uses. Fixed both in the test (40/40 standalone passes after; before: always failed alone, ~1 in 6 afterwards) | **None.** Test-only; the service imports the whole app and the direct write exists only in the test |
Nothing was skipped or hidden.

## 6. Synthetic end-to-end test (run only after §1 P0/P1; synthetic data only — a reserved test number/inbox, never a real person)
Evidence collector (read-only, prints ids/counts/scopes only, V): `DATABASE_URL=<pilot DB, read-only role preferred> python -m scripts.mt_e2e_evidence --tenant <klaros tenant> --halla-lead-id <id> --other-tenant <another tenant id>`.
| # | Action | Pass evidence |
|---|---|---|
| 1 | Halla: synthetic enquiry call; agent asks consent; caller answers `contact` + `store_personal_data`; lead created through the real Halla API | Halla `lead_consents` rows, outbox `delivered_at` set; webhook delivery log `lead.created` 2xx with `consent`; collector: exactly 1 lead under the mapped tenant, evidence row `source=halla`, derived scopes `contact, store_personal_data` |
| 2 | Duplicate delivery (resend the same event) | response `duplicate_ignored`; collector `duplicate_leads_for_halla_lead=0`, `duplicate_evidence_event_ids=0` |
| 3 | Invalid signature, stale timestamp (>300 s), wrong `tenant_id` in body, unknown Klaros tenant | 401/400, 400/401, 403, 404; collector counts unchanged |
| 4 | Tenant isolation | collector `isolation` block all 0 for another tenant |
| 5 | Partial withdrawal: caller withdraws `contact` | Halla outbox entry delivered; collector `derived_consent_now` = `store_personal_data` only; `POST …/leads/{id}/halla/call` → 422 and no Halla `/calls/outbound` request |
| 6 | Reordered delivery: replay the earlier grant event after the withdrawal | state unchanged; evidence rows both retained |
| 7 | Re-grant `contact` | scopes = `contact, store_personal_data`; call allowed again |
| 8 | Withdraw `store_personal_data`; send a later `call.completed` | lead unchanged (frozen); operator erase → collector `personal_data_erased=true`; `audit_logs` row `lead.personal_data_erased` with counts only |
| 9 | Declined-from-the-start second lead | no lead row; one evidence row (declined) |
| 10 | Escalation: synthetic "I have chest pain" | Klaros event `halla.lead.escalated` `category=EMERGENCY`, no text; Halla escalation reaches the verified destination (receipt from the owner's test inbox/phone) |
| 11 | Failure + retry: make the receiver return 500 for one event, restore | Halla delivery log shows failed then successful attempt; one lead only; `webhook_events` FAILED→handled |
| 12 | Non-Halla intake negatives: public form (closed), `POST /leads` without consent, `/leads/import`, outbound message to the withdrawn lead | 403 / 422 / 422 / not sent; zero new rows |
| 13 | Log hygiene | Render logs of both services contain no synthetic name, phone, summary text or secret prefix |
| 14 | Outbox recovery (on a Halla staging copy with Redis disabled) | outbox row pending, then delivered after restore |
Pass only if every row's evidence is captured with event ids and timestamps (no personal data in the report).
