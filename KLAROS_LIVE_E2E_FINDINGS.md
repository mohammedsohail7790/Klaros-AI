# Klaros live end-to-end test — findings (2026-10-13)

Tested on https://meetklaros.com as the signed-in user, business idea: a dropshipping store for ergonomic home-office accessories.
Account creation and password entry were done by the user (not allowed for me on a live site). I did not publish the website or launch the business.

## Flow result
| Step | Result |
|---|---|
| Landing → idea → sign-up | Works |
| Build: "What are you building?" → Start building | Works, but ~25 s with only a "Starting…" label |
| Discovery questions | Works but weak: Q1 generic ("what systems do you require"), Q2 asked the industry the idea already states, then "no more questions" after one answer |
| Blueprint | Works; extracted customers, products, geography, model. Revenue/Channels/Marketing/etc. all "Missing" |
| "Confirm all" | Works but takes ~60-90 s (12 sequential calls) with no progress indicator |
| Requirements / Recommendations | Works; Stripe suggested and accepted |
| Business Map | Works after a reload (see #6) |
| Website generation | Works but output is a stub (see #8) |
| AI Workforce → Create my AI receptionist | FAILS (see #1) |
| Launch Business | Run with your OK: journey COMPLETED, site published at /w/<tenant>. But Home still showed "Not launched yet" until a reload (stale state) |
| Home / Dashboard | Load; content is generic |

## Problems (highest impact first)
1. **Create my AI receptionist fails**: after ~15 s shows "Set up your business first, so Klaros knows what kind of receptionist to create." even though the Blueprint, Requirements and Map are all complete. No link or hint to what is missing.
2. **Dropshipping is not actually runnable**: Online storefront, Cart, Product pages, Order management and Supplier connection are all "Planned — not available". The user cannot take an order or fulfil one, so "run a real business" stops at planning.
3. **Slow API**: `/integrations` 15 s, `/business-builder/overview` ~9 s, `/operations` ~5 s, others 1-3 s per call (Render + DB latency). Dashboard fan-out is very slow on first load.
4. **No progress feedback** on long actions (Start building ~25 s, Confirm all ~60-90 s, Create receptionist ~15 s). Needs staged progress and optimistic UI.
5. **Wrong business name**: generated site and Home call the business "Halla AI" (the organization name entered at sign-up) rather than anything about the store; Blueprint "Business Identity" = "online store".
6. **Business Map hangs on its skeleton** after in-app navigation from Recommendations; only a hard reload shows it. Its right-most column (Stripe card) is also clipped.
7. **"Looks right — generate my website" doesn't generate**: it lands on /website showing "No website yet" and needs a second "Generate website" click.
8. **Generated website is a stub**: title only, empty subheadline, four product chips, "Who we serve: Remote workers." No product catalog, cart or checkout, no pricing, no real copy.
9. **Home stepper glyphs**: Website step shows "?" and AI workforce shows "8" while Launch is current — state not consistent with the pages' own tracker.
10. **Copy leaks from the medical-tourism vertical**: AI Workforce says "Patient calls only begin…" for an online store (fixed in the redesign branch).
11. **Developer wording shown to users**: "Integration adapter required", "DETERMINISTIC SUMMARY", "AI is PAUSED tenant-wide", "Owner Cockpit … role OWNER", webhook address block (all fixed or collapsed in the redesign branch).
12. **Dashboard is generic home-services**: QuickBooks/jobs/quotes/"Autonomy status: Automatic 33" on a brand-new account (33 automatic actions with no data looks wrong), "AI assistant: openai" shown by vendor name.
13. **Pricing copy jargon**: "Owner Attention Queue", "AI Next Action".

## Environment / repo issues
- Local `backend/dev.db` is out of date: sign-up returns 500 (`knowledge_files.provenance` missing). Run `alembic upgrade head`.
- `backend/.env` contains **live** Stripe (sk_live…), Twilio and OpenAI keys in plaintext. Rotate them and use a secrets store; I blanked them for my local run.
- The local `main` checkout is far behind `origin/main` and dirty; production is built from `origin/main` (+ backend from `deploy/klaros-halla`). The redesign was built in a separate worktree off `origin/main` for that reason.
- Local login/register response shapes differ (`access_token` top-level vs nested under `tokens`) between the working tree and production.

## Redesign delivered (branch `feat/klaros-redesign`, local commit a068577, not pushed)
Design system: indigo/slate tokens, Geist type, Radix Dialog modals, Sonner toasts, new logo, dark sidebar with user menu, ⌘K command palette, new auth layout, marketing header/footer and hero, plain-language copy helper. 460/460 tests pass, `next build` succeeds.
Not yet redesigned page-by-page (inherit the new tokens only): Build/Discovery/Blueprint/Requirements/Map content layouts, Home, Dashboard body, Website editor, Leads/Customers tables.


## Update (after launch)
- Live site is published at https://meetklaros.com/w/4bdb4033-53f8-4918-bbf7-ac81fdb61520 (still the thin stub; the generator fix below needs the backend deployed, then "Regenerate from blueprint").
- **Bug found and fixed**: a stated business description longer than 200 characters was silently discarded by the website generator (cap meant for names), leaving an empty subheadline. Fixed (`website_generation_service.py`), plus: hero headline/subheadline now derived from stated products, customers and region; a trust strip from stated facts; the hero falls back to a "Get in touch" button to the Contact page when no email/phone is stated (renderer change, ships with the frontend).
- Honest limit: with a thin Blueprint the site is still short, because the generator will not invent claims. Richer sites need richer Discovery answers (live Discovery asked 1 useful question).
- Home stepper, hero, KPI tiles, launch panel, Dashboard headings, Integrations cards and the journey stepper were redesigned. 460 frontend tests + all website backend tests pass.
