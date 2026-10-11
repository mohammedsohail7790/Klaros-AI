# Medical Tourism pilot — consent policy (as implemented), operations and rollback

Status: **not live.** Wording, retention periods and legal sufficiency are **not approved**; nothing here is legal advice or a compliance claim.

## 1. Consent model (implemented)
Halla records an explicit spoken answer per scope with the `record_consent` tool (never a keypress, a call, or an AI interaction) and sends
`data.consent` on `lead.created` / `lead.updated`. Klaros keeps three **independent** scopes:

| Scope | Klaros behaviour while granted | While not granted |
|---|---|---|
| `store_personal_data` | a Halla-created lead (name, phone, email) is stored | no lead is created from Halla; an existing lead is **frozen** (later Halla events do not change it) |
| `store_medical_information` | call summary / outcome text is kept on lead events; treatment text is kept | that text is dropped (category only) |
| `contact` | operators can ask Halla to call the lead (`POST …/leads/{id}/halla/call`) | the call is refused (422) |

State = the newest evidence by Halla's `recorded_at`; a scope it omits is **not** granted; older / replayed evidence never overrides newer;
same-instant evidence resolves to the restrictive reading. `granted:false` means declined *or* withdrawn (Halla does not distinguish them).
Safety escalations (emergency, diagnosis/prescription request, guaranteed-outcome request) still reach a person while a lead is frozen.

## 2. Withdrawal, suppression and erasure
* Withdrawal reaches Klaros through Halla's durable outbox (migration 076): a `lead.updated` carrying the freshly derived evidence is published
  after every `record_consent`; if Redis is down the entry stays pending and the 60-second sweeper retries it.
* Existing records are **suppressed automatically** (frozen, no outbound contact, no medical text) and **erased only by an operator**:
  `POST /api/v1/business-builder/leads/{id}/halla/erase-personal-data` (needs MANAGE_INTEGRATIONS; refused with 409 while consent is granted).
  It clears name, phone, email, description, location and service on the lead; the lead id, status and the consent history remain. A linked
  customer record is not touched (it may carry other business) and must be handled by the owner.
* The consent history (`halla_consent_evidence`) is append-only and holds no personal data. **No automatic purge exists** because no retention
  period is approved. DECISION NEEDED: retention period for the lead and for the consent history.

## 3. Data minimisation
Webhook records keep envelope ids only. Logs carry ids and categories, never names, phones, summaries, wording text or secrets. Unsafe
conversation text is replaced by a category. Consent wording **text** is never stored or sent (only the owner's label `wording_version`).

## 4. Proposed consent wording (NOT approved — launch blocker)
The business owner must supply and approve the wording and its `wording_version` label (`voice_tenants.metadata.consent_capture.wording_version` in Halla).
Without it Halla does not offer `record_consent`, so no evidence is produced and Klaros stores no Halla-created lead (fail closed).

## 5. Human escalation procedure
Halla routes urgent / uncertain cases to the verified escalation destination configured for the tenant (owner decision: destination, hours,
response time). Klaros records the escalation as an event with a category only and sets the lead to REQUIRES_HUMAN.

## 6. Monitoring and failure handling
* Klaros: `GET /api/v1/webhooks/halla/{tenant}` failures are recorded FAILED in `webhook_events` and retried by Halla's redelivery; the
  receiver returns 500 so Halla retries; duplicates return `duplicate_ignored`. Operators see consent state per lead at
  `GET …/leads/{id}/halla/consent`.
* Halla: pending notifications = rows in `lead_consent_outbox` with `delivered_at IS NULL` (alert if any is older than 15 minutes); bus failures
  log `EVENT_PUBLISH_FAILED`; consumer retries / DLQ as before.

## 7. Rollout order and rollback
Apply in this order, each with its own backup and verification: (1) Halla migrations 075 then 076 on the **Medical Tourism tenant's** database
(additive; both are `IF NOT EXISTS`), (2) deploy the Halla gateway with the consent code, (3) Klaros pilot migration `0065_halla_consent_evidence`
(runs at boot via `scripts/start.py`), (4) deploy the Klaros pilot, (5) connect the Halla tenant. Rollback: redeploy the previous commit
(`8c18afe` for Klaros — the table is additive and unused by it); Halla: redeploy the previous commit (075/076 tables are additive and can stay);
`alembic downgrade 0064` drops the Klaros table if required (it holds only consent history — export it first).
