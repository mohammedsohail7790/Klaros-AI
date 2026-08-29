"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  MorningBriefData,
  MorningBriefSettings,
  dismissRecommendation,
  executeRecommendation,
  generateMorningBrief,
  getLatestMorningBrief,
  getMorningBriefSettings,
  updateMorningBriefSettings,
} from "@/lib/api";

const PRIORITY_COLOR: Record<string, string> = {
  HIGH: "border-red-800 text-red-300",
  MEDIUM: "border-amber-800 text-amber-300",
  LOW: "border-neutral-700 text-neutral-400",
};

const ENTITY_LINK: Record<string, (id: string) => string> = {
  customer: (id) => `/customers/${id}`,
  lead: (id) => `/leads/${id}`,
  job: (id) => `/jobs/${id}`,
  invoice: (id) => `/finance/invoices/${id}`,
};

export default function MorningBriefPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [brief, setBrief] = useState<MorningBriefData | null>(null);
  const [settings, setSettings] = useState<MorningBriefSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    setNotice(null);
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
    setNotice(null);
    try {
      const result = await executeRecommendation(token, id);
      setNotice(
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
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Morning Brief</h1>
          <button
            disabled={busy}
            onClick={handleGenerate}
            className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
          >
            Generate now
          </button>
        </div>

        {notice && <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">{notice}</div>}

        {authLoading || loading ? (
          <p className="text-sm text-neutral-500">Loading...</p>
        ) : error ? (
          <div className="rounded-md border border-red-900 bg-red-950/30 p-4 text-sm text-red-300">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">Retry</button>
          </div>
        ) : (
          <>
            {brief && brief.brief_id ? (
              <>
                <div className="mb-6 rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                      {brief.mode === "DETERMINISTIC"
                        ? "DETERMINISTIC SUMMARY — AI NOT CONNECTED"
                        : `AI — ${brief.ai_provider ?? "unknown"}${brief.ai_model ? ` (${brief.ai_model})` : ""}`}
                    </span>
                    <span className="text-xs text-neutral-500">
                      {brief.generated_at ? new Date(brief.generated_at).toLocaleString() : ""} · generated by {brief.generated_by}
                    </span>
                  </div>
                  <p className="text-lg">{brief.headline}</p>
                </div>

                <h2 className="mb-3 text-sm font-medium text-neutral-300">Needs attention</h2>
                {brief.insights.length === 0 ? (
                  <p className="mb-6 text-sm text-neutral-500">No significant activity.</p>
                ) : (
                  <div className="mb-8 space-y-2">
                    {brief.insights.map((i) => (
                      <div
                        key={i.insight_id}
                        className={`rounded-lg border p-3 text-sm ${PRIORITY_COLOR[i.priority] ?? "border-neutral-700"}`}
                      >
                        <div className="flex items-center gap-2">
                          <span className="rounded-full border border-current px-2 py-0.5 text-[10px]">{i.category}</span>
                          <span className="rounded-full border border-current px-2 py-0.5 text-[10px]">{i.priority}</span>
                        </div>
                        <p className="mt-1 text-neutral-200">{i.summary}</p>
                        {i.related_entity_type && i.related_entity_id && ENTITY_LINK[i.related_entity_type] && (
                          <Link href={ENTITY_LINK[i.related_entity_type](i.related_entity_id)} className="mt-1 inline-block text-xs underline">
                            View {i.related_entity_type}
                          </Link>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <h2 className="mb-3 text-sm font-medium text-neutral-300">Recommended actions</h2>
                {brief.recommendations.length === 0 ? (
                  <p className="text-sm text-neutral-500">No recommendations.</p>
                ) : (
                  <div className="space-y-3">
                    {brief.recommendations.map((r) => (
                      <div key={r.recommendation_id} className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
                        <p className="font-medium">{r.what}</p>
                        <p className="mt-1 text-sm text-neutral-400">Why: {r.why}</p>
                        <p className="mt-1 text-sm text-neutral-500">Next action: {r.next_action}</p>
                        <div className="mt-3 flex items-center gap-3">
                          <span
                            className={`rounded-full border px-2 py-0.5 text-xs ${
                              r.status === "APPROVAL_REQUESTED" ? "border-amber-800 text-amber-300" : "border-neutral-700"
                            }`}
                          >
                            {r.status}
                          </span>
                          {r.related_entity_type && r.related_entity_id && ENTITY_LINK[r.related_entity_type] && (
                            <Link href={ENTITY_LINK[r.related_entity_type](r.related_entity_id)} className="text-xs underline">
                              View {r.related_entity_type}
                            </Link>
                          )}
                          {r.status === "APPROVAL_REQUESTED" && r.approval_request_id && (
                            <Link href="/approvals" className="text-xs underline text-amber-400 hover:text-white">
                              Review in Approvals
                            </Link>
                          )}
                          {r.status === "PENDING" && r.executable && (
                            <button
                              disabled={busy}
                              onClick={() => handleExecute(r.recommendation_id)}
                              className="text-xs underline text-emerald-400 hover:text-white"
                            >
                              Execute
                            </button>
                          )}
                          {r.status === "PENDING" && (
                            <button
                              disabled={busy}
                              onClick={() => handleDismiss(r.recommendation_id)}
                              className="text-xs underline text-red-400 hover:text-white"
                            >
                              Dismiss
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="mb-8 text-sm text-neutral-500">No brief generated yet.</p>
            )}

            {settings && (
              <div className="mt-10 rounded-lg border border-neutral-800 bg-neutral-950 p-6">
                <h2 className="mb-3 text-sm font-medium text-neutral-300">Schedule</h2>
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
                    <label className="block text-xs text-neutral-500">Local time</label>
                    <input
                      type="time"
                      value={settings.local_time}
                      onChange={(e) => handleSettingsSave({ ...settings, local_time: e.target.value })}
                      className="rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-neutral-500">Timezone (IANA)</label>
                    <input
                      value={settings.timezone}
                      onChange={(e) => setSettings({ ...settings, timezone: e.target.value })}
                      onBlur={(e) => handleSettingsSave({ ...settings, timezone: e.target.value })}
                      className="w-48 rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1"
                    />
                  </div>
                </div>
                <p className="mt-3 text-xs text-neutral-600">
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
