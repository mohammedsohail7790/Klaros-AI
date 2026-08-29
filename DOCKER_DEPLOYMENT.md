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

## Known gaps in the current Docker setup (static read, Phase 12B)

- Neither `backend/Dockerfile` nor `frontend/Dockerfile` declares a non-root `USER` —
  both containers run as root.
- `frontend/Dockerfile`'s `CMD` is `npm run dev` unconditionally. There is no production
  build stage (`next build` + `next start`) — the image as written is dev-mode-only.
- No `restart:` policy on any `docker-compose.yml` service.
- Postgres/Redis/Temporal ports are all published to the host — convenient for local
  dev, likely undesirable to expose on a real multi-host production deployment.
- `POSTGRES_PASSWORD` defaults to the literal `klaros` via `${POSTGRES_PASSWORD:-klaros}`
  in `docker-compose.yml` if no `.env` override is set — fine for local dev, must be
  overridden for anything real.

What IS correct in the current setup: `postgres`/`redis` have real healthchecks;
`backend`/`worker`/`event-worker` correctly `depends_on` them with
`condition: service_healthy`; every internal service address uses the Docker Compose
service name (`postgres`, `redis`, `temporal`), never `localhost`.

## Prerequisites

```bash
docker --version
docker compose version
```

## Clean startup

```bash
cp .env.example .env
# edit .env: at minimum set a real JWT_SECRET before anything resembling production use
docker compose up -d --build
```

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
