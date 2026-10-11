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

## Safety screen: what it covers and what it does not (implementation facts, not clinical validation)
- **What it is:** a deterministic, lexical screen (`backend/app/services/medical_tourism_safety.py`). No model, no translation, no judgement. Every result is a category and a rule id; the patient's words are never returned, stored in an escalation event, or logged.
- **Where it runs on Klaros-originated text:** lead creation and edits, the public form, patient-lead intake, customer notes, consultation notes, and Halla events (`mt_intake_safety.py`, `halla_event_processor.py`). A flagged lead is held for a person (`REQUIRES_HUMAN`) and cannot be qualified by a machine.
- **Languages:** English rules, plus best-effort emergency / diagnosis / prescription / guarantee terms in Arabic, Turkish, Russian, Spanish, French and German. These lists are partial. **Any text in another script, or a long passage that does not look like English, is classified `LANGUAGE_UNSUPPORTED` and goes to a person** instead of passing. Short fragments (a service name, a city) are not flagged.
- **Obfuscation:** zero-width characters, spaced-out letters, simple leetspeak and look-alike letters are normalised; the highest-severity phrases are also matched with all separators removed.
- **Known limits (pinned by tests):** it does not understand meaning or negation ("I do not have chest pain" is flagged), it can miss a paraphrase that uses none of the listed words, and ordinary phrases such as "the clinic" or "back stroke" are flagged. Flags only ever send a message to a person; they never block a patient from emergency services. A classifier error flags the lead (`SAFETY_CHECK_FAILED`).
- **Open decision for the owner and clinical advisers:** the emergency wording shown to a patient, which languages the business actually supports, and who reads `LANGUAGE_UNSUPPORTED` leads.

## Funnel telemetry (`GET /api/v1/medical-tourism/funnel`)
Counts only; no names, contact details, text or ids. Stages are distinct facts: leads received; consent granted per scope and "frozen" leads; qualified vs held for a person; appointments by their own status (**TENTATIVE is a request, never a booking**); consultations by status; escalations *requested* (a person was asked, not proof they acted); Halla event delivery (processed, failed, in flight, stuck past the lease); sends blocked by consent.

## Staging transport limit
Klaros staging runs with the in-memory event transport. Events queued in memory do not survive a restart, and the funnel's delivery counters describe only what was persisted in `webhook_events`.
