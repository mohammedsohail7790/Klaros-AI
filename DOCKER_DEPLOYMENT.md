# Docker Deployment

Status: the commands below are **not verified in this project's history** — no Docker
daemon has ever been available in the sandbox this project was built in (confirmed
Phase 12B: no `docker`, `podman`, `colima`, `lima`, or `brew`). They are derived directly
from reading `docker-compose.yml` and both Dockerfiles, not copied from a working run.
Run them yourself and report back if anything here is wrong.

What Phase 12B verified INSTEAD of Docker: real PostgreSQL 16.2 and real Redis, run as
actual compiled binaries via the `pgserver`/`redislite` Python packages (no Docker
needed) — the full backend test suite (290 tests), a live HTTP business-flow run, and
a browser-driven frontend session all ran against those real engines. See
`PRODUCTION_AUDIT.md` and `PRODUCTION_READINESS.md` for what that did and did not prove.
Docker itself — image builds, container networking, `depends_on`/healthcheck ordering
under a real daemon — remains unverified.

## Status after Phase 33A deployment-artifact hardening

Corrections to this section's prior (Phase 12B) claims, and what changed:

- **Corrected**: `frontend/Dockerfile`'s `CMD` is `npm run start`, preceded by a real
  `RUN npm run build` — a genuine production build stage already exists in the Dockerfile
  itself. The prior claim that it was "dev-mode-only" was stale; `docker-compose.yml`
  overrides the Dockerfile's command to `npm run dev` for local hot-reload convenience
  only — `docker-compose.prod.yml` (new, Phase 33A) restores the Dockerfile's real
  production command for an actual deployment.
- **Fixed** (Phase 33A): every service in `docker-compose.yml` now has
  `restart: unless-stopped`.
- **Fixed** (Phase 33A): `postgres`/`redis`/`temporal`/`temporal-ui` ports are now bound to
  `127.0.0.1` only (`127.0.0.1:5432:5432`, etc.) instead of published to every interface —
  still reachable from the same host (local dev unaffected), not reachable from outside it.
- **Fixed** (Phase 33A): `docker-compose.prod.yml` (new) removes the `POSTGRES_PASSWORD`
  default fallback for production use — `docker compose -f docker-compose.yml -f
  docker-compose.prod.yml up` now fails loudly if `POSTGRES_PASSWORD` isn't set, instead of
  silently using the `klaros` dev default.
- **Fixed** (Phase 33A): both `backend/.dockerignore` and `frontend/.dockerignore` now
  exist (neither did before) — the build context no longer includes `.venv/`,
  `node_modules/`, `.git/`, `__pycache__/`, any `.env*` file, or the test suite.
- **Fixed** (Phase 33A): `backend/requirements.txt` no longer installs `pytest`,
  `pytest-asyncio`, or `pip-audit` into the production image — those moved to
  `backend/requirements-dev.txt` (`pip install -r requirements.txt -r requirements-dev.txt`
  for local development).
- **Still open, deliberately not changed this phase**: neither Dockerfile declares a
  non-root `USER`. Investigated and NOT implemented — `docker-compose.yml` bind-mounts
  `./backend:/app` and `./frontend:/app` for local dev hot-reload, and the backend's local
  storage adapter (`app/storage/local_adapter.py`, active whenever
  `OBJECT_STORAGE_ENDPOINT` is unset) writes to that same bind-mounted tree
  (`STORAGE_LOCAL_ROOT`). A non-root container UID that doesn't match the host user's UID
  would risk silent permission-denied write failures against that bind mount in local dev.
  This needs a real Docker daemon to verify safely (none available in this project's
  sandbox) and is flagged here as a SHOULD HAVE rather than implemented blind.

What IS correct in the current setup: `postgres`/`redis` have real healthchecks;
`backend`/`worker`/`event-worker` correctly `depends_on` them with
`condition: service_healthy`; every internal service address uses the Docker Compose
service name (`postgres`, `redis`, `temporal`), never `localhost`.

None of the above has been run against a real Docker daemon — same standing limitation as
every prior phase (no `docker`/`podman`/`colima`/`lima` available in this sandbox). The YAML
syntax of both compose files was validated with `pyyaml`; the actual `docker compose config`
merge and a real `docker build` remain unverified.

## Prerequisites

```bash
docker --version
docker compose version
```

## Clean startup (local development)

```bash
cp .env.example .env
# edit .env: at minimum set a real JWT_SECRET before anything resembling production use
docker compose up -d --build
```

## Production startup

```bash
cp .env.example .env
# edit .env: real JWT_SECRET, INTEGRATION_CREDENTIAL_ENCRYPTION_KEY, POSTGRES_PASSWORD,
# CORS_ORIGINS, FRONTEND_BASE_URL, and every provider credential actually in use.
docker compose -f docker-compose.yml -f docker-compose.prod.yml config   # sanity-check the merge first
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

See `KLAROS_PRODUCTION_DEPLOYMENT_PLAN.md` for the full recommended architecture — in
particular, a real deployment should generally point `DATABASE_URL`/`REDIS_URL` at managed
Postgres/Redis instead of running this file's `postgres`/`redis` services at all.

## Run database migrations

Migrations do not run automatically on container start. Run them explicitly after the
`postgres` service is healthy:

```bash
docker compose exec backend alembic upgrade head
```

## Check health and readiness

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

`/health` is liveness only (always `ok` if the process is up). `/ready` (added Phase
12B) actually checks Postgres and Redis and returns HTTP 503 with a `checks` breakdown
if either is unreachable — this is the one to point an orchestrator's readiness probe
at, not `/health`.

## Provider webhooks (Phase 12C)

`POST /api/v1/webhooks/stripe` and `POST /api/v1/webhooks/twilio/status` need to be
reachable from the public internet for Stripe/Twilio to actually deliver to them — a
`docker compose up` on a laptop is not reachable by either provider without a tunnel
(`stripe listen --forward-to`, `ngrok`, or a real deployed host with a public DNS name
and TLS). Configure each provider's dashboard to point at
`https://<your-real-domain>/api/v1/webhooks/stripe` /
`.../api/v1/webhooks/twilio/status` respectively. See `INTEGRATIONS.md` for the exact
webhook setup steps per provider — this has not been tested behind Docker's own
networking in this project's history (Docker itself is unverified; see the gaps section
above).

## View logs

```bash
docker compose logs -f backend
docker compose logs -f worker          # Temporal worker
docker compose logs -f event-worker    # event-bus consumer
docker compose logs -f postgres
docker compose logs -f redis
```

## Inspect the database

```bash
docker compose exec postgres psql -U klaros -d klaros -c '\dt'
docker compose exec postgres psql -U klaros -d klaros -c 'SELECT count(*) FROM jobs;'
```

## Inspect Redis

```bash
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli xinfo groups klaros.events.job.dispatched   # example stream
```

## Inspect Temporal

Temporal UI: http://localhost:8080

```bash
docker compose exec worker python -c "
import asyncio
from temporalio.client import Client
async def main():
    client = await Client.connect('temporal:7233')
    print(await client.list_workflows().__anext__())
asyncio.run(main())
"
```

## Restart a single service

```bash
docker compose restart backend
docker compose restart worker
docker compose restart event-worker
```

Restarting `worker`/`event-worker` should not lose in-flight work — Temporal persists
workflow state independently of any one worker process, and events live durably in
Postgres before ever reaching the transport (see `EventBus.publish()` and
`EventBus.reconcile_stuck_events()`). This claim is verified at the application layer
(Phase 12B's real-Temporal and real-Redis tests) but not yet re-verified under an
actual Docker restart of these specific containers.

## Persistence

`postgres_data` is a named Docker volume — data survives `docker compose down` (without
`-v`) and container restarts. It does NOT survive `docker compose down -v`, which
deletes volumes.

## Full shutdown

```bash
docker compose down          # stop, keep data
docker compose down -v       # stop, DELETE all data (Postgres, Redis) — destructive
```

## Dev reset (wipe and start clean)

```bash
docker compose down -v
docker compose up -d --build
docker compose exec backend alembic upgrade head
```
