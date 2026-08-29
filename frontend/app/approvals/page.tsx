"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  ApprovalDetail,
  ApprovalRow,
  approveApproval,
  getApprovalDetail,
  listApprovals,
  rejectApproval,
  retryApprovalExecution,
} from "@/lib/api";

const STATUS_FILTERS = ["ALL", "PENDING", "APPROVED", "REJECTED"] as const;

const STATUS_COLOR: Record<string, string> = {
  PENDING: "border-amber-800 text-amber-300",
  APPROVED: "border-emerald-800 text-emerald-300",
  REJECTED: "border-red-800 text-red-300",
};

const EXECUTION_COLOR: Record<string, string> = {
  NOT_STARTED: "border-neutral-700 text-neutral-400",
  EXECUTING: "border-blue-800 text-blue-300",
  EXECUTED: "border-emerald-800 text-emerald-300",
  FAILED: "border-red-800 text-red-300",
};

export default function ApprovalsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [filter, setFilter] = useState<(typeof STATUS_FILTERS)[number]>("PENDING");
  const [approvals, setApprovals] = useState<ApprovalRow[] | null>(null);
  const [selected, setSelected] = useState<ApprovalDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listApprovals(token, filter === "ALL" ? undefined : filter);
      setApprovals(result.approvals);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load approvals.");
    } finally {
      setLoading(false);
    }
  }, [token, filter]);

  useEffect(() => {
    load();
  }, [load]);

  async function openDetail(id: string) {
    if (!token) return;
    setError(null);
    try {
      const detail = await getApprovalDetail(token, id);
      setSelected(detail);
      setNote("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load approval detail.");
    }
  }

  async function refreshSelected() {
    if (!token || !selected) return;
    try {
      const detail = await getApprovalDetail(token, selected.approval_request_id);
      setSelected(detail);
    } catch {
      // If it's gone/inaccessible, just close the panel — the list reload below still runs.
      setSelected(null);
    }
  }

  async function handleApprove() {
    if (!token || !selected) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await approveApproval(token, selected.approval_request_id, note || undefined);
      setNotice(
        result.execution_status === "EXECUTED"
          ? "Approved — the original action executed automatically."
          : result.execution_status === "FAILED"
          ? "Approved, but execution failed — see the error below. You can retry."
          : "Approved."
      );
      await refreshSelected();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to approve.");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    if (!token || !selected) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await rejectApproval(token, selected.approval_request_id, note || undefined);
      setNotice("Rejected.");
      await refreshSelected();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reject.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRetry() {
    if (!token || !selected) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await retryApprovalExecution(token, selected.approval_request_id);
      setNotice(result.execution_status === "EXECUTED" ? "Retried — executed successfully." : "Retried.");
      await refreshSelected();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Approvals</h1>
          <div className="flex gap-2">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded-md border px-3 py-1.5 text-xs ${
                  filter === f ? "border-neutral-400 bg-neutral-800" : "border-neutral-700 hover:bg-neutral-900"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <p className="mb-6 text-xs text-neutral-500">
          Every human-required action funnels through here — approving one resumes and executes the
          original action through the same Tool Registry pipeline every other call goes through. Nobody,
          including the AI, can approve their own request.
        </p>

        {notice && (
          <div className="mb-4 rounded-md border border-emerald-900 bg-emerald-950/30 p-3 text-sm text-emerald-300">
            {notice}
          </div>
        )}
        {error && (
          <div className="mb-4 rounded-md border border-red-900 bg-red-950/30 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div>
            {authLoading || loading ? (
              <p className="text-sm text-neutral-500">Loading...</p>
            ) : !approvals || approvals.length === 0 ? (
              <p className="text-sm text-neutral-500">No {filter === "ALL" ? "" : filter.toLowerCase()} approvals.</p>
            ) : (
              <div className="space-y-2">
                {approvals.map((a) => (
                  <button
                    key={a.id}
                    onClick={() => openDetail(a.id)}
                    className={`block w-full rounded-lg border p-3 text-left text-sm hover:bg-neutral-900 ${
                      selected?.approval_request_id === a.id ? "border-neutral-400" : "border-neutral-800"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{a.tool_name}</span>
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${STATUS_COLOR[a.status] ?? "border-neutral-700"}`}>
                        {a.status}
                      </span>
                    </div>
                    <p className="mt-1 text-neutral-400">{a.reason}</p>
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-neutral-500">
                      <span className={`rounded-full border px-2 py-0.5 ${EXECUTION_COLOR[a.execution_status] ?? "border-neutral-700"}`}>
                        {a.execution_status}
                      </span>
                      <span>{new Date(a.created_at).toLocaleString()}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            {!selected ? (
              <p className="text-sm text-neutral-500">Select an approval to see its detail.</p>
            ) : (
              <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-5">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="font-medium">{selected.tool_name}</h2>
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] ${STATUS_COLOR[selected.status] ?? "border-neutral-700"}`}>
                    {selected.status}
                  </span>
                </div>
                <p className="text-sm text-neutral-300">{selected.reason}</p>

                <dl className="mt-4 grid grid-cols-2 gap-y-2 text-xs text-neutral-500">
                  <dt>Requested by</dt>
                  <dd className="text-neutral-300">{selected.requested_by_type}</dd>
                  <dt>Created</dt>
                  <dd className="text-neutral-300">{new Date(selected.created_at).toLocaleString()}</dd>
                  {selected.decided_at && (
                    <>
                      <dt>Decided</dt>
                      <dd className="text-neutral-300">{new Date(selected.decided_at).toLocaleString()}</dd>
                    </>
                  )}
                  <dt>Execution</dt>
                  <dd>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] ${EXECUTION_COLOR[selected.execution_status] ?? "border-neutral-700"}`}>
                      {selected.execution_status}
                    </span>
                  </dd>
                  {selected.execution_attempts > 0 && (
                    <>
                      <dt>Attempts</dt>
                      <dd className="text-neutral-300">{selected.execution_attempts}</dd>
                    </>
                  )}
                </dl>

                <div className="mt-4">
                  <p className="mb-1 text-xs text-neutral-500">Input</p>
                  <pre className="max-h-40 overflow-auto rounded-md border border-neutral-800 bg-black p-2 text-[11px] text-neutral-400">
                    {JSON.stringify(selected.tool_input, null, 2)}
                  </pre>
                </div>

                {selected.execution_result && (
                  <div className="mt-4">
                    <p className="mb-1 text-xs text-neutral-500">Execution result</p>
                    <pre className="max-h-40 overflow-auto rounded-md border border-emerald-900 bg-emerald-950/20 p-2 text-[11px] text-emerald-300">
                      {JSON.stringify(selected.execution_result, null, 2)}
                    </pre>
                  </div>
                )}

                {selected.execution_error && (
                  <div className="mt-4 rounded-md border border-red-900 bg-red-950/30 p-3 text-xs text-red-300">
                    {selected.execution_error}
                  </div>
                )}

                {selected.status === "PENDING" && (
                  <div className="mt-5">
                    <input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="Decision note (optional)"
                      className="mb-2 w-full rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
                    />
                    <div className="flex gap-2">
                      <button
                        disabled={busy}
                        onClick={handleApprove}
                        className="rounded-md border border-emerald-800 bg-emerald-950/30 px-3 py-1.5 text-sm text-emerald-300 hover:bg-emerald-950/60 disabled:opacity-50"
                      >
                        Approve
                      </button>
                      <button
                        disabled={busy}
                        onClick={handleReject}
                        className="rounded-md border border-red-800 bg-red-950/30 px-3 py-1.5 text-sm text-red-300 hover:bg-red-950/60 disabled:opacity-50"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                )}

                {selected.status === "APPROVED" && selected.execution_status === "FAILED" && (
                  <div className="mt-5">
                    <button
                      disabled={busy}
                      onClick={handleRetry}
                      className="rounded-md border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-900 disabled:opacity-50"
                    >
                      Retry execution
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
