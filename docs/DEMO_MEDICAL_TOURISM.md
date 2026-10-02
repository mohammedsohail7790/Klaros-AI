# Demo — Medical Tourism, end to end

The primary deep demonstration. It uses the existing Medical Tourism module (providers,
procedures, patient leads, consultations, referral commissions); the Business Builder only
connects it to the journey.

## Prerequisites

Real PostgreSQL with `alembic upgrade head` applied and the restricted `klaros_app` role
provisioned (see `BUSINESS_BUILDER.md` §5); backend and frontend running.
With an AI key configured Discovery is adaptive; without one it uses the deterministic fallback.
Both paths work; say which one you are showing.

## Script

1. **Landing** (`/`): type *"I want to build a medical tourism company connecting international
   patients with hospitals in India."* in the hero field and press **Build My Business**.
2. **Sign up**: the idea carries through; after sign-up you land on **What are you building?**
   with the idea pre-filled. Press **Start building**.
3. **Discovery**: answer the questions (the "what you've told us so far" list pairs each answer with
   its question). You can end early with **That's enough**.
4. **Blueprint**: confirm the statements ("Is this right?" / "confirm all"). Anything wrong can be
   corrected with **Edit** — e.g. list the capabilities under *Required Capabilities*, one per line.
   If a required section is empty the screen names it. **Confirm and continue**.
5. **Requirements**: each requirement shows why it exists (your own words) and its honest state.
   The *Medical Tourism module* banner appears: press **Enable** (an explicit action — nothing is
   enabled automatically). Then **Continue to recommendations**.
6. **Recommendations**: grouped by requirement. Stripe and Google Calendar are *available now — you
   connect your own account*; ad/lead-source providers are *Planned — adapter required*.
7. **Business Map**: customers → front door (website, communication, AI workforce) → Klaros →
   operations (lead qualification, provider directory, referral workflow, scheduling…) → money &
   growth (commission tracking, payments, marketing, analytics) → systems & partners (providers,
   Stripe, Google Calendar). Select any node to see what it receives and sends. **Next actions** below.
8. **Website**: **Build my website** → generated from the Blueprint. With the module enabled it has
   *Our partners* and *What we offer* pages bound to the live provider/procedure data. Add a couple of
   providers and procedures (Medical Tourism screens) — they appear on the site at once.
   **Publish this version**, then open the public site (`/w/<tenantId>`).
9. **Public lead**: submit the contact form as a visitor. The lead appears under **Leads** *and* as a
   Medical Tourism patient lead (the extension row is created because the module is enabled).
10. **Business Home** (`/business/home`): live counts (leads, providers, procedures), the launch
    checklist, next actions, and the AI workforce card — **Not connected**, with the reason.

## What was verified when this was written (real PostgreSQL, restricted role, real AI provider)

Landing → sign-up → idea carried → Discovery (4 answers) → Blueprint correction + confirmation →
Requirements (module enabled via the UI) → Recommendations (87 raw rows presented as ~19 decisions
grouped by 16 requirements) → Business Map (six lanes) → website generated, previewed,
published (v4 PUBLISHED, earlier versions SUPERSEDED) → public lead submitted → 1 `leads` row and
1 `medical_tourism_patient_leads` row verified directly in PostgreSQL → Business Home shows
Live / 1 lead / directory 2 / catalog 2 / workforce Not connected.

Not demonstrated: any real Halla connection, Stripe/Google Calendar connection, or payment —
none was configured, and none is claimed.
