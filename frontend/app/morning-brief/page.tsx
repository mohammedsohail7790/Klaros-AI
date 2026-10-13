"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Lightbulb, Sunrise } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  MorningBriefData,
  MorningBriefSettings,
  createCompanyMemory,
  dismissRecommendation,
  executeRecommendation,
  generateMorningBrief,
  getLatestMorningBrief,
  getMorningBriefSettings,
  updateMorningBriefSettings,
} from "@/lib/api";

const PRIORITY_COLOR: Record<string, string> = {
  HIGH: "border-danger/25 text-danger",
  MEDIUM: "border-warning/25 text-warning",
  LOW: "border-border-strong text-muted",
};

const ENTITY_LINK: Record<string, (id: string) => string> = {
  customer: (id) => `/customers/${id}`,
  lead: (id) => `/leads/${id}`,
  job: (id) => `/jobs/${id}`,
  invoice: (id) => `/finance/invoices/${id}`,
  contract: (id) => `/contracts/${id}`,
  quote: (id) => `/quotes/${id}`,
};

export default function MorningBriefPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [brief, setBrief] = useState<MorningBriefData | null>(null);
  const [settings, setSettings] = useState<MorningBriefSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rememberingId, setRememberingId] = useState<string | null>(null);
  const [rememberText, setRememberText] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [briefResult, settingsResult] = await Promise.all([
        getLatestMorningBrief(token),
        getMorningBriefSettings(token),
      ]);
      setBrief(briefResult);
      setSettings(settingsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load Morning Brief.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate() {
    if (!token) return;
    setBusy(true);
    try {
      await generateMorningBrief(token);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to generate brief.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExecute(id: string) {
    if (!token) return;
    setBusy(true);
    try {
      const result = await executeRecommendation(token, id);
      toast.success(
        result.status === "APPROVAL_REQUESTED"
          ? "This action requires approval — a real approval request was created. Review it on the Approvals page."
          : "Recommendation executed through the normal Tool Registry pipeline."
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to execute recommendation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDismiss(id: string) {
    if (!token) return;
    setBusy(true);
    try {
      await dismissRecommendation(token, id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to dismiss recommendation.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemember(recommendationId: string) {
    if (!token || !rememberText.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const key = `feedback_${recommendationId.replace(/-/g, "").slice(0, 16)}`;
      await createCompanyMemory(token, {
        memory_type: "AI_FEEDBACK",
        key,
        value: rememberText.trim(),
        source: "OWNER_CORRECTION",
        reason: `Owner correction on morning brief recommendation ${recommendationId}`,
      });
      toast.success("Saved to Company Memory — future AI recommendations will see this.");
      setRememberingId(null);
      setRememberText("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save to Company Memory.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSettingsSave(next: MorningBriefSettings) {
    if (!token) return;
    setBusy(true);
    try {
      const result = await updateMorningBriefSettings(token, next);
      setSettings(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save settings.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <div className="mb-10 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-[2rem] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-[2.75rem]">Morning Brief</h1>
            <p className="mt-2 text-[15px] text-muted sm:text-[17px]">What happened, what needs you, and what to do next.</p>
          </div>
          <button
            disabled={busy}
            onClick={handleGenerate}
            className="klaros-btn-primary disabled:opacity-50"
          >
            Generate now
          </button>
        </div>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="rounded-xl border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {brief && brief.brief_id ? (
              <>
                <div className="klaros-dark mb-12 rounded-3xl p-7 sm:p-10">
                  <div className="mb-5 flex flex-wrap items-center gap-3">
                    <span className="rounded-full bg-surface px-3 py-1 text-xs font-medium text-muted">
                      {brief.mode === "DETERMINISTIC"
                        ? "Standard summary — AI not connected"
                        : `AI — ${brief.ai_provider ?? "unknown"}${brief.ai_model ? ` (${brief.ai_model})` : ""}`}
                    </span>
                    <span className="text-[13px] text-muted">
                      {brief.generated_at ? new Date(brief.generated_at).toLocaleString() : ""} · generated by {brief.generated_by}
                    </span>
                  </div>
                  <p className="text-balance font-display text-2xl font-semibold leading-tight tracking-[-0.02em] sm:text-[2.1rem]">{brief.headline}</p>
                </div>

                <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Needs attention</h2>
                {brief.insights.length === 0 ? (
                  <p className="mb-12 rounded-3xl bg-surface-muted px-6 py-6 text-[15px] text-muted">No significant activity.</p>
                ) : (
                  <div className="mb-12 space-y-3">
                    {brief.insights.map((i) => (
                      <div
                        key={i.insight_id}
                        className="rounded-3xl bg-surface-muted p-6"
                      >
                        <div className="flex items-center gap-2">
                          <span className="rounded-full bg-surface px-3 py-1 text-xs font-medium text-muted">{i.category}</span>
                          <span className={`rounded-full px-3 py-1 text-xs font-medium ${i.priority === "CRITICAL" || i.priority === "HIGH" ? "bg-danger/10 text-danger" : i.priority === "MEDIUM" ? "bg-warning/10 text-warning" : "bg-surface text-muted"}`}>{i.priority}</span>
                        </div>
                        <p className="mt-3 text-[17px] leading-snug text-foreground">{i.summary}</p>
                        {i.related_entity_type && i.related_entity_id && ENTITY_LINK[i.related_entity_type] && (
                          <Link href={ENTITY_LINK[i.related_entity_type](i.related_entity_id)} className="mt-1 inline-block text-xs underline">
                            View {i.related_entity_type}
                          </Link>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <h2 className="mb-4 font-display text-[1.5rem] font-semibold tracking-[-0.02em] text-foreground">Recommended actions</h2>
                {brief.recommendations.length === 0 ? (
                  <EmptyState icon={Lightbulb} title="No recommendations." compact />
                ) : (
                  <div className="space-y-3">
                    {brief.recommendations.map((r) => (
                      <div key={r.recommendation_id} className="rounded-3xl bg-surface-muted p-6">
                        <p className="text-[17px] font-semibold tracking-tight text-foreground">{r.what}</p>
                        <p className="mt-2 text-[15px] leading-snug text-muted">Why: {r.why}</p>
                        <p className="mt-1 text-[15px] leading-snug text-muted">Next action: {r.next_action}</p>
                        <div className="mt-5 flex flex-wrap items-center gap-2">
                          <span
                            className={`rounded-full border px-2 py-0.5 text-xs ${
                              r.status === "APPROVAL_REQUESTED" ? "border-warning/25 text-warning" : "border-border-strong"
                            }`}
                          >
                            {r.status}
                          </span>
                          {r.related_entity_type && r.related_entity_id && ENTITY_LINK[r.related_entity_type] && (
                            <Link href={ENTITY_LINK[r.related_entity_type](r.related_entity_id)} className="rounded-full bg-surface px-4 py-1.5 text-[13px] font-medium text-accent hover:bg-border">
                              View {r.related_entity_type}
                            </Link>
                          )}
                          {r.status === "APPROVAL_REQUESTED" && r.approval_request_id && (
                            <Link href="/approvals" className="rounded-full bg-warning/10 px-4 py-1.5 text-[13px] font-medium text-warning hover:bg-warning/20">
                              Review in Approvals
                            </Link>
                          )}
                          {r.status === "PENDING" && r.executable && (
                            <button
                              disabled={busy}
                              onClick={() => handleExecute(r.recommendation_id)}
                              className="rounded-full bg-success/10 px-4 py-1.5 text-[13px] font-medium text-success hover:bg-success/20"
                            >
                              Execute
                            </button>
                          )}
                          {r.status === "PENDING" && (
                            <button
                              disabled={busy}
                              onClick={() => handleDismiss(r.recommendation_id)}
                              className="rounded-full bg-danger/10 px-4 py-1.5 text-[13px] font-medium text-danger hover:bg-danger/20"
                            >
                              Dismiss
                            </button>
                          )}
                          <button
                            disabled={busy}
                            onClick={() =>
                              setRememberingId(rememberingId === r.recommendation_id ? null : r.recommendation_id)
                            }
                            className="rounded-full bg-surface px-4 py-1.5 text-[13px] font-medium text-foreground hover:bg-border"
                          >
                            Remember this
                          </button>
                        </div>
                        {rememberingId === r.recommendation_id && (
                          <div className="mt-3 border-t border-border pt-3">
                            <label className="mb-1 block text-xs text-muted">
                              Why? This is saved to Company Memory and read by future AI recommendations.
                            </label>
                            <textarea
                              value={rememberText}
                              onChange={(e) => setRememberText(e.target.value)}
                              rows={2}
                              placeholder="e.g. This customer is strategic — always prioritize commercial accounts like this."
                              className="w-full rounded-xl border border-border-strong bg-background px-2 py-1.5 text-xs"
                            />
                            <button
                              disabled={busy || !rememberText.trim()}
                              onClick={() => handleRemember(r.recommendation_id)}
                              className="mt-2 rounded-xl border border-border-strong bg-surface-muted px-3 py-1.5 text-xs hover:bg-surface-muted disabled:opacity-50"
                            >
                              Save to memory
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="mb-8">
                <EmptyState icon={Sunrise} title="No brief generated yet — click &ldquo;Generate now&rdquo; above." />
              </div>
            )}

            {settings && (
              <div className="mt-10 rounded-3xl bg-surface-muted p-6">
                <h2 className="mb-3 font-display text-[1.35rem] font-semibold tracking-[-0.02em] text-foreground">Schedule</h2>
                <div className="flex flex-wrap items-end gap-3 text-sm">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={settings.enabled}
                      onChange={(e) => handleSettingsSave({ ...settings, enabled: e.target.checked })}
                    />
                    Auto-generate daily
                  </label>
                  <div>
                    <label className="block text-xs text-muted">Local time</label>
                    <input
                      type="time"
                      value={settings.local_time}
                      onChange={(e) => handleSettingsSave({ ...settings, local_time: e.target.value })}
                      className="rounded-xl border border-border-strong bg-surface-muted px-2 py-1"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-muted">Timezone (IANA)</label>
                    <input
                      value={settings.timezone}
                      onChange={(e) => setSettings({ ...settings, timezone: e.target.value })}
                      onBlur={(e) => handleSettingsSave({ ...settings, timezone: e.target.value })}
                      className="w-48 rounded-xl border border-border-strong bg-surface-muted px-2 py-1"
                    />
                  </div>
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  Generated automatically by the Event Worker's tick loop once enabled — no manual trigger needed.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  );
}
