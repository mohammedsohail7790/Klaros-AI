"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  IntegrationConnectionRow,
  IntegrationStatusRow,
  GoogleCalendarImportResult,
  QuickBooksImportResult,
  connectIntegration,
  disconnectIntegration,
  getGoogleCalendarAuthorizeUrl,
  getQuickBooksAuthorizeUrl,
  importFromGoogleCalendar,
  importFromQuickBooks,
  listIntegrationConnections,
  listIntegrationStatus,
  verifyIntegrationConnection,
} from "@/lib/api";

const CATEGORY: Record<string, string> = {
  stripe: "Finance",
  quickbooks: "Finance",
  servicetitan: "Operations",
  jobber: "Operations",
  google_ads: "Marketing",
  meta_ads: "Marketing",
  gmail: "Communications",
  twilio: "Communications",
  sendgrid: "Communications",
  anthropic: "AI",
  openai: "AI",
  groq: "AI",
  deepseek: "AI",
  nvidia: "AI",
  google_ai: "AI",
  supplier_procurement: "Operations",
};

const DISPLAY_NAME: Record<string, string> = {
  stripe: "Stripe",
  quickbooks: "QuickBooks",
  servicetitan: "ServiceTitan",
  jobber: "Jobber",
  google_ads: "Google Ads",
  meta_ads: "Meta Ads",
  gmail: "Gmail",
  twilio: "Twilio",
  sendgrid: "SendGrid",
  anthropic: "Anthropic",
  openai: "OpenAI",
  groq: "Groq",
  deepseek: "DeepSeek",
  nvidia: "NVIDIA",
  google_ai: "Google AI",
  supplier_procurement: "Supplier / Procurement",
};

function groupByCategory(rows: IntegrationStatusRow[]): Record<string, IntegrationStatusRow[]> {
  const groups: Record<string, IntegrationStatusRow[]> = {};
  for (const row of rows) {
    const category = CATEGORY[row.provider] ?? "Other";
    if (!groups[category]) groups[category] = [];
    groups[category].push(row);
  }
  return groups;
}

// Klaros itself provides the AI — a tenant never brings their own AI key.
// One platform-wide setting (AI_PROVIDER=auto, see
// backend/app/services/ai_provider.py's get_ai_provider) picks the first
// configured engine in this exact priority order and uses it for every AI
// Next Action, Morning Brief, and voice call; the rest are just optional
// fallback engines, not something a tenant needs configured. Showing all
// 6 individually on the main page read as "AI is half-broken" when really
// only one needs to work — so the main list shows a single synthesized
// row for whichever engine is actually active, with the raw per-provider
// breakdown moved behind an owner-only disclosure for real diagnostics.
const AI_PROVIDER_PRIORITY = ["anthropic", "openai", "groq", "deepseek", "nvidia", "google_ai"];

function getActiveAIRow(aiRows: IntegrationStatusRow[]): IntegrationStatusRow | null {
  for (const provider of AI_PROVIDER_PRIORITY) {
    const row = aiRows.find((r) => r.provider === provider);
    // NOT_CONNECTED means no key is configured for that engine at all, so
    // the backend cascade skips straight past it — CONNECTED or ERROR both
    // mean a key IS configured there, which is exactly what the cascade
    // actually uses to pick the active engine.
    if (row && row.status !== "NOT_CONNECTED") return row;
  }
  return null;
}

// Phase 12D: each tenant has their OWN account with these providers (unlike
// Stripe/Twilio/etc. above, which use one shared platform credential) — so
// they need the tenant-scoped IntegrationConnection model, not the
// platform-status list. None have a real OAuth client built yet (see
// INTEGRATIONS.md) — shown here honestly as NOT_IMPLEMENTED, with no
// "Connect" button pointed at nothing real. QuickBooks moved OUT of this
// list in Phase 13, Google Calendar in Phase 14 — both have real OAuth2
// clients now (see their dedicated sections below).
const PLANNED_OAUTH_PROVIDERS = [
  { provider: "gmail_oauth", name: "Gmail (OAuth)", category: "Communications" },
  { provider: "google_ads_oauth", name: "Google Ads (per-tenant)", category: "Marketing" },
  { provider: "meta_ads_oauth", name: "Meta Ads (per-tenant)", category: "Marketing" },
];

export default function IntegrationsPage() {
  return (
    <Suspense fallback={<Skeleton />}>
      <IntegrationsPageInner />
    </Suspense>
  );
}

function IntegrationsPageInner() {
  const { token, user, loading: authLoading } = useAuth();
  const searchParams = useSearchParams();
  const [rows, setRows] = useState<IntegrationStatusRow[] | null>(null);
  const [connections, setConnections] = useState<IntegrationConnectionRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [stripeKeyInput, setStripeKeyInput] = useState("");
  const [stripeActionPending, setStripeActionPending] = useState(false);
  const [stripeActionError, setStripeActionError] = useState<string | null>(null);
  const [quickbooksActionPending, setQuickbooksActionPending] = useState(false);
  const [quickbooksActionError, setQuickbooksActionError] = useState<string | null>(null);
  const [quickbooksImportPending, setQuickbooksImportPending] = useState(false);
  const [quickbooksImportResult, setQuickbooksImportResult] = useState<QuickBooksImportResult | null>(null);
  // Set only from the ?quickbooks=connected|error query param the backend's
  // real OAuth callback redirects back to after a genuine attempt — never
  // fabricated locally.
  const [quickbooksCallbackNotice, setQuickbooksCallbackNotice] = useState<
    { kind: "connected" | "error"; detail?: string } | null
  >(null);
  const [googleCalendarActionPending, setGoogleCalendarActionPending] = useState(false);
  const [googleCalendarActionError, setGoogleCalendarActionError] = useState<string | null>(null);
  const [googleCalendarImportPending, setGoogleCalendarImportPending] = useState(false);
  const [googleCalendarImportResult, setGoogleCalendarImportResult] = useState<GoogleCalendarImportResult | null>(null);
  const [googleCalendarCallbackNotice, setGoogleCalendarCallbackNotice] = useState<
    { kind: "connected" | "error"; detail?: string } | null
  >(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [statusResult, connectionsResult] = await Promise.all([
        listIntegrationStatus(token),
        listIntegrationConnections(token),
      ]);
      setRows(statusResult);
      setConnections(connectionsResult);
      setLastChecked(new Date());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load integration status.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const qb = searchParams.get("quickbooks");
    if (qb === "connected") {
      setQuickbooksCallbackNotice({ kind: "connected" });
    } else if (qb === "error") {
      setQuickbooksCallbackNotice({ kind: "error", detail: searchParams.get("detail") ?? undefined });
    }
    const gcal = searchParams.get("google_calendar");
    if (gcal === "connected") {
      setGoogleCalendarCallbackNotice({ kind: "connected" });
    } else if (gcal === "error") {
      setGoogleCalendarCallbackNotice({ kind: "error", detail: searchParams.get("detail") ?? undefined });
    }
  }, [searchParams]);

  async function handleConnectGoogleCalendar() {
    if (!token) return;
    setGoogleCalendarActionPending(true);
    setGoogleCalendarActionError(null);
    try {
      const { authorization_url } = await getGoogleCalendarAuthorizeUrl(token);
      window.location.href = authorization_url;
    } catch (err) {
      setGoogleCalendarActionError(err instanceof ApiError ? err.message : "Unable to start the Google Calendar connection.");
      setGoogleCalendarActionPending(false);
    }
  }

  async function handleDisconnectGoogleCalendar() {
    if (!token) return;
    setGoogleCalendarActionPending(true);
    setGoogleCalendarActionError(null);
    try {
      await disconnectIntegration(token, "google_calendar");
      await load();
    } catch (err) {
      setGoogleCalendarActionError(err instanceof ApiError ? err.message : "Unable to disconnect Google Calendar.");
    } finally {
      setGoogleCalendarActionPending(false);
    }
  }

  async function handleVerifyGoogleCalendar() {
    if (!token) return;
    setGoogleCalendarActionPending(true);
    setGoogleCalendarActionError(null);
    try {
      await verifyIntegrationConnection(token, "google_calendar");
      await load();
    } catch (err) {
      setGoogleCalendarActionError(err instanceof ApiError ? err.message : "Unable to verify the Google Calendar connection.");
    } finally {
      setGoogleCalendarActionPending(false);
    }
  }

  async function handleImportFromGoogleCalendar() {
    if (!token) return;
    setGoogleCalendarImportPending(true);
    setGoogleCalendarActionError(null);
    setGoogleCalendarImportResult(null);
    try {
      const now = new Date();
      const timeMin = new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000);
      const timeMax = new Date(now.getTime() + 90 * 24 * 60 * 60 * 1000);
      const result = await importFromGoogleCalendar(token, {
        time_min: timeMin.toISOString(),
        time_max: timeMax.toISOString(),
      });
      setGoogleCalendarImportResult(result);
    } catch (err) {
      setGoogleCalendarActionError(err instanceof ApiError ? err.message : "Unable to import from Google Calendar.");
    } finally {
      setGoogleCalendarImportPending(false);
    }
  }

  async function handleConnectQuickBooks() {
    if (!token) return;
    setQuickbooksActionPending(true);
    setQuickbooksActionError(null);
    try {
      const { authorization_url } = await getQuickBooksAuthorizeUrl(token);
      // A real redirect to Intuit's own consent page — the frontend never
      // collects a QuickBooks credential itself (there is none to collect;
      // the backend's callback does the real token exchange server-side).
      window.location.href = authorization_url;
    } catch (err) {
      setQuickbooksActionError(err instanceof ApiError ? err.message : "Unable to start the QuickBooks connection.");
      setQuickbooksActionPending(false);
    }
  }

  async function handleDisconnectQuickBooks() {
    if (!token) return;
    setQuickbooksActionPending(true);
    setQuickbooksActionError(null);
    try {
      await disconnectIntegration(token, "quickbooks");
      await load();
    } catch (err) {
      setQuickbooksActionError(err instanceof ApiError ? err.message : "Unable to disconnect QuickBooks.");
    } finally {
      setQuickbooksActionPending(false);
    }
  }

  async function handleVerifyQuickBooks() {
    if (!token) return;
    setQuickbooksActionPending(true);
    setQuickbooksActionError(null);
    try {
      await verifyIntegrationConnection(token, "quickbooks");
      await load();
    } catch (err) {
      setQuickbooksActionError(err instanceof ApiError ? err.message : "Unable to verify the QuickBooks connection.");
    } finally {
      setQuickbooksActionPending(false);
    }
  }

  async function handleImportFromQuickBooks() {
    if (!token) return;
    setQuickbooksImportPending(true);
    setQuickbooksActionError(null);
    setQuickbooksImportResult(null);
    try {
      const result = await importFromQuickBooks(token);
      setQuickbooksImportResult(result);
    } catch (err) {
      setQuickbooksActionError(err instanceof ApiError ? err.message : "Unable to import from QuickBooks.");
    } finally {
      setQuickbooksImportPending(false);
    }
  }

  async function handleConnectStripe() {
    if (!token || !stripeKeyInput.trim()) return;
    setStripeActionPending(true);
    setStripeActionError(null);
    try {
      await connectIntegration(token, "stripe", { secret_key: stripeKeyInput.trim() });
      setStripeKeyInput("");
      await load();
    } catch (err) {
      setStripeActionError(err instanceof ApiError ? err.message : "Unable to connect Stripe.");
    } finally {
      setStripeActionPending(false);
    }
  }

  async function handleVerifyStripe() {
    if (!token) return;
    setStripeActionPending(true);
    setStripeActionError(null);
    try {
      await verifyIntegrationConnection(token, "stripe");
      await load();
    } catch (err) {
      setStripeActionError(err instanceof ApiError ? err.message : "Unable to verify Stripe connection.");
    } finally {
      setStripeActionPending(false);
    }
  }

  async function handleDisconnectStripe() {
    if (!token) return;
    setStripeActionPending(true);
    setStripeActionError(null);
    try {
      await disconnectIntegration(token, "stripe");
      await load();
    } catch (err) {
      setStripeActionError(err instanceof ApiError ? err.message : "Unable to disconnect Stripe.");
    } finally {
      setStripeActionPending(false);
    }
  }

  if (authLoading) return null;

  const grouped = rows ? groupByCategory(rows) : {};
  const activeAIRow = grouped["AI"] ? getActiveAIRow(grouped["AI"]) : null;
  // The header count reflects what's actually visible on the page — the 6
  // raw AI rows collapse into 1 synthesized row here too, so this never
  // says "N of 16" while only listing a dozen or so rows.
  const visibleRows = rows
    ? [
        ...rows.filter((r) => CATEGORY[r.provider] !== "AI"),
        activeAIRow ?? { provider: "ai", status: "NOT_CONNECTED", detail: "No AI engine configured." },
      ]
    : [];
  const connectedCount = visibleRows.filter((r) => r.status === "CONNECTED").length;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="font-display text-2xl text-foreground">Integrations</h1>
            <p className="mt-1 text-sm text-muted">
              Every status here is checked live against the real provider — nothing is fabricated.
              {rows && ` ${connectedCount} of ${visibleRows.length} connected.`}
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
          >
            {loading ? "Checking..." : "Recheck all"}
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded border border-danger/25 bg-danger/[0.06] px-4 py-3 text-sm text-danger">
            {error}
          </div>
        )}

        {!rows && !error && <Skeleton />}

        {rows &&
          Object.entries(grouped).map(([category, categoryRows]) => {
            if (category === "AI") {
              const active = getActiveAIRow(categoryRows);
              return (
                <div key={category} className="mb-6">
                  <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">AI</h2>
                  <div className="divide-y divide-border rounded border border-border">
                    <div className="flex items-center justify-between px-4 py-3">
                      <div>
                        <div className="text-sm font-medium text-foreground">
                          Klaros AI{active ? ` — powered by ${DISPLAY_NAME[active.provider] ?? active.provider}` : ""}
                        </div>
                        <div className="mt-0.5 text-xs text-muted">
                          {active
                            ? active.detail
                            : "No AI engine configured — Klaros falls back to deterministic (non-AI) behavior."}
                        </div>
                      </div>
                      <Badge status={active?.status ?? "NOT_CONNECTED"}>{active?.status ?? "NOT_CONNECTED"}</Badge>
                    </div>
                  </div>
                  {user?.role === "OWNER" && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-muted hover:text-foreground">
                        Show all {categoryRows.length} AI engines (owner-only diagnostics)
                      </summary>
                      <p className="mb-2 mt-2 text-muted-foreground">
                        Klaros picks ONE of these automatically (Anthropic &gt; OpenAI &gt; Groq &gt; DeepSeek &gt;
                        NVIDIA &gt; Google AI, first one with a configured key) — the rest are optional fallback
                        engines, not something you need to configure yourself.
                      </p>
                      <div className="divide-y divide-border rounded border border-border">
                        {categoryRows.map((row) => (
                          <div key={row.provider} className="flex items-center justify-between px-4 py-3">
                            <div>
                              <div className="text-sm font-medium text-foreground">
                                {DISPLAY_NAME[row.provider] ?? row.provider}
                              </div>
                              <div className="mt-0.5 text-xs text-muted">{row.detail}</div>
                            </div>
                            <Badge status={row.status}>{row.status}</Badge>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              );
            }
            return (
              <div key={category} className="mb-6">
                <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
                  {category}
                </h2>
                <div className="divide-y divide-border rounded border border-border">
                  {categoryRows.map((row) => (
                    <div key={row.provider} className="flex items-center justify-between px-4 py-3">
                      <div>
                        <div className="text-sm font-medium text-foreground">
                          {DISPLAY_NAME[row.provider] ?? row.provider}
                        </div>
                        <div className="mt-0.5 text-xs text-muted">{row.detail}</div>
                      </div>
                      <Badge status={row.status}>{row.status}</Badge>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}

        {rows && user && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
              Inbound Twilio Lead Capture
            </h2>
            <p className="mb-2 text-xs text-muted">
              Paste these into your Twilio phone number&apos;s console configuration to capture inbound
              texts and calls as real leads. The tenant ID in the URL is how requests are routed to
              your account — Twilio&apos;s own request signature covers the exact URL, so it cannot be
              reused for another tenant.
            </p>
            <div className="space-y-2 rounded border border-border p-3 text-xs">
              <div>
                <div className="text-muted">Messaging &mdash; &quot;A message comes in&quot;</div>
                <code className="break-all text-muted">
                  {(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") +
                    `/api/v1/webhooks/twilio/inbound-sms/${user.tenant_id}`}
                </code>
              </div>
              <div>
                <div className="text-muted">Voice &mdash; &quot;A call comes in&quot;</div>
                <code className="break-all text-muted">
                  {(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") +
                    `/api/v1/webhooks/twilio/inbound-voice/${user.tenant_id}`}
                </code>
              </div>
            </div>
          </div>
        )}

        {rows && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
              Your Own Stripe Account (Phase 12F)
            </h2>
            <p className="mb-2 text-xs text-muted">
              Optional — connect your OWN Stripe secret key to use it instead of the platform-shared
              key above for your checkout links. Verified with a real, live API call the moment you
              connect. The key is encrypted at rest and never shown again after submission.
            </p>
            <div className="rounded border border-border px-4 py-3">
              {(() => {
                const stripeConnection = connections?.find((c) => c.provider === "stripe");
                return (
                  <>
                    {stripeConnection && (
                      <div className="mb-3 flex items-center justify-between">
                        <div>
                          <div className="text-sm font-medium text-foreground">Stripe (your account)</div>
                          <div className="mt-0.5 text-xs text-muted">
                            {stripeConnection.last_error ??
                              (stripeConnection.last_verified_at
                                ? `Last verified ${new Date(stripeConnection.last_verified_at).toLocaleString()}`
                                : "Not yet verified")}
                          </div>
                        </div>
                        <Badge status={stripeConnection.status}>{stripeConnection.status}</Badge>
                      </div>
                    )}

                    {stripeActionError && (
                      <div className="mb-2 rounded border border-danger/25 bg-danger/[0.06] px-3 py-2 text-xs text-danger">
                        {stripeActionError}
                      </div>
                    )}

                    <div className="flex gap-2">
                      <input
                        type="password"
                        autoComplete="off"
                        placeholder="sk_test_... or sk_live_..."
                        value={stripeKeyInput}
                        onChange={(e) => setStripeKeyInput(e.target.value)}
                        className="flex-1 rounded border border-border-strong bg-surface px-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground"
                      />
                      <button
                        onClick={handleConnectStripe}
                        disabled={stripeActionPending || !stripeKeyInput.trim()}
                        className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                      >
                        {stripeActionPending ? "Working..." : "Connect"}
                      </button>
                      {stripeConnection && stripeConnection.status !== "DISCONNECTED" && (
                        <>
                          <button
                            onClick={handleVerifyStripe}
                            disabled={stripeActionPending}
                            className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                          >
                            Verify
                          </button>
                          <button
                            onClick={handleDisconnectStripe}
                            disabled={stripeActionPending}
                            className="rounded border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
                          >
                            Disconnect
                          </button>
                        </>
                      )}
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        )}

        {quickbooksCallbackNotice && (
          <div
            className={`mb-4 rounded border px-4 py-3 text-sm ${
              quickbooksCallbackNotice.kind === "connected"
                ? "border-success/20 bg-success/[0.06] text-success"
                : "border-danger/25 bg-danger/[0.06] text-danger"
            }`}
          >
            {quickbooksCallbackNotice.kind === "connected"
              ? "QuickBooks connected — verified with a real, live API call."
              : `QuickBooks connection failed${
                  quickbooksCallbackNotice.detail ? `: ${quickbooksCallbackNotice.detail}` : "."
                }`}
          </div>
        )}

        {rows && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
              Your Own QuickBooks Company (Phase 13)
            </h2>
            <p className="mb-2 text-xs text-muted">
              Connect your own QuickBooks Online company to sync approved invoices there. Uses a real
              OAuth2 flow through Intuit&apos;s own consent page — Klaros never sees or asks for a
              QuickBooks password, and the connection is verified with a real, live API call the
              moment it completes.
            </p>
            <div className="rounded border border-border px-4 py-3">
              {(() => {
                const qbConnection = connections?.find((c) => c.provider === "quickbooks");
                return (
                  <>
                    {qbConnection && (
                      <div className="mb-3 flex items-center justify-between">
                        <div>
                          <div className="text-sm font-medium text-foreground">
                            QuickBooks{qbConnection.external_account_id ? ` (company ${qbConnection.external_account_id})` : ""}
                          </div>
                          <div className="mt-0.5 text-xs text-muted">
                            {qbConnection.last_error ??
                              (qbConnection.last_verified_at
                                ? `Last verified ${new Date(qbConnection.last_verified_at).toLocaleString()}`
                                : "Not yet verified")}
                          </div>
                        </div>
                        <Badge status={qbConnection.status}>{qbConnection.status}</Badge>
                      </div>
                    )}

                    {quickbooksActionError && (
                      <div className="mb-2 rounded border border-danger/25 bg-danger/[0.06] px-3 py-2 text-xs text-danger">
                        {quickbooksActionError}
                      </div>
                    )}

                    <div className="flex gap-2">
                      <button
                        onClick={handleConnectQuickBooks}
                        disabled={quickbooksActionPending}
                        className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                      >
                        {quickbooksActionPending
                          ? "Working..."
                          : qbConnection && qbConnection.status !== "DISCONNECTED"
                            ? "Reconnect"
                            : "Connect with QuickBooks"}
                      </button>
                      {qbConnection && qbConnection.status !== "DISCONNECTED" && (
                        <>
                          <button
                            onClick={handleVerifyQuickBooks}
                            disabled={quickbooksActionPending}
                            className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                          >
                            Verify
                          </button>
                          <button
                            onClick={handleDisconnectQuickBooks}
                            disabled={quickbooksActionPending}
                            className="rounded border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
                          >
                            Disconnect
                          </button>
                        </>
                      )}
                    </div>

                    {qbConnection && qbConnection.status === "CONNECTED" && (
                      <div className="mt-3 border-t border-border pt-3">
                        <p className="mb-2 text-xs text-muted">
                          Bring your existing QuickBooks customers and invoices into Klaros — real data,
                          matched or created for real, never simulated. Safe to run more than once: anything
                          already imported is skipped, not duplicated.
                        </p>
                        <button
                          onClick={handleImportFromQuickBooks}
                          disabled={quickbooksImportPending}
                          className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                        >
                          {quickbooksImportPending ? "Importing..." : "Import existing data"}
                        </button>
                        {quickbooksImportResult && (
                          <div className="mt-2 rounded border border-success/20 bg-success/[0.06] px-3 py-2 text-xs text-success">
                            {quickbooksImportResult.customers_created} customer(s) created,{" "}
                            {quickbooksImportResult.customers_matched} matched to existing customers —{" "}
                            {quickbooksImportResult.invoices_created} invoice(s) imported
                            {quickbooksImportResult.invoices_skipped > 0 &&
                              `, ${quickbooksImportResult.invoices_skipped} skipped`}
                            .
                            {quickbooksImportResult.invoice_results.some((r) => r.status === "skipped") && (
                              <ul className="mt-1 list-disc pl-4">
                                {quickbooksImportResult.invoice_results
                                  .filter((r) => r.status === "skipped")
                                  .map((r) => (
                                    <li key={r.quickbooks_invoice_id}>{r.reason}</li>
                                  ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
        )}

        {googleCalendarCallbackNotice && (
          <div
            className={`mb-4 rounded border px-4 py-3 text-sm ${
              googleCalendarCallbackNotice.kind === "connected"
                ? "border-success/20 bg-success/[0.06] text-success"
                : "border-danger/25 bg-danger/[0.06] text-danger"
            }`}
          >
            {googleCalendarCallbackNotice.kind === "connected"
              ? "Google Calendar connected — verified with a real, live API call."
              : `Google Calendar connection failed${
                  googleCalendarCallbackNotice.detail ? `: ${googleCalendarCallbackNotice.detail}` : "."
                }`}
          </div>
        )}

        {rows && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
              Your Own Google Calendar (Phase 14)
            </h2>
            <p className="mb-2 text-xs text-muted">
              Connect your own Google Calendar to sync confirmed Klaros appointments there. Uses a
              real OAuth2 flow through Google&apos;s own consent page — Klaros never sees or asks
              for a Google password, and the connection is verified with a real, live API call the
              moment it completes.
            </p>
            <div className="rounded border border-border px-4 py-3">
              {(() => {
                const gcalConnection = connections?.find((c) => c.provider === "google_calendar");
                return (
                  <>
                    {gcalConnection && (
                      <div className="mb-3 flex items-center justify-between">
                        <div>
                          <div className="text-sm font-medium text-foreground">Google Calendar</div>
                          <div className="mt-0.5 text-xs text-muted">
                            {gcalConnection.last_error ??
                              (gcalConnection.last_verified_at
                                ? `Last verified ${new Date(gcalConnection.last_verified_at).toLocaleString()}`
                                : "Not yet verified")}
                          </div>
                        </div>
                        <Badge status={gcalConnection.status}>{gcalConnection.status}</Badge>
                      </div>
                    )}

                    {googleCalendarActionError && (
                      <div className="mb-2 rounded border border-danger/25 bg-danger/[0.06] px-3 py-2 text-xs text-danger">
                        {googleCalendarActionError}
                      </div>
                    )}

                    <div className="flex gap-2">
                      <button
                        onClick={handleConnectGoogleCalendar}
                        disabled={googleCalendarActionPending}
                        className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                      >
                        {googleCalendarActionPending
                          ? "Working..."
                          : gcalConnection && gcalConnection.status !== "DISCONNECTED"
                            ? "Reconnect"
                            : "Connect with Google"}
                      </button>
                      {gcalConnection && gcalConnection.status !== "DISCONNECTED" && (
                        <>
                          <button
                            onClick={handleVerifyGoogleCalendar}
                            disabled={googleCalendarActionPending}
                            className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                          >
                            Verify
                          </button>
                          <button
                            onClick={handleDisconnectGoogleCalendar}
                            disabled={googleCalendarActionPending}
                            className="rounded border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
                          >
                            Disconnect
                          </button>
                        </>
                      )}
                    </div>

                    {gcalConnection && gcalConnection.status === "CONNECTED" && (
                      <div className="mt-3 border-t border-border pt-3">
                        <p className="mb-2 text-xs text-muted">
                          Bring your existing Google Calendar events (90 days back to 90 days ahead) into
                          Klaros as real appointments — matched or created for real. Safe to run more than
                          once: anything already imported is skipped, not duplicated.
                        </p>
                        <button
                          onClick={handleImportFromGoogleCalendar}
                          disabled={googleCalendarImportPending}
                          className="rounded border border-border-strong px-3 py-1.5 text-sm text-muted hover:bg-surface-muted disabled:opacity-50"
                        >
                          {googleCalendarImportPending ? "Importing..." : "Import existing events"}
                        </button>
                        {googleCalendarImportResult && (
                          <div className="mt-2 rounded border border-success/20 bg-success/[0.06] px-3 py-2 text-xs text-success">
                            {googleCalendarImportResult.appointments_created} appointment(s) imported
                            {googleCalendarImportResult.appointments_skipped > 0 &&
                              `, ${googleCalendarImportResult.appointments_skipped} skipped`}
                            .
                            {googleCalendarImportResult.results.some((r) => r.status === "skipped") && (
                              <ul className="mt-1 list-disc pl-4">
                                {googleCalendarImportResult.results
                                  .filter((r) => r.status === "skipped")
                                  .map((r) => (
                                    <li key={r.google_event_id}>{r.reason}</li>
                                  ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
          </div>
        )}

        {rows && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
              Tenant-Owned Connections (OAuth)
            </h2>
            <p className="mb-2 text-xs text-muted">
              Unlike the providers above (one shared credential for the whole platform), each business
              would connect their OWN account for these — real OAuth is not built yet for any of them.
            </p>
            <div className="divide-y divide-border rounded border border-border">
              {PLANNED_OAUTH_PROVIDERS.map((p) => {
                const existing = connections?.find((c) => c.provider === p.provider);
                const status = existing?.status ?? "NOT_IMPLEMENTED";
                return (
                  <div key={p.provider} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <div className="text-sm font-medium text-foreground">{p.name}</div>
                      <div className="mt-0.5 text-xs text-muted">
                        {existing?.last_error ?? "No real OAuth client implemented for this provider yet."}
                      </div>
                    </div>
                    <Badge status={status}>{status}</Badge>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {lastChecked && (
          <p className="mt-2 text-xs text-muted-foreground">Last checked {lastChecked.toLocaleTimeString()}</p>
        )}

        <div className="mt-8 rounded border border-border bg-surface px-4 py-3 text-xs text-muted">
          CONNECTED means the configured credential was just verified against the provider&apos;s real
          API (a live, read-only call). NOT_CONNECTED means no credential is configured. ERROR means a
          credential is configured but was rejected, or a required companion setting (e.g. a verified
          sender email/number) is missing — see the detail message above. NOT_IMPLEMENTED (below) means
          the tenant-scoped connection API exists and enforces real tenant isolation, but no real OAuth
          client has been built for that provider yet — see INTEGRATIONS.md.
        </div>
      </div>
    </AppShell>
  );
}
