# Medical Tourism pilot — launch runbook, proposed consent wording, retention matrix, rollback

**Status: NOT LIVE.** Nothing here has been deployed, migrated or configured on any hosted service. Wording and retention periods below are **proposals for the business owner and their counsel**; none is legally approved, and this repository makes no legal claim.

## 1. Proposed consent wording (DRAFT — label `MT-CONSENT-v1-DRAFT`; not approved)
Three independent questions, each answered separately and recordable as granted / declined / withdrawn. An unclear answer is recorded as nothing. Pressing a key to talk to the AI is never consent.
1. **Contact** (`contact`): "May [Business] contact you about your enquiry by phone, text or email? You can change your mind at any time."
2. **Personal data** (`store_personal_data`): "May we store your name and contact details so we can follow up on your enquiry?"
3. **Medical information** (`store_medical_information`): "Do you agree that we record health details you choose to share, so a coordinator can assess your enquiry? Please only share what you are comfortable with."
Each question must be followed, in the approved final text, by who the controller is, the purpose, who receives the data (including any AI or hospital recipient), how long it is kept and how to withdraw. The owner chooses the final wording and label; the label (`wording_version`) is configured per tenant (`voice_tenants.metadata.consent_capture.wording_version` in Halla; `approved_wording_versions` in the Klaros Halla connection for web forms). The wording text itself is never sent between systems.

## 2. Proposed retention and erasure matrix (periods are owner decisions; defaults are fail-closed)
| Data | Where | Erased by the system today | Not erasable automatically / action |
|---|---|---|---|
| Lead contact details, free text | Klaros `leads` | Operator erase endpoint after withdrawal of `store_personal_data` (refused while granted) | Period: owner decision (checklist #13) |
| Appointment title/notes/location/service | Klaros `appointments` | Yes, by the same endpoint | |
| Patient-lead health intake | Klaros `patient_leads` | Yes | |
| Call number, transcript, engine state | Klaros `call_sessions` | Yes | Halla keeps its own call records (below) |
| Halla event summaries / outcomes | Klaros `events` | Text keys scrubbed | Event rows kept without text |
| Linked customer | Klaros `customers` | Erased only when nothing else references it; **customer notes and the free-text `notes` field are always erased** | A referenced customer row (name/contact) is kept and reported — financial records follow their own retention; owner decision (#14) |
| Invoices, quotes, jobs, contracts for that customer | Klaros | **No** — free text inside them is not scrubbed | Owner decision (#17); legal retention may apply |
| Consent history, idempotency ids | Klaros `halla_consent_evidence`, `webhook_events` | No (no personal data; it is the audit trail and stops re-creation) | Period: owner decision |
| Audit log | Klaros `audit_logs` | Gated tenants' rows are written redacted; none erased | Retention period: owner decision |
| Agent steps / results | Klaros `agent_execution_steps` | Written redacted for gated tenants | |
| AI provider copies | external | None leave for gated tenants unless a provider is allowlisted (default none) | Any allowlisted provider needs its own deletion process |
| Stripe | external | The customer e-mail is passed only with contact consent; Stripe keeps its own payment records | Stripe retention is Stripe's/owner's |
| Halla call recordings, transcripts, lead rows, outbox | Halla | **Not** erased by Klaros | Halla-side erasure procedure and period: owner/Halla operator |
| Database backups | Neon / Supabase | No | Backup retention window must be stated to data subjects |

## 3. Deployment order (every step needs the right account owner; none has been done)
1. Business owner creates their own Halla tenant (never Call IQ / Halla AI) and a Klaros tenant; record both ids; check the Klaros id is unused.
2. **Back up** the Klaros pilot database (Neon branch/snapshot) and the Halla production database (Supabase backup). Record the backup ids.
3. **Verify** each database's identity (host, project, current revision) before any change. Klaros: `alembic current` must read `0064` (expect single head `0065_halla_consent_evidence`). Halla: confirm which of `075`/`076`/`077` are applied (all additive, idempotent).
4. Apply migrations: Halla `075` → `076` → `077`, then Klaros `alembic upgrade head` (only `0065`). **Halla code must not be deployed before 077** (evidence reads order by `seq`).
5. Merge reviewed code: Klaros `feat/mt-halla-contract-hardening` → `release/pilot-prod` (manual deploy of `klaros-halla-pilot`, Auto-Deploy off); Halla `feat/medical-tourism-consent-evidence` → the branch that the actual production service (`middle-east`) tracks, deployed by its Render owner.
6. Environment (names only; values entered by the owner in Render, never in chat or Git): Klaros `DATABASE_URL`, `HALLA_API_BASE_URL=https://gateway.hallaai.com`, `HALLA_ALLOWED_HOSTS=gateway.hallaai.com`, `HALLA_API_KEY_HEADER`, `KLAROS_PUBLIC_API_URL`, `HALLA_PILOT_TENANTS`, the key/secret variables it names, `AI_PROVIDER=deterministic` (or no AI keys), `AI_EXTERNAL_PROCESSING_ALLOWED_PROVIDERS` empty. Webhook: `https://klaros-halla-pilot.onrender.com/api/v1/webhooks/halla/{klaros_tenant_id}`, unique signing secret, the 9 supported events.
7. Verify: deployed SHAs equal the merged SHAs, `/health`, `/ready`, migration revisions, a signed webhook delivery.
8. Run the synthetic end-to-end scenarios (section 5) with synthetic data only.

## 4. Rollback
- **Code:** Render → service → Deploys → redeploy the previous deploy (Klaros previous release `8c18afe5c10b3ced81478f9c5c484bb83dda8b44`; Halla: the SHA recorded before step 5). Branch pointers are never force-moved.
- **Database:** all migrations here are additive (new tables/columns/index). Rollback of code needs no schema change; the new objects can stay. Only if a restore is genuinely required: restore from the backup taken in step 2 (this loses data written after it).
- **Stop contact immediately:** unset the Halla webhook subscription or the Halla API key; Klaros fails closed (no consent evidence → no storage, no contact).

## 5. Synthetic end-to-end scenarios and what is proven so far
Proven locally by automated tests (SQLite; PostgreSQL 16 with real RLS under the restricted role; Redis 7): consent-gated intake, replay de-duplication, missing/declined/partial consent, partial withdrawal with delayed/reordered events and newer re-grant, frozen lead and blocked outbound, invalid signature / stale timestamp / tenant mismatch rejection, medical text only with its scope, erasure and linked-record handling, no PII in audit/log outputs, Halla outbox recovery and retry.
**Not proven (requires the deployed systems):** the same flow through the real Halla gateway, the real webhook and the isolated Neon database; human escalation reaching a verified destination. Until that is run and recorded, the integration must not be called verified, and the system is **NOT LIVE**.

## 6. Monitoring and failure handling
Watch: Klaros `/ready` and `webhook_events` statuses (failed/duplicate counts), Halla `lead_consent_outbox` rows with `delivered_at IS NULL` and rising `attempts`, `BLOCKED_CONSENT` statuses on collection/outbound/nurture/retention/review rows, and log lines `outbound_blocked_*`, `consent_gate_status_unknown_failing_closed`, `ai_boundary_blocked_external_call`. A rising `consent_gate_status_unknown_failing_closed` count means the database/vertical lookup is failing (the system is blocking, by design).
