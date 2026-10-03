# Klaros ↔ Halla — go-live runbook

Status date: 2026-10-03. Classification: **REAL** = a real request happened · **NOT TESTED** · **BLOCKED**.

## Where things stand

| Phase | Status |
|---|---|
| 1. Deploy Klaros at a public HTTPS origin | **BLOCKED — needs hosting + DNS access I do not have** (see below) |
| 2. Tenant UUID + webhook URL | Format fixed; tenant does not exist until Klaros is deployed. Route behaviour checked locally (REAL, local only) |
| 3. Register Halla webhook / connect | Ready for you to run (snippet below) once Phase 1 is done |
| 4. Live verification | NOT TESTED — nothing to test against until 1–3 are done |

### What blocks Phase 1 (exact)
This machine has no hosting CLI or credentials (no vercel/render/fly/railway/aws/gcloud/docker), no DNS access, and no cloud account.
`api.meetklaros.com` has **no DNS record** today. `meetklaros.com` is a Vercel-hosted frontend (apex A record 76.76.21.21); its nameservers are
GoDaddy (`ns59/ns60.domaincontrol.com`). So the DNS record and the backend host both have to be created by you.

**You need to provide / do:**
1. A host for the FastAPI backend that can run Python 3.12 (or Docker) with a **PostgreSQL ≥ 15 that has the `vector` extension**, and a **Redis ≥ 5**
   (`docker-compose.prod.yml` describes the intended stack; it has never been run on a real daemon — see `DOCKER_DEPLOYMENT.md`).
2. A DNS record for `api.meetklaros.com` at GoDaddy pointing at that host (CNAME to the host's hostname, or an A record), and a TLS certificate
   for it (most platforms issue it automatically once DNS resolves).
3. Production environment variables (names only; set the values in the host's secret store): `ENV=production`, `DATABASE_URL`
   (restricted app role), `DATABASE_MIGRATION_URL` (owner role), `REDIS_URL`, `JWT_SECRET`, `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY` (a real random
   value — without it credentials are encrypted with a publicly known key), `CORS_ORIGINS=["https://meetklaros.com"]`, `FRONTEND_BASE_URL`,
   plus the Halla settings: `WORKFORCE_ADAPTER=halla`, `HALLA_API_BASE_URL=https://gateway.hallaai.com`, `HALLA_API_KEY_HEADER=Authorization`,
   `HALLA_API_KEY_SCHEME=Bearer`, `HALLA_ALLOWED_HOSTS=gateway.hallaai.com`, `KLAROS_PUBLIC_API_URL=https://api.meetklaros.com`.
4. Run `alembic upgrade head` (owner role, to head `0064`), then `python -m scripts.db.provision_app_role`.
5. Make sure the proxy forwards **`/api/v1/webhooks/halla/<uuid>` exactly as sent** (no rewrite, no trailing slash, no http→https hop on that path):
   Halla does not follow redirects.

When that is done, tell me and I will run Phases 1.2–1.4 and 2 against the real origin.

## What I verified locally (REAL, but against a local backend — not production)
Real Klaros backend on real PostgreSQL, real adapter enabled, tenant connected with throw-away values:

| Request | Answer |
|---|---|
| `POST /api/v1/webhooks/halla/<tenant>` unsigned | **401** directly, no redirect |
| `POST …/<tenant>/` (trailing slash) | **307 redirect** — so register the URL **without** a trailing slash |
| `GET …/<tenant>` | 405 |
| `POST …/<unknown tenant>` | 404 |

Note: a tenant that has **not** connected Halla yet answers 404 (deliberately indistinguishable from "unknown"), so the "unsigned → 401/403" check is only
meaningful **after** the tenant is connected (Phase 3, step 8).

## Phase 3 — register the webhook (you run this; the key never leaves your clipboard)

Put your Halla API key (`sk_calliq_…`) on the clipboard, set `$tenant` to your Klaros tenant UUID (the Workforce page shows the whole URL once
`KLAROS_PUBLIC_API_URL` is set), and run in PowerShell:

```powershell
$tenant = '<KLAROS_TENANT_UUID>'
$key = (Get-Clipboard).Trim()
if (-not $key.StartsWith('sk_calliq_')) { throw 'The clipboard does not hold a Halla API key (expected sk_calliq_...).' }
$url = "https://api.meetklaros.com/api/v1/webhooks/halla/$tenant"      # no trailing slash
$events = 'lead.created','lead.updated','lead.qualified','lead.escalated','call.completed','appointment.confirmed','appointment.rescheduled','appointment.cancelled'
$body = @{ name = 'Klaros'; url = $url; events = $events } | ConvertTo-Json
try {
  $r = Invoke-RestMethod -Method Post -Uri 'https://gateway.hallaai.com/api/v1/webhooks' `
        -Headers @{ Authorization = "Bearer $key" } -ContentType 'application/json' -Body $body
} finally { Remove-Variable key -ErrorAction SilentlyContinue }
$d = if ($r.PSObject.Properties.Name -contains 'data' -and $r.data) { $r.data } else { $r }
# find the signing secret in the response WITHOUT printing it (the field name is not documented to me)
function Find-Secret($o) { foreach ($p in $o.PSObject.Properties) { if ($p.Name -match '(?i)secret' -and $p.Value -is [string]) { return $p.Value } elseif ($p.Value -is [psobject] -and $p.Value -isnot [string]) { $s = Find-Secret $p.Value; if ($s) { return $s } } } }
$secret = Find-Secret $d; if (-not $secret) { $secret = Find-Secret $r }
if ($secret) { Set-Clipboard -Value $secret; Remove-Variable secret; Write-Host 'Signing secret copied to the clipboard (not printed).' }
else { Write-Host ('No secret field found. Response fields: ' + (($d.PSObject.Properties.Name) -join ', ')) }
Write-Host ('webhook id : ' + $d.id)
Write-Host ('active     : ' + ($(if ($null -ne $d.active) { $d.active } else { $d.isActive })))
```

(macOS/zsh equivalent: use `pbpaste` / `pbcopy` with `curl` and `jq`; the idea is identical — never echo the key or the secret.)

## Phase 3, step 8 — connect Klaros (you type the secrets)
Either **Workforce page → Connect Halla** (fields: Halla tenant ID `f90e10ca-e975-4bf6-bc8d-97d7318cd9da`, API key, signing secret from the clipboard), or on the
host: `HALLA_API_KEY=… HALLA_WEBHOOK_SECRET=… python -m scripts.connect_halla_tenant --klaros-tenant-id <uuid> --halla-tenant-id f90e10ca-e975-4bf6-bc8d-97d7318cd9da`.
Afterwards clear the clipboard (`Set-Clipboard $null`). "Connected" appears only after Klaros's real health request succeeds.

## Phase 4 — what I will run once connected (all real requests)
Workforce GET → PUT (a harmless " (test)" suffix, then restored); agents GET (confirm no `systemPrompt`/`transferNumber`); one synthetic lead
`KLAROS-LIVE-TEST-<timestamp>` via Klaros (`klarosLeadId` round-trip, update, no duplicate); signed `lead.created`/`lead.updated` arriving at Klaros
(signature, tenant, persistence, replay dedupe); negative tests (invalid signature, stale timestamp, modified body, unknown tenant, replay).
**NOT TESTED live unless a safe real event exists:** `lead.qualified`, `call.completed`, `lead.escalated`, appointments, outbound calls (no call is placed).

## Deleting the synthetic test lead afterwards
Klaros: open the lead in Leads (it is named `KLAROS-LIVE-TEST-…`) and remove/close it there. Halla: delete it in the Halla dashboard (I do not know of a
documented delete endpoint for leads, so I will not call one). Nothing else is created.
