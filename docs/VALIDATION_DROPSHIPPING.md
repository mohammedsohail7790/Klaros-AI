# Validation — Dropshipping (genericity check, not a product)

Goal: show that Klaros understands a completely different business **without changing the core**.
Dropshipping is a validation business only. No dropshipping module, supplier integration, storefront,
inventory sync, order management or fulfilment engine was built, and Inventory Source was not
integrated.

> This supersedes the *conceptual* walkthrough in `KLAROS_DROPSHIPPING_VALIDATION.md` for what is
> actually executable today; that document still describes the longer-term target.

## How to run it

Same steps as `DEMO_MEDICAL_TOURISM.md` steps 1–7, using the idea
*"I want to start a dropshipping business using a supplier catalog."* (offered as an example chip on
the entry screen — chips only fill the text box).

## What was observed (real PostgreSQL, real AI provider)

| | Medical tourism | Dropshipping |
|---|---|---|
| Requirements | provider directory, treatment catalog, patient lead capture & qualification, consultation scheduling, referral workflow, commission tracking, CRM, payments, marketing, analytics, communication | online storefront, supplier connection, product catalog, inventory, pricing, order management, fulfilment, payments, marketing, analytics, customer communication |
| Industry module offered | yes (explicit enable action) | none — the dropshipping registry row has no capabilities, so there is nothing to enable; the enable endpoint refuses it (409) and it is never offered as enable-able |
| Readiness | many READY (native modules) | storefront, supplier, catalog, inventory, pricing, orders, fulfilment all **PLANNED** |
| Outside parties on the map | Providers & partners | Supplier (PLANNED) |
| Revenue node | commission | "margin between supplier cost and retail price" (the user's own words) |
| Integrations | Stripe, Google Calendar (not connected) | Stripe (not connected) |

Same engine, same code path, different blueprint, requirements, recommendations and map.

## Automated evidence

`backend/tests/test_business_builder_api.py` runs a dropshipping-shaped and a consulting-shaped
business through the identical pipeline and asserts different requirement sets and maps, that every
planned capability is PLANNED (never READY/CONNECTED), and that nothing is CONNECTED for a new tenant.
`test_capability_vocabulary.py` asserts the Business Builder modules never name a vertical.

## Intentionally not built

Supplier API integration · Shopify · inventory synchronisation · order management · fulfilment ·
payments flow for orders · Inventory Source.

## V2 re-run (live PostgreSQL, real AI provider)

"I want to start a dropshipping business using supplier X." plus one answer produced 11 capability claims →
requirements storefront, supplier_integration, product_catalog, inventory, pricing, order_management, fulfillment,
payments (Stripe), accounting (QuickBooks/Xero), marketing, communication; the Business Map has a Supplier actor and
the AI-workforce node ("Integration required"); the operations console shows no industry contribution. A one-sentence
idea alone did not complete Discovery, and once the industry section had to be filled in the Blueprint editor — see
"AI question quality" in BUSINESS_BUILDER.md. No Inventory Source work was done.
