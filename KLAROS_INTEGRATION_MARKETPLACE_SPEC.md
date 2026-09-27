# Klaros — Integration Marketplace Specification

Status: design proposal. No schema or code was created. Verified current state: `IntegrationConnection` (`backend/app/models/integration.py:61-94`) has fields `provider`, `status`, `encrypted_credential`, `external_account_id`, `scopes`, `connection_metadata` (JSON), `last_verified_at`, `last_error`, `created_by`, unique on `(tenant_id, provider)`. Its `ConnectionStatus` enum (`integration.py:53-58`) has exactly `NOT_CONNECTED`, `CONNECTING`, `CONNECTED`, `ERROR`, `DISCONNECTED` — **no `AVAILABLE`/`STUB`/`RECOMMENDED` values exist today**. A second, differently-scoped enum also named `ConnectionStatus` exists in `backend/app/integrations/base.py:14-17` with only `NOT_CONNECTED`/`CONNECTED`/`ERROR`, used by the platform-level `IntegrationProvider` ABC (`get_status()`/`check_status()`) for shared-credential providers (Stripe/OpenAI/etc.) — a separate, smaller status space from the per-tenant `IntegrationConnection` model. Neither enum is the marketplace catalog this spec defines.

## 1. Principle

The marketplace is a **new metadata/catalog layer read alongside, never instead of**, the existing `IntegrationConnection` per-tenant connection record. `IntegrationConnection` continues to be the source of truth for "is tenant X actually connected to provider Y, with what credential" — unchanged. The marketplace adds "what providers exist, what do they do, are they real or a stub, does this tenant's Blueprint suggest they need it."

## 2. New entity: `IntegrationProviderCatalog` (one row per provider, tenant-independent, seeded/maintained by Klaros, not per-tenant data)

| Field | Type | Notes |
|---|---|---|
| `provider_key` | string, PK | e.g. `stripe`, `quickbooks`, `google_calendar`, `xero`, `google_ads` |
| `display_name` | string | |
| `category` | enum | `PAYMENTS`, `ACCOUNTING`, `CALENDAR`, `COMMUNICATION`, `ADVERTISING`, `CRM_EXTERNAL`, `FIELD_SERVICE`, `SUPPLIER`, `OTHER` |
| `capabilities` | string[] | matched against `BlueprintSection[REQUIRED_CAPABILITIES]` (`KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §5), e.g. `["payment_processing", "subscription_billing"]` for Stripe |
| `auth_method` | enum | `OAUTH2`, `API_KEY`, `HMAC_WEBHOOK`, `NONE` |
| `oauth_supported` | bool | |
| `webhook_supported` | bool | |
| `tenant_scope` | enum | `PER_TENANT_CREDENTIAL` (QuickBooks, Google Calendar, tenant's own Stripe) or `PLATFORM_SHARED_CREDENTIAL` (platform `STRIPE_SECRET_KEY`) — mirrors the real dual-credential-path design already in the codebase (audit §14) |
| `required_credentials` | JSON schema | drives the connect-flow form |
| `availability` | enum | `GENERALLY_AVAILABLE`, `COMING_SOON`, `DEPRECATED` |
| **`implementation_status`** | enum | **`REAL`, `STUB`, `WEBHOOK_NORMALIZER`** — see §3, this is the field that must never be fudged |
| `configuration_schema` | JSON schema | per-provider config beyond credentials (e.g. QuickBooks sandbox/production) |
| `health_check_supported` | bool | whether `verify_connection`-equivalent exists (real today for Stripe, `stripe_client.py:185`) |
| `supported_actions` | string[] | tool names this provider backs, cross-referenced against `ToolRegistry` |
| `supported_events` | string[] | webhook/event types this provider can emit into `EventBus` |

## 3. `implementation_status` — mapped against verified current reality

| Provider | `implementation_status` | Evidence |
|---|---|---|
| Stripe | `REAL` | Direct `httpx` calls, real webhook verification (`stripe_client.py:40,349`, verified in prior audit) |
| QuickBooks | `REAL` | Real OAuth2 app, client code read |
| Google Calendar | `REAL` | Real OAuth2, two-way sync |
| Twilio | `REAL` (voice), `PARTIAL` for non-voice channels — surfaced as a `capabilities` subset, not a blanket status | Voice path traced; SMS breadth not independently verified |
| Xero, Google Ads, Meta Ads, Google Business Profile, ServiceTitan, Jobber | `STUB` | `.env.example:82-85` explicit disclosure; no client file exists for Xero/Ads/GBP/ServiceTitan/Jobber (confirmed absent from `backend/app/integrations/`) |
| Angi, Thumbtack, Nextdoor | `WEBHOOK_NORMALIZER` | `marketplace_adapters.py:1-29` — real generic HMAC webhook + field-map, explicitly not a live provider API |
| SendGrid | `REAL` (per code disclosure, not independently re-verified) | flag `verification_note: "stated real, not independently code-read"` |

**Rule (enforced at the API layer, not just documentation)**: any provider whose `implementation_status != REAL` must render with a visually distinct badge in the frontend marketplace UI and must never appear in a Recommendation with a `required=True` flag unless the recommendation text itself explains the limitation (e.g. "Angi lead capture available via webhook — requires a Zapier or similar bridge, not a direct Angi connection"). This directly closes the P2 gap the original audit flagged (§31: "worth checking that the UI doesn't imply these are live").

## 4. Connection status (per tenant) — extend, do not replace, `IntegrationConnection.status`

The existing 5-value `ConnectionStatus` (`NOT_CONNECTED/CONNECTING/CONNECTED/ERROR/DISCONNECTED`) remains the connection-lifecycle truth. The marketplace UI status shown to a user is a **derived** combination of `IntegrationProviderCatalog.implementation_status` + the tenant's own `IntegrationConnection.status` (or its absence) + whether the Recommendation Engine flagged it:

| Derived UI status | Condition |
|---|---|
| `CONNECTED` | `IntegrationConnection.status = CONNECTED` |
| `RECOMMENDED` | No `IntegrationConnection` row; provider's `capabilities` intersects the tenant's Blueprint `REQUIRED_CAPABILITIES`; `implementation_status = REAL` |
| `REQUIRED` | Same as above, and the capability is marked non-optional by the Recommendation Engine (e.g. payments for any revenue-collecting business model) |
| `OPTIONAL` | Capability match exists but the Recommendation Engine marked it non-essential |
| `AVAILABLE` | No Blueprint match, but provider is `GENERALLY_AVAILABLE` and `REAL` — user can still manually connect (mirrors today's actual UX, where a user manually picks from a fixed list) |
| `NOT_CONNECTED` | `IntegrationConnection.status = NOT_CONNECTED` or `DISCONNECTED` |
| `COMING_SOON` | `IntegrationProviderCatalog.availability = COMING_SOON` |
| `STUB` | `implementation_status = STUB`, regardless of connection attempts — a tenant can still technically submit credentials (existing behavior, `.env.example` disclosure), but the UI must show `STUB`, never `CONNECTED`-looking, even if `IntegrationConnection.status` happens to say `CONNECTED` from a credential-save action with no live verification behind it |
| `CUSTOM` | Tenant-configured `marketplace_adapters.py`-style webhook normalizer instance |

## 5. Recommendation → Marketplace contract

The Recommendation Engine (`KLAROS_TARGET_ARCHITECTURE.md` §6) produces `Recommendation` rows (new table): `id`, `tenant_id`, `blueprint_id`, `capability` (matches `IntegrationProviderCatalog.capabilities`), `provider_candidates` (ranked list of `provider_key`, computed by capability-tag intersection — deterministic, not a second LLM call per recommendation), `reason` (text, template-filled from the matching Blueprint field, e.g. "Your business model requires online payment collection ({{source_field}})"), `evidence` (FK to the specific `BlueprintClaim` that triggered this), `required` (bool), `estimated_complexity` (LOW/MEDIUM/HIGH, static per-provider metadata, not AI-guessed), `user_approval_state` (PENDING/ACCEPTED/DISMISSED), `connection_state` (mirrors the derived UI status in §4 at time of last read).

## 6. Migration impact

New tables only (`IntegrationProviderCatalog`, `Recommendation`); zero changes to `IntegrationConnection`, `credential_store.py`, or any existing OAuth flow. The catalog table is seeded via a data migration (Alembic, additive-only, populating rows for the 6 real + 6 stub + 3 webhook-normalizer providers already in the codebase) rather than application code, so it can be updated without a deploy for metadata-only changes (e.g. `availability` flip when Xero moves from `STUB` to `REAL`).

## 7. Risks

- **Metadata drift**: `IntegrationProviderCatalog.implementation_status` must be updated the moment a stub becomes real (e.g. Xero client code lands) — recommend a CI check that greps `backend/app/integrations/` for a client file matching each `REAL`-status `provider_key` and fails the build if one goes missing, catching drift in the other direction too.
- **False confidence from `supported_actions`**: this list must be generated from actual `ToolRegistry` registrations (a build-time introspection script), never hand-maintained prose, to avoid claiming a capability the tool layer doesn't actually expose.

## Cross-references

`KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §5, `KLAROS_TARGET_ARCHITECTURE.md` §6, `KLAROS_GAP_ANALYSIS.md` §3.4, `KLAROS_DO_NOT_BUILD_YET.md` §9.
