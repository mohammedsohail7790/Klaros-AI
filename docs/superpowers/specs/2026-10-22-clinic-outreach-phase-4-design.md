# Clinic Outreach Phase 4: Go live, sending, live replies, funnel

Parent: `2026-10-20-clinic-outreach-campaign-design.md` sections 6 to 8, and Phases 2 and 3 on this branch's parents. **Everything here is off until the owner presses Go live on a campaign that has passed the go-live checks. Tests use a fake channel; nothing in the test suite or the build contacts a real clinic.**

## Behaviour

1. **Go live** (`POST /campaigns/{id}/go-live`, `{confirm: true}`, manage permission): refused unless the campaign is ACTIVE in SHADOW mode, all four go-live checks pass, a real email provider is configured, and `confirm` is true. It sets mode LIVE and `live_at`, archives the shadow drafts (status `SHADOW_ARCHIVED`, kept for history, ignored by the engine) and resets clinics that only ever had shadow drafts to READY so each gets a real introduction. Audit `outreach.went_live`, owner notification.
2. **Live composition**: the Phase 2 engine also runs LIVE campaigns and stores drafts as `QUEUED_LIVE`. Pacing: first 5 intros a day rising by 2 a day toward the owner's cap; during the first 7 live days at most 10 clinics get an introduction.
3. **Sender** (`ClinicOutreachSender.send_due`): sends `QUEUED_LIVE` messages whose time has come through `get_clinic_channel`, only when the kill switch is off and the campaign is ACTIVE and LIVE. Before each send it re-checks: suppression (cancelled, except the single opt-out or decline confirmation), the Onboarder's 14-day rule (deferred), a clinic that has since replied or been handed off (follow-ups cancelled), an address that came from automatic sourcing and was never confirmed by a person (held, clinic handed to a person), the daily cap counted by messages actually sent today, and that a real email provider is configured. A failed send is retried once the next day; a second failure hands the clinic to a person. A success marks the message SENT and sets `emails_sent` and `last_sent_at`.
4. **Safety stop**: with at least 10 sent, a bounce rate or opt-out rate above 5 percent pauses the campaign and notifies the owner. Only a person can resume it.
5. **Live replies**: outgoing emails carry the hidden reply address, including for the outreach contact address; an incoming reply from the clinic is recorded by the existing inbound path and handed to the Phase 3 reply handler, whose drafted answers are `QUEUED_LIVE` and go through the same sender guards. Handoffs create an owner notification.
6. **Hourly run**: the scheduler gains an `HOURLY` frequency for a scheduled action (its idempotency key includes the hour). The Outreach Runner becomes hourly; its tool composes then sends. Nothing else changes schedule.
7. **Funnel and UI**: the campaign page shows live numbers (sent today and in total, replies, interested, opted out, bounced, handed to a person) and a Go live button that enables only when the checks pass, with a confirmation naming what will happen.

## Kept guardrails

No patient data in any email. Audit rows hold ids, labels and counts only. Tenant isolation everywhere. An opt-out, decline, bounce or suppression can never be overridden by the engine or sender. A person can pause or stop at any time, and the AI kill switch stops sending.
