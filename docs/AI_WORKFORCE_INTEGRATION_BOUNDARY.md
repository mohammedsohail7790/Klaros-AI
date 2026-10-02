# AI workforce (Halla AI) — the Klaros-side integration boundary

**Halla AI is a separate platform with its own repository.** It provides the AI workforce: voice,
inbound and outbound calls, qualification, appointment booking, support, outbound communication.
Klaros does **not** contain, copy or re-implement any of it, and the Halla repository was not
touched by this work.

## What exists in Klaros

| Piece | Location | What it is |
|---|---|---|
| Adapter contract | `backend/app/integrations/workforce/contract.py` | `WorkforceIntegration` ABC: `get_status`, `configure`, `deploy_agent`, `get_agent`, `update_agent`, `health_check`; plain dataclasses; the expected capability vocabulary |
| Honest placeholder | `backend/app/integrations/workforce/registry.py` | `PendingWorkforceIntegration`: always reports `NOT_CONNECTED`, `adapter_implemented=false`; every mutating call raises `WorkforceNotConnectedError` |
| Status API | `GET /api/v1/business-builder/workforce` | returns exactly what the adapter reports |
| UI | `/workforce`, the "AI workforce" node on the Business Map, the Business Home card | shows the status; no connect/deploy controls exist while no adapter is implemented |

The contract deliberately specifies **no transport**: no URLs, no auth scheme, no payload format.
Those belong to Halla's own API contract, which is not available inside Klaros.

## Status vocabulary

In the UI, an AI workforce whose adapter does not exist is shown as **Integration required** (nothing to connect),
not merely *Not connected*; *Not connected* is reserved for the case where an adapter exists but the tenant has
not connected it.

`NOT_CONNECTED` · `CONFIGURATION_REQUIRED` · `CONNECTED` · `ERROR`.
`CONNECTED` may only be returned after a real health check against the live external system has
just succeeded. Today nothing can return it.

## Expected capabilities (the contract's vocabulary, not a claim of what is deployed)

Voice · Incoming calls · Outgoing calls · Lead qualification · Appointment booking ·
Customer support · Outbound communication.

A business requirement is *workforce-addressable* when the shared capability vocabulary marks it so
(communication, lead qualification, scheduling). Such requirements make the **AI workforce** node
appear on the Business Map — as **Not connected**, and as planned because no adapter exists.

## To connect Halla later

1. Obtain Halla's documented API (auth, agent deployment, health endpoint).
2. Implement `HallaWorkforceIntegration(WorkforceIntegration)` in `app/integrations/workforce/`,
   storing per-tenant credentials through the existing `IntegrationConnection` +
   `credential_store.py` pattern (never a shared global credential).
3. Register it in `registry.py`. Nothing else changes — every consumer depends on the contract.
4. Add contract tests: `get_status` must not return `CONNECTED` without a successful live check.

Pending (requires information that is not available inside Klaros): Halla's external API contract.
