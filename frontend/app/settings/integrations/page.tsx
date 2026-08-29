"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  IntegrationConnectionRow,
  IntegrationStatusRow,
  connectIntegration,
  disconnectIntegration,
  listIntegrationConnections,
  listIntegrationStatus,
  verifyIntegrationConnection,
} from "@/lib/api";

const STATUS_COLOR: Record<string, string> = {
  CONNECTED: "border-emerald-800 text-emerald-300",
  NOT_CONNECTED: "border-neutral-700 text-neutral-400",
  ERROR: "border-red-800 text-red-300",
};

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

// Phase 12D: each tenant has their OWN account with these providers (unlike
// Stripe/Twilio/etc. above, which use one shared platform credential) — so
// they need the tenant-scoped IntegrationConnection model, not the
// platform-status list. None have a real OAuth client built yet (see
// INTEGRATIONS.md) — shown here honestly as NOT_IMPLEMENTED, with no
// "Connect" button pointed at nothing real.
const PLANNED_OAUTH_PROVIDERS = [
  { provider: "quickbooks", name: "QuickBooks", category: "Finance" },
  { provider: "google_calendar", name: "Google Calendar", category: "Operations" },
  { provider: "gmail_oauth", name: "Gmail (OAuth)", category: "Communications" },
  { provider: "google_ads_oauth", name: "Google Ads (per-tenant)", category: "Marketing" },
  { provider: "meta_ads_oauth", name: "Meta Ads (per-tenant)", category: "Marketing" },
];

export default function IntegrationsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [rows, setRows] = useState<IntegrationStatusRow[] | null>(null);
  const [connections, setConnections] = useState<IntegrationConnectionRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [stripeKeyInput, setStripeKeyInput] = useState("");
  const [stripeActionPending, setStripeActionPending] = useState(false);
  const [stripeActionError, setStripeActionError] = useState<string | null>(null);

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
  const connectedCount = rows?.filter((r) => r.status === "CONNECTED").length ?? 0;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-neutral-100">Integrations</h1>
            <p className="mt-1 text-sm text-neutral-400">
              Every status here is checked live against the real provider — nothing is fabricated.
              {rows && ` ${connectedCount} of ${rows.length} connected.`}
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="rounded border border-neutral-700 px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-900 disabled:opacity-50"
          >
            {loading ? "Checking..." : "Recheck all"}
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded border border-red-800 bg-red-950/30 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {!rows && !error && <p className="text-sm text-neutral-500">Loading...</p>}

        {rows &&
          Object.entries(grouped).map(([category, categoryRows]) => (
            <div key={category} className="mb-6">
              <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
                {category}
              </h2>
              <div className="divide-y divide-neutral-800 rounded border border-neutral-800">
                {categoryRows.map((row) => (
                  <div key={row.provider} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <div className="text-sm font-medium text-neutral-200">
                        {DISPLAY_NAME[row.provider] ?? row.provider}
                      </div>
                      <div className="mt-0.5 text-xs text-neutral-500">{row.detail}</div>
                    </div>
                    <span
                      className={`rounded border px-2 py-0.5 text-xs font-medium ${
                        STATUS_COLOR[row.status] ?? "border-neutral-700 text-neutral-400"
                      }`}
                    >
                      {row.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}

        {rows && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
              Your Own Stripe Account (Phase 12F)
            </h2>
            <p className="mb-2 text-xs text-neutral-500">
              Optional — connect your OWN Stripe secret key to use it instead of the platform-shared
              key above for your checkout links. Verified with a real, live API call the moment you
              connect. The key is encrypted at rest and never shown again after submission.
            </p>
            <div className="rounded border border-neutral-800 px-4 py-3">
              {(() => {
                const stripeConnection = connections?.find((c) => c.provider === "stripe");
                return (
                  <>
                    {stripeConnection && (
                      <div className="mb-3 flex items-center justify-between">
                        <div>
                          <div className="text-sm font-medium text-neutral-200">Stripe (your account)</div>
                          <div className="mt-0.5 text-xs text-neutral-500">
                            {stripeConnection.last_error ??
                              (stripeConnection.last_verified_at
                                ? `Last verified ${new Date(stripeConnection.last_verified_at).toLocaleString()}`
                                : "Not yet verified")}
                          </div>
                        </div>
                        <span
                          className={`rounded border px-2 py-0.5 text-xs font-medium ${
                            STATUS_COLOR[stripeConnection.status] ?? "border-neutral-700 text-neutral-400"
                          }`}
                        >
                          {stripeConnection.status}
                        </span>
                      </div>
                    )}

                    {stripeActionError && (
                      <div className="mb-2 rounded border border-red-800 bg-red-950/30 px-3 py-2 text-xs text-red-300">
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
                        className="flex-1 rounded border border-neutral-700 bg-neutral-950 px-3 py-1.5 text-sm text-neutral-200 placeholder:text-neutral-600"
                      />
                      <button
                        onClick={handleConnectStripe}
                        disabled={stripeActionPending || !stripeKeyInput.trim()}
                        className="rounded border border-neutral-700 px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-900 disabled:opacity-50"
                      >
                        {stripeActionPending ? "Working..." : "Connect"}
                      </button>
                      {stripeConnection && stripeConnection.status !== "DISCONNECTED" && (
                        <>
                          <button
                            onClick={handleVerifyStripe}
                            disabled={stripeActionPending}
                            className="rounded border border-neutral-700 px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-900 disabled:opacity-50"
                          >
                            Verify
                          </button>
                          <button
                            onClick={handleDisconnectStripe}
                            disabled={stripeActionPending}
                            className="rounded border border-red-900 px-3 py-1.5 text-sm text-red-300 hover:bg-red-950 disabled:opacity-50"
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

        {rows && (
          <div className="mb-6">
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
              Tenant-Owned Connections (OAuth)
            </h2>
            <p className="mb-2 text-xs text-neutral-500">
              Unlike the providers above (one shared credential for the whole platform), each business
              would connect their OWN account for these — real OAuth is not built yet for any of them.
            </p>
            <div className="divide-y divide-neutral-800 rounded border border-neutral-800">
              {PLANNED_OAUTH_PROVIDERS.map((p) => {
                const existing = connections?.find((c) => c.provider === p.provider);
                const status = existing?.status ?? "NOT_IMPLEMENTED";
                return (
                  <div key={p.provider} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <div className="text-sm font-medium text-neutral-200">{p.name}</div>
                      <div className="mt-0.5 text-xs text-neutral-500">
                        {existing?.last_error ?? "No real OAuth client implemented for this provider yet."}
                      </div>
                    </div>
                    <span
                      className={`rounded border px-2 py-0.5 text-xs font-medium ${
                        status === "NOT_IMPLEMENTED"
                          ? "border-neutral-800 text-neutral-600"
                          : STATUS_COLOR[status] ?? "border-neutral-700 text-neutral-400"
                      }`}
                    >
                      {status}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {lastChecked && (
          <p className="mt-2 text-xs text-neutral-600">Last checked {lastChecked.toLocaleTimeString()}</p>
        )}

        <div className="mt-8 rounded border border-neutral-800 bg-neutral-950 px-4 py-3 text-xs text-neutral-500">
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
