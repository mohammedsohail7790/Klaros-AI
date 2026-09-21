"use client";

import { useCallback, useEffect, useState } from "react";
import { Layers, Sparkles } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
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
  updatePendingMemory,
} from "@/lib/api";

const MEMORY_TYPES = [
  "OWNER_PREFERENCE", "BUSINESS_RULE", "OPERATIONAL_PREFERENCE", "AI_FEEDBACK", "COMPANY_CONTEXT", "TEMPORAL_CONTEXT",
] as const;

const STATUS_FILTERS = ["ALL", "ACTIVE", "PENDING", "ARCHIVED", "REVOKED", "REJECTED"] as const;

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
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<CompanyMemoryRow | null>(null);
  const [history, setHistory] = useState<CompanyMemoryRow[] | null>(null);
  const [aiFeedbackPending, setAiFeedbackPending] = useState<CompanyMemoryRow[] | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

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
    try {
      await createCompanyMemory(token, {
        memory_type: form.memory_type, key: form.key, value: form.value, description: form.description || null,
        source: "OWNER_EXPLICIT",
      });
      toast.success("Preference saved.");
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
      toast.success("Confirmed — now active and part of AI context.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to confirm.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit(id: string) {
    if (!token || !editValue.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await updatePendingMemory(token, id, editValue.trim());
      toast.success("Updated.");
      setEditingId(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update.");
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
      toast.success("Rejected — this candidate will never become memory.");
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
      toast.success("Revoked — no longer part of AI context.");
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
        <h1 className="font-display text-2xl text-foreground mb-2">Company Memory</h1>
        <p className="mb-6 text-xs text-muted">
          Durable owner preferences, business rules, and context that Klaros reads before generating AI
          recommendations (e.g. the Morning Brief). The owner is always the authority — AI can only ever
          propose a candidate here, awaiting your confirm or reject; it can never silently become policy.
          Revoking is the only way to remove an active preference — the full history stays auditable.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
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
                <EmptyState icon={Sparkles} title="No active memory yet — nothing is fed into AI recommendations." compact />
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
                className="mb-3 block w-full rounded-lg border border-violet-200 bg-violet-50/20 p-3 text-left text-sm text-violet-700 hover:bg-violet-50/40"
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
              <Skeleton />
            ) : !memories || memories.length === 0 ? (
              <EmptyState icon={Layers} title="No memory entries." />
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
                            <span className="rounded-full border border-violet-200 bg-violet-50/30 px-2 py-0.5 text-[10px] font-normal text-violet-700">
                              Learned from an AI decision
                            </span>
                          )}
                        </span>
                        <Badge status={m.status} className="text-[10px]">
                          {m.status}
                        </Badge>
                      </div>
                      <p className="mt-1 text-muted">{m.value}</p>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {m.memory_type} · {SOURCE_LABEL[m.source] ?? m.source}
                        {m.confidence != null && ` · confidence ${(m.confidence * 100).toFixed(0)}%`}
                      </p>
                      {m.description && <p className="mt-1 text-xs text-muted">{m.description}</p>}
                    </button>
                    {editingId === m.id && (
                      <div className="mt-2 flex items-center gap-2">
                        <input
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          className="flex-1 rounded-md border border-border-strong bg-surface px-2 py-1 text-sm"
                        />
                        <button
                          onClick={() => handleSaveEdit(m.id)}
                          disabled={busy || !editValue.trim()}
                          className="text-xs text-success underline hover:text-foreground disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button
                          onClick={() => setEditingId(null)}
                          className="text-xs text-muted underline hover:text-foreground"
                        >
                          Cancel
                        </button>
                      </div>
                    )}
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
                          <button onClick={() => handleConfirm(m.id)} disabled={busy} className="text-xs text-success underline hover:text-foreground">
                            {m.memory_type === "AI_FEEDBACK" ? "Confirm — apply to future AI decisions" : "Confirm"}
                          </button>
                          <button
                            onClick={() => {
                              setEditingId(m.id);
                              setEditValue(m.value);
                            }}
                            disabled={busy}
                            className="text-xs text-muted underline hover:text-foreground"
                          >
                            Edit before confirming
                          </button>
                          <button onClick={() => handleReject(m.id)} disabled={busy} className="text-xs text-danger underline hover:text-foreground">
                            {m.memory_type === "AI_FEEDBACK" ? "Discard" : "Reject"}
                          </button>
                        </>
                      )}
                      {m.status === "ACTIVE" && (
                        <button onClick={() => handleRevoke(m.id)} disabled={busy} className="text-xs text-danger underline hover:text-foreground">
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
                        <Badge status={h.status} className="text-[10px]">
                          {h.status}
                        </Badge>
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
