# Klaros production for the Halla pilot — setup and secret rotation

**Status: prepared, NOT deployed.** Nothing here has been run against Render, a production database or Halla. Everything below is what the owner does (or authorizes) next.

Rules that apply to every step: the existing Klaros production/Prisma database is never used; no secret is typed into git, a chat, a log or a command line; values live only in the host's secret store.

## 1. Render service (free web service — acceptable for the pilot only with an uptime pinger)

| Setting | Value |
|---|---|
| Branch | `deploy/klaros-halla` |
| Dockerfile | `backend/Dockerfile` |
| **Docker Command** | `python -m scripts.start` (replaces the old hex one-liner; clear the field of anything else) |
| Health check path | `/health` |
| Uptime pinger | an external monitor hitting `/health` every ≤10 minutes — a sleeping free instance misses Halla's webhooks |

`scripts.start` runs, on every start and idempotently: preflight → database safety check → `alembic upgrade head` → create missing pilot organizations → (re-)save each Halla connection → exec `uvicorn` on `$PORT`. If preflight fails it prints variable NAMES only and exits 2 (Render shows the deploy as failed; nothing is migrated).

## 2. The separate durable pilot database — create it, then verify its identity BEFORE any migration

1. Create a NEW project at Supabase or Neon (free tier) — a new project, never an existing one. Give the database a name that is unmistakably the pilot, e.g. `klaros_halla_pilot`.
2. Make sure `vector` (pgvector) is available: Supabase and Neon both provide it. Migration `0032` runs `CREATE EXTENSION IF NOT EXISTS vector` itself, so the role in `DATABASE_URL` must be allowed to (the project's default owner role is). `scripts.start` refuses to continue if the server does not offer pgvector at all.
3. Take the connection string, change the scheme to `postgresql+asyncpg://`, and store it in Render as `DATABASE_URL` (secret store only).
4. Set the identity variables (non-secret, names/hosts only):
   * `KLAROS_PILOT_DB_NAME` = the pilot database's exact name. **Required in production.** The boot refuses unless the name in `DATABASE_URL` (and `DATABASE_MIGRATION_URL`) equals it AND `SELECT current_database()` on the live connection returns it.
   * `KLAROS_FORBIDDEN_DB_NAMES` and `KLAROS_FORBIDDEN_DB_HOSTS` = comma-separated names / hosts of the existing Klaros production (Prisma) database. Any URL pointing at them is refused before a connection is opened.
5. Verification before the first migration (all enforced by `scripts.start`; nothing is changed until they pass): the identity above matches; the database contains no `_prisma_migrations` table; if it has tables at all it already has Alembic history (a brand-new empty database is the normal first run). A refusal prints the reason and exits 2; Render shows the deploy as failed and nothing was written.
6. **Read-only verification** (run it in a Render Shell, or any shell where the variables are set; it changes nothing, starts nothing, prints no secret): `python -m scripts.start --check`. It reports the database name and host the URLs really point at, that they match `KLAROS_PILOT_DB_NAME` and miss the forbidden lists, both URLs use `postgresql+asyncpg://` and name the same database and host, whether the database is empty or already Klaros-managed, and for pgvector: available, installed, and whether THIS role can create it (superuser, or the extension is installed, or it is a trusted extension and the role has CREATE on the database). A role that cannot create it fails with the exact fix (run `CREATE EXTENSION vector;` once in the provider's SQL editor, or use an owner role in `DATABASE_MIGRATION_URL`).
7. Owner check, independent of the code: in the Supabase/Neon dashboard confirm the project is the new one, and that its host differs from the existing Prisma database's host.

Migration compatibility: head is `0064`; verified on PostgreSQL from empty to head, `downgrade -1` and `upgrade head` again. The pilot database should NOT be shared with any other application.

## 3. Environment (names only — set the values in Render's secret store)

| Variable | Kind | Value / source |
|---|---|---|
| `ENV` | setting | `production` |
| `DATABASE_URL` | **secret** | the pilot database, `postgresql+asyncpg://…` |
| `DATABASE_MIGRATION_URL` | secret, optional | owner-role URL for migrations if different from `DATABASE_URL`; must name the same database |
| `KLAROS_PILOT_DB_NAME` | setting | the pilot database's name |
| `KLAROS_FORBIDDEN_DB_NAMES`, `KLAROS_FORBIDDEN_DB_HOSTS` | setting | the existing Prisma database's name / host |
| `JWT_SECRET` | **secret** | generated on the host: `openssl rand -hex 32` |
| `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` | **secret** | generated on the host: a Fernet key (`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`) |
| `EVENT_TRANSPORT`, `RATE_LIMIT_BACKEND` | setting | `memory`, `memory` (one free instance) |
| `WORKFORCE_ADAPTER` | setting | `halla` |
| `HALLA_API_BASE_URL` | setting | `https://halla-ai-gateway.onrender.com` |
| `HALLA_ALLOWED_HOSTS` | setting | `halla-ai-gateway.onrender.com` |
| `HALLA_API_KEY_HEADER`, `HALLA_API_KEY_SCHEME` | setting | as Halla specifies (owner to confirm) |
| `KLAROS_PUBLIC_API_URL` | setting | this service's https origin, no trailing slash |
| `CORS_ORIGINS` | setting | the frontend origin(s), JSON list |
| `HALLA_PILOT_TENANTS` | setting (JSON, no secrets) | see §4 |
| `HALLA_API_KEY_MEDICAL_TOURISM`, `HALLA_WEBHOOK_SECRET_MEDICAL_TOURISM` | **secrets** | owner / Halla |
| `HALLA_API_KEY_DROPSHIPPING`, `HALLA_WEBHOOK_SECRET_DROPSHIPPING` | **secrets** | owner / Halla (a different value from the other tenant's) |
| `OWNER_PASSWORD_MEDICAL_TOURISM`, `OWNER_PASSWORD_DROPSHIPPING` | secrets, optional | only if `owner_email` is configured |
| `PORT` | set by Render | |
| `OPENAI_API_KEY` | secret | optional until OpenAI is verified |

Startup settings: Docker Command `python -m scripts.start`; health check `/health`; branch `deploy/klaros-halla`; auto-deploy OFF until the owner says go.

Until the owner supplies the values: leave `HALLA_PILOT_TENANTS` and the Halla variables UNSET. The boot then runs migrations and serves with NO tenant, NO Halla connection and NO webhook (a deploy never creates tenants or webhooks on its own).

## 4. The two tenants

Set `HALLA_PILOT_TENANTS` (non-secret JSON; the secrets are only NAMED, never included — a secret inside it makes the boot refuse):

```json
[
 {"slug":"medical-tourism","name":"Medical Tourism Pilot","klaros_tenant_id":"<new uuid>","halla_tenant_id":"<from the owner>",
  "api_key_env":"HALLA_API_KEY_MEDICAL_TOURISM","secret_env":"HALLA_WEBHOOK_SECRET_MEDICAL_TOURISM","safety_profile":"medical_tourism",
  "owner_email":"<owner address>","owner_password_env":"OWNER_PASSWORD_MEDICAL_TOURISM"},
 {"slug":"dropshipping","name":"Dropshipping Pilot","klaros_tenant_id":"<new uuid>","halla_tenant_id":"<from the owner>",
  "api_key_env":"HALLA_API_KEY_DROPSHIPPING","secret_env":"HALLA_WEBHOOK_SECRET_DROPSHIPPING","safety_profile":"dropshipping",
  "owner_email":"<owner address>","owner_password_env":"OWNER_PASSWORD_DROPSHIPPING"}
]
```

Then set the six secret variables it names. Each tenant's webhook URL is `https://<service>/api/v1/webhooks/halla/<klaros_tenant_id>` (no trailing slash — it redirects and Halla does not follow redirects). Each tenant verifies only its own signing secret; an authentic event carrying the other tenant's Halla id is refused (403). The organizations are created once with fixed ids so the URLs never change; an existing organization or owner is never modified (the owner password is not reset by a redeploy).

* **Dropshipping:** on this branch it is a registry entry only. Events create/route leads and human escalations; there is no order, payment, refund or supplier automation, and none is enabled.
* **Medical Tourism:** the Medical Tourism module is NOT auto-enabled and the public intake takes no health fields until it is. **No real patient data until the owner confirms compliance.**
* Pilot organizations start on a 14-day trial (`trial_days` per tenant, 1–365): set it to cover the pilot or set the billing state deliberately, because plan limits apply when a trial ends.

### Safety profiles (required per tenant)

`safety_profile` (`medical_tourism` or `dropshipping`) is recorded, non-secret, on the tenant's Halla connection and does three things, deterministically and without any model:
* the tenant's Halla workforce configuration carries that profile's escalation triggers and rules (Medical Tourism: emergencies, diagnosis, medication, outcome guarantees, unknown doctor/hospital/price, "speak to a person"; Dropshipping: payment disputes, chargebacks, refund decisions, fraud, delivery guarantees, invented price/stock/spec/discount, prohibited products, legal threats);
* the text of each Halla conversation (summary/outcome) is classified; if it needs a person, Halla's "qualified" is NOT applied, no qualified-lead workflow fires, the lead goes to a human (`REQUIRES_HUMAN`) with the CATEGORY only, and the sensitive text is not stored in any event;
* no catalogue, service, price or SKU is invented: `servicesOffered` is empty until the owner supplies the catalogue.
Limit that remains: Klaros enforces these rules on what it receives and stores; it cannot control what Halla's live agents say (Halla's configuration has no safety-policy field). Payment, supplier ordering, refunds, chargebacks, fulfillment and carrier APIs do not exist in Klaros on this branch and stay human-operated.

## 5. Connection states

`CONNECTED` — a real health request to Halla succeeded. `ERROR` — the health request failed; the credential IS saved and signed webhooks are still accepted (only `DISCONNECTED` blocks them). `UNVERIFIED` — saved with `--skip-verify` (no Halla call was made); never shown as connected; a later health check promotes it. A Halla outage at deploy time therefore never stops the boot. Manual tool: `python -m scripts.connect_halla_tenant --klaros-tenant-id <uuid> [--halla-tenant-id <id>] [--skip-verify]` with `HALLA_API_KEY` / `HALLA_WEBHOOK_SECRET` in the environment.

## 6. Secret rotation (no secret is ever printed)

**Halla webhook signing secret** (do it in this order to avoid a window of rejected events):
1. In Halla, create the new webhook secret (or webhook) for the tenant; keep the old one valid if Halla allows overlap.
2. In Render, replace the value of `HALLA_WEBHOOK_SECRET_<TENANT>` (the secret store, never a command line). Redeploy — `scripts.start` re-saves the connection with the new secret; the old secret stops verifying immediately.
3. Switch Halla to the new secret. Events signed with the old one in the gap get 401 and are retried by Halla after the switch. Check `halla connection CONNECTED` for that tenant in the deploy log (ids are masked).
4. Revoke the old secret in Halla.

**Halla API key:** replace `HALLA_API_KEY_<TENANT>` in Render and redeploy; outbound calls use the new key from the next request.

**Klaros `JWT_SECRET`:** replace and redeploy; every user is signed out (tokens signed with the old secret stop working). **`INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`:** there is a single key and no overlap support — changing it makes every stored credential unreadable. `scripts.start` re-saves the Halla credentials from the environment on that same deploy, so Halla self-heals; credentials of any other integration a tenant stored (e.g. QuickBooks) must be reconnected by their owners.

**Owner password:** change it in the app; the environment variable is only used to create the owner the first time.

If a secret may have leaked: rotate it per the above immediately, then check the webhook log for rejected deliveries (`halla_webhook_non_2xx` lines carry status, route, request id, tenant id and event id — never bodies or signatures).

## 7. OpenAI (deferred)

No production key is available to me, so **OPENAI = BLOCKED/DEFERRED**. When the owner puts the key in the host's environment I run exactly ONE minimal request (a model-list or one-token completion) to confirm key, model availability and credits, never print the key, and report BLOCKED/DEFERRED again on a 429. Billing is never changed.

## 8. Twilio — one number per business (nothing purchased, no call made)

For EACH business (Medical Tourism, Dropshipping), once the owner buys/assigns the number in the Twilio console:
* **A call comes in → Voice webhook, HTTP POST** to the Halla production gateway: `https://halla-ai-gateway.onrender.com/<Halla voice-webhook path for that tenant>`
* **Media stream, WebSocket** (returned by Halla's TwiML `<Connect><Stream>`): `wss://halla-ai-gateway.onrender.com/<Halla media-stream path>`
* Status callback, if Halla uses one: the Halla status-callback path on the same host.
The three `<…path…>` values are Halla-owned; they are not in this repository and I did not invent them — Halla must supply them (they may differ per tenant). Klaros's own `/api/v1/webhooks/twilio/...` routes are NOT used for the Halla pilot. A real test call happens only after the owner explicitly approves it, to a number the owner designates, with no real customer data.

## 9. Before going live (owner checklist)

Always-on hosting or a working pinger · the durable database created and its `vector` extension enabled · both webhooks created in Halla with the URLs above · compliance confirmed before any real patient data · billing state set for the pilot · production restricted DB role provisioned (`scripts/db/provision_app_role.py`) · remaining blockers in `KLAROS_HALLA_SECURITY_AND_WEBHOOK_READINESS.md`.
