# Medical Tourism — unresolved policy decisions

These are decisions for the business owner and their counsel. This repository **makes no legal claim, grants no exemption and records no approval**; the behaviour below is the fail-closed default the code enforces until a decision is made and implemented.

| # | Question | Current behaviour (default) | What a decision would change |
|---|---|---|---|
| 1 | **Transactional messages** (appointment confirmations, invoices, payment receipts, collection notices): are they exempt from `contact` consent? | No exemption. Every patient-facing e-mail/SMS needs `contact` evidence for the patient the message is about; otherwise it is `BLOCKED_CONSENT` | An exemption would have to be an explicit, per-template allowlist in `consent_guard`, with its own tests |
| 2 | **Operator-attested consent**: may a staff member record consent on a patient's behalf (phone call, paper form)? | Accepted as `operator_attested` evidence with the staff user id and wording version; agents/workflows can never attest | Counsel may require signed evidence, a second person, or to disallow it |
| 3 | **Hospital / provider sharing**: may patient data be sent to a clinic or hospital? | Not implemented. Nothing in the gate authorises it | A separate scope, recipient allowlist and agreement |
| 4 | **AI-processing consent / processor agreement**: may a gated tenant's content go to an external AI or embedding provider? | **No.** `AI_EXTERNAL_PROCESSING_ALLOWED_PROVIDERS` is empty; AI calls for gated tenants degrade to the deterministic path. If a provider is allowlisted, e-mail/phone patterns are scrubbed but medical wording in free text cannot be reliably redacted | A named provider, a data-processing agreement, a lawful basis and probably a dedicated consent scope (`ai_processing`) |
| 5 | **Free text** on appointments, jobs, quotes, contracts, consultations, notes on non-lead records | Not gated (it describes records that already exist); redacted in audit logs by key name only | Gate by scope, or restrict to structured fields |
| 6 | **Shared contact details** (family e-mail/phone) | A message is sent only if the address resolves to one patient, or the caller binds a validated patient identity; ambiguous sends are blocked | Whether a family contact may receive a message about a patient is a consent question for counsel |
| 7 | **Team invitations to an address that is also a patient's** | A genuine pending invitation is delivered (it is not about a patient, carries no patient data) | None needed unless counsel disagrees |
| 8 | Retention and erasure periods; financial records referencing an erased lead | See launch checklist items 13–14 | |
| 9 | Blocked messages: who is told, and when is the send retried after consent is granted? | Recorded as `BLOCKED_CONSENT`, not retried automatically; an operator must re-schedule after consent exists | An automatic re-queue on new consent evidence |
