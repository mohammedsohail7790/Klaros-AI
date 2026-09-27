# Klaros AI — Implementation Dependency Graph

Covers item Y. Derived from the actual entity/subsystem dependencies specified across the Final architecture documents, not assumed from the naive Phase-0-through-11 linear numbering.

## Hard (blocking) dependencies

- Phase 0 → **everything.** No phase 1-11 item may ship ahead of Phase 0's CI (0.1) existing to test it, and Phase 7 (Agent Runtime autonomous execution) specifically cannot exceed Recommend tier until Phase 0's full pre-autonomy gate (RLS instrumentation+enforcement on agent-touched tables, staging environment, security test suite) is complete.
- Phase 1 (`VerticalExtension`/`DomainDefinition` registry, `IntegrationProviderCatalog`, tool catalog) → Phase 2 (Discovery, reads capability data), Phase 4 (Recommendation, reads catalog), Phase 5 (Marketplace UI, reads catalog), Phase 9/10 (domain extensions, reference the registry), Phase 7 (Agent tool-permission UI reads the tool catalog).
- Phase 2 (Discovery) → Phase 3 (Blueprint, promotes `DiscoverySession` output into `BlueprintClaim` rows) — **hard**, Phase 3 cannot start meaningfully without Phase 2's output shape settled, though Phase 3's tables could technically be created earlier (they are not, per the sequencing decision in KLAROS_ARCHITECTURE_RECONCILIATION.md #4, specifically to avoid this coupling being implicit).
- Phase 3 (Blueprint) → Phase 4 (Recommendation reads claims), Phase 6 (Website Requirements derive from Blueprint sections), Phase 7 (Agents reference Blueprint sections), Phase 8 (Workflows section of Blueprint).
- Phase 0's RLS/security gate → Phase 7's autonomous-execution tiers specifically (not Phase 7's schema/DRAFT-tier work, which only needs Phase 0's general CI + Phase 1's tool catalog).

## Soft (non-blocking but logically related) dependencies

- Phase 4 (Recommendation) and Phase 5 (Marketplace UI) are logically related (recommendations often target integrations) but Phase 5 can ship its taxonomy improvements using only Phase 1's catalog data, independent of whether Phase 4 exists yet — a recommendation is one *input* to the UI's RECOMMENDED status, not a prerequisite for the taxonomy itself.
- Phase 8 (Workflow Generation) references Phase 7 (Agents) for workflow steps that invoke an agent, but workflows with only deterministic-action steps (no agent step) can compile and ship using only the existing Automation Engine, without waiting on Phase 7's autonomous-tier gate — soft dependency, not hard.
- Phase 6 (Website Builder) references Phase 3 (Blueprint) for requirements-derivation but its component-registry/preview/publish mechanics are independently buildable and testable against a manually-constructed spec before Blueprint-driven generation is wired in — soft dependency for the generation *trigger*, not for the builder's core mechanics.

## Parallel-safe work

- Phase 9 (Medical Tourism) and Phase 10 (Dropshipping) share **no** table-level dependency on each other (confirmed in KLAROS_FINAL_DOMAIN_MODEL.md — Medical Tourism extends `Lead`/`Appointment`/`Referral`; Dropshipping is a wholly new relational domain) — fully parallelizable once Phase 1's registry exists.
- Phase 0's individual items (0.1 CI, 0.5 Staging, 0.7 Frontend test harness) are internally parallel-safe with each other; only 0.2/0.3 (RLS instrumentation → enforcement) have a strict internal order, and 0.4 (autonomy deprecation) is fully independent of the rest of Phase 0.
- Within Phase 1, items 1.1-1.4 have no dependencies on each other and can all proceed in parallel.
- Frontend and backend work within any given phase are parallel-safe once the API contract for that phase (KLAROS_FINAL_API_ARCHITECTURE.md) is settled — which it is, for every phase, in this document set.

## Risky dependencies (explicitly called out)

- **Phase 7's autonomous-execution tiers depend on Phase 0's RLS having expanded beyond the initial 5 tables to cover every table any shipped agent's declared tools can touch** — this is a moving target (RLS expansion is explicitly ongoing in Phase 11, not a fixed Phase 0 endpoint) and is the single riskiest cross-phase dependency in the whole roadmap: shipping an agent with tool access to a table RLS hasn't reached yet would silently reopen the CRITICAL tenant-isolation gap this whole plan exists to close. **Mitigation:** Phase 7's acceptance criteria must include an explicit per-agent check, at agent-publish time, that every table its declared tools touch is confirmed RLS-enforced — a mechanical gate, not a manual review step.
- **Phase 6 (Website Builder)'s website-runtime deploy unit depends on Phase 0's staging environment existing and having been proven** (0.5) — a new isolated-origin deploy target is a natural place for deployment-manifest gaps (confirmed absent today) to resurface; sequencing Phase 6 after Phase 0.5 has been exercised at least once mitigates this.
- **Medical Tourism's `ReferralCommission` depends on the additive `ReferralReward.currency`/`commission_basis` columns landing cleanly** — a small but real schema-change risk on an existing, presumably-populated table; mitigated by the nullable-column, no-backfill-required design already specified in KLAROS_FINAL_DATABASE_ARCHITECTURE.md.

## Corrected ordering vs. the naive linear 0→11 numbering

The naive linear order is largely correct and is **not** substantially reordered by this analysis — the one adjustment worth naming explicitly: **Phase 5 (Marketplace UI taxonomy) can begin immediately after Phase 1, in parallel with Phases 2-4**, rather than waiting for Phase 4 to complete, since its hard dependency is only on Phase 1's catalog table (soft dependency on Phase 4 noted above). Everywhere else, the numbered order matches the actual dependency graph.
