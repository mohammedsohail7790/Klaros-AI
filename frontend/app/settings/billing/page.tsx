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
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem] mb-3">Billing</h1>
        <p className="mb-6 max-w-2xl text-sm text-muted">
          Klaros's own subscription — separate from any Stripe key you connect under Settings →
          Integrations, which only ever collects payments from your own customers.
        </p>

        {error && (
          <div className="mb-4 rounded-xl border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}

        {authLoading || loading || !status ? (
          <Skeleton />
        ) : status.enforced === false ? (
          <section aria-label="Included" className="rounded-3xl bg-surface-muted p-7 sm:p-9">
            <h2 className="font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Included, nothing to pay</h2>
            <p className="mt-2 max-w-2xl text-[15px] leading-snug text-muted">
              This Klaros is run for your own businesses, so there is no trial, no subscription and no limit on AI work. Nothing here needs your attention.
            </p>
          </section>
        ) : (
          <>
            <section aria-label="Current plan" className="klaros-dark mb-12 rounded-3xl p-7 sm:p-9">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-[13px] font-medium text-muted">Your plan</p>
                  <h2 className="mt-1 flex flex-wrap items-center gap-3 font-display text-[2.25rem] font-semibold capitalize leading-none tracking-[-0.03em]">
                    {status.plan}
                    {isTrialing && <span className="rounded-full bg-warning/15 px-3 py-1 text-xs font-medium normal-case tracking-normal text-warning">Trial</span>}
                    {status.billing_status === "active" && <span className="rounded-full bg-success/15 px-3 py-1 text-xs font-medium normal-case tracking-normal text-success">Active</span>}
                    {status.billing_status === "past_due" && <span className="rounded-full bg-danger/15 px-3 py-1 text-xs font-medium normal-case tracking-normal text-danger">Past due</span>}
                    {status.billing_status === "canceled" && <span className="rounded-full bg-danger/15 px-3 py-1 text-xs font-medium normal-case tracking-normal text-danger">Canceled</span>}
                  </h2>
                  {isTrialing && (
                    <p className="mt-3 text-[15px] leading-snug text-muted">
                      {trialDaysLeft !== null && trialDaysLeft > 0
                        ? `${trialDaysLeft} day(s) left in your free trial (ends ${formatDate(status.trial_ends_at)}).`
                        : "Your free trial has ended. Subscribe below to keep using AI recommendations."}
                    </p>
                  )}
                  {status.current_period_end && status.billing_status === "active" && (
                    <p className="mt-3 text-[15px] text-muted">Renews {formatDate(status.current_period_end)}.</p>
                  )}
                </div>
                <button onClick={handleManageBilling} disabled={portalBusy} className="klaros-btn-primary disabled:opacity-50">
                  {portalBusy ? "Opening..." : "Manage billing"}
                </button>
              </div>

              <div className="mt-8">
                <div className="flex items-baseline justify-between text-[14px]">
                  <span className="text-muted">AI recommendations this month</span>
                  <span className="font-medium text-foreground">
                    {status.ai_usage_this_month}
                    {status.ai_usage_limit !== null ? ` / ${status.ai_usage_limit}` : " (unlimited)"}
                  </span>
                </div>
                {status.ai_usage_limit !== null && status.ai_usage_limit > 0 && (
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-border" aria-hidden="true">
                    <div className="h-full rounded-full bg-accent transition-[width] duration-700 ease-apple" style={{ width: `${Math.min(100, (status.ai_usage_this_month / status.ai_usage_limit) * 100)}%` }} />
                  </div>
                )}
              </div>
            </section>

            <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Change plan</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {PLANS.map((plan) => {
                const current = status.plan === plan.id && status.billing_status === "active";
                return (
                  <div key={plan.id} className={`relative flex flex-col rounded-3xl p-7 transition-all duration-500 ease-apple hover:-translate-y-0.5 ${current ? "bg-accent-soft ring-2 ring-accent" : "bg-surface-muted"}`}>
                    {plan.id === "growth" && <span className="absolute right-5 top-5 rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-foreground">Most popular</span>}
                    <h3 className="font-display text-2xl font-semibold tracking-tight text-foreground">{plan.name}</h3>
                    <p className="mt-1 font-display text-[2.25rem] font-semibold leading-none tracking-[-0.03em] text-foreground">{plan.price}</p>
                    <p className="mt-3 flex-1 text-[15px] leading-snug text-muted">{plan.blurb}</p>
                    {current ? (
                      <p className="mt-5 text-[15px] font-medium text-success">Current plan</p>
                    ) : (
                      <button onClick={() => handleSubscribe(plan.id)} disabled={busyPlan !== null} className="klaros-btn-secondary mt-5 w-full !py-2.5 disabled:opacity-50">
                        {busyPlan === plan.id ? "Redirecting..." : `Subscribe to ${plan.name}`}
                      </button>
                    )}
                  </div>
                );
              })}
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
