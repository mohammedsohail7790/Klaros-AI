# Halla consent evidence in Klaros (Medical Tourism) — draft, not deployed

Source contract: Halla commit `10bce7ad` (patch sha256 `9e4653dca6353586cb897bda17c2991731f7c1f93f782e114e14d19dec313142`),
`HALLA_KLAROS_INTEGRATION_CONTRACT.md` §3.1. This is evidence that a spoken answer was recorded. It is **not** a statement of legal compliance;
the approved wording, retention period and deletion policy are owner/legal decisions that this code does not make.

## What Klaros accepts
`data.consent` on a signed `lead.created` / `lead.updated`: `granted` (boolean), `scope` (non-empty array of `contact`, `store_personal_data`,
`store_medical_information`), `method` (`voice_ai_verbal`), `wording_version` (`[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`), `recorded_at` (zone-aware ISO time,
at most 5 minutes in the future). Anything else is "no evidence" (`halla_webhook.consent_evidence`). Verified against Halla's own
`sanitizeConsentEvidence` on 16 signed fixtures: both accept and reject exactly the same objects.

## Rules (Medical Tourism tenants only; others are unchanged)
| Situation | Klaros does |
|---|---|
| no `consent` key / malformed | no evidence; a Halla-created lead is **not stored**; nothing inferred from a call, an AI interaction or another scope |
| `granted:false` (declined **or** withdrawn — Halla does not say which, neither does Klaros) | decision kept as history; nothing granted; no lead stored |
| `granted:true` with `store_personal_data` | lead stored (name / phone / email), idempotent on the Halla lead id |
| `store_medical_information` absent | any treatment/service field from the event is dropped |
| `contact` absent | `POST /business-builder/leads/{id}/halla/call` is refused (422); personal-data consent is not contact consent |
| newest evidence wins | by Halla's `recorded_at`; an omitted scope is **not** granted (this is the only way a later withdrawal can show up) |
| older / replayed evidence | kept as history, changes nothing; same event id is ignored |
| same instant | the restrictive reading wins |
| `lead.updated` with a newer grant for a lead Klaros never stored | creates the lead (re-grant path) |

## Storage (draft migration `0065_halla_consent_evidence`, NOT applied anywhere)
The existing `Lead` row has no consent columns, so a new append-only table `halla_consent_evidence` is required (tenant-scoped, RLS select/insert/update, **no
delete policy**). It stores the opaque Halla lead id, scopes, granted flag, method, wording label and `recorded_at` — no name, phone, wording text, transcript or
medical content. Nothing is deleted or retained under a policy Klaros was not given. **Renumber** if it merges after the main tree's unreleased `0065`–`0067`.
Note `scripts/start.py` runs `alembic upgrade head` on boot, so merging this migration to the deployed branch applies it at the next (manual, approved) deploy.

## Gaps in the Halla contract (not changed here)
1. **Withdrawal latency.** A consent change emits no event; it reaches Klaros only on the next `lead.created`/`lead.updated` for that lead. A caller who
   withdraws after the last lead event is not known to Klaros until some later event. Smallest safe improvement: when `record_consent` writes a row, publish a
   `lead.updated` carrying the freshly derived `consent` (Halla already derives it in `resolveConsentForLeadEvent`).
2. **Decline vs withdrawal are indistinguishable** (`granted:false`). Fine for fail-closed gating; a `decision` field would be needed for reporting.
3. **Per-scope time.** `recorded_at` is one time for the whole object (the newest among the listed scopes), so Klaros stores it for every listed scope.
4. **Mixed decisions.** `{granted:true, scope:[contact]}` does not say that another scope was explicitly refused; Klaros treats every omitted scope as not granted.
5. **Treatment text.** Halla's `lead.created` carries no service field, so the medical-information gate on `service_requested` is dormant until it does.
