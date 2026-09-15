"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, Check, Power, Sunrise, Workflow } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { useAuth } from "@/lib/useAuth";
import Link from "next/link";
import {
  ActivityFeed,
  ApiError,
  AttentionItem,
  AttentionQueue,
  AutomationSummary,
  AutonomyStats,
  BillingStatus,
  CommercialPipeline,
  CrmMetrics,
  FinanceSummary,
  IntegrationConnectionRow,
  KillSwitchStatus,
  MarketingSummary,
  MorningBriefData,
  OperationsDashboard,
  RetentionSummary,
  AiHealth,
  getActivityFeed,
  getAiHealth,
  getAttentionQueue,
  getAutomationSummary,
  getAutonomyStats,
  getBillingStatus,
  getCommercialPipeline,
  getCrmMetrics,
  getFinanceSummary,
  getKillSwitchStatus,
  getLatestMorningBrief,
  getMarketingSummary,
  getOperationsDashboard,
  getRetentionSummary,
  listApprovals,
  listCompanyMemories,
  listIntegrationConnections,
  setKillSwitch,
} from "@/lib/api";

import { Badge } from "@/components/ui/Badge";
const ACTIVITY_CATEGORIES = ["ALL", "CRM", "SALES", "CONTRACT", "OPERATIONS", "QA", "FINANCE", "RETENTION", "REFERRAL", "AUTOMATION", "AI"];

const ACTIVITY_SEVERITY_STYLE: Record<string, string> = {
  ERROR: "border-red-200 bg-red-50/30 text-red-700",
  WARNING: "border-amber-200 bg-amber-50/30 text-amber-700",
  INFO: "border-border bg-surface text-muted",
};

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const PRIORITY_STYLE: Record<AttentionItem["priority"], string> = {
  CRITICAL: "border-red-200 bg-red-50/30 text-red-700",
  HIGH: "border-amber-200 bg-amber-50/30 text-amber-700",
  MEDIUM: "border-blue-200 bg-blue-50/20 text-blue-700",
  LOW: "border-border bg-surface text-muted",
};

const CATEGORY_LABELS: Record<string, string> = {
  qualified_lead_no_appointment: "Lead",
  quote_stale: "Quote",
  contract_pending: "Contract",
  job_qa_failed: "QA",
  job_blocked: "Job",
  invoice_overdue: "Invoice",
  retention_opportunity: "Retention",
  referral_opportunity: "Referral",
  ai_approval_required: "AI approval",
  ai_feedback_pending: "AI learning",
  automation_failed: "Automation",
};

const PENDING_MODULES: string[] = [];

// The real, working connectors Klaros ships today — deliberately not a
// generic "popular apps" marketplace list. Each maps to a segment of the
// business so a brand-new owner has a concrete next step, and each check
// reflects an actual connection status, never a fabricated one.
const GETTING_STARTED_ITEMS: { segment: string; name: string; provider: string; blurb: string }[] = [
  { segment: "Payments", name: "Stripe", provider: "stripe", blurb: "Collect deposits and invoice payments." },
  { segment: "Accounting", name: "QuickBooks", provider: "quickbooks", blurb: "Sync invoices and payments automatically." },
  { segment: "Scheduling", name: "Google Calendar", provider: "google_calendar", blurb: "Two-way sync for booked appointments." },
];

const METRIC_LABELS: { key: keyof CrmMetrics; label: string }[] = [
  { key: "new_leads_today", label: "New leads today" },
  { key: "qualified_leads", label: "Qualified leads" },
  { key: "appointments_today", label: "Appointments today" },
  { key: "conversion_rate_pct", label: "Conversion rate" },
  { key: "uncontacted_leads", label: "Uncontacted leads" },
  { key: "at_risk_leads", label: "At-risk leads" },
];

export default function DashboardPage() {
  const { token, user, loading: authLoading, error: authError } = useAuth();
  const [metrics, setMetrics] = useState<CrmMetrics | null>(null);
  const [pipeline, setPipeline] = useState<CommercialPipeline | null>(null);
  const [operations, setOperations] = useState<OperationsDashboard | null>(null);
  const [finance, setFinance] = useState<FinanceSummary | null>(null);
  const [marketing, setMarketing] = useState<MarketingSummary | null>(null);
  const [retention, setRetention] = useState<RetentionSummary | null>(null);
  const [brief, setBrief] = useState<MorningBriefData | null>(null);
  const [attention, setAttention] = useState<AttentionQueue | null>(null);
  const [aiHealth, setAiHealth] = useState<AiHealth | null>(null);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [killSwitch, setKillSwitchState] = useState<KillSwitchStatus | null>(null);
  const [killSwitchBusy, setKillSwitchBusy] = useState(false);
  const [connections, setConnections] = useState<IntegrationConnectionRow[] | null>(null);
  const [autonomy, setAutonomy] = useState<AutonomyStats | null>(null);
  const [automations, setAutomations] = useState<AutomationSummary | null>(null);
  const [aiApprovalsPending, setAiApprovalsPending] = useState<number | null>(null);
  const [aiFeedbackPending, setAiFeedbackPending] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activity, setActivity] = useState<ActivityFeed | null>(null);
  const [activityPage, setActivityPage] = useState(1);
  const [activityCategory, setActivityCategory] = useState("ALL");
  const [activityLoading, setActivityLoading] = useState(false);

  const loadActivity = useCallback(
    async (page: number, category: string) => {
      if (!token) return;
      setActivityLoading(true);
      try {
        const result = await getActivityFeed(token, {
          page, pageSize: 10, category: category === "ALL" ? undefined : category,
        });
        setActivity(result);
      } catch {
        // Activity feed failing to load must never block the rest of the
        // cockpit — it already has its own error boundary implicitly via
        // the empty/error state rendered below (activity stays null).
      } finally {
        setActivityLoading(false);
      }
    },
    [token]
  );

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [
        metricsResult,
        pipelineResult,
        operationsResult,
        financeResult,
        marketingResult,
        retentionResult,
        briefResult,
        autonomyResult,
        automationsResult,
        pendingApprovalsResult,
        aiFeedbackResult,
        attentionResult,
        aiHealthResult,
        connectionsResult,
        billingResult,
        killSwitchResult,
      ] = await Promise.all([
        getCrmMetrics(token),
        getCommercialPipeline(token),
        getOperationsDashboard(token),
        getFinanceSummary(token),
        getMarketingSummary(token),
        getRetentionSummary(token),
        getLatestMorningBrief(token),
        getAutonomyStats(token),
        getAutomationSummary(token),
        // Phase 21: small cockpit summary counts, reusing the existing
        // Approvals/Company Memory list APIs — no new backend endpoint.
        // AI origin isn't filterable server-side for approvals, so it's
        // counted client-side from the existing PENDING list.
        listApprovals(token, "PENDING"),
        listCompanyMemories(token, { status_filter: "PENDING", memory_type: "AI_FEEDBACK" }),
        // Phase 26: the single prioritized attention queue.
        getAttentionQueue(token),
        getAiHealth(token),
        listIntegrationConnections(token),
        getBillingStatus(token),
        getKillSwitchStatus(token),
      ]);
      setMetrics(metricsResult);
      setPipeline(pipelineResult);
      setOperations(operationsResult);
      setFinance(financeResult);
      setMarketing(marketingResult);
      setRetention(retentionResult);
      setBrief(briefResult);
      setAutonomy(autonomyResult);
      setAutomations(automationsResult);
      setAiApprovalsPending(pendingApprovalsResult.approvals.filter((a) => a.requested_by_type === "AI").length);
      setAiFeedbackPending(aiFeedbackResult.memories.length);
      setAttention(attentionResult);
      setAiHealth(aiHealthResult);
      setConnections(connectionsResult);
      setBilling(billingResult);
      setKillSwitchState(killSwitchResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load business metrics.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadActivity(activityPage, activityCategory);
  }, [loadActivity, activityPage, activityCategory]);

  if (authError) return <p className="p-8 text-sm text-red-600">{authError}</p>;

  async function handleToggleKillSwitch() {
    if (!token) return;
    setKillSwitchBusy(true);
    try {
      const result = await setKillSwitch(token, !killSwitch?.ai_paused);
      setKillSwitchState(result);
    } catch {
      // best-effort — banner just won't update; user can retry
    } finally {
      setKillSwitchBusy(false);
    }
  }

  const gettingStartedChecks = connections
    ? GETTING_STARTED_ITEMS.map((item) => ({
        ...item,
        connected: connections.some((c) => c.provider === item.provider && c.status === "CONNECTED"),
      })).concat([
        {
          segment: "AI Assistant",
          name: aiHealth?.provider_name ?? "AI provider",
          provider: "ai",
          blurb: "Powers AI-drafted summaries, recommendations, and replies.",
          connected: aiHealth?.provider_configured ?? false,
        },
      ])
    : null;
  const gettingStartedRemaining = gettingStartedChecks?.filter((c) => !c.connected).length ?? 0;

  return (
    <AppShell user={user}>
      <div className="px-8 py-10">
        <header className="mb-8 flex items-center justify-between border-b border-border pb-4">
          <div>
            <h1 className="font-display text-2xl text-foreground">Owner Cockpit</h1>
            {user && (
              <p className="text-sm text-muted">
                Signed in as {user.full_name} · {user.email} · role {user.role}
              </p>
            )}
          </div>
        </header>

        {killSwitch && (
          <div
            className={`mb-6 flex items-center justify-between rounded-lg border px-4 py-3 text-sm ${
              killSwitch.ai_paused
                ? "border-red-300 bg-red-50/40 text-red-800"
                : "border-border bg-surface text-muted"
            }`}
          >
            <div className="flex items-center gap-2">
              {killSwitch.ai_paused ? (
                <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
              ) : (
                <Power className="h-4 w-4 shrink-0" strokeWidth={2} />
              )}
              <span>
                {killSwitch.ai_paused
                  ? "AI is PAUSED tenant-wide — every AI and automation action is refused until you turn it back on. You can still work manually."
                  : "AI is active — automations and AI-initiated actions are running normally."}
              </span>
            </div>
            {user?.role === "OWNER" && (
              <button
                onClick={handleToggleKillSwitch}
                disabled={killSwitchBusy}
                className={`shrink-0 rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
                  killSwitch.ai_paused
                    ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                    : "border-red-300 bg-red-50 text-red-700 hover:bg-red-100"
                }`}
              >
                {killSwitchBusy ? "Working..." : killSwitch.ai_paused ? "Resume AI" : "Pause all AI"}
              </button>
            )}
          </div>
        )}

        {gettingStartedChecks && (
          <section className="mb-8">
            {gettingStartedRemaining === 0 ? (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50/10 px-4 py-2.5 text-sm text-emerald-700">
                <Check className="h-4 w-4 shrink-0" strokeWidth={2} />
                All set — payments, accounting, scheduling, and your AI assistant are connected.
              </div>
            ) : (
              <>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-medium text-muted">Getting started</h2>
                  <span className="text-xs text-muted">{gettingStartedRemaining} step(s) left</span>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {gettingStartedChecks.map((item) => (
                    <Link
                      key={item.provider}
                      href="/settings/integrations"
                      className={`rounded-lg border p-4 shadow-card transition hover:border-border-strong ${
                        item.connected ? "border-emerald-200 bg-emerald-50/10" : "border-border bg-surface"
                      }`}
                    >
                      <div className="mb-1 flex items-center justify-between">
                        <span className="text-[10px] uppercase tracking-wide text-muted">{item.segment}</span>
                        {item.connected ? (
                          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
                            <Check className="h-3 w-3" strokeWidth={3} />
                          </span>
                        ) : (
                          <span className="h-4 w-4 rounded-full border border-border-strong" />
                        )}
                      </div>
                      <p className="text-sm font-medium text-foreground">{item.name}</p>
                      <p className="mt-0.5 text-xs text-muted">{item.connected ? "Connected" : item.blurb}</p>
                    </Link>
                  ))}
                </div>
              </>
            )}
          </section>
        )}

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Needs your attention</h2>
            {attention && attention.items.length > 0 && (
              <span className="text-xs text-muted">
                {attention.critical_count > 0 && <span className="text-red-600">{attention.critical_count} critical</span>}
                {attention.critical_count > 0 && attention.high_count > 0 && " · "}
                {attention.high_count > 0 && <span className="text-amber-700">{attention.high_count} high</span>}
              </span>
            )}
          </div>
          {!attention ? (
            <p className="text-sm text-muted">Loading...</p>
          ) : attention.items.length === 0 ? (
            <p className="rounded-lg border border-emerald-200 bg-emerald-50/10 p-4 shadow-card text-sm text-emerald-700">
              Nothing needs your attention right now.
            </p>
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border bg-surface">
              {attention.items.slice(0, 8).map((item) => (
                <li key={`${item.category}-${item.entity_id}`}>
                  <Link
                    href={item.link}
                    className="flex items-start justify-between gap-4 p-4 transition hover:bg-surface-muted"
                  >
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${PRIORITY_STYLE[item.priority]}`}>
                          {item.priority}
                        </span>
                        <span className="text-[10px] uppercase tracking-wide text-muted">
                          {CATEGORY_LABELS[item.category] ?? item.category}
                        </span>
                      </div>
                      <p className="truncate text-sm text-foreground">{item.title}</p>
                      <p className="mt-0.5 truncate text-xs text-muted">{item.reason}</p>
                    </div>
                    {item.monetary_value && (
                      <div className="shrink-0 text-sm font-medium text-muted">${item.monetary_value}</div>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Autonomy status — today</h2>
            <Link href="/settings/automation" className="text-xs text-muted underline">
              Configure automation
            </Link>
          </div>
          {autonomy ? (
            autonomy.total === 0 ? (
              <p className="rounded-lg border border-border bg-surface p-4 shadow-card text-sm text-muted">
                No actions yet today.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/20 p-4 shadow-card">
                  <div className="text-2xl font-semibold text-emerald-700">{autonomy.automatic}</div>
                  <div className="text-xs text-muted">Automatic</div>
                </div>
                <div className="rounded-lg border border-amber-200 bg-amber-50/20 p-4 shadow-card">
                  <div className="text-2xl font-semibold text-amber-700">{autonomy.approval_required}</div>
                  <div className="text-xs text-muted">Awaiting approval</div>
                </div>
                <div className="rounded-lg border border-red-200 bg-red-50/20 p-4 shadow-card">
                  <div className="text-2xl font-semibold text-red-700">{autonomy.blocked}</div>
                  <div className="text-xs text-muted">Blocked</div>
                </div>
                <div className="rounded-lg border border-border-strong bg-surface-muted/40 p-4 shadow-card">
                  <div className="text-2xl font-semibold text-muted">{autonomy.failed}</div>
                  <div className="text-xs text-muted">Failed</div>
                </div>
              </div>
            )
          ) : (
            <p className="text-sm text-muted">Loading...</p>
          )}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">AI Control Center</h2>
            <Link href="/ai-activity" className="text-xs text-muted underline">
              View AI activity
            </Link>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Link
              href="/approvals"
              className={`rounded-lg border p-4 transition hover:border-violet-400 ${
                (aiApprovalsPending ?? 0) > 0 ? "border-violet-200 bg-violet-50/20" : "border-border bg-surface"
              }`}
            >
              <div className="text-2xl font-semibold text-violet-700">{aiApprovalsPending ?? 0}</div>
              <div className="text-xs text-muted">Awaiting approval</div>
            </Link>
            <Link
              href="/settings/memory"
              className={`rounded-lg border p-4 transition hover:border-violet-400 ${
                (aiFeedbackPending ?? 0) > 0 ? "border-violet-200 bg-violet-50/20" : "border-border bg-surface"
              }`}
            >
              <div className="text-2xl font-semibold text-violet-700">{aiFeedbackPending ?? 0}</div>
              <div className="text-xs text-muted">Feedback to review</div>
            </Link>
            {aiHealth && (
              <>
                <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                  <div className={`text-sm font-semibold ${aiHealth.provider_configured ? "text-emerald-700" : "text-muted"}`}>
                    {aiHealth.provider_configured ? "Connected" : "Not connected"}
                  </div>
                  <div className="mt-1 text-xs text-muted">AI provider ({aiHealth.provider_name})</div>
                </div>
                <Link
                  href="/ai-activity"
                  className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
                >
                  <div className="text-2xl font-semibold text-muted">
                    {aiHealth.invocations_24h_succeeded}/{aiHealth.invocations_24h}
                  </div>
                  <div className="text-xs text-muted">Calls succeeded (24h)</div>
                </Link>
              </>
            )}
            {billing && (
              <Link
                href="/settings/billing"
                className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
              >
                <div className="text-sm font-semibold capitalize text-foreground">
                  {billing.plan}
                  {billing.billing_status === "trialing" && billing.trial_ends_at && (
                    <span className="ml-1 font-normal text-amber-700">
                      (trial ends{" "}
                      {new Date(billing.trial_ends_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                      )
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-muted">
                  {billing.ai_usage_limit !== null
                    ? `${billing.ai_usage_this_month}/${billing.ai_usage_limit} recommendations used`
                    : "Unlimited recommendations"}
                </div>
              </Link>
            )}
          </div>
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Automations</h2>
            <Link href="/automations" className="text-xs text-muted underline">
              Manage automations
            </Link>
          </div>
          {automations ? (
            automations.automations_total === 0 ? (
              <div className="rounded-lg border border-border bg-surface shadow-card">
                <EmptyState icon={Workflow} title="No automations set up yet." compact />
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                <Link
                  href="/automations"
                  className="rounded-lg border border-blue-200 bg-blue-50/20 p-4 shadow-card transition hover:border-blue-300"
                >
                  <div className="text-2xl font-semibold text-blue-700">{automations.executions_running}</div>
                  <div className="text-xs text-muted">Running now</div>
                </Link>
                <Link
                  href="/automations"
                  className={`rounded-lg border p-4 transition hover:border-red-300 ${
                    automations.executions_failed > 0 ? "border-red-200 bg-red-50/20" : "border-border bg-surface"
                  }`}
                >
                  <div className="text-2xl font-semibold text-red-700">{automations.executions_failed}</div>
                  <div className="text-xs text-muted">Failed</div>
                </Link>
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/20 p-4 shadow-card">
                  <div className="text-2xl font-semibold text-emerald-700">{automations.executions_completed_today}</div>
                  <div className="text-xs text-muted">Completed today</div>
                </div>
                <Link
                  href="/automations"
                  className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
                >
                  <div className="text-2xl font-semibold text-muted">{automations.automations_scheduled}</div>
                  <div className="text-xs text-muted">Scheduled</div>
                </Link>
                <div className="rounded-lg border border-border-strong bg-surface-muted/40 p-4 shadow-card">
                  <div className="text-2xl font-semibold text-muted">
                    {automations.automations_enabled}/{automations.automations_total}
                  </div>
                  <div className="text-xs text-muted">Enabled</div>
                </div>
              </div>
            )
          ) : (
            <p className="text-sm text-muted">Loading...</p>
          )}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Morning Brief</h2>
            <Link href="/morning-brief" className="text-xs text-muted underline">
              View full brief
            </Link>
          </div>
          {brief && brief.brief_id ? (
            <div className="rounded-lg border border-border bg-surface p-6">
              <div className="mb-2 flex items-center gap-2">
                <Badge status={brief.mode === "DETERMINISTIC" ? "DRAFT" : "AI"}>
                  {brief.mode === "DETERMINISTIC" ? "DETERMINISTIC SUMMARY — AI NOT CONNECTED" : "AI"}
                </Badge>
              </div>
              <p className="mb-4">{brief.headline}</p>
              {brief.insights.filter((i) => i.priority === "HIGH").length > 0 && (
                <div className="mb-3">
                  <h3 className="mb-1 text-xs font-medium text-red-700">Needs attention</h3>
                  <ul className="space-y-1 text-sm text-muted">
                    {brief.insights
                      .filter((i) => i.priority === "HIGH")
                      .slice(0, 3)
                      .map((i) => (
                        <li key={i.insight_id}>{i.summary}</li>
                      ))}
                  </ul>
                </div>
              )}
              {brief.recommendations.filter((r) => r.status === "PENDING").length > 0 && (
                <div>
                  <h3 className="mb-1 text-xs font-medium text-muted">Recommended actions</h3>
                  <ul className="space-y-1 text-sm text-muted">
                    {brief.recommendations
                      .filter((r) => r.status === "PENDING")
                      .slice(0, 3)
                      .map((r) => (
                        <li key={r.recommendation_id}>{r.what}</li>
                      ))}
                  </ul>
                </div>
              )}
              {brief.recommendations.filter((r) => r.status === "APPROVAL_REQUESTED").length > 0 && (
                <div className="mt-3">
                  <h3 className="mb-1 text-xs font-medium text-amber-700">Awaiting your approval</h3>
                  <ul className="space-y-1 text-sm text-muted">
                    {brief.recommendations
                      .filter((r) => r.status === "APPROVAL_REQUESTED")
                      .slice(0, 3)
                      .map((r) => (
                        <li key={r.recommendation_id}>
                          {r.what} —{" "}
                          <Link href="/approvals" className="underline text-amber-700">
                            review
                          </Link>
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-lg border border-border bg-surface shadow-card">
              <EmptyState
                icon={Sunrise}
                title="No brief generated yet."
                compact
                action={
                  <Link href="/morning-brief" className="text-xs text-accent underline">
                    Generate one
                  </Link>
                }
              />
            </div>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-muted">Today — CRM</h2>
          {authLoading || loading ? (
            <p className="text-sm text-muted">Loading metrics...</p>
          ) : error ? (
            <div className="rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
              {error}{" "}
              <button onClick={load} className="ml-2 underline">
                Retry
              </button>
            </div>
          ) : metrics ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              {METRIC_LABELS.map(({ key, label }) => (
                <div key={key} className="rounded-lg border border-border bg-surface p-4 shadow-card">
                  <p className="text-2xl font-semibold">
                    {key === "conversion_rate_pct" ? `${metrics[key]}%` : metrics[key]}
                  </p>
                  <p className="mt-1 text-xs text-muted">{label}</p>
                </div>
              ))}
            </div>
          ) : null}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Commercial Pipeline — Quote → Contract → Deposit → Job</h2>
            <Link href="/quotes" className="text-xs text-muted underline">
              View quotes
            </Link>
          </div>
          {pipeline?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50/30 p-4 text-sm text-amber-700">
              PIPELINE NEEDS ATTENTION — {pipeline.contracts_awaiting_signature} contract(s) awaiting signature,{" "}
              {pipeline.deposits_awaiting_payment} deposit(s) outstanding.
            </div>
          )}
          {pipeline ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              <Link
                href="/quotes"
                className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
              >
                <p className="text-2xl font-semibold">{pipeline.quotes_awaiting_response}</p>
                <p className="mt-1 text-xs text-muted">Quotes awaiting response</p>
              </Link>
              <Link
                href="/quotes"
                className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
              >
                <p className="text-2xl font-semibold">${pipeline.quotes_accepted_value}</p>
                <p className="mt-1 text-xs text-muted">{pipeline.quotes_accepted} quote(s) accepted</p>
              </Link>
              <Link
                href="/contracts"
                className={`rounded-lg border p-4 transition hover:border-border-strong ${
                  pipeline.contracts_awaiting_signature > 0
                    ? "border-amber-200 bg-amber-50/20"
                    : "border-border bg-surface"
                }`}
              >
                <p className="text-2xl font-semibold">{pipeline.contracts_awaiting_signature}</p>
                <p className="mt-1 text-xs text-muted">Contracts awaiting signature</p>
              </Link>
              <Link
                href="/contracts"
                className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
              >
                <p className="text-2xl font-semibold">{pipeline.contracts_signed}</p>
                <p className="mt-1 text-xs text-muted">Contracts signed</p>
              </Link>
              <Link
                href="/quotes"
                className={`rounded-lg border p-4 transition hover:border-border-strong ${
                  pipeline.deposits_awaiting_payment > 0
                    ? "border-amber-200 bg-amber-50/20"
                    : "border-border bg-surface"
                }`}
              >
                <p className="text-2xl font-semibold">${pipeline.deposits_awaiting_value}</p>
                <p className="mt-1 text-xs text-muted">
                  {pipeline.deposits_awaiting_payment} deposit(s) outstanding
                </p>
              </Link>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${pipeline.deposits_collected}</p>
                <p className="mt-1 text-xs text-muted">Deposits collected</p>
              </div>
              <Link
                href="/jobs"
                className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong"
              >
                <p className="text-2xl font-semibold">{pipeline.jobs_from_quotes}</p>
                <p className="mt-1 text-xs text-muted">Jobs created from quotes</p>
              </Link>
            </div>
          ) : (
            <p className="text-sm text-muted">Loading...</p>
          )}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Operations — Jobs in flight</h2>
            <Link href="/operations" className="text-xs text-muted underline">
              View operations
            </Link>
          </div>
          {operations && (operations.blocked_jobs > 0 || operations.at_risk_jobs > 0 || operations.open_exceptions > 0) && (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50/30 p-4 text-sm text-amber-700">
              OPERATIONS NEEDS ATTENTION — {operations.blocked_jobs} blocked job(s), {operations.at_risk_jobs} at-risk
              job(s), {operations.open_exceptions} open exception(s).
            </div>
          )}
          {operations ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <Link href="/jobs" className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong">
                <p className="text-2xl font-semibold">{operations.jobs_today}</p>
                <p className="mt-1 text-xs text-muted">Jobs today</p>
              </Link>
              <Link href="/jobs" className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong">
                <p className="text-2xl font-semibold">{operations.unassigned_jobs}</p>
                <p className="mt-1 text-xs text-muted">Unassigned</p>
              </Link>
              <Link
                href="/operations"
                className={`rounded-lg border p-4 transition hover:border-border-strong ${
                  operations.at_risk_jobs > 0 ? "border-amber-200 bg-amber-50/20" : "border-border bg-surface"
                }`}
              >
                <p className="text-2xl font-semibold">{operations.at_risk_jobs}</p>
                <p className="mt-1 text-xs text-muted">At risk</p>
              </Link>
              <Link
                href="/operations"
                className={`rounded-lg border p-4 transition hover:border-border-strong ${
                  operations.blocked_jobs > 0 ? "border-red-200 bg-red-50/20" : "border-border bg-surface"
                }`}
              >
                <p className="text-2xl font-semibold">{operations.blocked_jobs}</p>
                <p className="mt-1 text-xs text-muted">Blocked</p>
              </Link>
              <Link href="/jobs" className="rounded-lg border border-border bg-surface p-4 shadow-card transition hover:border-border-strong">
                <p className="text-2xl font-semibold">{operations.qa_pending_jobs}</p>
                <p className="mt-1 text-xs text-muted">Awaiting QA</p>
              </Link>
              <Link
                href="/exceptions"
                className={`rounded-lg border p-4 transition hover:border-border-strong ${
                  operations.open_exceptions > 0 ? "border-amber-200 bg-amber-50/20" : "border-border bg-surface"
                }`}
              >
                <p className="text-2xl font-semibold">{operations.open_exceptions}</p>
                <p className="mt-1 text-xs text-muted">Open exceptions</p>
              </Link>
            </div>
          ) : (
            <p className="text-sm text-muted">Loading...</p>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-muted">Business Health — Finance</h2>
          {finance?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50/30 p-4 text-sm text-amber-700">
              FINANCE NEEDS ATTENTION — {finance.overdue_invoice_count} overdue invoice(s),{" "}
              {finance.open_finance_exception_count} open finance exception(s).
            </div>
          )}
          {finance && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${finance.total_ar}</p>
                <p className="mt-1 text-xs text-muted">Total AR outstanding</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{finance.overdue_invoice_count}</p>
                <p className="mt-1 text-xs text-muted">Overdue invoices</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{finance.pending_approval_invoice_count}</p>
                <p className="mt-1 text-xs text-muted">Pending approval</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${finance.total_paid}</p>
                <p className="mt-1 text-xs text-muted">Total paid</p>
              </div>
            </div>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-muted">Marketing &amp; Demand Generation</h2>
          {marketing?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50/30 p-4 text-sm text-amber-700">
              MARKETING NEEDS ATTENTION — {marketing.open_marketing_exception_count} open marketing exception(s).
            </div>
          )}
          {marketing && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${marketing.marketing_spend}</p>
                <p className="mt-1 text-xs text-muted">Marketing spend</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{marketing.leads}</p>
                <p className="mt-1 text-xs text-muted">Leads</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{marketing.jobs_won}</p>
                <p className="mt-1 text-xs text-muted">Jobs won</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${marketing.revenue}</p>
                <p className="mt-1 text-xs text-muted">Revenue attributed</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{marketing.cac ? `$${marketing.cac}` : "—"}</p>
                <p className="mt-1 text-xs text-muted">CAC</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{marketing.roas ? `${marketing.roas}x` : "—"}</p>
                <p className="mt-1 text-xs text-muted">ROAS</p>
              </div>
            </div>
          )}
        </section>

        <section className="mb-8">
          <h2 className="mb-3 text-sm font-medium text-muted">Retention &amp; Referral</h2>
          {retention?.needs_attention && (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50/30 p-4 text-sm text-amber-700">
              RETENTION NEEDS ATTENTION — {retention.open_retention_exception_count} open retention exception(s).
            </div>
          )}
          {retention && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{retention.at_risk_customers}</p>
                <p className="mt-1 text-xs text-muted">At-risk customers</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{retention.retention_opportunities_open}</p>
                <p className="mt-1 text-xs text-muted">Retention opportunities</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${retention.repeat_customer_revenue}</p>
                <p className="mt-1 text-xs text-muted">Repeat revenue</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{retention.referral_leads}</p>
                <p className="mt-1 text-xs text-muted">Referral leads</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">${retention.referral_revenue}</p>
                <p className="mt-1 text-xs text-muted">Referral revenue</p>
              </div>
              <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
                <p className="text-2xl font-semibold">{retention.negative_feedback_count}</p>
                <p className="mt-1 text-xs text-muted">Review issues</p>
              </div>
            </div>
          )}
        </section>

        <section className="mb-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-muted">Recent Activity</h2>
            <div className="flex items-center gap-2">
              <select
                value={activityCategory}
                onChange={(e) => {
                  setActivityCategory(e.target.value);
                  setActivityPage(1);
                }}
                className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-muted"
              >
                {ACTIVITY_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c === "ALL" ? "All categories" : c}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {!activity && activityLoading ? (
            <p className="text-sm text-muted">Loading...</p>
          ) : !activity || activity.items.length === 0 ? (
            <div className="rounded-lg border border-border bg-surface shadow-card">
              <EmptyState icon={Activity} title="No activity yet." compact />
            </div>
          ) : (
            <>
              <ul className="divide-y divide-border rounded-lg border border-border bg-surface">
                {activity.items.map((item) => {
                  const row = (
                    <div className="flex items-start justify-between gap-4 p-4 transition hover:bg-surface-muted">
                      <div className="min-w-0">
                        <div className="mb-1 flex items-center gap-2">
                          <span
                            className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${
                              ACTIVITY_SEVERITY_STYLE[item.severity] ?? ACTIVITY_SEVERITY_STYLE.INFO
                            }`}
                          >
                            {item.category}
                          </span>
                          <span className="text-[10px] text-muted-foreground">{timeAgo(item.timestamp)}</span>
                        </div>
                        <p className="truncate text-sm text-foreground">{item.title}</p>
                        {item.description && (
                          <p className="mt-0.5 truncate text-xs text-muted">{item.description}</p>
                        )}
                      </div>
                      <div className="shrink-0 text-right text-xs text-muted">
                        {item.actor_name && <div>{item.actor_name}</div>}
                        {item.link && <div className="mt-1 text-muted underline">View</div>}
                      </div>
                    </div>
                  );
                  return (
                    <li key={item.id}>
                      {item.link ? (
                        <Link href={item.link}>{row}</Link>
                      ) : (
                        row
                      )}
                    </li>
                  );
                })}
              </ul>
              <div className="mt-3 flex items-center justify-between text-xs text-muted">
                <span>
                  Page {activity.page} · {activity.total} total
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setActivityPage((p) => Math.max(1, p - 1))}
                    disabled={activityPage <= 1 || activityLoading}
                    className="rounded-md border border-border px-2 py-1 disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setActivityPage((p) => p + 1)}
                    disabled={activityPage * activity.page_size >= activity.total || activityLoading}
                    className="rounded-md border border-border px-2 py-1 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </section>

        {PENDING_MODULES.length > 0 && (
          <section className="rounded-lg border border-border bg-surface p-6">
            <h2 className="mb-2 text-sm font-medium text-muted">Foundation status</h2>
            <p className="text-sm text-muted">
              Authentication, multi-tenancy, RBAC, the event bus, and CRM (leads/customers/
              appointments) are live. The modules below are not yet built — this cockpit will never
              show placeholder numbers for them.
            </p>
            <ul className="mt-4 space-y-2">
              {PENDING_MODULES.map((m) => (
                <li key={m} className="flex items-center justify-between text-sm">
                  <span className="text-muted">{m}</span>
                  <span className="rounded-full border border-border-strong px-2 py-0.5 text-xs text-muted">
                    not connected
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </AppShell>
  );
}
