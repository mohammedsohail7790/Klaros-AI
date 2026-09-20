"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  ApiError,
  BillingStatus,
  createBillingCheckout,
  createBillingPortalSession,
  getBillingStatus,
} from "@/lib/api";

const PLANS: { id: "solo" | "growth"; name: string; price: string; blurb: string }[] = [
  { id: "solo", name: "Solo", price: "$49/mo", blurb: "Up to 50 AI recommendations/mo, 1 user." },
  { id: "growth", name: "Growth", price: "$129/mo", blurb: "Unlimited AI recommendations, full automation engine." },
];

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function BillingSettingsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [portalBusy, setPortalBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setStatus(await getBillingStatus(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load billing status.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSubscribe(plan: "solo" | "growth") {
    if (!token) return;
    setBusyPlan(plan);
    setError(null);
    try {
      const origin = window.location.origin;
      const result = await createBillingCheckout(
        token,
        plan,
        `${origin}/settings/billing`,
        `${origin}/settings/billing`
      );
      window.location.href = result.checkout_url;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to start checkout.");
      setBusyPlan(null);
    }
  }

  async function handleManageBilling() {
    if (!token) return;
    setPortalBusy(true);
    setError(null);
    try {
      const result = await createBillingPortalSession(token, `${window.location.origin}/settings/billing`);
      window.location.href = result.portal_url;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to open billing management.");
      setPortalBusy(false);
    }
  }

  const isTrialing = status?.billing_status === "trialing";
  const trialDaysLeft =
    isTrialing && status?.trial_ends_at
      ? Math.max(0, Math.ceil((new Date(status.trial_ends_at).getTime() - Date.now()) / 86_400_000))
      : null;

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="font-display text-2xl text-foreground mb-2">Billing</h1>
        <p className="mb-6 max-w-2xl text-sm text-muted">
          Klaros's own subscription — separate from any Stripe key you connect under Settings →
          Integrations, which only ever collects payments from your own customers.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}

        {authLoading || loading || !status ? (
          <Skeleton />
        ) : (
          <>
            <div className="mb-6 rounded-lg border border-border bg-surface p-5">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="font-medium capitalize">
                    {status.plan} plan
                    {isTrialing && <span className="ml-2 text-xs text-warning">Trial</span>}
                    {status.billing_status === "active" && (
                      <span className="ml-2 text-xs text-success">Active</span>
                    )}
                    {status.billing_status === "past_due" && (
                      <span className="ml-2 text-xs text-danger">Past due</span>
                    )}
                    {status.billing_status === "canceled" && (
                      <span className="ml-2 text-xs text-danger">Canceled</span>
                    )}
                  </h2>
                  {isTrialing && (
                    <p className="mt-1 text-xs text-muted">
                      {trialDaysLeft !== null && trialDaysLeft > 0
                        ? `${trialDaysLeft} day(s) left in your free trial (ends ${formatDate(status.trial_ends_at)}).`
                        : "Your free trial has ended — subscribe below to keep using AI recommendations."}
                    </p>
                  )}
                  {status.current_period_end && status.billing_status === "active" && (
                    <p className="mt-1 text-xs text-muted">Renews {formatDate(status.current_period_end)}.</p>
                  )}
                </div>
                <button
                  onClick={handleManageBilling}
                  disabled={portalBusy}
                  className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  {portalBusy ? "Opening..." : "Manage billing"}
                </button>
              </div>

              <div className="mt-4 text-sm">
                <span className="text-muted">AI recommendations this month: </span>
                <span className="font-medium text-foreground">
                  {status.ai_usage_this_month}
                  {status.ai_usage_limit !== null ? ` / ${status.ai_usage_limit}` : " (unlimited)"}
                </span>
              </div>
            </div>

            <h2 className="mb-3 font-medium">Change plan</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {PLANS.map((plan) => (
                <div
                  key={plan.id}
                  className={`rounded-lg border p-4 ${
                    status.plan === plan.id && status.billing_status === "active"
                      ? "border-accent bg-accent-soft"
                      : "border-border bg-surface"
                  }`}
                >
                  <div className="flex items-baseline justify-between">
                    <h3 className="font-medium">{plan.name}</h3>
                    <span className="text-sm text-muted">{plan.price}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted">{plan.blurb}</p>
                  {status.plan === plan.id && status.billing_status === "active" ? (
                    <p className="mt-3 text-xs text-success">Current plan</p>
                  ) : (
                    <button
                      onClick={() => handleSubscribe(plan.id)}
                      disabled={busyPlan !== null}
                      className="mt-3 rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                    >
                      {busyPlan === plan.id ? "Redirecting..." : `Subscribe to ${plan.name}`}
                    </button>
                  )}
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-muted">
              Need multiple users or custom automation policies? Scale is contact-based —{" "}
              <a href="mailto:hello@meetklaros.com" className="underline">
                reach out
              </a>
              .
            </p>
          </>
        )}
      </div>
    </AppShell>
  );
}
