# AI Voice Receptionist — OpenAI Realtime Engine (Phase 32)

Status legend: 🟢 LIVE VERIFIED (proven with a real completed phone call) · 🟡 TEST VERIFIED (proven with real code paths against scripted/fake external I/O) · ⚫ NOT VERIFIED (implemented, not yet exercised) · 🔴 NOT IMPLEMENTED.

**As of this writing: no real phone call has been placed. Twilio credentials are now configured and webhook signature validation has been proven live against the real Twilio account — but every number on this Twilio account is already serving other production traffic, so no call has been safely placed. Every capability below is 🟡 TEST VERIFIED at best — see "Blocker" below.**

## Two engines, one governed core

Klaros has two selectable voice engines, chosen by `VOICE_AI_ENGINE` (default `"cascaded"`):

| Engine | STT | AI | TTS | Conversation logic |
|---|---|---|---|---|
| `cascaded` (default, Phase 4-6) | Deepgram | `AIProvider.generate_structured` (once, for intent classification only) | ElevenLabs | `VoiceConversationService.handle_turn` — deterministic booking sub-state-machine after the first turn |
| `openai_realtime` (Phase 32, opt-in) | OpenAI Realtime (built in) | OpenAI Realtime (continuous, native function calling) | OpenAI Realtime (built in) | The model drives the conversation naturally; every real action still goes through the identical governed pipeline (see below) |

Both engines share: the Twilio webhook (`app/api/v1/webhooks.py`), signature validation, tenant resolution, `CallSession` persistence, the tool allowlist (`_VOICE_ALLOWED_TOOLS`), emergency detection (`detect_emergency`), and `AIExecutionService`/`ToolRegistry`/`ActionPolicy`/`AuditLog`. Nothing about the cascaded engine changed in this phase.

## Architecture (openai_realtime engine)

```
Caller phone
  → Twilio (PSTN)
  → POST /api/v1/webhooks/twilio/inbound-voice/{tenant_id}   [signature-verified]
  → CallSession created (idempotent on provider+external_call_id)
  → TwiML <Connect><Stream> to wss://.../api/v1/voice-stream, tenant_id/call_session_id signed as <Parameter>
  → app/api/v1/voice_stream.py: _voice_media_stream_openai_realtime()
      ↕ raw g711_ulaw audio, both directions, no conversion
  → OpenAIRealtimeVoiceBridge (app/services/openai_realtime_voice_service.py)
      → wss://api.openai.com/v1/realtime  (OPENAI_API_KEY, never logged)
      → function calls intercepted → AIExecutionService.request_tool_execution
          → ToolRegistry → ActionPolicy → ApprovalRequest → AuditLog
      → transcripts appended to CallSession.transcript (owner visibility)
      → emergency detection (deterministic) on every transcript
  → Twilio → caller hears the response
```

## Configuration

| Variable | Purpose | Required for |
|---|---|---|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Webhook signature verification, outbound Twilio calls | Any inbound call at all (both engines) |
| `TWILIO_FROM_NUMBER` | The number callers dial | Outbound-initiated flows, display only for inbound |
| `OPENAI_API_KEY` | Already-certified (Phase 31) credential, reused as-is — no second key mechanism | `openai_realtime` engine, and the cascaded engine's classification call |
| `VOICE_AI_ENGINE` | `"cascaded"` (default) or `"openai_realtime"` | Selects the engine |
| `OPENAI_REALTIME_MODEL` | Default `"gpt-4o-realtime-preview"` — configurable, never hardcoded, since OpenAI's realtime model naming changes over time | `openai_realtime` engine |

No new credential mechanism was introduced. `OPENAI_API_KEY` is read through the exact same `Settings`/`get_ai_provider()`-adjacent configuration path as every other AI surface.

## Security model

- The model never touches the database. Every real action is a Realtime "function call" event, intercepted in `OpenAIRealtimeVoiceBridge._handle_function_call` and executed *only* via `AIExecutionService.request_tool_execution` — same ToolRegistry/ActionPolicy/ApprovalRequest/AuditLog pipeline as every other governed AI surface in Klaros.
- Tool allowlist: `crm.create_lead`, `crm.create_customer`, `crm.check_availability`, `crm.create_appointment`, `knowledge.search` — imported from the cascaded engine's `_VOICE_ALLOWED_TOOLS`, one source of truth. Any other function name the model attempts is rejected before `AIExecutionService` is ever reached.
- Function-calling schemas are generated from each tool's own real Pydantic `input_schema`, never hand-duplicated.
- `crm.create_appointment` arguments are validated against a real, session-local `_SlotGuard`: the `start_time`/`end_time` must match a real prior `crm.check_availability` result, and `customer_id` must reference a customer actually created earlier in the same call. The model architecturally cannot have an invented timestamp accepted.
- Idempotency keys for `crm.create_appointment`/`crm.create_lead` are always server-derived from the call id — never supplied or controlled by the model — so a duplicate/retried function-call delivery cannot double-book.
- Emergency detection (`detect_emergency`) is deterministic regex matching, runs on every completed transcription, and forcibly cancels any in-flight model response before the safety announcement (a fixed string, synthesized via a one-shot non-conversational TTS call, never left to the live model) plays.
- Tenant identity comes only from the signed webhook URL path, never from any caller-controlled or model-controlled field.
- The OpenAI API key is never logged, printed, or included in any error string reaching a log (`httpx`'s exception `str()`/`repr()` never includes request headers, verified in Phase 31; the same holds here).

## Failure handling

- No `OPENAI_API_KEY` configured → `bridge.open()` raises `RuntimeError` honestly before any WebSocket is attempted; the call ends with `PROVIDER_FAILURE`, never a silent hang.
- Malformed Realtime protocol frames are logged and skipped, never crash the bridge.
- Malformed/invalid function-call arguments (bad JSON, wrong schema, invented slot/customer, disallowed tool) are all rejected with a structured error returned to the model — the call continues, never crashes.
- A tool execution failure (e.g. a real `ActionPolicy` denial) is caught and reported back to the model as `execution_failed`, never surfaced as an unhandled exception.

## Tenant isolation

Identical mechanism to the cascaded engine and every other webhook in this codebase: `tenant_id` is resolved from the signed webhook URL path only, carried through Twilio's `<Parameter>` mechanism (itself covered by the webhook's own signature), and re-validated on the WebSocket's `start` event. The Realtime bridge never accepts a tenant_id from caller speech, model output, or any other untrusted source.

## Caller identification

`OpenAIRealtimeVoiceBridge.open()` looks up the caller's number against `find_matching_customer` — the identical tenant-scoped function the cascaded engine's `VoiceConversationService._identify_caller` already uses, never a second implementation. A match is injected into the Realtime session's `instructions` as an explicitly fenced `EXISTING CUSTOMER DATA` block (never as an unfenced instruction) so the model can greet the caller by name and reuse their `customer_id` without re-asking. An unmatched caller gets no such block — treated as new, exactly like the cascaded engine. Tenant isolation is structural (the lookup is always scoped to the resolved `tenant_id`) and proven directly: the same caller phone number registered under two different tenants never leaks the wrong tenant's customer name into the other tenant's session.

## Owner visibility

Real caller and agent turns are persisted to `CallSession.transcript` from OpenAI's own transcription events (`conversation.item.input_audio_transcription.completed` for the caller, `response.audio_transcript.done` for the agent) — text only, never raw audio, matching the cascaded engine's existing privacy posture. `AIInvocationLog` records session open/close with real latency, no raw prompt/response content (consistent with Phase 31's finding that this table has no such fields at all).

## Known limitations (disclosed, not hidden)

1. **No real phone call has been placed yet.** Twilio credentials (`TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_FROM_NUMBER`) are now configured, and webhook signature validation was proven live over a real HTTP round-trip (valid signature accepted, invalid rejected) against the real Twilio account — but every phone number on that account (9 total) is already pointed at other production services (`call-iq-gateway.onrender.com`, `gateway.hallaai.com`), so no number is safe to repoint for a test call without disrupting real traffic. Live certification is blocked on getting a dedicated/safe number, not on credentials. See the Phase 32 certification report for the exact path forward.
2. **Human escalation remains flag-only** in both engines — `handoff_requested`/`handoff_reason` plus a spoken promise, no Twilio `<Dial>` live transfer exists anywhere in this codebase. Pre-existing, not new to this phase.
3. **Company Memory is not consumed** by either voice engine — confirmed as the cascaded engine's existing behavior in this phase's own audit; not added to the realtime engine either, per the mission's explicit "do not redesign to force it in" instruction.
4. **Emergency TTS fallback audio quality**: the one-shot safety-message synthesis downsamples OpenAI's 24kHz TTS output to Twilio's 8kHz via simple nearest-neighbor decimation (not a production-grade resampler) — acceptable for a short, safety-critical, rarely-triggered announcement; not used for any other audio in the call.
5. **Default engine is unchanged** (`VOICE_AI_ENGINE=cascaded`) — the OpenAI Realtime engine is opt-in only, so no existing deployment's behavior changes without an explicit configuration change.
