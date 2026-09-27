# Klaros — Business Discovery Engine Specification

Status: design proposal. No backend/frontend code was created. Confirmed current state: no `business_discovery`/`recommend` route, model, or service exists anywhere in `backend/app` (grep-confirmed, both audit passes); `frontend/app/onboarding/page.tsx` is a real but unrelated 4-step plan/timezone/free-text-knowledge wizard (verified) — it is the closest existing UI pattern to reuse for the intake step, not a discovery engine itself.

## 1. Design principle

Free-text business description in, **no giant predefined form**. Adaptive follow-up questions are asked only where they materially change the resulting Blueprint's `REQUIRED_CAPABILITIES` or a `BlueprintSection`'s completion status — never as an exhaustive checklist. Both test businesses (`KLAROS_MEDICAL_TOURISM_VALIDATION.md`, `KLAROS_DROPSHIPPING_VALIDATION.md`) are walked through this exact flow to validate the "adaptive, not exhaustive" claim rather than asserting it abstractly.

## 2. Requirements Engine data model — claim types

All persisted as `BlueprintClaim` rows (`KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §3), discriminated by `claim_type`:

| Claim type | Meaning | Source | Example |
|---|---|---|---|
| **Fact** | Directly stated by the user, high confidence | `USER_STATED` | "We connect patients with hospitals in Turkey and India." → `geography.target_countries = [TR, IN]` |
| **Inference** | Derived by the discovery model from stated facts, not directly said | `AI_INFERRED` | From "connecting patients with hospitals" → infer `business_model.type = referral_brokerage` |
| **Assumption** | A default applied when the user hasn't specified, needed to proceed | `SYSTEM_DEFAULT` | No mention of currency → assume tenant's Organization billing currency |
| **Requirement** | A capability the business model necessitates regardless of what the user asked for | `AI_INFERRED` | Cross-border referrals → `required_capabilities += "multi_currency_commission"` |
| **Preference** | User-expressed but non-binding | `USER_STATED` | "We'd like a modern, clean website style." |
| **Constraint** | A hard limit the user stated | `USER_STATED` | "We can only operate where we have licensed provider partners." |
| **Decision** | A choice the user made among presented options (e.g. which of several recommended integrations to connect) | `USER_STATED` | "Connect Stripe, not a manual invoice flow." |
| **Unknown** | Explicitly flagged as not yet determinable — surfaced to the user as an open question, never silently defaulted for capability-critical fields | N/A (`status=PROPOSED`, no `value`) | "Which country's medical licensing body governs the clinics you list?" — deferred until answered because it affects `COMPLIANCE` section completion |

Every claim carries `confidence` (0–1, only meaningful for `AI_INFERRED`) and `evidence` (a pointer back into the discovery conversation transcript, stored as a `DiscoverySession` message reference — see §4) so a human reviewing the Blueprint can see *why* Klaros believes something, addressing the "why did Klaros do this" observability requirement at the data-capture stage, not just at execution time.

## 3. Pipeline

```
Free-text business description (new DiscoverySession, one per Blueprint attempt)
  → LLM extraction pass (AI_PROVIDER-selected, same provider abstraction as
    ai_provider.py, DETERMINISTIC fallback when no key configured — see §6)
  → Draft BlueprintClaim rows (Facts + Inferences), each tagged with which
    REQUIRED_CAPABILITIES / BlueprintSection they touch
  → Gap check: for each BlueprintSection needed to reach ACTIVE status
    (KLAROS_BUSINESS_BLUEPRINT_SPEC.md §6), is there at least one CONFIRMED-
    eligible claim? If not → generate an adaptive follow-up question (see §4)
  → User answers (free text or structured choice) → new claims extracted from
    the answer → gap check repeats
  → Once minimum sections are claim-covered, user reviews and confirms/edits
    claims in bulk (single review screen, not per-claim confirmation friction)
  → BlueprintSection.data recomputed from CONFIRMED claims → Blueprint → ACTIVE
```

## 4. Adaptive follow-up question generation — bounded, not open-ended

Questions are generated only against the **gap list** (which `REQUIRED_CAPABILITIES`-relevant sections still lack a confident claim), and each question generation call is itself schema-constrained: the model must return a question object referencing one specific missing field, not freeform chat. This bounds question count naturally — a business description that already covers most sections (e.g. a detailed dropshipping description naming the supplier, product category, and target market) produces few or zero follow-ups; a one-line description produces more, but only for genuinely unresolved, capability-relevant gaps. A hard cap (configurable, default 8 follow-up questions per session) prevents runaway interrogation even if gap detection misbehaves.

`DiscoverySession` entity: `id`, `tenant_id`, `blueprint_id`, `status` (IN_PROGRESS/COMPLETE/ABANDONED), `messages` (JSONB array: role, content, timestamp — the conversation transcript claims cite as `evidence`), `questions_asked` (count, for the cap).

## 5. What Discovery does NOT do

It does not ask about anything that doesn't map to a `REQUIRED_CAPABILITIES` entry or a section-completion gap (no "what's your company culture" filler). It does not silently assume capability-critical Unknowns (compliance/licensing-relevant fields are never auto-defaulted — they become an explicit follow-up or an `Unknown` claim surfaced to the user before the Blueprint can reach `ACTIVE`). It does not write directly to CRM/Finance/any operational table — its only output is `BlueprintClaim`/`BlueprintSection` rows, kept strictly upstream of the operational data model per `KLAROS_TARGET_ARCHITECTURE.md`'s layering.

## 6. AI provider reuse

Extraction and question-generation calls reuse the existing `AI_PROVIDER` abstraction (`backend/app/services/ai_provider.py`, verified real) — same Anthropic/OpenAI switch, same `DeterministicAIProvider` fallback when no API key is configured. In the deterministic-fallback case, Discovery degrades to a fixed, still-useful behavior: the free-text description is stored as a single `Fact` claim against `IDENTITY.description`, and the user is walked through a short fixed set of the most capability-differentiating questions (business type, geography, revenue model) via structured choice rather than open text — never a silent failure, matching the existing codebase's "honest degrade" pattern (`ai_provider.py`'s `mode=DETERMINISTIC`, `error_monitoring.py`'s Sentry-optional fallback).

## 7. New surfaces

- **Backend**: `POST /api/v1/business-discovery/sessions`, `POST /api/v1/business-discovery/sessions/{id}/messages`, `GET /api/v1/business-discovery/sessions/{id}/gaps`, `POST /api/v1/business-blueprint/claims/{id}/confirm` (see `KLAROS_API_EVOLUTION_PLAN.md`).
- **Frontend**: new `/discovery` route (conversational intake), feeding into the existing-pattern-reused Blueprint review screen. The existing `/onboarding` wizard's plan/timezone steps are preserved as-is (billing and scheduling setup are orthogonal to business discovery); its free-text knowledge-capture step (`services`/`pricing`/`voice` → `KnowledgeFile`) is a candidate to be superseded by Discovery's richer extraction for new tenants, while remaining available for existing tenants who already completed it (no forced re-onboarding).

## 8. Failure modes (see `KLAROS_EXECUTIVE_ARCHITECTURE_SUMMARY.md` appendix for the full matrix)

Incomplete information → session stays `IN_PROGRESS`, Blueprint stays `DRAFT`, user can resume later. AI misunderstands the business → user edits claims directly in the review screen before confirming (human-in-the-loop is the correction mechanism, not a "regenerate" black box). User changes business model after Blueprint is `ACTIVE` → new `DiscoverySession` against the same Blueprint, producing a new `version` (§`KLAROS_BUSINESS_BLUEPRINT_SPEC.md` §6), never silently overwriting confirmed claims without a diff/review step.

## Cross-references

`KLAROS_BUSINESS_BLUEPRINT_SPEC.md`, `KLAROS_TARGET_ARCHITECTURE.md` §1, `KLAROS_MEDICAL_TOURISM_VALIDATION.md` §Discovery walkthrough, `KLAROS_DROPSHIPPING_VALIDATION.md` §Discovery walkthrough.
