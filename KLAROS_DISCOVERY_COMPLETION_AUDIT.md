# KLAROS DISCOVERY COMPLETION AUDIT

Audit-only investigation. No product code, tests, migrations, or database schema were modified. Nothing was committed or pushed. HEAD verified unchanged throughout (see §17-equivalent findings at the end).

Tag legend used throughout: **FACT** (directly verifiable in code/git), **OBSERVED** (something run in this session and the result seen), **INFERRED** (reasonable conclusion, not literally stated in code), **RECOMMENDED** (judgment call for future work — never to be read as already-decided).

---

## 1. Executive Summary

**FACT**: At HEAD `af4937e403e47cdc141f2db349dfcc46a3df6c4b`, a Business Journey started with no real AI provider configured (`AI_PROVIDER=deterministic`, or no API key set for any provider) can begin Discovery but cannot complete it through the live UI. **OBSERVED**: the deterministic fallback (`_deterministic_fallback` in `backend/app/services/discovery_extraction_service.py`) returns a claim and one follow-up question only on the very first call (`is_initial=True`); every subsequent call returns `claims=[]`, `follow_up_question=None`, unconditionally, with no internal state or turn-counting at all.

**INFERRED**: This is not a broken AI architecture — it is an under-implemented fallback path. The real AI-provider architecture (`backend/app/services/ai_provider.py`) is already general-purpose, already supports Anthropic/OpenAI/Groq/DeepSeek/NVIDIA/Google, already returns structured, schema-validated output via `generate_structured()`, already audits every call, and `DiscoveryExtractionService` already calls it correctly with no bypass. **INFERRED**: if any one of those providers were configured with a real key, Discovery would already generate genuinely adaptive follow-up questions turn after turn and would complete normally — nothing in the Discovery/Blueprint/Journey code path forces the deterministic fallback specifically.

**RECOMMENDED**: The smallest correct fix is bounded to `_deterministic_fallback()` (and possibly a small signal change in `DiscoveryExtractionService`/`BusinessDiscoveryService` to recognize "the fallback has nothing more to ask" as its own completion trigger) — not a new AI framework, not a new provider abstraction, not a Business Journey or Blueprint change. See §16.

---

## 2. Current Discovery Architecture

**FACT**, file:line evidence:

**A. What starts a Discovery session?**
`BusinessDiscoveryService.start_session` (`backend/app/services/business_discovery_service.py:73-103`). It calls `BusinessBlueprintService.get_or_create_draft` (creates a version-1 DRAFT blueprint with all 20 empty `BlueprintSection` rows if none exists — `business_blueprint_service.py:65-103`), creates a `DiscoverySession` (`status=ACTIVE`, `questions_asked=0`), creates the sequence-0 `DiscoveryTurn` (`kind=INITIAL_DESCRIPTION`, `question=None`, `answer=<the free-text idea>`), then calls `_process_turn(..., is_initial=True)`.

**B. What determines the next question?**
`DiscoveryExtractionService.extract()` (`discovery_extraction_service.py:150-201`): if `ai_provider.is_connected` is `True`, it builds a schema-constrained prompt (`_build_prompt`, lines 106-115) and calls `AIProvider.generate_structured()`; if not connected, it calls `_deterministic_fallback()` (lines 118-142). `BusinessDiscoveryService._process_turn` (lines 146-236) then either stores that follow-up question onto the current turn's `question` field (lines 217-224) or marks the session `COMPLETED` (see C).

**C. What determines when Discovery is complete?**
`_process_turn` (`business_discovery_service.py:200-225`): after proposing all extracted claims, it recomputes `remaining_gaps` via `_gap_keys()` (lines 64-71 — the set of `MINIMUM_BAR_SECTIONS` whose `BlueprintSection.status != COMPLETE`). Completion happens when **either** `remaining_gaps` is empty (`not remaining_gaps and not at_cap`, line 205) **or** `questions_asked >= max_questions` (`at_cap`, line 204, default `max_questions=8` — `business_discovery.py` model default, confirmed via `DiscoverySession.max_questions`). Only these two conditions ever set `DiscoverySessionStatus.COMPLETED`.

**D. What creates confirmed `BlueprintClaim`s?**
Never Discovery itself. `BusinessBlueprintService.confirm_claim` (`business_blueprint_service.py:207-304`) is the only place a claim's `status` becomes `CONFIRMED` and the only place a `BlueprintSection.status` becomes `COMPLETE` (lines 233-255). It is reached only via the explicit `POST /business-blueprint/claims/{id}/confirm` route — a human action on the Blueprint review screen, never called by `BusinessDiscoveryService` or `DiscoveryExtractionService`.

**E. What provider is currently expected to perform extraction?**
Whatever `get_ai_provider()` (`ai_provider.py:437-467`) returns, based on `Settings.AI_PROVIDER` + whichever API key is configured — `DiscoveryExtractionService` is constructed with an injected `AIProvider` instance (`discovery_extraction_service.py:145-148`), never hardcoding a specific provider class. See `backend/app/api/tool_deps_business_discovery.py` for the actual DI wiring (calls `get_ai_provider()`).

**F. What happens when no external AI provider is configured?**
`DeterministicAIProvider.is_connected = False` (`ai_provider.py`, class body) → `DiscoveryExtractionService.extract()`'s `if not self._provider.is_connected:` branch (line 160) always takes the deterministic path, never calling `generate_structured` at all.

**G. Why does the deterministic fallback stop after the initial follow-up?**
`_deterministic_fallback()` (`discovery_extraction_service.py:118-142`) is a pure function with an `if is_initial:` branch that returns one claim + one fixed question, and an unconditional `return DiscoveryExtractionResult(claims=[], follow_up_question=None, follow_up_section_key=None)` for every other call — **OBSERVED** directly in this session (see §6) with no turn count, no state, no reference to which gaps remain, at all.

**H. Is this limitation intentional?**
Partially. **FACT**: `PHASE_2_IMPLEMENTATION_LOG.md` "Known limitations" states: *"The adaptive follow-up question, in the deterministic-AI-fallback path, is a single fixed question ... rather than the spec's fuller 'structured choice' UX (business type / geography / revenue model as separate prompts) — implemented as one combined free-text question for this phase; a real AI provider ... would produce genuinely adaptive per-gap questions."* **FACT**: `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §6 itself specifies the intended deterministic degrade as *"the user is walked through **a short fixed set of** the most capability-differentiating questions (business type, geography, revenue model)"* — plural, not singular. **INFERRED**: the single-question implementation is a known, documented under-build of the spec's own described fallback, not a design decision that the fallback should dead-end after one question. Nothing in the code or docs states "Discovery is intentionally uncompletable without a real provider" — that specific consequence (a hard deadlock) does not appear to have been foreseen until Phase 14's live validation (`PHASE_14_LIVE_VALIDATION_REPORT.md` §9).

**I. Is the existing AI provider abstraction already sufficient for Discovery's needs?**
**FACT/INFERRED**: Yes. `generate_structured(prompt) -> AICallOutcome` (`ai_provider.py:236-245` on the base class, overridden per-provider) is exactly what `DiscoveryExtractionService.extract()` already calls (line 168) when a provider is connected — free-text-in, raw-JSON-string-out, validated by the caller against its own Pydantic schema (`DiscoveryExtractionResult`). This is the same pattern `AIQualificationService` already uses (cited in both the module docstring and `PHASE_2_IMPLEMENTATION_LOG.md`). No Discovery-specific gap in the provider layer was found.

**J. Can the existing provider be used without introducing another AI architecture?**
**FACT**: Yes — it already is being used this way for the connected-provider path; only the *fallback* path (used when nothing is connected) is deficient. No new provider class, no new settings keys, and no new abstraction are needed to make a *configured* provider work; that path is already fully wired and tested (see §13).

**K. Does Business Journey already correctly consume Discovery completion?**
**FACT**: Yes. `BusinessJourneyService.complete_discovery` (`business_journey_service.py:248-289`) requires `discovery_session.status == DiscoverySessionStatus.COMPLETED` (lines 261-267) before moving the journey to `BLUEPRINT_REVIEW`; it never inspects claim/section state itself. `confirm_blueprint` (lines 293- ) then separately requires the Blueprint to pass `BusinessBlueprintService.activate()`'s own minimum-bar check (delegated entirely, line 320, "never re-implements or loosens it"). These are two independent, correctly-ordered checkpoints — Discovery completing (even via the `at_cap` path with claims still `PROPOSED`) does **not** bypass the separate human claim-confirmation gate before Blueprint can go `ACTIVE`. This is by design, not a bug: a still-`PROPOSED`-claims blueprint reaching `BLUEPRINT_REVIEW` is the whole point of that stage.

---

## 3. Actual Runtime Request Path

**FACT**, traced end-to-end by file:line:

1. `frontend/app/business/discovery/page.tsx` — `handleSubmitAnswer()` (line 93) calls `answerDiscoveryQuestion(token, sessionId, answer)` from `frontend/lib/api.ts`.
2. That POSTs to `POST /api/v1/business-discovery/sessions/{id}/answer` — handled by `answer_discovery_question` (`backend/app/api/v1/business_discovery.py:105-122`), gated by `require_permission(Permission.MANAGE_BUSINESS_DISCOVERY)`.
3. Route calls `BusinessDiscoveryService.submit_answer` (`business_discovery_service.py:105-144`) — persists the new `DiscoveryTurn`, increments `questions_asked`, then calls `_process_turn` (line 142-144).
4. `_process_turn` (lines 146-236) calls `DiscoveryExtractionService.extract()` (line 158), which either calls the real `AIProvider.generate_structured()` or `_deterministic_fallback()` (per §2.E/F above).
5. Each valid extracted claim is written via `BusinessBlueprintService.propose_claim` (line 186, → `business_blueprint_service.py:161-205`) — always `status=PROPOSED`.
6. Gap re-check (`_gap_keys`, line 200) and completion decision (lines 202-225) happen against the current `BlueprintSection.status` values.
7. Response serialized by `TurnResponse.from_result` (`business_discovery.py:42-63`) back to the frontend.
8. Frontend re-renders: if `session_status === "COMPLETED"`, calls `completeDiscoveryJourneyStep` → `POST /business-journey/{id}/complete-discovery` → `BusinessJourneyService.complete_discovery` (§2.K) → journey moves to `BLUEPRINT_REVIEW` → frontend routes to `/business/blueprint`.
9. If not completed, the frontend sets `question = result.next_question` (`page.tsx:105`) and re-renders the answer form — **this is the exact point where the deterministic fallback's `next_question=None` produces an indefinite "Loading your next question..." state with no form rendered at all** (`page.tsx:195-199`), matching **OBSERVED** behavior in §6.

**The smallest intervention would live entirely inside step 4** (`_deterministic_fallback`, and/or the small amount of state `DiscoveryExtractionService`/`_process_turn` would need to know "the fallback is exhausted, not just silent this turn") — no other file in this path needs to change for that specific fix category (see §16-§17).

---

## 4. AI Provider Architecture

**FACT** (`backend/app/services/ai_provider.py`, full file read):

- `AIProvider` ABC (lines ~185-246) defines `is_connected`, `name`, `model`, `enrich_brief()`, and `generate_structured()` — the last is the one Discovery uses.
- `DeterministicAIProvider` (`is_connected=False`) is the null-object fallback returned whenever no key is configured.
- `_HTTPAIProvider` is shared plumbing: retry with exponential backoff (`_call_with_retry`, capped by `_max_retries`), real error classification (`AIErrorType`: authentication/rate_limit/timeout/provider_error/malformed_response/network_error), API-key redaction in any exception text (`_redact_key`), and a `__repr__` that never leaks the key.
- Concrete providers already implemented: `AnthropicAIProvider`, `OpenAIAIProvider`, `GroqAIProvider`, `DeepSeekAIProvider`, `NvidiaAIProvider`, `GoogleAIProvider` (the last four share `_OpenAICompatibleProvider`).
- `get_ai_provider()` (lines ~437-467) is the single factory: explicit `AI_PROVIDER=<name>` selects a provider (falling back to `DeterministicAIProvider` if that provider's key is absent), or `AI_PROVIDER=auto` (default) tries Anthropic → OpenAI → Groq → DeepSeek → NVIDIA → Google → deterministic, in that order.
- Every real call is audited: `DiscoveryExtractionService.extract()` calls `record_ai_invocation()` (`discovery_extraction_service.py:170-180`) for every non-fallback call — same audit path as every other AI feature in the codebase (`ai_invocation_log_service.py`), not a second mechanism.
- Config surface (`backend/app/core/config.py`, names only, no values read or printed): `AI_PROVIDER`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_TIMEOUT_SECONDS`, `ANTHROPIC_MAX_RETRIES`, `ANTHROPIC_MAX_OUTPUT_TOKENS`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`, `OPENAI_MAX_OUTPUT_TOKENS`, `GROQ_API_KEY`, `GROQ_MODEL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `NVIDIA_API_KEY`, `NVIDIA_MODEL`, `GOOGLE_API_KEY`, `GOOGLE_MODEL`.

**INFERRED**: This is a mature, already-hardened, general-purpose provider layer (per its own docstring, "Hardened Phase 12E"). `DiscoveryExtractionService` is a thin, correct consumer of it, mirroring `AIQualificationService`'s established pattern exactly (confirmed by direct comparison of both files' structure and by the Phase 2 log's own explicit claim of that mirroring). **No Discovery-specific AI architecture is missing.**

---

## 5. Deterministic Fallback Behavior

**FACT**, from `_deterministic_fallback()` (`discovery_extraction_service.py:118-142`):

- **Initial turn** (`is_initial=True`, i.e. `start_session`'s first call): returns one `ExtractedClaim` (`claim_type=Fact`, `section_key=IDENTITY`, `key="identity.description"`, `value=<the raw free-text idea>`, `provenance=USER_STATED`) plus `follow_up_question="What type of business is this, and in which market/geography does it operate?"`, `follow_up_section_key=INDUSTRY`.
- **Second turn onward** (`is_initial=False`, every subsequent `submit_answer` call, unconditionally): returns `claims=[]`, `follow_up_question=None`, `follow_up_section_key=None`. There is no branching on turn number, on which gaps remain, or on the content of the answer — it is the same return value forever after turn 1.
- **Claim extraction**: none at all past turn 1 — the user's free-text answers to turns 2+ are persisted verbatim on `DiscoveryTurn.answer` (never lost, per the module's own stated design) but never converted into any `BlueprintClaim`.
- **Follow-up generation**: none past turn 1, as above.
- **Completion behavior**: because `remaining_gaps` (INDUSTRY/BUSINESS_MODEL/REQUIRED_CAPABILITIES minus IDENTITY, which got one PROPOSED-but-never-CONFIRMED claim) can never become empty via this path — closing a gap requires a `COMPLETE` section, which requires a *confirmed* claim, and Discovery itself never confirms claims (§2.D) — the "all gaps closed" completion branch is **structurally unreachable for a brand-new Discovery session under deterministic fallback**. The only reachable completion path is the hard cap (`questions_asked >= max_questions`, default 8).

---

## 6. Current Reproduction

**OBSERVED** in this session (read-only; no DB touched, no file modified) — ran `_deterministic_fallback()` directly as a pure function against current HEAD:

```
initial: [ExtractedClaim(... section_key='IDENTITY' ...)] "What type of business is this, and in which market/geography does it operate?" INDUSTRY
turn2:   []  None  None
turn3:   []  None  None
```

This exactly matches the exact concern raised in the task and in `PHASE_14_LIVE_VALIDATION_REPORT.md` §9: after the first follow-up, every later call returns `claims=[]`/`follow_up_question=None`.

Separately, **FACT**: `backend/tests/test_business_discovery_service.py::test_session_completes_once_question_cap_reached` (lines 84-99) already exercises the `at_cap` completion branch at the service layer — but only by **manually overriding `max_questions` down to `1`** before calling `submit_answer` once. It does **not** exercise (and no test in the repository exercises) the real default cap of 8 with the deterministic fallback actually stalling across turns 2 through 8 — i.e., no existing automated test reproduces the actual live-validation-reported deadlock. This is why the Phase 2/14 unit test suites stayed green while Phase 14's live browser walkthrough still hit the stall (§14, gap list).

**Frontend confirmation** (`frontend/app/business/discovery/page.tsx:174-199`): when `question` is falsy, the page renders only `"Loading your next question..."` — no textarea, no submit button. So even though the backend's `answer_discovery_question` route itself does not require a `next_question` to exist in order to accept another answer (no such guard in `business_discovery.py`), the **live UI provides no mechanism to submit a further answer once `next_question` is null**, making the deadlock absolute at the UI layer regardless of the theoretical `at_cap` escape hatch in the backend.

---

## 7. Root Cause

**INFERRED**, synthesizing §2-§6:

The root cause is **not** a missing or broken AI provider abstraction (§4 shows that layer is complete and already correctly used). It is a **narrower gap in exactly one function**: `_deterministic_fallback()` implements only the first sentence of its own spec citation (`KLAROS_BUSINESS_DISCOVERY_SPEC.md` §6's *"short fixed set of"* questions was built as a *single* question) and has no state to know it has already asked its one question, so every subsequent call is a no-op. Because Discovery's "gaps closed" completion branch is structurally unreachable without human claim confirmation (which cannot happen before Discovery even finishes), the *only* legitimate completion path for a brand-new, provider-less Discovery session is the hard question cap — and the fallback stops generating anything the UI can act on well before that cap is reached (after turn 1 of 8). The defect is the compound of these two facts, not either alone: a fallback that asked, say, 3-4 fixed generic questions (matching the spec's literal wording) covering the remaining `MINIMUM_BAR_SECTIONS` gaps, or that explicitly signaled "I'm exhausted, complete now" once its fixed set ran out, would resolve this without touching the provider layer, Business Journey, or Blueprint code at all.

---

## 8. Business Journey Compatibility

**FACT** (§2.K, `business_journey_service.py:248-360`): Business Journey's two checkpoints (`complete_discovery` requiring `DiscoverySessionStatus.COMPLETED`; `confirm_blueprint` requiring `BusinessBlueprintService.activate()` to succeed) are independently correct and require no change. **INFERRED**: whatever mechanism eventually lets Discovery legitimately reach `COMPLETED` (§16) will flow through this unchanged gate with no modification needed here.

## 9. Blueprint Compatibility

**FACT**: Blueprint's activation gate (`business_blueprint_service.py:431-467`) already requires per-section `COMPLETE` status via `confirm_claim` (human action) regardless of how Discovery reached `COMPLETED` — this is an existing, independent, already-correct human-in-the-loop boundary that a Discovery-side fix does not need to touch or weaken. **INFERRED**: even with an improved deterministic fallback proposing several more generic claims, the human must still confirm them on the Blueprint review page before `activate()` can succeed — the intended flow, not a workaround.

## 10. Recommendation Compatibility

**FACT** (`backend/app/services/recommendation_service.py`, lines ~107, ~374-375, ~390): `generate_recommendations` reads `CONFIRMED` `BlueprintClaim` rows scoped to `section_key == REQUIRED_CAPABILITIES` off the currently `ACTIVE` blueprint version. Since `REQUIRED_CAPABILITIES` is one of the four `MINIMUM_BAR_SECTIONS` that must already be `COMPLETE` (i.e., have a confirmed claim) before that same blueprint could ever have been activated (§9), Recommendation Engine's input contract is already satisfied by the existing Blueprint activation gate — **no field or shape gap was found** between what Discovery/Blueprint currently produce and what Recommendations already consumes.

---

## 11. Security Findings

**FACT/INFERRED**, from source inspection (no live exploitation attempted):

- AI credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) are loaded server-side only via `Settings` (`app/core/config.py`), never exposed to any API response or frontend code (`frontend/lib/api.ts` was grepped in the Phase 14 live-validation session per its own report — no such fields appear in any Discovery/Blueprint/Journey response schema, confirmed independently here by reading `TurnResponse`/`_session_to_dict`/`_turn_to_dict` in `business_discovery.py`, none of which carry provider/key/model fields).
- API keys never appear in a repr or exception message (`_HTTPAIProvider.__repr__`, `_redact_key` — §4).
- Tenant identity throughout Discovery is JWT-derived: every route (`business_discovery.py`) takes `current_user: CurrentUser = Depends(require_permission(...))` and passes `current_user.tenant_id` — never a client-supplied tenant id — into every service call.
- Users cannot influence provider/model selection: `get_ai_provider()` is called with no per-request parameters; the client never sends a "model" or "provider" field (confirmed: `StartSessionRequest`/`AnswerRequest` in `business_discovery.py` only carry `description`/`answer` strings).
- No arbitrary external URL can be supplied by a client anywhere in this path — provider base URLs are hardcoded constants in `ai_provider.py` (e.g. `"https://api.anthropic.com/v1/messages"`), never derived from request input.
- Prompt construction (`_build_prompt`, `discovery_extraction_service.py:106-115`) always fences the user's free text as `DISCOVERY INPUT` DATA inside a JSON-encoded string, with explicit system instructions telling the model to treat it as data, never as commands — mirroring the same pattern already used in `ai_provider.py`'s Morning Brief prompt and `AIQualificationService`. This does not make prompt injection impossible (no LLM boundary can guarantee that), but it follows the codebase's established, consistent defensive pattern.
- Discovery input is safely persisted: `DiscoveryTurn.answer` is a plain text column, no dynamic SQL, always via the ORM.
- AI output is validated before database writes: `DiscoveryExtractionResult`/`ExtractedClaim` (Pydantic) validate the parsed JSON shape; `ExtractedClaim.is_valid_vocabulary()` (lines 84-89) additionally rejects any `claim_type`/`section_key`/`provenance` value outside the fixed enums, and `DiscoveryExtractionService.extract()` (lines 194-199) drops invalid claims and nulls out an invalid `follow_up_section_key` before returning — a malformed or adversarial model response cannot inject an out-of-vocabulary claim.
- AI output cannot create unauthorized claims or bypass RBAC: every claim, however produced, is written as `status=PROPOSED` only (§2.D) — it can never itself become `CONFIRMED`/authoritative without the separate, RBAC-gated (`MANAGE_BLUEPRINT`) human confirm endpoint. The AI/fallback path has no code path to `confirm_claim`.

**No security defect was found in the existing Discovery/Blueprint/AI-provider path.** The completion deadlock discussed in this audit is a functional/availability issue, not a security one.

---

## 12. Tenant Isolation Findings

**FACT**: Every `BusinessDiscoveryService`/`BusinessBlueprintService` method takes an explicit `tenant_id` parameter and filters/validates against it (`business_discovery_service.py:110`, `business_blueprint_service.py` throughout, e.g. line 132 `if blueprint is None or blueprint.tenant_id != tenant_id`). API routes derive `tenant_id` exclusively from `current_user.tenant_id` (JWT), never from the request body or path beyond the resource id itself. This chain (auth → service param → DB filter) is intact through Discovery → Blueprint → Business Journey, matching the codebase's existing "unenforced-by-DB-RLS, enforced-by-application" convention documented in both files' own docstrings.

**Explicitly preserved per task instruction**: RLS on `discovery_sessions`, `discovery_turns`, `business_blueprints`, `blueprint_sections`, `blueprint_claims` is **audit-mode only** (`ENABLE ROW LEVEL SECURITY` + permissive `USING (true)`, per `PHASE_2_IMPLEMENTATION_LOG.md`'s migration section and `test_postgres_business_discovery_blueprint_rls_audit_mode.py`'s own assertions of `relforcerowsecurity=false`) — **this audit does not recommend upgrading that to enforcement**; that remains explicitly out of scope here, as instructed.

---

## 13. Existing Test Coverage

**FACT**, by file name (`backend/tests/`):

| Area | File(s) |
|---|---|
| Discovery service (start/answer/session lifecycle) | `test_business_discovery_service.py` (9 tests: deterministic sanity check, initial turn + Fact claim, raw-answer-persisted-on-failure, unknown-session error, tenant isolation, cap-forced completion (rigged `max_questions=1`), cannot-answer-completed, claims-never-auto-confirmed, gap-check-uses-confirmed-only) |
| Discovery + Blueprint API | `test_business_discovery_blueprint_api.py` (9 tests: auth required, RBAC 403, full happy-path start→confirm→activate, reject-claim + 409, cross-tenant 404s, 404/409 edge cases) |
| Blueprint service | `test_business_blueprint_service.py` (15 tests: creation/idempotency, tenant isolation, propose/confirm/reject incl. idempotent-confirm/invalid-transitions, minimum-bar activation gating, one-ACTIVE-per-tenant, versioning-on-edit) |
| RLS audit-mode (Postgres only) | `test_postgres_business_discovery_blueprint_rls_audit_mode.py` (7 tests, incl. one full Discovery→claims→confirm→activate pipeline test against real Postgres) |
| Cross-vertical validation | `test_cross_vertical_blueprint_validation.py` (4 tests: medical-tourism-shaped and dropshipping-shaped businesses both activate via the identical generic schema; static no-vertical-branch guard) |
| Business Journey API | `test_business_journey_api.py` |
| Business Journey concurrency (Postgres only) | `test_postgres_business_journey_concurrency.py` |
| Recommendation engine | `test_recommendation_service.py`, `test_recommendation_api.py`, `test_cross_vertical_recommendation_validation.py`, `test_retention_review_recommendation.py`, `test_postgres_recommendation_rls_audit_mode.py` |
| AI provider layer | `test_ai_provider.py`, `test_ai_provider_hardening.py` (retry/backoff/error-classification), `test_ai_qualification_service.py`, `test_ai_qualification_tool_registry.py`, `test_live_ai_provider.py` |

**FACT**: no test file named anything like `test_discovery_extraction_service.py` exists — `DiscoveryExtractionService`/`_deterministic_fallback` are exercised only indirectly, through `BusinessDiscoveryService`'s own tests, and always through the deterministic path (no configured key in the test environment, confirmed by `test_ai_provider_is_deterministic_in_test_env`).

---

## 14. Test Gaps

**INFERRED**, gaps that a future real fix would need to close (none written in this audit):

1. **Multi-turn deterministic fallback exhaustion at the real default cap (8)** — no test drives `submit_answer` repeatedly with the *real* `max_questions=8` default to confirm the fallback's behavior across turns 2-8 (today: stalls; after a fix: should keep producing answerable questions or a clean, deliberate completion signal).
2. **`DiscoveryExtractionService`-level unit tests** — no dedicated test file exists for this service in isolation (schema validation of a malformed/adversarial model response, vocabulary-stripping of an out-of-enum claim, a `follow_up_section_key` outside `_VALID_SECTION_KEYS`, provider-failure → `available=False` propagation).
3. **AI provider success path integration for Discovery specifically** — the AI provider layer itself is well-tested (`test_ai_provider.py`/`test_ai_provider_hardening.py`), but no test configures a (mocked) connected provider and drives a full Discovery session through `generate_structured` end-to-end to prove genuinely adaptive multi-turn completion works today when a provider *is* configured — the entire "provider path already works" conclusion in this audit (§2.I/J) is an **inference** from reading the code, not something an existing test proves end-to-end for Discovery.
4. **Malformed AI output specifically for Discovery** — `DiscoveryExtractionResult.model_validate` failure and the vocabulary-drop logic (`extract()` lines 194-199) have no dedicated test with a deliberately malformed/out-of-vocabulary payload.
5. **Retry behavior for Discovery's own calls** — `_HTTPAIProvider`'s retry logic is tested generically (`test_ai_provider_hardening.py`) but not through `DiscoveryExtractionService`'s specific call site.
6. **Duplicate/concurrent `submit_answer` requests on the same session** — no test in `test_business_discovery_service.py` or the API test file covers two concurrent answers to the same session (a double-submit / race on `questions_asked` increment or `sequence` assignment). `test_postgres_business_journey_concurrency.py` covers Business Journey-level concurrency, not Discovery-turn-level.
7. **Frontend**: no RTL test (per `PHASE_14_LIVE_VALIDATION_REPORT.md` §8's own admission) covers the exact "resume from a fresh/near-empty session" shape that caused the Phase 14 UI defect, nor a "next_question is null while status is ACTIVE" render path (today: silently stuck on "Loading...", arguably should show a different, more honest state).

---

## 15. Real Provider Validation Requirements

**RECOMMENDED**, what a future live end-to-end validation against a real provider would need (env var **names** only — no values, none read or printed in this audit):

- One of: `ANTHROPIC_API_KEY` (with `AI_PROVIDER=anthropic` or left as `auto`), or `OPENAI_API_KEY` (`AI_PROVIDER=openai`), or any other already-supported provider's key (`GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `NVIDIA_API_KEY`, `GOOGLE_API_KEY`).
- A real, disposable PostgreSQL instance at `alembic heads` (currently `0051` per Phase 14's own log — worth reconfirming at execution time), with `pgvector` installed (existing pattern used by every phase's live validation).
- Backend started with `uvicorn app.main:app`, the chosen `AI_PROVIDER`/API key env var, and `EVENT_TRANSPORT=memory` (confirmed sufficient — no Redis dependency found anywhere in the Discovery/Blueprint/Journey/Recommendation code paths, per Phase 14's own grep and independently plausible from this audit's own file reads).
- Frontend started via `next dev`, pointed at the backend (`NEXT_PUBLIC_API_URL`).
- A freshly registered test tenant/user via the real `/register` flow (OWNER role), not seeded/scripted, matching the existing project convention for live validations.
- Expected turn count: **variable, not fixed** — a real provider produces genuinely adaptive questions per remaining gap, so the number of turns to reach `COMPLETED` should depend on how much the initial free-text description already covers (per `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §4's own stated design — "a detailed description...produces few or zero follow-ups; a one-line description produces more"), bounded above by `max_questions=8`.
- Expected completion behavior: `DiscoverySession.status` reaches `COMPLETED` via the "all `MINIMUM_BAR_SECTIONS` gaps closed" path becoming reachable **only if** claims get confirmed mid-session (re-run against an already-`ACTIVE` blueprint) — for a **brand-new** journey, expect completion via the `at_cap` path exactly as with the deterministic fallback (§7), just with real, useful claims/questions along the way instead of a stall.
- Expected Blueprint state after Discovery completes: `DRAFT`, with several `PROPOSED` claims across multiple sections (not just `IDENTITY`) — requiring the same human confirm/activate flow as today, unchanged.
- Expected Business Journey state: `DISCOVERY_ACTIVE` → `BLUEPRINT_REVIEW` (via `complete-discovery`) → `BLUEPRINT_ACTIVE` (via `confirm-blueprint`, after confirming enough claims) → `RECOMMENDATIONS_READY` (via `generate-recommendations`) — unchanged from the already-code-reviewed Phase 14 contract.
- Expected Recommendations state: generated from `CONFIRMED` `REQUIRED_CAPABILITIES` claims on the now-`ACTIVE` blueprint, per §10 — no change expected from today's contract.
- No credential value should ever be printed, logged, or included in any resulting report.

---

## 16. Smallest Correct Implementation Boundary

**RECOMMENDED** (not implemented; judgment call for a future task):

Based strictly on what exists today (§2-§7), the smallest correct fix is bounded to:

- **(B) Improve the existing deterministic fallback** — extend `_deterministic_fallback()` (or the small amount of surrounding state needed to let it know which turn it's on / which gaps remain) so that, in the no-provider case, it asks a short **fixed sequence** of additional generic, non-vertical-specific questions targeting the remaining `MINIMUM_BAR_SECTIONS` gaps (`INDUSTRY`, `BUSINESS_MODEL`, `REQUIRED_CAPABILITIES`) — this is exactly what `KLAROS_BUSINESS_DISCOVERY_SPEC.md` §6 already describes ("business type, geography, revenue model") and what `PHASE_2_IMPLEMENTATION_LOG.md` already flags as under-built, not a new invention.
- Likely also requires **(G) a small, surgical change to `DiscoveryExtractionService`/`BusinessDiscoveryService`**: a signal distinct from "no follow-up this turn" for "the deterministic fallback has now asked everything it's going to ask" (e.g., an `exhausted: bool` on `ExtractionOutcome`/`DiscoveryExtractionResult`), so `_process_turn` can transition the session to `COMPLETED` once the fixed sequence is exhausted rather than only at the `max_questions` hard cap. This keeps the hard cap as a safety net (still relevant for the connected-provider path, per spec §4) rather than the only exit.
- **(A) No new AI architecture** — a configured Anthropic/OpenAI/etc. key is already sufficient today for the "real AI" path (§2.I/J, §4); this audit found no gap there to fix.
- **(C)/(D)/(E) not needed** — no new provider adapter, no new structured-output schema, and no new settings surface were found to be missing; the existing `DiscoveryExtractionResult`/`ExtractedClaim` schema and existing provider config already cover this.
- **(F) `BusinessDiscoveryService`**: only the minimal change needed to consume the new "exhausted" signal from (G); no change to its tenant-isolation, RBAC, or persistence logic.
- **(H) Business Journey**: **no change** — its two checkpoints are already correct (§8).
- **(I) Frontend**: **RECOMMENDED, separately** — even independent of the backend fix, `frontend/app/business/discovery/page.tsx`'s "silently stuck on Loading..." state when `question` is null but `session.status === "ACTIVE"` is a weak UX state that a future defensive improvement (e.g., a distinct message, or a `409`/reconciliation nudge) could address — but this is not required to unblock legitimate completion once the backend fallback itself is fixed, since a fixed backend would keep sending a real `next_question` until genuine completion.

**Not recommended**: hardcoding a medical-tourism- or dropshipping-specific questionnaire (explicitly forbidden by this task and inconsistent with the codebase's own repeatedly-enforced no-vertical-branching guard, e.g. `test_cross_vertical_blueprint_validation.py`'s static check) — the fixed question set described in §6 of the spec is generic across all verticals by design, matching the existing `IDENTITY`/`INDUSTRY`/`BUSINESS_MODEL`/`REQUIRED_CAPABILITIES` section vocabulary, not a vertical-specific script.

---

## 17. Files That Would Need Modification

**RECOMMENDED**, scoped strictly to §16's boundary:

- `backend/app/services/discovery_extraction_service.py` — extend `_deterministic_fallback()`'s fixed question sequence; possibly add an `exhausted`/similar field to `DiscoveryExtractionResult` or `ExtractionOutcome`.
- `backend/app/services/business_discovery_service.py` — `_process_turn`'s completion-decision block (lines ~200-225) would need to recognize the new "fallback exhausted" signal as a third completion trigger alongside `remaining_gaps`-empty and `at_cap`.
- Possibly `backend/app/models/business_discovery.py` if any new column/state is judged necessary to track "how many fixed fallback questions has this session already asked" (RECOMMENDED to first attempt this without a new column — e.g., derivable from `questions_asked` plus a fixed, ordered question list — before assuming a schema change is required; **no new table was found to be necessary from source**, per the task's explicit instruction to prove this before recommending it).
- New/expanded backend tests: `backend/tests/test_business_discovery_service.py` (multi-turn deterministic exhaustion at the real cap) and a new `backend/tests/test_discovery_extraction_service.py` (§14 gaps 1-5).
- Optionally, `frontend/app/business/discovery/page.tsx` for the independent UX improvement noted in §16(I) — not required for backend correctness.

## 18. Files That Must NOT Be Modified

Per this audit's own findings of already-correct, already-sufficient behavior:

- `backend/app/services/ai_provider.py` — provider abstraction is already complete and correct for Discovery's needs (§4).
- `backend/app/services/business_blueprint_service.py` — claim/section/activation lifecycle is already correct and must remain the single source of truth for `CONFIRMED`/`COMPLETE` transitions (§2.D, §9).
- `backend/app/services/business_journey_service.py` — both checkpoints already correctly gate on Discovery/Blueprint state (§2.K, §8).
- `backend/app/services/recommendation_service.py` — already consumes Blueprint's existing contract correctly (§10).
- Any RLS/migration file — this audit found no schema gap, and RLS enforcement upgrade is explicitly out of scope (§12).
- `backend/app/api/v1/business_discovery.py`, `business_blueprint.py`, `business_journey.py` — route/permission/error-handling shape is already correct; no change identified.

---

## 19. Risks

**RECOMMENDED** (risk assessment for the future fix, not a decision):

- **Scope creep risk**: it would be easy to over-build the deterministic fallback into something that looks like a real adaptive question generator (e.g., branching logic per business type) — this would re-introduce exactly the kind of implicit vertical-awareness the codebase's cross-vertical guard tests exist to prevent. The fix should stay a short, fixed, generic sequence, explicitly bounded, per §16.
- **Completion-signal risk**: introducing a new "exhausted" completion trigger changes `_process_turn`'s control flow, a function already covered by several passing tests (§13) — any change here needs new tests added, not just relying on existing ones, since none currently exercise turns 2-8 of the real 8-question cap (§14 gap 1).
- **False sense of completeness risk**: even after this fix, a Discovery session completed entirely via the deterministic fallback will still only have a `PROPOSED`, never `CONFIRMED`, `IDENTITY` claim plus a few more `PROPOSED` claims from the new fixed questions — the human must still do real confirmation work on the Blueprint page before Blueprint (and therefore Recommendations) can activate. This is correct/intended (§9), but worth stating so a future implementer doesn't mistake "Discovery completes" for "Blueprint is now populated with rich, ready-to-use data."
- **No security or tenant-isolation risk was identified** in the fix boundary described in §16 — it touches no auth, no tenant-scoping, no RBAC-gated action.

## 20. Explicit Non-Goals

Per the task's Step 14, explicitly confirmed **not required** by this investigation, and **not proven necessary** from source in this audit:

- Halla / Dropshipping implementation — not touched, not required.
- MCP client — not touched, not required (Discovery has no tool access at all, §2.I / module docstrings).
- RLS enforcement upgrade — explicitly out of scope; audit-mode-only status preserved (§12).
- A new Agent runtime — Discovery does not use `AgentReasoningService`/`ToolRegistry` and this audit found no reason it should start.
- A new Website Builder architecture — no interaction found between Discovery and Website Builder (Phases 11-12) in this path.
- A new Medical Tourism architecture — Discovery/Blueprint are already vertical-agnostic by construction (§2, cross-vertical tests); no vertical-specific code is implicated.
- OAuth — not implicated anywhere in this path.
- A Temporal redesign — no workflow engine appears in this path; `_process_turn` is a plain synchronous service call.
- New database tables — **not proven necessary**; §17 explicitly recommends attempting the fix without a schema change first.
- A new frontend state-management library — the existing `useState`/`useCallback` pattern in `page.tsx` is already sufficient for the (optional) frontend improvement noted in §16(I).
- A new AI framework — §4/§16 explicitly rule this out; the existing `AIProvider` abstraction is sufficient.

## 21. Proposed Future Implementation Phases

**RECOMMENDED**, sequencing only — not started, not scoped in detail here:

1. **Phase 16a (backend, deterministic fallback)**: extend `_deterministic_fallback()` to a short fixed sequence of generic questions covering `INDUSTRY`/`BUSINESS_MODEL`/`REQUIRED_CAPABILITIES`; add the "exhausted" completion signal to `DiscoveryExtractionService`/`BusinessDiscoveryService`; add the test-gap coverage from §14 (items 1-2 at minimum).
2. **Phase 16b (backend, hardening)**: close remaining test gaps from §14 (items 3-6) — dedicated `DiscoveryExtractionService` tests, malformed-output tests, concurrency test for `submit_answer`.
3. **Phase 16c (frontend, optional)**: improve the "no question, session still ACTIVE" UI state in `page.tsx` (§14 item 7, §16(I)) — independent of 16a/16b, not blocking.
4. **Phase 16d (validation)**: a real live end-to-end walkthrough with a configured provider (§15), and separately, a live walkthrough exercising the improved deterministic fallback with `AI_PROVIDER=deterministic`, to prove both paths now reach `COMPLETED` through the real UI.

This sequencing is a recommendation for future planning only — this audit performs none of it.

## 22. Final Recommendation

**INFERRED/RECOMMENDED**: The repository already contains everything needed to make Discovery completable in the general (real-provider) case with zero code changes — an operator simply needs to configure a supported provider's API key. The only genuine gap is the honesty/completeness of the **no-provider deterministic degrade path**, and that gap is narrow, well-understood, already partially documented as a known limitation by the codebase's own prior phases, and does not require touching the AI provider layer, Business Journey, Blueprint activation, Recommendation Engine, or any database schema. The fix is bounded to one service function plus a small completion-signal change, with new tests to actually exercise the real 8-question cap path this audit found is currently untested.

---

# FINAL CLASSIFICATION: READY FOR IMPLEMENTATION
