# Clinic Outreach Phase 3: reply handling and Try a reply

Parent: `2026-10-20-clinic-outreach-campaign-design.md` sections 6, 7 and 8. Phase 2 (drafts and shadow review) is on this branch. **Nothing in this phase sends a message or hooks the inbound mailbox.** Real sending, Go live, ramping and funnel numbers are Phase 4.

## Scope

1. **Classifier** (`outreach_reply_classifier.py`, pure): reads a clinic reply and returns a label, a confidence and the reasons. Rule-based and deterministic, no model, so it is predictable and testable. Anything unclear, conflicting, non-English or below the 0.6 confidence threshold is `OTHER` and hands off to a person. A model-backed classifier can later replace it behind the same function signature.
   Labels: `OPT_OUT`, `BOUNCE`, `AUTO_REPLY`, `HANDOFF` (legal, complaint or threat, regulator, press, medical, patient information, price negotiation or any figure), `NOT_INTERESTED`, `ASKS_TERMS`, `ASKS_DOCS`, `ASKS_WHO`, `INTERESTED`, `OTHER`. Precedence: opt-out, bounce, auto-reply, handoff, not interested, then the answerable labels. Opt-out wins over everything. Handoff wins over every answerable label. Two answerable labels at once lowers confidence below the threshold.
2. **Action table and reply composer** (`outreach_reply_composer.py`, pure): maps a label to the next stage and, where the table says so, a drafted answer built only from the owner-approved blocks, the term sheet and fixed neutral lead-ins. Every draft goes through the Phase 2 checker with the term sheet's own figures allowed. A failing full draft falls back to the single most relevant block, then to `NEEDS_PERSON`.
   - `OPT_OUT`: `OPTED_OUT`, short confirmation, address suppressed.
   - `NOT_INTERESTED`: `DECLINED`, short thanks, address suppressed.
   - `INTERESTED`, `ASKS_WHO`: `REPLIED`, how referrals work plus what we need.
   - `ASKS_TERMS`: `TERMS_PROPOSED`, the commission block. One proposal only: a second terms question from a clinic already in `TERMS_PROPOSED` goes to a person.
   - `ASKS_DOCS`: `REPLIED`, what we do, what we need, commission.
   - `AUTO_REPLY`: no stage change, next action pushed back 3 days.
   - `BOUNCE`: `NO_EMAIL`, address cleared.
   - `HANDOFF`, `OTHER`, low confidence, or the same question twice in a row: `NEEDS_PERSON` with a plain reason.
3. **Reply service** (in `ClinicOutreachService`): `try_reply` classifies and drafts without touching any clinic, records only that a test of that kind ran (`exit_checks.reply_tests` counts and an audit row with the label, never the text). `handle_reply` applies the table to a real clinic row in a SHADOW campaign and stores the incoming text and any drafted answer (`QUEUED_SHADOW`, never sent). It refuses a LIVE campaign until Phase 4.
4. **Go-live checks** (computed, read only): at least 7 days in shadow; at least 20 drafts reviewed with under 10 percent flagged; at least 5 reply tests covering interested, terms, opt-out, legal and unclear; term sheet and sender complete. Shown in the shadow review. The Go live button stays disabled.
5. **API**: `POST /api/v1/outreach/campaigns/{id}/try-reply` (body `{text, provider_id?}`, manage permission); `shadow-progress` gains `try_a_reply_tests`, `try_a_reply_available: true` and `golive_checks`.
6. **Frontend**: a Try a reply box and the go-live checklist inside Shadow review.

## Guardrails kept

No send path is called. Audit rows hold ids, labels and counts only. Reply text is stored only on a real clinic row, never in audit. Tenant isolation on every query. Replies containing any figure, percentage or currency hand off, so the composer can never commit to terms the clinic proposed.
