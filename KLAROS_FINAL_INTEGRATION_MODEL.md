# Klaros AI — Final Integration, Provider, Capability, Credential Architecture

Covers item K. Verified current state: adapter-based framework in `backend/app/integrations/` — interfaces in `base.py`, concrete adapters in `adapters.py`. Real, code-verified clients: Stripe (`stripe_client.py`, live `GET /v1/balance` health check), Twilio, SendGrid, Anthropic, OpenAI, QuickBooks, Google Calendar. Honest, self-disclosed stubs: `ServiceTitanAdapter`, `JobberAdapter` ("OAuth flow not implemented"), `GoogleAdsAdapter`/`MetaAdsAdapter` ("Client not implemented"), `GmailAdapter` ("OAuth not configured"). Angi/Thumbtack/Nextdoor are a generic HMAC webhook normalizer, not live outbound API integrations. Credentials stored via Fernet encryption (`credential_store.py`), keyed by `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`, with a boot-time refusal (`main.py:42-46`) if the key is default/unset.

Frontend: `frontend/app/settings/integrations/page.tsx` (872 lines) is real and substantial — groups providers by category, shows real per-provider connect/disconnect (Stripe key-based, QuickBooks and Google Calendar real OAuth2 with callback-notice handling), synthesizes a single "AI provider" row from whichever of anthropic/openai/groq/deepseek/nvidia/google_ai is active, and shows `PLANNED_OAUTH_PROVIDERS` honestly as `NOT_IMPLEMENTED` with no working Connect button.

## Validated status taxonomy

Two independent axes must never be collapsed into one field:
1. **`implementation_status`** (platform-level, tenant-independent): `REAL` / `STUB` / `WEBHOOK_NORMALIZER` — matches verified current reality exactly (Stripe/QuickBooks/Google Calendar = REAL; Xero/Google Ads/Meta Ads/GBP/ServiceTitan/Jobber = STUB; Angi/Thumbtack/Nextdoor = WEBHOOK_NORMALIZER).
2. **UI/connection status** (per-tenant, derived): `AVAILABLE` / `CONNECTED` / `NOT_CONNECTED` / `RECOMMENDED` / `REQUIRED` / `OPTIONAL` / `COMING_SOON` / `STUB` / `CUSTOM`.

**Derivation rule (hard constraint):** the rendered UI status is `min(implementation_status-derived-ceiling, tenant-connection-derived-status)` — a provider whose `implementation_status == STUB` can **never** render as `CONNECTED`, even if a tenant's `IntegrationConnection.status` was somehow set to `CONNECTED` by a credential-save action with no live verification. `COMING_SOON` is reserved for providers the catalog lists but that have no adapter code at all yet (distinct from `STUB`, which has adapter code that explicitly fails). This directly operationalizes `KLAROS_DO_NOT_BUILD_YET.md` §9 ("do not present stub integrations as production").

## Entity model

- **`IntegrationProviderCatalog`** (new, tenant-independent reference data): `provider_key`, `display_name`, `category`, `implementation_status`, `auth_shape` (OAUTH2/API_KEY/WEBHOOK), `recommended_for_verticals` (array of `VerticalExtension` ids), `health_check_strategy_ref`. Written only by `MANAGE_INTEGRATIONS_CATALOG` (platform-admin permission, kept distinct from `MANAGE_INTEGRATIONS` per KLAROS_ARCHITECTURE_RECONCILIATION.md #3).
- **`IntegrationConnection`** (existing, tenant-scoped, unchanged schema): per-tenant credential + connection state, `status` (existing 5-value `ConnectionStatus` enum on this model, confirmed distinct from the adapter-level 3-value enum per KLAROS_ARCHITECTURE_RECONCILIATION.md #8).
- **`Provider`** (marketplace-generic, see KLAROS_FINAL_DOMAIN_MODEL.md) references `IntegrationProviderCatalog.provider_key` when the provider *is* an integration; vertical-owned `Provider` tables (e.g. Medical Tourism's hospital/clinic `Provider`) are separate, unrelated entities that happen to share a name — never joined or unioned with the integration catalog.
- **`Capability`**: declared function units an integration contributes (e.g. `sync_invoices`, `book_calendar_event`) — used by the Recommendation Engine to match a Blueprint requirement (e.g. "needs calendar booking") to available means (Google Calendar integration vs. a first-party scheduling tool) without hardcoding the match.
- **Credential/Connection**: unchanged Fernet-encrypted flow; OAuth token refresh and API-key rotation continue through existing per-adapter logic.
- **Webhook**: existing HMAC-verified normalizer path for Angi/Thumbtack/Nextdoor is reused as-is for any future webhook-only provider — no new webhook framework.
- **Health check**: adapter-level `check_connection()` (existing `ConnectionStatus` 3-value result) feeds catalog-level `implementation_status` monitoring; a REAL provider that starts failing health checks does not change its `implementation_status` (still REAL — it's a real, currently-erroring integration) but does change its per-tenant `IntegrationConnection.status` to reflect the outage. This is the concrete reason the two enums (Reconciliation #8) must stay separate.
- **Permissions**: `MANAGE_INTEGRATIONS` (existing, tenant-scoped connect/disconnect) vs. `MANAGE_INTEGRATIONS_CATALOG` (new, platform-admin catalog curation) — decision and reasoning in KLAROS_ARCHITECTURE_RECONCILIATION.md #3.

## Recommendation integration

Every catalog-driven `Recommendation` (KLAROS_FINAL_DOMAIN_MODEL.md) targeting an integration carries `source = VerticalExtensionRule | DiscoveryInference`, never a hardcoded per-business-type branch — the `recommended_for_verticals` tag on the catalog row is data a vertical's plugin function reads, not a code path the Recommendation Engine special-cases.
