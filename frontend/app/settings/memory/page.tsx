"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  CompanyMemoryContextEntry,
  CompanyMemoryRow,
  confirmMemory,
  createCompanyMemory,
  getCompanyMemoryContext,
  getCompanyMemoryHistory,
  listCompanyMemories,
  rejectMemory,
  revokeMemory,
} from "@/lib/api";

const MEMORY_TYPES = [
  "OWNER_PREFERENCE", "BUSINESS_RULE", "OPERATIONAL_PREFERENCE", "AI_FEEDBACK", "COMPANY_CONTEXT", "TEMPORAL_CONTEXT",
] as const;

const STATUS_FILTERS = ["ALL", "ACTIVE", "PENDING", "ARCHIVED", "REVOKED", "REJECTED"] as const;

const STATUS_COLOR: Record<string, string> = {
  ACTIVE: "border-emerald-200 text-emerald-700",
  PENDING: "border-amber-200 text-amber-700",
  ARCHIVED: "border-border-strong text-muted",
  REVOKED: "border-red-200 text-red-600",
  REJECTED: "border-red-200 text-red-600",
};

const SOURCE_LABEL: Record<string, string> = {
  OWNER_EXPLICIT: "Owner (explicit)",
  OWNER_CORRECTION: "Owner (correction)",
  OWNER_APPROVAL: "Owner (approval)",
  SYSTEM_DERIVED: "System-derived",
  AI_PROPOSED: "AI-proposed",
};

export default function CompanyMemoryPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [memories, setMemories] = useState<CompanyMemoryRow[] | null>(null);
  const [context, setContext] = useState<CompanyMemoryContextEntry[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>("ALL");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<CompanyMemoryRow | null>(null);
  const [history, setHistory] = useState<CompanyMemoryRow[] | null>(null);
  const [aiFeedbackPending, setAiFeedbackPending] = useState<CompanyMemoryRow[] | null>(null);

  const [form, setForm] = useState({
    memory_type: "OWNER_PREFERENCE" as (typeof MEMORY_TYPES)[number],
    key: "",
    value: "",
    description: "",
  });

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [memResult, ctxResult, aiFeedbackResult] = await Promise.all([
        listCompanyMemories(token, statusFilter === "ALL" ? undefined : { status_filter: statusFilter }),
        getCompanyMemoryContext(token),
        // Phase 21: a dedicated "AI Feedback awaiting review" count/banner,
        // independent of the status filter buttons above — reuses the
        // same existing listCompanyMemories() API, no new endpoint.
        listCompanyMemories(token, { status_filter: "PENDING", memory_type: "AI_FEEDBACK" }),
      ]);
      setMemories(memResult.memories);
      setContext(ctxResult.context);
      setAiFeedbackPending(aiFeedbackResult.memories);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load company memory.");
    } finally {
      setLoading(false);
    }
  }, [token, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate() {
    if (!token || !form.key || !form.value) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await createCompanyMemory(token, {
        memory_type: form.memory_type, key: form.key, value: form.value, description: form.description || null,
        source: "OWNER_EXPLICIT",
      });
      setNotice("Preference saved.");
      setForm({ memory_type: "OWNER_PREFERENCE", key: "", value: "", description: "" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save preference.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(id: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await confirmMemory(token, id);
      setNotice("Confirmed — now active and part of AI context.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to confirm.");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject(id: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await rejectMemory(token, id);
      setNotice("Rejected — this candidate will never become memory.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reject.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(id: string) {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await revokeMemory(token, id);
      setNotice("Revoked — no longer part of AI context.");
      if (selected?.id === id) setSelected(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to revoke.");
    } finally {
      setBusy(false);
    }
  }

  async function openHistory(memory: CompanyMemoryRow) {
    if (!token) return;
    setSelected(memory);
    setHistory(null);
    try {
      const result = await getCompanyMemoryHistory(token, memory.key);
      setHistory(result.history);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load history.");
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <h1 className="mb-2 text-xl font-semibold">Company Memory</h1>
        <p className="mb-6 text-xs text-muted">
          Durable owner preferences, business rules, and context that Klaros reads before generating AI
          recommendations (e.g. the Morning Brief). The owner is always the authority — AI can only ever
          propose a candidate here, awaiting your confirm or reject; it can never silently become policy.
          Revoking is the only way to remove an active preference — the full history stays auditable.
        </p>

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50/30 p-3 text-sm text-emerald-700">
            {notice}
          </div>
        )}
        {error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50/30 p-3 text-sm text-red-700">{error}</div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="space-y-6">
            <div className="rounded-lg border border-border bg-surface p-5">
              <h2 className="mb-3 text-sm font-medium">Add an explicit preference</h2>
              <div className="space-y-2">
                <select
                  value={form.memory_type}
                  onChange={(e) => setForm((f) => ({ ...f, memory_type: e.target.value as typeof form.memory_type }))}
                  className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                >
                  {MEMORY_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <input
                  value={form.key}
                  onChange={(e) => setForm((f) => ({ ...f, key: e.target.value }))}
                  placeholder="preferred_appointment_time"
                  className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                />
                <input
                  value={form.value}
                  onChange={(e) => setForm((f) => ({ ...f, value: e.target.value }))}
                  placeholder="morning"
                  className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                />
                <input
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="Description (optional)"
                  className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                />
                <button
                  onClick={handleCreate}
                  disabled={busy || !form.key || !form.value}
                  className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm hover:bg-surface-muted disabled:opacity-50"
                >
                  Save preference
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-border bg-surface p-5">
              <h2 className="mb-3 text-sm font-medium">Active AI context ({context?.length ?? 0})</h2>
              {!context || context.length === 0 ? (
                <p className="text-xs text-muted">No active memory yet — nothing is fed into AI recommendations.</p>
              ) : (
                <div className="space-y-2">
                  {context.map((c) => (
                    <div key={c.key} className="rounded-md border border-border p-2 text-xs">
                      <span className="text-muted">[{c.memory_type}]</span> <span className="font-medium">{c.key}</span>: {c.value}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div>
            {aiFeedbackPending && aiFeedbackPending.length > 0 && (
              <button
                onClick={() => setStatusFilter("PENDING")}
                className="mb-3 block w-full rounded-lg border border-violet-900 bg-violet-950/20 p-3 text-left text-sm text-violet-300 hover:bg-violet-950/40"
              >
                AI feedback awaiting review: {aiFeedbackPending.length} — Klaros learned something from{" "}
                {aiFeedbackPending.length === 1 ? "a decision you made" : "decisions you made"}. Review below to
                confirm or discard.
              </button>
            )}
            <div className="mb-3 flex flex-wrap gap-2">
              {STATUS_FILTERS.map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`rounded-md border px-3 py-1.5 text-xs ${
                    statusFilter === s ? "border-border-strong bg-surface-muted" : "border-border-strong hover:bg-surface-muted"
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>

            {authLoading || loading ? (
              <p className="text-sm text-muted">Loading...</p>
            ) : !memories || memories.length === 0 ? (
              <p className="text-sm text-muted">No memory entries.</p>
            ) : (
              <div className="space-y-2">
                {memories.map((m) => (
                  <div
                    key={m.id}
                    className={`rounded-lg border p-3 text-sm ${selected?.id === m.id ? "border-border-strong" : "border-border"}`}
                  >
                    <button onClick={() => openHistory(m)} className="block w-full text-left">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-2 font-medium">
                          {m.key}
                          {m.memory_type === "AI_FEEDBACK" && (
                            <span className="rounded-full border border-violet-800 bg-violet-950/30 px-2 py-0.5 text-[10px] font-normal text-violet-300">
                              Learned from an AI decision
                            </span>
                          )}
                        </span>
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] ${STATUS_COLOR[m.status] ?? "border-border-strong"}`}>
                          {m.status}
                        </span>
                      </div>
                      <p className="mt-1 text-muted">{m.value}</p>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {m.memory_type} · {SOURCE_LABEL[m.source] ?? m.source}
                        {m.confidence != null && ` · confidence ${(m.confidence * 100).toFixed(0)}%`}
                      </p>
                      {m.description && <p className="mt-1 text-xs text-muted">{m.description}</p>}
                    </button>
                    {m.memory_type === "AI_FEEDBACK" && m.source_entity_type === "approval_request" && m.source_entity_id && (
                      <a
                        href={`/approvals?id=${m.source_entity_id}`}
                        className="mt-1 inline-block text-[11px] text-muted underline hover:text-muted"
                      >
                        View the approval this came from →
                      </a>
                    )}
                    <div className="mt-2 flex gap-3">
                      {m.status === "PENDING" && (
                        <>
                          <button onClick={() => handleConfirm(m.id)} disabled={busy} className="text-xs text-emerald-600 underline hover:text-foreground">
                            {m.memory_type === "AI_FEEDBACK" ? "Confirm — apply to future AI decisions" : "Confirm"}
                          </button>
                          <button onClick={() => handleReject(m.id)} disabled={busy} className="text-xs text-red-600 underline hover:text-foreground">
                            {m.memory_type === "AI_FEEDBACK" ? "Discard" : "Reject"}
                          </button>
                        </>
                      )}
                      {m.status === "ACTIVE" && (
                        <button onClick={() => handleRevoke(m.id)} disabled={busy} className="text-xs text-red-600 underline hover:text-foreground">
                          Revoke
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {selected && history && (
              <div className="mt-4 rounded-lg border border-border bg-surface p-4">
                <h3 className="mb-2 text-sm font-medium">History: {selected.key}</h3>
                <div className="space-y-2">
                  {history.map((h) => (
                    <div key={h.id} className="rounded-md border border-border p-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span>{h.value}</span>
                        <span className={`rounded-full border px-2 py-0.5 text-[10px] ${STATUS_COLOR[h.status] ?? "border-border-strong"}`}>
                          {h.status}
                        </span>
                      </div>
                      <span className="text-muted-foreground">{new Date(h.created_at).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
