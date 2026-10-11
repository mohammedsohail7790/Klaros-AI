# Event durability (Klaros event bus + worker)

What happens to an event — for example a Halla `lead.escalated` human handoff — when a worker, Redis or the network fails. Written from the code and the tests in `backend/tests/test_event_durability.py`; every claim names the evidence.

## The model
1. **Postgres is the source of truth.** `EventBus.publish` commits the `events` row first, then pushes a pointer to the transport. If the transport is down the call still succeeds and the event is delivered later (`test_an_event_published_while_the_transport_is_down_is_stored_and_delivered_later`).
2. **Redis Streams (or the in-memory transport) only carries pointers.** One stream per event type, one consumer group per handler.
3. **A worker** (`python -m app.events.worker`, or in-process, see below) reads, runs the handler and acks.
4. **Delivery is AT LEAST ONCE.** A handler can run twice after a crash that falls between its side effect and its SUCCESS commit. "Exactly once" holds for **business effects** only where the handler is idempotent. The handoff path is: workflow dispatch is keyed on (automation version, source event); the test `…handoff_still_produces_exactly_one_workflow_run_and_one_notification` and the OS-level `kill -9` run prove one run and one notification.

## Recovery paths (what recovers what)
| Failure | Recovered by | Evidence |
|---|---|---|
| Worker read a message, died before acking (Redis) | `XAUTOCLAIM` takeover of messages idle ≥ `EVENT_PENDING_RECLAIM_IDLE_MS` (a plain read never returns them) | `test_redis_a_message_read_by_a_crashed_consumer_is_taken_over_and_processed_once` |
| Worker claimed the event, died mid-handler | atomic lease on the (event, handler) record (`EVENT_PROCESSING_LEASE_SECONDS`); a delivery inside the lease backs off **without acking**, so the message stays pending and is taken over once the lease expires | `…blocked_by_a_live_lease_stays_pending_and_is_taken_over…` |
| Same, but the message is gone | the relay (`reconcile_stuck_events`) re-sends events with an unfinished record whose lease expired | `test_a_worker_that_dies_after_claiming_an_event_is_recovered_once_the_lease_expires` |
| Redis restarts empty / loses the stream | the relay re-sends `PUBLISHED` events older than 30 s from Postgres | OS-level run, scenario 2 |
| Two workers get the same event | the lease compare-and-set: exactly one runs the handler | `test_two_simultaneous_deliveries_of_an_abandoned_event_run_the_handler_once` (SQLite and PostgreSQL) |
| Handler fails | bounded retries (3), then DEAD_LETTER; a redelivery never re-runs or double-records it; `replay()` is the way back | `test_retries_dead_letter_once_and_a_redelivered_dead_letter_is_not_rerun` |
| Handoff workflow cannot start | the dispatch handler now **raises** (it used to swallow the error and report success, silently losing the handoff); it retries, then dead-letters visibly | `test_a_transient_failure_starting_the_handoff_workflow_is_retried_not_swallowed`, `…permanent…dead_lettered_visibly` |
| Halla webhook receiver worker dies | lease on `webhook_events` (separate mechanism, `HALLA_WEBHOOK_LEASE_SECONDS`) | `tests/test_halla_webhook_recovery.py` |

## Defects found and fixed while building this (all would have affected a Redis deployment)
- A message read but never acked was never redelivered (Redis `>` reads only new messages).
- An event left in `PROCESSING` by a crash was never re-sent (the relay only looked at `PUBLISHED`).
- Any redelivery re-ran an unfinished handler immediately, even while another worker was still running it.
- A redelivered dead-lettered event wrote a second dead-letter row.
- `publish()` raised when Redis blipped after the event was saved, inviting a duplicate retry of an event that was already durable.
- With the restricted `klaros_app` database role (real RLS) the worker could not read **any** event: it read by primary key before setting a tenant. It now stamps the tenant carried in the stream message first.
- The dispatch handler hid workflow-start failures (above).
- A first version of the new relay query used `SELECT DISTINCT` over `events` and failed on PostgreSQL (the `payload` column is `json`): caught by running the OS-level test, fixed with a subquery.

## Settings
| Variable | Meaning | Default |
|---|---|---|
| `EVENT_TRANSPORT` | `redis` or `memory`. A separate worker **refuses** `memory` and refuses an unreachable Redis (it never falls back silently). | `redis` |
| `REDIS_URL` | the Klaros service's **own** Redis. Never another product's. Never printed or logged. | local |
| `EVENT_PROCESSING_LEASE_SECONDS` | a handler run unfinished this long is presumed dead | 300 |
| `EVENT_PENDING_RECLAIM_IDLE_MS` | a delivered-but-unacked message idle this long is taken over | 60000 |
| `EVENT_WORKER_POLL_SECONDS` | tick interval | 1.0 |
| `EVENT_WORKER_CONSUMER_NAME` | Redis consumer name (same name after a restart is fine) | `worker-1` |
| `EVENT_WORKER_IN_PROCESS` | run the worker inside the API process although the transport is Redis (free-tier staging only) | `false` |
| `PORT` / `EVENT_WORKER_HEALTH_PORT` | if set, the worker serves `GET /health` (200 while ticking, 503 if stalled; counters only) | unset |

Visibility: `GET /ready` on the API adds `event_worker` (`ok (heartbeat Ns ago)` or `no_heartbeat`) for the Redis transport. It is informational and does not fail readiness: the API can serve without the worker, but then events are queued, not processed, and that must be visible.

## Deploying on Render
Separate worker service (production shape):
- Docker command: `python -m app.events.worker`; same image and the same environment as the API **except** it needs no inbound traffic: `DATABASE_URL`, `JWT_SECRET`, `INTEGRATION_CREDENTIAL_ENCRYPTION_KEY`, `ENV`, `WORKFORCE_ADAPTER`, the `HALLA_*` variables, `EVENT_TRANSPORT=redis`, `REDIS_URL`.
- A Render **Background Worker** needs a paid instance. A free **Web Service** worker (set `PORT`, health path `/health`) **spins down after ~15 minutes without inbound requests and then consumes nothing**; it is not a reliable worker. Use a paid worker, or `EVENT_WORKER_IN_PROCESS=true` on the API service (it wakes with the first webhook and resumes from Postgres and Redis).
- Redis: a Render Key Value instance created for Klaros (free plan has no persistence guarantee; that is acceptable here because Postgres, not Redis, is the source of truth and the relay re-sends what Redis lost).

## Limits (read before relying on it)
- **At-least-once**, not exactly-once, for handler execution. Non-idempotent handlers can repeat their effect after a crash in the narrow window described above.
- **The Postgres relay needs to see every tenant's events.** Under the restricted `klaros_app` role (RLS enforced) it sees none; the design for that (`klaros_discovery` role, `provision_discovery_role.py`) is documented as deferred and is **not** wired into the bus. Under such a role, crash recovery still works through the pending-message takeover (proven), but recovery from **Redis data loss** does not. The current Render staging and pilot services connect as the database owner, where the relay works.
- The in-memory transport (default for dev and the current staging deployment until Redis is configured) loses queued events on restart and is single-process.
- Redis must be ≥ 6.2 (`XAUTOCLAIM`).
- Ordering across events is not guaranteed; handlers must not assume it.
