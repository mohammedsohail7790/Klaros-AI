# Clinic outreach campaign: design

Date: 2026-10-20. Status: draft for owner review. Nothing in this document is built.

## 1. Purpose

MedLuxe Travel earns commission from clinics, and no clinic is verified or on agreed terms (0 of 43 verified; 0 of 37 with terms; 30 of 37 have no contact email). Until that changes there are no referrals and no revenue. This design adds a **clinic outreach campaign** that the owner sets up and starts from the Klaros dashboard. Once started, Klaros finds each clinic's business contact, introduces MedLuxe, follows up, reads and answers replies, and proposes commission terms inside limits the owner set. People make every commitment and every verification decision.

Decisions already made with the owner:

- Audience first: **clinics** (business to business), not patients.
- Autonomy: **fully automatic including replies**, made safe by the guardrails in section 6.
- It runs as a **campaign launched from the dashboard**: nothing runs until the owner presses Start.
- A **shadow week** comes first: everything runs except sending.

## 2. Non-goals

Patient marketing, paid ads, social posting, WhatsApp or SMS outreach, automatic acceptance of terms, automatic clinic verification, generating or e-signing a clinic agreement (a later phase), and scraping or buying contact lists.

## 3. What exists today and is reused

- `Campaign` (channel `OUTBOUND`, status DRAFT/ACTIVE/PAUSED/COMPLETED/ARCHIVED) and the Marketing > Campaigns pages.
- Clinic directory (`Provider`, status, credentials) and `ProviderTerms` (per-clinic commission), with the rule that no clinic can receive a referral unless verified.
- Clinic sourcing from public pages (`mt_clinic_sourcing`, web search provider).
- Clinic Onboarder invitation email (`medical_tourism.invite_clinic`, approval required) and `get_clinic_channel()` for sending to a clinic's own address.
- Reply capture: every clinic email carries a hidden Reply-To token and replies are stored and notified (`inbound_email_service`).
- Audit log, approvals, AI kill switch, `tenant_settings`, the crew framework with direct scheduled actions.

## 4. The campaign in the dashboard

The campaign is a normal `Campaign` row (channel OUTBOUND, objective LEAD_GEN renamed in the UI to "Clinic partners") with a configuration attached. It appears in Marketing > Campaigns beside any other campaign.

**Create** (button "New clinic outreach campaign", a short wizard):

1. Name and countries to target (Turkey, Thailand, South Korea, China) and which clinics (all with no terms, or a hand-picked list).
2. **Term sheet:** commission basis (percentage of treatment price or flat fee), the allowed range (minimum and maximum), payment days, currency, and the qualifying outcome. The AI may only offer terms inside this range.
3. Sender: display name, signature, company name and postal address (printed in every email).
4. Limits: daily send cap (default 20, ramping from 5), at most 3 emails per clinic, send window (default weekdays 09:00 to 17:00 in the clinic's local time).
5. Review: a preview of the first intro email and the rules in plain words.

**Start, Pause, Stop** (campaign page):

- **Start in shadow mode** is the default first action. Status becomes ACTIVE with mode SHADOW.
- **Go live** is a separate button, available only after the shadow exit checks in section 8. It opens a confirmation listing the term sheet, caps and sender, and needs a ticked acknowledgement. Only a user with campaign management permission can press it.
- **Pause** sets PAUSED. The engine stops at once: no send happens after the pause is saved. **Stop** sets COMPLETED and ends all outstanding steps. The existing AI kill switch also stops it.
- The campaign page shows the funnel (found, email found, intro sent, replied, interested, terms proposed, agreed, verification), per-country counts, the next scheduled sends, and an **Needs you** list.

## 5. The pipeline

Each clinic in the campaign has an outreach record and moves through these stages. The engine, not an AI agent, decides what happens next.

| Stage | Meaning | Automatic action | Moves on when |
|---|---|---|---|
| `QUEUED` | Selected for the campaign | Find a contact email | Email found, or NO_EMAIL |
| `NO_EMAIL` | No usable business address found | Retry discovery after 14 days; show in Needs you | Email added by a person, or found later |
| `READY` | Has a verified business email | Schedule intro | Send window and daily cap allow |
| `INTRO_SENT` | Intro email sent | Wait 4 days | Reply, or no reply |
| `FOLLOWUP_1` | First follow-up sent | Wait 5 days | Reply, or no reply |
| `FOLLOWUP_2` | Second follow-up sent (the last email) | Wait 7 days | Reply, or no reply |
| `NO_RESPONSE` | Nothing after the last follow-up | None. Never emailed again by this campaign | A later reply |
| `REPLIED` | A reply arrived | Classify it and act (section 7) | Classification outcome |
| `TERMS_PROPOSED` | Terms inside the owner's range were offered | Wait for the clinic | Clinic accepts, counters or declines |
| `NEEDS_PERSON` | A human decision is required | Notify the owner with the thread; stop automatic replies | Owner resolves it |
| `AGREED_PENDING` | Clinic accepted the terms in writing | Create the Needs-you task to record terms and agreement reference | A person records the terms |
| `DECLINED` / `OPTED_OUT` | Said no, or asked to stop | Suppress permanently | None |
| `HANDED_TO_VERIFICATION` | Terms recorded and credentials requested | Existing Verification Assistant takes over | A person marks it verified |

## 6. Guardrails

These are enforced in code, not left to the AI's judgement.

- **Commitment gate.** Offered terms must lie inside the owner's term sheet. The reply composer receives the term sheet as structured data and may only insert those numbers. Any outgoing text containing a percentage, amount, date or promise is checked against the term sheet before it is sent; a mismatch blocks the send and moves the clinic to `NEEDS_PERSON`. A counter-offer, a request outside the range, or any request to change the term sheet always goes to a person.
- **A proposal is not an agreement.** `ProviderTerms` is written only by a person. The AI can never mark terms agreed, verify a clinic, or promise a referral volume.
- **Opt-out.** Any reply expressing "stop", "unsubscribe", "remove me" or equivalent, in any language the classifier recognises, sets `OPTED_OUT` immediately and sends one short confirmation. A suppression list is checked before every send. Every email carries a plain opt-out line.
- **Handoff triggers.** A legal term, threat, complaint, regulator, press enquiry, medical question, price negotiation, a request for patient information, anything the classifier is unsure about (confidence below a threshold), and a second consecutive automatic reply with no new information all move the clinic to `NEEDS_PERSON`.
- **Limits.** Daily cap with a warm-up ramp, at most 3 emails per clinic per campaign, a send window in the clinic's local time, no emails on local public holidays when known, and a global stop on bounce rate above a set limit.
- **Sender honesty.** Every email names MedLuxe Travel, the sender, the company address and the reason for writing. It never claims a partnership, an endorsement, volumes or results that do not exist.
- **Addresses.** Only a business address found on the clinic's own site or official listing, preferring departmental addresses such as international patients. Never guessed patterns, never bought lists, never a personal address. A syntactic and MX check runs first.
- **No patient data.** Outreach emails and replies never include any patient or lead information.
- **Audit and kill switch.** Every send, block, classification and handoff writes an audit row. The AI kill switch pauses the engine. Nothing runs in a paused or stopped campaign.

## 7. Reply handling

An inbound clinic reply (already captured by `inbound_email_service`) becomes an outreach message, then:

1. **Classify** into: interested, asks for terms, asks for documents or information, asks who we are, not interested, opt-out, auto-reply or out-of-office, bounce, and other. A model reads the text under the existing AI boundary (clinic emails contain no patient data). Low confidence or `other` means `NEEDS_PERSON`.
2. **Act** by a fixed table, not by the model:
   - interested or asks who we are: send the owner-approved "how referrals work" block, ask for their licence or accreditation, and offer terms on request.
   - asks for terms: send the owner's term proposal, built from the term sheet. One proposal only; any follow-up on price goes to a person.
   - asks for documents or information: send the approved FAQ blocks (what we do, what we need, how commission is paid).
   - not interested: thank, stop, mark `DECLINED`.
   - opt-out: confirm, stop, suppress.
   - auto-reply: ignore, reschedule the follow-up.
   - bounce: mark the address bad, return to `NO_EMAIL`.
3. **Compose** from the owner-approved blocks. A model may choose and lightly adapt wording for the clinic's name and question, but the output is validated (no numbers outside the term sheet, no new claims, no patient data, length limit). A failed validation falls back to the plain approved block, or to `NEEDS_PERSON`.

## 8. Shadow week

Mode `SHADOW` runs the whole pipeline exactly as live mode would, except that **no email leaves**. Each message is stored as `QUEUED_SHADOW` and shown in the campaign page for the owner to read. Contact discovery is real (it only reads public pages). Because no clinic replies, the campaign page offers **Try a reply**: paste a sample clinic reply and see its classification and the drafted answer.

Go-live checks, all required before the Go live button enables:

- At least 7 days in shadow mode.
- At least 20 shadow messages reviewed (marked good or flagged) by the owner, with fewer than 10 percent flagged.
- At least 5 "Try a reply" tests covering interested, terms, opt-out, legal and unclear.
- Term sheet, sender details and postal address complete.

Going live starts with the first 10 clinics and a daily cap of 5, rising automatically toward the owner's cap only while the bounce rate and opt-out rate stay under limits.

## 9. Data model

New tables, all tenant-scoped with row-level security and a unique tenant filter:

- `clinic_outreach_campaigns`: `campaign_id` (unique, references `campaigns`), `mode` (SHADOW or LIVE), `term_sheet` (JSON), `sender` (JSON), `limits` (JSON), `countries`, `live_at`, `shadow_started_at`, `exit_checks` (JSON).
- `clinic_outreach`: `campaign_id`, `provider_id`, `stage`, `contact_email`, `contact_source_url`, `contact_found_at`, `next_action_at`, `emails_sent`, `last_sent_at`, `last_reply_at`, `handoff_reason`, unique on (campaign, provider).
- `clinic_outreach_messages`: `outreach_id`, `direction` (OUT or IN), `kind` (intro, followup, reply, proposal, confirmation), `subject`, `body`, `status` (QUEUED_SHADOW, SENT, BLOCKED, FAILED, RECEIVED), `blocked_reason`, `classification`, `confidence`, `external_id`, timestamps.
- `outreach_suppressions`: tenant-wide list of addresses and domains never to contact.

Reuse: `Campaign`, `Provider`, `ProviderTerms`, `tenant_settings` (key `outreach` for approved blocks and defaults), `AuditLog`.

## 10. Components

- **Contact finder** (`clinic_contact_finder`): reads the clinic's own public pages through the existing search provider, extracts candidate emails, keeps only those that appear in the page text on the clinic's own domain or official listing, prefers departmental addresses, runs the MX check, and stores the source URL.
- **Outreach engine** (`clinic_outreach_engine`): a deterministic function run by a scheduled crew action (a new "Outreach Runner" member) every hour. It enforces mode, caps, send windows and spacing, builds each message, sends through `get_clinic_channel()` (live) or stores it (shadow), and advances stages.
- **Reply handler**: hooks the existing inbound clinic reply path, classifies, and applies the table in section 7.
- **Composer and validator**: builds messages from the approved blocks and the term sheet and rejects anything that breaks the rules in section 6.
- **API** under `/api/v1/outreach`: campaign create and edit, start, go live, pause, stop; stage list, thread, sample-reply test, review marks, suppression list, funnel numbers.
- **Dashboard**: a "Clinic outreach" campaign wizard and detail page in Marketing, a Needs-you list, and the shadow review screen.
- **Crew**: the Outreach Runner is a direct scheduled action (no model decides anything), so it cannot stop after one step the way model-driven members can.

## 11. Failure handling

A send failure marks the message FAILED and retries once the next day; two failures park the clinic in `NEEDS_PERSON`. A classifier or provider error never sends anything and moves the thread to `NEEDS_PERSON`. A duplicate run cannot double-send (unique message per clinic, stage and day). Email delivery is limited by the email plan (Brevo free: 300 a day, shared with all other mail), so the default cap is far below it.

## 12. Testing

- State machine: every transition, timing and the three-email limit.
- Contact finder: fixtures with real-shaped pages; never invents an address; ignores addresses off the clinic's domain; MX failure handling.
- Guardrails: a term outside the range is never sent; any number not in the term sheet blocks the message; opt-out in several phrasings; a legal or medical reply hands off; low confidence hands off; caps, warm-up and quiet hours.
- Shadow: the channel is never called in shadow mode.
- Pause and kill switch: nothing sends after a pause.
- Tenant isolation and row-level security on real PostgreSQL.
- End to end through the real app with a fake channel and a fake model, like the existing medical-tourism end-to-end test: shadow run, go live, intro, no reply, follow-ups, a reply asking for terms, a proposal, an acceptance, the Needs-you task, and the audit trail.
- Frontend component tests for the wizard, the Start, Go live, Pause and Stop controls, and the Needs-you list.

## 13. Phases

1. Data, settings, term sheet, and the contact finder with the dashboard campaign wizard (nothing sends).
2. Engine, composer, shadow mode and the shadow review screen.
3. Reply classification and handling, with Try a reply.
4. Go live, ramping, funnel numbers and notifications.
5. Later: clinic agreement generation and e-signature; clinic verification help from the documents they send.

Each phase is shipped behind the campaign's mode, so nothing reaches a clinic until the owner presses Go live.

## 14. Inputs needed from the owner

The commission range and payment days; company name, postal address and signature for the emails; which countries to start with; the daily cap; confirmation of the approved wording blocks (written in phase 2 and shown for approval); and legal advice on outreach rules in the clinics' countries.

## 15. Risks and open questions

- **Deliverability:** a new sender domain contacting strangers can be marked as spam. Mitigation: warm-up, low caps, a clear opt-out, departmental addresses only, and watching bounces and opt-outs.
- **Contact discovery accuracy:** many clinics publish forms, not emails. Expect a share to stay `NO_EMAIL`; the plan then lists them for a human to add.
- **Misclassification:** a reply may be misread. The design is conservative: uncertainty hands off, and the action table never lets a misclassification make a commitment.
- **Commercial wording:** the approved blocks and term sheet are the owner's. They should be reviewed by an adviser before going live.
- **Open:** whether to write outreach in the clinic's language (English first, translation later) and whether replies in non-English should hand off at first. Recommended: English only at launch, with non-English replies handing off.
