"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckSquare } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
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

export default function ApprovalsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [filter, setFilter] = useState<(typeof STATUS_FILTERS)[number]>("PENDING");
  const [approvals, setApprovals] = useState<ApprovalRow[] | null>(null);
  const [selected, setSelected] = useState<ApprovalDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  // Phase 21: deep-link support — Company Memory's "View the approval"
  // link (for a PENDING AI_FEEDBACK proposal's source approval) opens
  // this page as /approvals?id=<approval_request_id>. Read directly from
  // the URL (no useSearchParams/Suspense boundary needed) since this page
  // is already fully client-rendered behind useAuth.
  useEffect(() => {
    if (!token) return;
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) openDetail(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

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
    try {
      const result = await approveApproval(token, selected.approval_request_id, note || undefined);
      toast.success(
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
    try {
      await rejectApproval(token, selected.approval_request_id, note || undefined);
      toast.success("Rejected.");
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
    try {
      const result = await retryApprovalExecution(token, selected.approval_request_id);
      toast.success(result.execution_status === "EXECUTED" ? "Retried — executed successfully." : "Retried.");
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
          <h1 className="font-display text-2xl text-foreground">Approvals</h1>
          <div className="flex gap-2">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded-md border px-3 py-1.5 text-xs ${
                  filter === f ? "border-border-strong bg-surface-muted" : "border-border-strong hover:bg-surface-muted"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <p className="mb-6 text-xs text-muted">
          Every human-required action funnels through here — approving one resumes and executes the
          original action through the same Tool Registry pipeline every other call goes through. Nobody,
          including the AI, can approve their own request.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div>
            {authLoading || loading ? (
              <Skeleton />
            ) : !approvals || approvals.length === 0 ? (
              <EmptyState icon={CheckSquare} title={`No ${filter === "ALL" ? "" : filter.toLowerCase() + " "}approvals.`} />
            ) : (
              <div className="space-y-2">
                {approvals.map((a) => (
                  <button
                    key={a.id}
                    onClick={() => openDetail(a.id)}
                    className={`block w-full rounded-lg border p-3 text-left text-sm hover:bg-surface-muted ${
                      selected?.approval_request_id === a.id ? "border-border-strong" : "border-border"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-2 font-medium">
                        {a.tool_name}
                        {a.requested_by_type === "AI" && (
                          <span className="rounded-full border border-violet-200 bg-violet-50/30 px-2 py-0.5 text-[10px] font-normal text-violet-700">
                            AI-proposed
                          </span>
                        )}
                      </span>
                      <Badge status={a.status} className="text-[10px]">
                        {a.status}
                      </Badge>
                    </div>
                    <p className="mt-1 text-muted">{a.reason}</p>
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
                      <Badge status={a.execution_status}>
                        {a.execution_status}
                      </Badge>
                      <span>{new Date(a.created_at).toLocaleString()}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            {!selected ? (
              <p className="text-sm text-muted">Select an approval to see its detail.</p>
            ) : (
              <div className="rounded-lg border border-border bg-surface p-5">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="font-medium">{selected.tool_name}</h2>
                  <Badge status={selected.status} className="text-[10px]">
                    {selected.status}
                  </Badge>
                </div>
                {selected.requested_by_type === "AI" && (
                  <div className="mb-3 rounded-md border border-violet-200 bg-violet-50/20 p-2 text-xs text-violet-700">
                    Klaros AI proposed this action — it was not executed automatically because this action
                    type requires your approval. Nothing has happened yet.
                  </div>
                )}
                <p className="text-sm text-muted">{selected.reason}</p>

                <dl className="mt-4 grid grid-cols-2 gap-y-2 text-xs text-muted">
                  <dt>Requested by</dt>
                  <dd className="text-muted">{selected.requested_by_type === "AI" ? "Klaros AI" : selected.requested_by_type}</dd>
                  <dt>Created</dt>
                  <dd className="text-muted">{new Date(selected.created_at).toLocaleString()}</dd>
                  {selected.decided_at && (
                    <>
                      <dt>Decided</dt>
                      <dd className="text-muted">{new Date(selected.decided_at).toLocaleString()}</dd>
                    </>
                  )}
                  <dt>Execution</dt>
                  <dd>
                    <Badge status={selected.execution_status} className="text-[10px]">
                      {selected.execution_status}
                    </Badge>
                  </dd>
                  {selected.execution_attempts > 0 && (
                    <>
                      <dt>Attempts</dt>
                      <dd className="text-muted">{selected.execution_attempts}</dd>
                    </>
                  )}
                </dl>

                <div className="mt-4">
                  <p className="mb-1 text-xs text-muted">Input</p>
                  <pre className="max-h-40 overflow-auto rounded-md border border-border bg-background p-2 text-[11px] text-muted">
                    {JSON.stringify(selected.tool_input, null, 2)}
                  </pre>
                </div>

                {selected.execution_result && (
                  <div className="mt-4">
                    <p className="mb-1 text-xs text-muted">Execution result</p>
                    <pre className="max-h-40 overflow-auto rounded-md border border-success/20 bg-success/10 p-2 text-[11px] text-success">
                      {JSON.stringify(selected.execution_result, null, 2)}
                    </pre>
                  </div>
                )}

                {selected.execution_error && (
                  <div className="mt-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-xs text-danger">
                    {selected.execution_error}
                  </div>
                )}

                {selected.status === "PENDING" && (
                  <div className="mt-5">
                    <input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="Decision note (optional)"
                      className="mb-2 w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    />
                    <div className="flex gap-2">
                      <button
                        disabled={busy}
                        onClick={handleApprove}
                        className="rounded-md border border-success/20 bg-success/[0.06] px-3 py-1.5 text-sm text-success hover:bg-success/10 disabled:opacity-50"
                      >
                        Approve
                      </button>
                      <button
                        disabled={busy}
                        onClick={handleReject}
                        className="rounded-md border border-danger/25 bg-danger/[0.06] px-3 py-1.5 text-sm text-danger hover:bg-danger/10 disabled:opacity-50"
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
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
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
