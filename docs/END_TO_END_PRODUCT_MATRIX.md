# Klaros AI — End-to-End Product Completeness Matrix (Phase 23, updated Phase 24)

Produced by tracing the real business lifecycle through the actual application — real HTTP, real PostgreSQL, real event processing — not by reading documentation. See `backend/tests/test_end_to_end_business_lifecycle.py` for the executable proof this matrix is derived from, and `backend/tests/test_phase24_automations.py`/`test_postgres_phase24_automations.py` for the three automations Phase 24 added on top of it.

Legend: 🟢 COMPLETE (backend + DB + event + frontend + E2E-tested) · 🟡 PARTIAL · 🔴 MISSING · ⚫ EXTERNAL DEPENDENCY (out of scope this phase)

External SaaS integrations (Stripe, QuickBooks, ServiceTitan, HubSpot, Google/Meta Ads, WhatsApp, etc.) and live external LLM credentials are **intentionally excluded** from this matrix's COMPLETE/MISSING judgment — see `ARCHITECTURE_TRACEABILITY.md` for their own tracked status. A row is never marked incomplete solely because an external integration isn't connected.

## Business Lifecycle

| Domain | Capability | Backend | Database | Event | Automation | AI | Approval | Audit | Frontend | E2E tested | Status | Remaining gap |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Lead | Create, qualify, list, search | `crm_service`/`lead_service`/`qualification_service` | `Lead` | `lead.created`, `lead.qualified` | yes (Phase 11) | `crm.ai_qualify_lead_advisory` (advisory) | n/a | 🟢 | `/leads` | 🟢 real HTTP | 🟢 | — |
| Qualification | Score + status, deterministic + AI-advisory | `qualification_service.py`, `ai_qualification_service.py` | `Lead.qualification_status`/`lead_score` | `lead.qualified` | yes | ✅ Company Memory-aware | n/a | 🟢 | `/leads` | 🟢 real HTTP | 🟢 | Does not auto-create a Customer — confirmed intentional (email/phone match only); a human/staff step creates one when no match exists |
| Appointment | Book, reschedule, cancel, availability | `appointment_tools.py` | `Appointment` | `appointment.created` | yes | — | n/a | 🟢 | `/calendar` | 🟢 real HTTP | 🟢 | — |
| Quote | Draft, send, view, accept/decline (public token) | `quote_service.py` | `Quote`, `QuoteLineItem` | `quote.created/sent/viewed/accepted/declined/expired` | yes (stale-quote AI Next Action) | ✅ `ai.propose_quote_followup` | ✅ | 🟢 | `/quotes` | 🟢 real HTTP incl. public token flow | 🟢 | — |
| Contract | Auto-created from accepted quote, sent, public sign/decline | `contract_service.py` | `Contract` | `contract.created`(implicit via handler), `contract.expired` (Phase 24) | yes (Phase 24: daily pending-sweep + owner notification) | — | n/a | 🟢 | `/contracts` | 🟢 real HTTP incl. public token flow + Phase 24 sweep/notify | 🟢 | — |
| Deposit | Configurable %/fixed, checkout intent, paid→convert | `quote_deposit_service.py`, `QuoteService.mark_deposit_paid` | `Quote.deposit_*`, `Payment.quote_id` | `quote.deposit_required`/deposit-paid transition | n/a | — | n/a | 🟢 | quote view page (`deposit=success` param) | 🟡 internal transition tested directly; the Stripe PaymentIntent/webhook HTTP round-trip is external | 🟡 | The **money movement** itself is Stripe (⚫, correctly out of scope); the internal DEPOSIT_PENDING→DEPOSIT_PAID→Job transition itself is real and E2E-proven |
| Job / Operations | Full state machine: schedule→dispatch→en route→on site→in progress→QA pending→completed→closed | `job_service.py`, `job_state_machine.py`, `job_transition_service.py` | `Job`, `Worker` | `job.*` (many) | yes | — | n/a | 🟢 | `/jobs`, `/operations` | 🟢 real HTTP, full transition chain | 🟢 | — |
| QA | Start/complete/fail, real precondition validation (tasks, attachments, actual_start) | `qa_service.py` | `Job.qa_status`, checks | `job.qa_passed`/`job.qa_failed` | yes (Phase 24: QA-failure escalation notification) | not yet | n/a | 🟢 | job detail page | 🟢 real HTTP, real photo upload required + Phase 24 escalation | 🟢 | — |
| Signoff | Internal (non-legally-binding) customer signoff | `signoff_service.py` (`InternalCustomerSignoffProvider`) | `CustomerSignoff` | — | n/a | — | n/a | 🟢 | job detail page | 🟢 real HTTP | 🟢 | Explicitly, honestly internal-only — no external e-signature provider (documented in the model's own docstring, not a gap) |
| Invoice | Trigger-from-job (real, event-driven), approval threshold, send, void | `invoice_service.py` | `Invoice` | `invoice.trigger_requested/created/sent/...` | yes (overdue-invoice AI Next Action) | ✅ `ai.propose_invoice_followup` | ✅ real threshold-gated approval | 🟢 | `/finance/invoices` | 🟢 real HTTP incl. approval-required path | 🟢 | — |
| Payment | Real internal test-payment provider, allocation, invoice PAID | `payment_service.py` | `Payment`, `PaymentAllocation` | `payment.received` | yes | — | n/a | 🟢 | `/finance/ar`, `/payments` | 🟢 real HTTP | 🟢 | Real Stripe card processing is ⚫ external; the internal allocation/PAID-status logic is real and E2E-proven |
| Retention | Lifecycle profile + review request, auto-created off job/payment events | `retention_service.py` | `CustomerLifecycleProfile`, `ReviewRequest` | `retention.*` | yes | — | ✅ review-send is APPROVAL_REQUIRED | 🟢 | `/retention`, `/retention/reviews` | 🟢 real HTTP, real event-driven creation | 🟢 | — |
| Referral | Program/code/referral, convert-to-lead (real new Lead, source=REFERRAL), reward approve/issue | `retention_referral_tools.py` | `ReferralProgram`, `ReferralCode`, `Referral`, `ReferralReward` | referral status progression via existing lead/appointment/payment events; `retention.opportunity_created` (REFERRAL_ELIGIBLE, Phase 24) | yes (Phase 24: referral-opportunity owner notification, triggered off positive review feedback) | — | ✅ reward approve/reject | 🟢 | `/retention/referrals` | 🟢 real HTTP, closes the loop into a brand-new real Lead + Phase 24 notification | 🟢 | — |

**The full chain — Lead → Qualification → Appointment → Quote → Contract → Deposit → Job → QA → Signoff → Invoice → Payment → Retention → Referral → a brand-new Lead — is proven in one real, passing, real-Postgres-verified test.** This is the single strongest piece of evidence in this phase.

## AI Decision Layer

| Surface | Provider | Deterministic/Live | Company Memory | ToolRequest | Approval | Execution | Learning | E2E tested |
|---|---|---|---|---|---|---|---|---|
| Morning Brief enrichment | `AIProvider.enrich_brief` | 🟢 **LIVE VERIFIED** (Phase 31, real `gpt-4o-mini`) | ✅ | ❌ (advisory) | n/a | n/a | n/a | 🟢 |
| Lead Qualification advisory | `generate_structured` | 🟢 **LIVE VERIFIED** (Phase 31) | ✅ | ❌ (advisory) | n/a | n/a | n/a | 🟢 |
| Marketing job captions | `generate_structured` | 🟢 **LIVE VERIFIED** (Phase 31) | ✅ | ❌ (advisory) | n/a | n/a | n/a | 🟢 |
| SEO page draft | `generate_structured` | test-provider only (not exercised live this phase) | ✅ | ❌ (advisory) | n/a | n/a | n/a | 🟢 |
| Knowledge Q&A | `generate_structured` | 🟢 **LIVE VERIFIED** (Phase 31, via `test_live_ai_provider.py`) | ✅ | ❌ (advisory) | n/a | n/a | n/a | 🟢 |
| **AI Next Action: quote follow-up** | `generate_structured` | 🟢 **LIVE VERIFIED** (Phase 31) | ✅ | ✅ | ✅ AUTO/APPROVAL_REQUIRED/BLOCKED all proven live | ✅ real | ✅ real, live learn loop proven end-to-end | 🟢 |
| **AI Next Action: invoice follow-up** | `generate_structured` | 🟢 **LIVE VERIFIED** (Phase 31) | ✅ | ✅ | ✅ | ✅ | ✅ (zero new code — same generic hook) | 🟢 |
| Voice conversation turn generation | `generate_structured` | test-provider only (not exercised live this phase) | ❌ | ❌ | n/a | n/a | n/a | 🟡 |

**Phase 31 (2026-09): first genuine LIVE VERIFIED certification.** A real OpenAI API key was added to this environment; `get_ai_provider()` resolves to a live, connected `OpenAIAIProvider` (`gpt-4o-mini`) through the unmodified `AI_PROVIDER=auto` factory. Every ✅ row above now has real, non-fabricated evidence (real `AIInvocationLog` rows with real latency/token counts) behind it, not just test-provider coverage. Full detail, including a real, live-observed prompt-clarity gap in `_SYSTEM_INSTRUCTIONS` (deliberately left unpatched per the frozen-core rule — every non-compliant model proposal was still correctly rejected by deterministic validation), is in `ARCHITECTURE_TRACEABILITY.md`'s Phase 31 section.

## Automation Coverage (meaningful lifecycle scenarios)

| # | Scenario | Event | Condition | Action | Execution | Audit | Owner visibility |
|---|---|---|---|---|---|---|---|
| 1 | New lead follow-up | `lead.created` | none | `notifications.create_notification` | ✅ | ✅ | ✅ `/automations` |
| 2 | High-value lead | `lead.created` | score/value threshold | `notifications.create_notification` | ✅ | ✅ | ✅ |
| 3 | Stale quote follow-up | `quote.expired` | none | `ai.propose_quote_followup` (governed AI decision) | ✅ | ✅ | ✅ |
| 4 | Overdue invoice follow-up | `exception.created` | `invoice.type == INVOICE_OVERDUE` | `ai.propose_invoice_followup` | ✅ | ✅ | ✅ |
| 5 | Daily stale-quote sweep | SCHEDULE | daily | `quotes.detect_expired_quotes` | ✅ | ✅ | ✅ |
| 6 | Daily overdue-invoice sweep | SCHEDULE | daily | `finance.detect_overdue_invoices` | ✅ | ✅ | ✅ |
| 7 | QA-failure escalation (Phase 24) | `job.qa_failed` | none | `notifications.create_notification` | ✅ | ✅ | ✅ `/automations`, `/approvals` (non-AI notification) |
| 8 | Daily contract-pending sweep (Phase 24) | SCHEDULE | daily | `contracts.detect_pending` (new tool; completes the pre-existing but previously-dead `ContractStatus.EXPIRED`/`EventType.CONTRACT_EXPIRED`) | ✅ | ✅ | ✅ |
| 9 | Contract-pending owner notification (Phase 24) | `contract.expired` | none | `notifications.create_notification` | ✅ | ✅ | ✅ |
| 10 | Referral-opportunity notification (Phase 24) | `retention.opportunity_created` | `retention_opportunity.type == REFERRAL_ELIGIBLE` | `notifications.create_notification` | ✅ | ✅ | ✅ |

Scenarios 7-10 were built in Phase 24, closing the three gaps this document previously flagged as credible-but-unbuilt candidates. All three are deterministic (no AI Next Action scenario was added for QA/contracts/referrals — see Phase 24 certification's AI boundary section), reuse the existing Automation Engine/EventBus/ToolRegistry/ActionPolicy/AuditLog exclusively, and are idempotent via the same `(automation_version_id, source_event_id)` unique constraint proven for scenarios 1-6. **Update, Phase 28**: `tests/test_postgres_phase24_automations.py` was executed against a real PostgreSQL 16.6 server for the first time — this run found and Phase 28 fixed a genuine concurrency defect in the contract-pending sweep (`ContractService.detect_pending()` raced under real concurrent transactions; SQLite's single-writer serialization had masked it). All 5 tests in that file now pass against real PostgreSQL, so scenarios 7-10's concurrency proof is now 🔵 (real-PostgreSQL-verified), matching scenarios 1-6.

**Update, Phase 29**: scenarios 5 and 6's own underlying sweeps (`QuoteService.detect_expired()`, `ARService.detect_overdue()`) were targeted for the same class of verification Phase 28 applied to scenario 8's contract sweep. Both had real, distinct defects under genuine PostgreSQL concurrency — `detect_expired()` shared Contract's silent-duplicate-event race (fixed identically, `.with_for_update(skip_locked=True)`); `detect_overdue()`'s downstream `CollectionService.schedule_next_action()` crashed with an unhandled `IntegrityError` under concurrency (fixed with the same try/except/rollback/re-fetch CAS pattern `ExceptionService.create_exception()` already used safely). Both fixes verified with real-PostgreSQL regression tests that fail without the fix and pass with it (`tests/test_postgres_sweep_concurrency.py`). Scenarios 5-6's concurrency proof is now 🔵 (real-PostgreSQL-verified).

## Owner Control Plane

| Surface | What it shows | Real backend? | Verified this phase |
|---|---|---|---|
| `/dashboard` | Owner Attention Queue (Phase 26, 10 real categories, deterministic priority), Autonomy stats, AI Control Center (approvals/feedback/provider health/24h invocations), automations, Morning Brief, Recent Activity feed (Phase 27, 23 real activity types, paginated, category-filterable) | ✅ | ✅ real browser, real data, incl. live dashboard→approval→approve→dashboard freshness proof (Phase 26) and live activity-feed→job navigation proof (Phase 27) |
| `/approvals` | Every PENDING/decided approval, AI-origin badge, approve/reject with note | ✅ | ✅ real browser (Phase 21) |
| `/settings/memory` | Company Memory incl. AI_FEEDBACK, provenance link, confirm/discard | ✅ | ✅ real browser (Phase 21) |
| `/automations` | Automation Studio, real action picker incl. both AI Next Action scenarios | ✅ | ✅ real browser (Phase 20) |
| `/settings/knowledge` | Knowledge files, real search/ask | ✅ | ✅ real browser (Phase 17) |
| `/leads`, `/quotes`, `/jobs`, `/customers`, `/finance/*`, `/retention/*` | Real CRM/ops/finance data | ✅ | ✅ real browser this phase (`/leads`, `/quotes`) |

## Public / Customer Flows

| Flow | Auth boundary | Tenant isolation | Idempotency | Verified |
|---|---|---|---|---|
| Public lead intake | rate-limited, no auth (by design) | tenant_id from URL path | dedup via existing lead-matching | pre-existing, unchanged |
| Public quote view/accept/decline | signed `quote_view` JWT (quote_id+tenant_id embedded) | token-embedded, never trusts path param alone | quote state machine itself | ✅ real HTTP this phase, incl. tampered-token rejection |
| Public contract view/sign/decline | signed `contract_view` JWT | same pattern | contract state machine | ✅ real HTTP this phase |
| Deposit checkout | reuses `quote_view` token | same | Stripe PaymentIntent idempotency (external) | not re-verified this phase (Stripe itself is ⚫) |

## Tenant Isolation

Proven this phase over real HTTP (SQLite and real PostgreSQL) for Lead, Customer, Quote (list + detail + mutation attempt), and public-token tamper-resistance — `tests/test_end_to_end_business_lifecycle.py::test_lifecycle_entities_are_tenant_isolated`. AI Next Action, Company Memory, and Approval tenant isolation were already exhaustively proven under real PostgreSQL concurrency in Phase 18-21 and are not re-derived here.

## EventBus Runtime Boundary (Phase 25)

Every HTTP route (authenticated and public), the scheduled-automation dispatch path, and every domain event producer now resolve the application's EventBus/ToolRegistry through one consistent `Depends(get_wired_event_bus)`/`Depends(get_tool_registry)` boundary — see `ARCHITECTURE_TRACEABILITY.md`'s "Phase 25" section for the full root-cause account. This replaces the process-wide-vs-per-test split noted in earlier phases (Phase 23's E2E test, Phase 24's real-HTTP automation test) with a genuine architectural fix rather than a documented workaround; both of those tests had their manual bus-access workarounds removed this phase and pass unchanged. `backend/tests/test_phase25_eventbus_boundary.py` is the dedicated regression.

## Known, Honestly-Labeled Non-Gaps

These are **not** counted as MISSING — they are correct, documented architectural decisions unrelated to this phase's scope:
- Signoff is internal-only (no e-signature provider) — by design.
- Deposit/invoice payment processing is a real Stripe integration boundary (⚫).
- No live external LLM credential (⚫, tracked separately, Phase 22).
- "Opportunity" in the mission's lifecycle language maps to the real, existing Lead qualification + Commercial Pipeline snapshot (`insight_service.py::CommercialPipelineSnapshot`) — there is no separate persisted `Opportunity` entity, and none was invented for this phase.
