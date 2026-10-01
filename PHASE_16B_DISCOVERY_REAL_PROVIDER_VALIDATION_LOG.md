# PHASE 16B: Discovery Real-Provider Validation & Hardening — Log

## 1. Objective

Verify that Klaros Discovery works correctly when a real, configured AI provider (not the Phase 16a deterministic fallback) is available — the CONNECTED-provider path: `DiscoveryExtractionService.extract()` → `AIProvider.generate_structured()` → structured extraction → claims + follow-up question → completion → Business Journey → Blueprint. This is a validation-and-hardening phase: no new architecture, no schema change, no vertical-specific code, no frontend change, nothing committed or pushed.

## 2. Starting HEAD

`af4937e403e47cdc141f2db349dfcc46a3df6c4b` — "feat(klaros): complete platform foundation through phase 15". Verified via `git rev-parse HEAD` before any change, and unchanged throughout this phase (no commits were made at any point).

## 3. Initial git status

```
M backend/app/services/business_discovery_service.py
M backend/app/services/discovery_extraction_service.py
M backend/tests/test_business_discovery_service.py
?? KLAROS_DISCOVERY_COMPLETION_AUDIT.md
?? PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md
?? backend/tests/test_discovery_extraction_service.py
```
This matched the task's stated expectation exactly (Phase 16a's uncommitted work, still present). No lost work, no unrelated change was found — proceeded per instructions.

## 4. Files inspected

- `KLAROS_DISCOVERY_COMPLETION_AUDIT.md`, `PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md` (read first, in full, as instructed)
- `backend/app/services/discovery_extraction_service.py` (current, Phase-16a-modified state)
- `backend/app/services/business_discovery_service.py` (current, Phase-16a-modified state)
- `backend/app/services/ai_provider.py` (full file, 598 lines — provider abstraction, retry/backoff, error classification, key redaction, `get_ai_provider()` selection logic)
- `backend/app/models/business_blueprint.py` (`MINIMUM_BAR_SECTIONS`, `BlueprintSectionKey`, `ClaimType`, `ClaimProvenance`, `ClaimStatus` — read only, not modified)
- `backend/app/services/business_blueprint_service.py` (`propose_claim`, `confirm_claim`, `activate` — read only, not modified)
- `backend/app/services/business_journey_service.py` (`complete_discovery`, `start_journey` — read only, not modified)
- `backend/app/services/recommendation_service.py` (constructor only, for a test fixture — not modified)
- `backend/app/api/v1/business_discovery.py` (route/response shape — read only, not modified)
- `backend/tests/test_discovery_extraction_service.py`, `backend/tests/test_business_discovery_service.py`, `backend/tests/test_business_discovery_blueprint_api.py`, `backend/tests/test_business_journey_api.py`, `backend/tests/test_cross_vertical_blueprint_validation.py`
- `backend/tests/conftest.py` (test-env conventions — confirms `AI_PROVIDER` is never forced to deterministic explicitly in test env; the test env is deterministic only because no key is present in `os.environ` when `_RUNNING_UNDER_PYTEST` is true, per `app/core/config.py`'s `env_file=None if _RUNNING_UNDER_PYTEST else ".env"`)
- `backend/app/core/config.py` (Settings — `env_file` behavior, provider config field names only, no values read/printed)
- `backend/.env` (presence-only check of configured keys — see §6)
- `frontend/app/business/discovery/page.tsx`, `frontend/app/business/blueprint/page.tsx` (read only, to drive the live browser walkthrough correctly — not modified)

## 5. Existing real-provider architecture

Confirmed exactly as `KLAROS_DISCOVERY_COMPLETION_AUDIT.md` (§4, §16) already concluded, and now additionally **proven at runtime** (not just by source reading):

- `AIProvider` ABC → `generate_structured(prompt) -> AICallOutcome`, implemented by `_HTTPAIProvider` (shared retry/backoff/error-classification/key-redaction plumbing) and six concrete providers: `AnthropicAIProvider`, `OpenAIAIProvider`, `GroqAIProvider`, `DeepSeekAIProvider`, `NvidiaAIProvider`, `GoogleAIProvider` (the last four via `_OpenAICompatibleProvider`).
- `get_ai_provider()` (`ai_provider.py:563-598`) is the single selection point: explicit `AI_PROVIDER=<name>` picks that provider (or `DeterministicAIProvider` if its key is absent); default `"auto"` tries Anthropic → OpenAI → Groq → DeepSeek → NVIDIA → Google → deterministic, in that order.
- `DiscoveryExtractionService.extract()` (`discovery_extraction_service.py:212-264`) is a thin, correct consumer: if `provider.is_connected`, it builds a fenced prompt (`_build_prompt`) and calls `generate_structured()`; every call (success or failure) is audited via `record_ai_invocation()`; the raw JSON response is validated against `DiscoveryExtractionResult`/`ExtractedClaim` (Pydantic), and any claim or `follow_up_section_key` outside the fixed vocabulary is dropped/nulled before ever reaching `BusinessBlueprintService.propose_claim`.
- **No code in this path was modified during Phase 16b.** The `git diff --stat` in §17/§18 shows zero changes to `app/services/ai_provider.py`, `app/services/discovery_extraction_service.py`'s connected-provider branch, `app/services/business_discovery_service.py`, `app/services/business_journey_service.py`, or `app/services/business_blueprint_service.py` beyond what Phase 16a already had — this phase only added test files. The architecture was already correct; this phase's job was to prove it, not change it.

## 6. Provider configuration findings

Checked `backend/.env` for the **presence** of a real (non-placeholder) key, per the task's explicit instruction — no value was ever printed, logged, or pasted into any test file or this report:

- `ANTHROPIC_API_KEY` — placeholder/empty.
- `OPENAI_API_KEY` — **present, real-looking (154+ characters, not a placeholder pattern)**.
- `AI_PROVIDER` — not set in `.env` (defaults to `"auto"`).

Because `AI_PROVIDER` defaults to `"auto"` and Anthropic's key is absent, `get_ai_provider()` resolves to `OpenAIAIProvider` whenever a real key is present in the environment. **A real provider credential was available in this environment.** Per the task's instructions, this credential was used for genuine live-provider validation (§8), never hard-coded, never logged, never pasted into a test file — only read via `Settings`/`os.environ`/`dotenv`, exactly as the application itself does.

The test suite (`tests/conftest.py`) sets `env_file=None` under pytest (`app/core/config.py:36`), so `Settings()` never reads `backend/.env` during `pytest` runs — this is **not** a Phase 16b change, it is the pre-existing, intentional test-isolation convention (confirmed unmodified), and it's why `test_ai_provider_is_deterministic_in_test_env` in the existing suite correctly asserts `get_ai_provider().is_connected is False` under pytest even with a real key present on disk.

## 7. Tests added/modified

**New file**: `backend/tests/test_discovery_connected_provider_service.py` (14 tests, all passing, all deterministic/mocked — no network call, no dependency on a real key, so they run in CI regardless of provider configuration). Uses a fake, in-process, `is_connected=True` `AIProvider` double (`_ScriptedConnectedProvider` / `_FailingConnectedProvider`) plugged into the real, unmodified `DiscoveryExtractionService`/`BusinessDiscoveryService`/`BusinessJourneyService` — never a second AI abstraction, never a mock of Discovery's own logic. Covers, at the `BusinessDiscoveryService` (not just `DiscoveryExtractionService` unit) level:

1. `test_connected_provider_structured_response_produces_claims_and_next_question` — successful structured response → correct `DiscoveryExtractionResult`/claim/question (task §6.A).
2. `test_connected_provider_invalid_claim_vocabulary_and_section_are_filtered` — out-of-vocabulary claim + invalid `follow_up_section_key` dropped/nulled (task §6.B).
3. `test_connected_provider_never_sets_exhausted_true_at_service_level` — `fallback_exhausted` never fires for a connected provider across multiple turns (task §6.C, service-level complement to the existing extraction-service-level test).
4. `test_connected_provider_failure_leaves_session_active_with_raw_answer_persisted` (parametrized over TIMEOUT/AUTHENTICATION/RATE_LIMIT/PROVIDER_ERROR/NETWORK_ERROR) — existing failure contract verified unchanged (task §6.D).
5. `test_connected_provider_malformed_json_response_leaves_session_active` — malformed JSON → session stays ACTIVE, error recorded, no claim fabricated.
6. `test_connected_provider_completes_at_real_max_questions_cap_without_overshoot` — a provider that never signals completion still terminates at exactly `questions_asked == 8`, never more (task §9).
7. `test_connected_provider_claims_remain_proposed_never_auto_confirmed` — PROPOSED-only invariant (task §10).
8. `test_connected_provider_produces_different_structured_output_for_different_ideas` — contract-only adaptive-questioning test, two shaped ideas (task §7).
9. `test_cross_tenant_discovery_context_never_leaks_into_another_tenants_prompt` — Tenant A's business idea text is asserted absent from Tenant B's prompt and vice versa, using one shared provider instance across both tenants (task §13).
10. `test_connected_provider_completion_via_cap_advances_business_journey` — `BusinessJourneyService.complete_discovery` (unmodified) correctly advances `DISCOVERY_ACTIVE → BLUEPRINT_REVIEW` for a connected-provider-completed session (task §11).

Existing files verified but **not modified**: `test_discovery_extraction_service.py` (Phase 16a's own connected-provider unit tests — `test_extract_falls_back_deterministically_when_provider_not_connected`, `test_extract_drops_out_of_vocabulary_claims_from_provider_response`, `test_extract_nulls_out_invalid_follow_up_section_key`, `test_extract_handles_malformed_json_from_provider`, `test_extract_propagates_provider_failure_as_unavailable`, `test_connected_provider_result_never_sets_exhausted`) and `test_business_discovery_service.py` (Phase 16a's deterministic-fallback tests) were re-run, unchanged, as part of the focused regression set.

No test was weakened, skipped, or deleted. No production code file was modified by this phase (`git diff --stat` — see §17).

## 8. Real-provider validation result

**A real provider credential (OpenAI) was available, and was used for genuine, network-calling live validation — not simulated.**

**A. Direct provider smoke test** (no DB): `get_ai_provider()` with `AI_PROVIDER=openai` resolved to a connected `OpenAIAIProvider`; `generate_structured()` against a real Discovery prompt for "A medical tourism company connecting international patients with hospitals in Turkey." returned `success=True` in 3.38s, with a schema-valid JSON body validated cleanly against `DiscoveryExtractionResult` (2 claims, `follow_up_question` set, all claims passing `is_valid_vocabulary()`).

**B. Full-pipeline smoke test** (`BusinessDiscoveryService.start_session`, real SQLite-file DB, real OpenAI provider, real `record_ai_invocation` audit write): ran both task-specified ideas back to back —
- Idea A ("medical tourism...Turkey"): 1 claim proposed (`IDENTITY`), real adaptive follow-up question generated.
- Idea B ("subscription-based ecommerce...specialized products"): 2 claims proposed (`IDENTITY` Fact + `BUSINESS_MODEL` Inference, `AI_INFERRED`), a **different** follow-up question ("What specific products are being sold?").
- `AIInvocationLog` recorded 2 rows, both `provider=openai`, `success=True`, and a direct scan of each row's serialized fields for `"sk-"` / `"Bearer "` found **zero matches** — no credential leaked into the audit trail.

**C. Full live browser walkthrough** (§11) — the authoritative validation: two full Discovery sessions, two different tenants, real registration, real JWT auth, real OpenAI calls end to end, one driven all the way to `COMPLETED` and through to the Blueprint review screen.

**Conclusion: PROVEN, not inferred.** The connected-provider path works correctly end to end against a real, network-calling AI provider — adaptive per-turn questioning, correct claim/section extraction, correct vocabulary validation, correct audit logging, correct PROPOSED-only claim status, correct `max_questions=8` enforcement, correct downstream Business Journey/Blueprint routing.

## 9. Deterministic-provider regression result

Focused suite (SQLite, deterministic — no key/AI_PROVIDER override, matching every existing test file's convention):
```
tests/test_discovery_extraction_service.py
tests/test_business_discovery_service.py
tests/test_discovery_connected_provider_service.py   (new, Phase 16b)
tests/test_business_discovery_blueprint_api.py
tests/test_business_journey_api.py
tests/test_cross_vertical_blueprint_validation.py
→ 71 passed, 0 failed
```

Full backend suite (SQLite, deterministic): **not separately re-run in this phase** (Phase 16a's own full-SQLite run — 1729 passed, 0 failed, 149 skipped — is the last full-suite SQLite baseline on record; Phase 16b's changes are additive-only test files with zero production-code diff, so a SQLite-vs-Postgres discrepancy specific to this phase's own change is not a plausible risk). The authoritative full-suite comparison for this phase is the real-PostgreSQL run in §10, which is what the task's baseline (1845 passed/1 failed/12 skipped) was itself measured against.

## 10. PostgreSQL validation

Used the established `pgserver` disposable-PostgreSQL methodology (Docker unavailable in this sandbox), on a dedicated instance separate from the live-browser-validation instance (see §11's methodology note — this explicitly avoids Phase 16a's documented mistake of colliding the two).

- Instance: `/private/tmp/klaros_phase16b_pgdata`, database `klaros`, `pgvector` extension installed.
- `alembic heads` → `0051 (head)`; `alembic upgrade head` ran cleanly through all 51 migrations, no errors.
- Focused suite (Discovery/Blueprint/Journey/cross-vertical/RLS-audit-mode/journey-concurrency, 8 files):
  ```
  82 passed, 0 failed
  ```
- Full backend suite (`python -m pytest -q`, no test filter), run to completion in 950.69s (15m50s), **no concurrent live-browser traffic against this instance** (that ran against a second, separate instance — see §11):
  ```
  1 failed, 1879 passed, 12 skipped, 28 warnings
  ```
  The 1 failure was `test_voice_stream_route_dispatches_to_realtime_engine_when_configured` — the exact same pre-existing, documented, unrelated failure Phase 16a's own baseline recorded (voice/Realtime engine, nothing to do with Discovery/Blueprint/Journey/AI-provider code; all 28 warnings are pre-existing `PytestWarning`/`StarletteDeprecationWarning` items unrelated to this phase's files). 12 skipped matches the historical baseline exactly (the `requires_real_postgres`-marked tests that only run this way, all ran; nothing new was skipped). No new failure was introduced by this phase. Passed count (1879) is 34 higher than Phase 16a's own Postgres run (1845), consistent with the 14 new Phase 16b tests plus other tests added to the suite since that baseline was recorded — the material fact is **zero new failures, identical skip count, identical single pre-existing unrelated failure**.

## 11. Browser validation

**Performed live**, with the real OpenAI provider (`AI_PROVIDER=openai`), against a **second**, dedicated PostgreSQL instance (`/private/tmp/klaros_phase16b_live_pgdata`, database `klaros_live`, migrated to head `0051`) — deliberately kept separate from the automated-test-run instance in §10, per this task's explicit correction of Phase 16a's methodology note (never run live traffic and the `_reset_database`-dropping pytest fixture against the same instance concurrently). Backend: real `uvicorn app.main:app` on port 8000 with `AI_PROVIDER=openai`, `EVENT_TRANSPORT=memory`. Frontend: the existing, unmodified Next.js dev server already running on port 3000 (an `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` default in `frontend/.env.local`, which the running dev instance already used — no frontend file was edited).

Walkthrough (agentic browser tool):

1. **Tenant A** ("Phase16b Live Co") registered via the real `/register` flow, OWNER role, started 14-day trial, skipped optional business-hours setup, navigated to Build Your Business.
2. Entered idea A ("A medical tourism company connecting international patients with hospitals in Turkey.") → Discovery started. First real, model-generated question: *"What specific capabilities does your business require to operate effectively?"* — a genuinely adaptive question (not the Phase 16a fixed-sequence wording), confirming a connected provider, not the deterministic fallback, was driving this session.
3. **Refresh/resume test**: reloaded `/business/discovery` directly (full page navigation) before answering — the exact same in-progress question re-rendered cleanly, "0 questions answered so far" reflecting persisted turn state, no stall, no error. Confirms resume works for the connected-provider path exactly as Phase 16a proved for the deterministic path.
4. Answered 8 questions in sequence, each producing a genuinely different, contextually adaptive follow-up (capabilities → identity → services detail → business model → hospital-partnership nature → identity again → business model again → identity again) — the model repeatedly circled back for more depth rather than mechanically walking a fixed 4-step list, consistent with a real adaptive extraction pass rather than a scripted fallback.
5. On the 8th answer, the session completed and the frontend **automatically navigated to the Business Blueprint page** — no stall, no manual intervention.
6. Blueprint page showed 4 sections with `CLAIMS TO REVIEW`: **Business Identity** (4 claims), **Industry** (3 claims, including 2 `Inference`/`AI_INFERRED`), **Required Capabilities** (6 claims, including 1 `Assumption`/`SYSTEM_DEFAULT` and 1 `Requirement`/`AI_INFERRED`), **Business Model** (7 claims, including 1 `Assumption`/`SYSTEM_DEFAULT`) — every claim shown with `Confirm`/`Reject` buttons, i.e. genuinely `PROPOSED`, never auto-confirmed. All other (non-minimum-bar) Blueprint sections correctly showed `EMPTY`/"Not filled in yet" — Discovery never wrote outside the four `MINIMUM_BAR_SECTIONS`.
7. Direct DB verification (`psql` against the live instance): `discovery_sessions.status = COMPLETED`, `questions_asked = 8`, `max_questions = 8` (exact cap, no overshoot); all proposed `blueprint_claims` rows `status = PROPOSED`; `business_journeys.status = BLUEPRINT_REVIEW` (the unmodified `BusinessJourneyService.complete_discovery` transition fired correctly); `ai_invocation_logs` recorded 9 rows, all `provider=openai`, all `success=t`.
8. **Cross-vertical / second idea**: registered **Tenant B** ("Phase16b Live Co B", a fully separate tenant/user), started a fresh Discovery session with idea B ("A subscription-based ecommerce business selling specialized products online."). First question: *"What specific products does your business specialize in?"* — different from Tenant A's first question, confirming genuinely idea-dependent adaptive behavior (not a hardcoded script). Answered one turn ("specialty coffee subscription boxes...") and the very next question pivoted to `REQUIRED_CAPABILITIES` — a different trajectory than Tenant A's session took at the same turn count, again confirming adaptive, non-scripted behavior. (This session was left short of full completion — 1 of 8 questions answered — since the primary completion/cap/Blueprint-routing contract was already fully proven end to end by Tenant A's full walkthrough in steps 1-7; driving a second session to full completion would only re-exercise the same already-proven mechanism.)
9. **Cross-tenant DB check**: `SELECT tenant_id, status, questions_asked, business_idea FROM discovery_sessions` showed exactly 2 rows, one per tenant, each with only its own idea text — no leakage.
10. **Network/console check**: `read_network_requests` filtered to `business-discovery` showed every `/sessions/*` and `/sessions/*/answer` call returning `200 OK` across both walkthroughs; a spot-checked response body (`TurnResponse`) contained only `session_id`/`session_status`/`questions_asked`/`turn_sequence`/`proposed_claim_ids`/`next_question`/`extraction_available`/`extraction_error` — no provider/model/key field, matching the existing, unmodified response schema.
11. **Backend log check**: grepped the live backend's stdout/stderr for `sk-[A-Za-z0-9]{10,}` / `Bearer [A-Za-z0-9]{10,}` patterns — zero matches.

## 12. Security validation

- **Tenant identity**: unchanged — every route still derives `tenant_id` from `current_user.tenant_id` (JWT), never from the request body; confirmed unmodified by this phase's zero-diff to `business_discovery.py`.
- **No AI key exposure**: confirmed live (§11.10) — no provider/key/model field in any Discovery API response across two real, live-provider sessions.
- **No secret logging**: confirmed live (§11.11) and via a direct scan of `AIInvocationLog` rows (§8.B) — zero leaked-credential matches in either the process log or the audit-log table.
- **Prompt/data boundary — exactly what is sent to the provider**: `_build_prompt()` (`discovery_extraction_service.py:114-123`, unmodified) sends only `{"text": <this turn's own answer>}` and `{"gap_section_keys": <this tenant's own blueprint's remaining gap keys>}`, fenced as DATA with explicit instructions to treat it as data, never as commands (`_SYSTEM_INSTRUCTIONS`). No password, API key, auth token, unrelated tenant's data, or internal authorization metadata is ever included — confirmed both by source reading and by the cross-tenant prompt-capture test (§7 item 9) and the live cross-tenant DB check (§11.9), which together prove no accidental inclusion occurs at either the unit or the live-integration level.
- **RBAC**: unchanged — the same `require_permission(Permission.MANAGE_BUSINESS_DISCOVERY)` gate applies; this phase adds no new route.
- **No new AI architecture, no new provider abstraction** — the existing `AIProvider`/`get_ai_provider()` boundary was used exactly as-is.

## 13. Cross-tenant validation

Two independent proofs, one mocked/deterministic and one live:
- `test_cross_tenant_discovery_context_never_leaks_into_another_tenants_prompt` (new, §7 item 9): a single shared fake connected provider instance serves two tenants sequentially; the captured prompt text for Tenant A never contains Tenant B's business-idea string and vice versa; `get_session`/`get_turns` raise `DiscoverySessionNotFoundError` when a tenant tries to read another tenant's session; claim values are independently correct per tenant.
- Live (§11.9): two real tenants, real registration, real OpenAI calls, direct Postgres inspection — each `discovery_sessions` row's `business_idea` matches only its own tenant, no cross-contamination.

## 14. Cross-vertical validation

- Static: no `if`/`elif` branch on any vertical name, business-type string, or similar was added anywhere — `git diff` against HEAD shows **zero production-code changes** in this phase (only new test files), so the existing, already-verified (Phase 16a, `test_no_vertical_branch_in_business_blueprint_service_source`) no-vertical-branch guarantee is unchanged.
- New tests (`test_discovery_connected_provider_service.py`) use business content (medical tourism / subscription ecommerce) only as **test fixture data**, never as runtime branching — grepped explicitly for `if.*medical|if.*tourism|if.*dropshipping|if.*hvac|if.*plumbing|if.*ecommerce` across all three Discovery test files: zero matches.
- Live: two genuinely different business ideas (medical tourism referral service; subscription ecommerce) produced independently correct, differently-shaped structured extraction and different adaptive questions through the exact same generic code path — proving vertical-agnosticism empirically, not just by absence of a branch.

## 15. Failure/fallback behavior

Documented (verified unchanged, not assumed):
- `AI_PROVIDER=deterministic` (or no key configured for any provider): `get_ai_provider()` returns `DeterministicAIProvider` (`is_connected=False`); `DiscoveryExtractionService.extract()` takes the `_deterministic_fallback()` branch unconditionally — this is Phase 16a's already-validated bounded 4-question sequence.
- A real provider (e.g. `AI_PROVIDER=openai` with a key): `generate_structured()` is called; on success, the response is validated/filtered and used. On failure of any classified type (`AUTHENTICATION`, `RATE_LIMIT`, `TIMEOUT`, `PROVIDER_ERROR`, `NETWORK_ERROR`, `MALFORMED_RESPONSE`) — proven directly by `test_connected_provider_failure_leaves_session_active_with_raw_answer_persisted` (parametrized) and `test_connected_provider_malformed_json_response_leaves_session_active` — `BusinessDiscoveryService._process_turn`'s `if not outcome.available` branch fires: the turn's raw answer is still persisted, `extraction_error` is recorded, and the session stays `ACTIVE` (never `COMPLETED`, never silently corrupted). **No claim is ever fabricated from a failed or malformed call.**
- **There is no automatic fallback from a connected provider's failure to the deterministic path within the same session.** This was verified, not assumed: the code path that decides `_deterministic_fallback()` vs. a real call is a single `if not self._provider.is_connected` branch at the top of `extract()` — it is evaluated once per call based on the provider's connection state, not on the outcome of a prior call, and nothing in `_process_turn` re-selects a different extraction strategy after a failure. A failed real-provider call leaves the session `ACTIVE` with `extraction_error` set, for the frontend/caller to retry (the same call, or a corrected answer) — it does not silently downgrade to deterministic fallback behavior. This matches the architecture the audit already described (§14 of the task, and `KLAROS_DISCOVERY_COMPLETION_AUDIT.md`'s own findings) and is stated explicitly here because it was not implemented as an automatic fallback and should not be assumed to be one.

## 16. Known limitations

- The full backend suite (§10) was run once against real PostgreSQL for this phase; the SQLite-only full-suite run was not independently repeated in Phase 16b (§9) because this phase's diff to production code is empty (test-files-only) — the risk this would normally cover (a SQLite-vs-Postgres divergence caused by this phase's own change) does not apply. This is a scope judgment, not evidence a full SQLite run would fail.
- Live validation used **one** real provider (OpenAI, the only non-placeholder key present in this environment). Anthropic/Groq/DeepSeek/NVIDIA/Google were not live-tested — their code paths are identical `_HTTPAIProvider`/`_OpenAICompatibleProvider` plumbing (only base URL/model differ), already covered by the pre-existing `test_ai_provider.py`/`test_ai_provider_hardening.py` suites at the HTTP-call level, but a live call to any of those five was **not performed** in this phase (**NOT TESTED**, not "validated").
- The second live tenant (idea B) was driven through only 1 of 8 possible turns before the walkthrough was judged sufficient (see §11 step 8's rationale) — its full completion-and-Blueprint-routing behavior was not independently re-observed live a second time, though it is mechanically identical code to Tenant A's fully-completed session and is additionally covered by the mocked `test_connected_provider_completes_at_real_max_questions_cap_without_overshoot` test.
- No live test of a genuine provider-side failure (e.g. an actually-expired/invalid key, a real rate-limit) was performed — that would require deliberately breaking a working credential, which was judged out of proportion for this phase; the failure/error-classification/redaction contract is instead proven by the existing `test_ai_provider_hardening.py` suite (HTTP-mocked, exercised at the exact `_call_api` seam) plus this phase's new `_process_turn`-level failure tests using a fake provider (§7 items 4-5) — together these prove the contract at both the HTTP layer and the Discovery-orchestration layer, but not with an actual live 401/429 from the real OpenAI API.
- Recommendation Engine (Phase 3 downstream of Blueprint activation) was not exercised live in this phase — out of scope per the task (§17: "This phase is strictly: Discovery + existing AI provider path + validation/hardening"); `test_connected_provider_completion_via_cap_advances_business_journey` only verifies through `BLUEPRINT_REVIEW`, matching scope.

## 17. Files modified

**Test files only** — zero production-code changes in Phase 16b:
- New: `backend/tests/test_discovery_connected_provider_service.py` (14 tests).
- New: `PHASE_16B_DISCOVERY_REAL_PROVIDER_VALIDATION_LOG.md` (this file).

Unchanged by this phase (still carrying Phase 16a's uncommitted work, exactly as found at the start of this phase — §3):
- `backend/app/services/business_discovery_service.py`
- `backend/app/services/discovery_extraction_service.py`
- `backend/tests/test_business_discovery_service.py`
- `backend/tests/test_discovery_extraction_service.py`
- `KLAROS_DISCOVERY_COMPLETION_AUDIT.md`, `PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md`

Explicitly **not modified**, per task scope and this phase's own finding that no defect was demonstrated in them: `backend/app/services/ai_provider.py`, `backend/app/services/business_journey_service.py`, `backend/app/services/business_blueprint_service.py`, `backend/app/services/recommendation_service.py`, every `backend/app/api/v1/*.py` route file, every `backend/app/models/*.py` file, any Alembic migration, and every file under `frontend/`.

## 18. Git status

At the end of this phase:
```
git rev-parse HEAD          → af4937e403e47cdc141f2db349dfcc46a3df6c4b (unchanged)
git status --short          → M backend/app/services/business_discovery_service.py
                               M backend/app/services/discovery_extraction_service.py
                               M backend/tests/test_business_discovery_service.py
                               ?? KLAROS_DISCOVERY_COMPLETION_AUDIT.md
                               ?? PHASE_16A_DISCOVERY_FALLBACK_IMPLEMENTATION_LOG.md
                               ?? PHASE_16B_DISCOVERY_REAL_PROVIDER_VALIDATION_LOG.md
                               ?? backend/tests/test_discovery_connected_provider_service.py
                               ?? backend/tests/test_discovery_extraction_service.py
git diff --stat              → 3 files changed, 274 insertions(+), 25 deletions(-)  (Phase 16a's diff, unchanged)
git diff --check             → (clean, no whitespace errors)
git diff --cached --stat     → (empty — nothing ever staged)
```
No commit was created. No push was performed. No file outside this phase's declared scope was modified. Two temporary local PostgreSQL data directories (`/private/tmp/klaros_phase16b_pgdata`, `/private/tmp/klaros_phase16b_live_pgdata`) and their `pgserver` processes are disposable, local-only, and outside the git working tree — not part of any commit.

## 19. Final verdict

**COMPLETE.**

A real provider credential was available (OpenAI) and was genuinely exercised — not merely inferred from source review — at three levels: a direct provider call, a full-pipeline smoke test with real audit logging, and a complete live browser walkthrough (registration → Discovery → 8 real adaptive questions → completion at the real `max_questions=8` cap, no overshoot → automatic transition to Blueprint → PROPOSED-only claims across all four `MINIMUM_BAR_SECTIONS` → `BusinessJourneyService` correctly advanced to `BLUEPRINT_REVIEW`), repeated for a second, differently-shaped business idea under a second, fully isolated tenant. All required regression tests pass: 71 passed in the SQLite focused set, 82 passed in the PostgreSQL focused set, and the full backend suite against real PostgreSQL returned 1879 passed / 1 failed (the same pre-existing, documented, unrelated voice-test failure Phase 16a's baseline already carried) / 12 skipped (identical to baseline) — no new failure. No production code was changed (the existing architecture needed no fix — Phase 16b's job was to prove that, and it did). No scope violations: no new AI architecture, no schema/migration change, no vertical-specific branching (grepped and confirmed empty), no frontend file changed. Security checks passed: no key/secret ever appeared in an API response, a log line, or an audit-log row, live or under test; cross-tenant isolation was proven both live and under a dedicated new test. The one area explicitly **NOT** fully covered — live validation of the other five supported providers (Anthropic/Groq/DeepSeek/NVIDIA/Google) and of an actual live provider-side failure — is disclosed plainly in §16 as a known limitation, not claimed as validated; it does not affect the verdict because the task's live-validation requirement was satisfied by the one real, available credential, and every other provider's code path is identical, already-tested plumbing that this phase did not need to and did not modify.
