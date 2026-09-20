"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CheckSquare, Clock, Package, Paperclip } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Invoice,
  Job,
  JobAttachment,
  JobCost,
  JobMaterial,
  JobProfitability,
  JobTask,
  ScopeChange,
  TimelineEntry,
  Worker,
  addMaterial,
  assignJob,
  blockJob,
  closeJob,
  completeQA,
  completeTask,
  createJobPurchaseOrderDraft,
  createScopeChange,
  createTask,
  downloadJobAttachment,
  failQA,
  generateCompletionPacket,
  generateContentFromJob,
  getJob,
  getJobProfitability,
  getJobSummary,
  getJobTimeline,
  listInvoices,
  listJobAttachments,
  listJobCosts,
  listJobMaterials,
  listJobTasks,
  listWorkers,
  recordJobCost,
  recordJobSignoff,
  requestScopeChangeApproval,
  scheduleJob,
  startQA,
  syncMaterialCosts,
  transitionJob,
  triggerInvoiceFromJob,
  unblockJob,
  updateJob,
  uploadJobFile,
} from "@/lib/api";

// Next valid forward action per status, matching the backend state machine.
const NEXT_ACTIONS: Record<string, { label: string; action: string }[]> = {
  DRAFT: [],
  SCHEDULED: [{ label: "Dispatch", action: "dispatch" }],
  DISPATCHED: [{ label: "Mark en route", action: "transition:EN_ROUTE" }],
  EN_ROUTE: [{ label: "Mark on site", action: "transition:ON_SITE" }],
  ON_SITE: [{ label: "Start job", action: "start" }],
  IN_PROGRESS: [{ label: "Complete field work", action: "complete" }],
  QA_PENDING: [],
  COMPLETED: [{ label: "Close job", action: "close" }],
  CLOSED: [],
  BLOCKED: [],
  CANCELLED: [],
};

// Statuses the backend state machine allows to transition into BLOCKED.
const BLOCKABLE_STATUSES = new Set(["DISPATCHED", "EN_ROUTE", "ON_SITE", "IN_PROGRESS", "QA_PENDING"]);
// Statuses BLOCKED can resume into.
const UNBLOCK_TARGETS = ["DISPATCHED", "EN_ROUTE", "ON_SITE", "IN_PROGRESS"];
// Statuses the backend state machine allows to transition into CANCELLED.
const CANCELLABLE_STATUSES = new Set(["DRAFT", "SCHEDULED", "DISPATCHED", "EN_ROUTE", "ON_SITE", "IN_PROGRESS"]);
// Statuses that can still be (re)scheduled/(un)assigned — not closed or cancelled.
const EDITABLE_STATUSES = new Set([
  "DRAFT", "SCHEDULED", "DISPATCHED", "EN_ROUTE", "ON_SITE", "IN_PROGRESS", "BLOCKED", "QA_PENDING", "COMPLETED",
]);
const JOB_PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT", "CRITICAL"];

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();

  const [job, setJob] = useState<Job | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [tasks, setTasks] = useState<JobTask[]>([]);
  const [materials, setMaterials] = useState<JobMaterial[]>([]);
  const [attachments, setAttachments] = useState<JobAttachment[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [blockReason, setBlockReason] = useState("");
  const [unblockTarget, setUnblockTarget] = useState("IN_PROGRESS");
  const [editingJob, setEditingJob] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editPriority, setEditPriority] = useState("NORMAL");
  const [editInternalNotes, setEditInternalNotes] = useState("");
  const [reschedulingJob, setReschedulingJob] = useState(false);
  const [scopeChanges, setScopeChanges] = useState<ScopeChange[]>([]);
  const [scopeDescription, setScopeDescription] = useState("");
  const [scopeReason, setScopeReason] = useState("");
  const [scopeCost, setScopeCost] = useState("");
  const [scopeRevenue, setScopeRevenue] = useState("");
  const [signedBy, setSignedBy] = useState("");
  const [signoffNotice, setSignoffNotice] = useState<string | null>(null);
  const [poSupplier, setPoSupplier] = useState("");
  const [poNotice, setPoNotice] = useState<string | null>(null);
  const [jobCosts, setJobCosts] = useState<JobCost[]>([]);
  const [profitability, setProfitability] = useState<JobProfitability | null>(null);
  const [syncingMaterials, setSyncingMaterials] = useState(false);

  const photoInputRef = useRef<HTMLInputElement>(null);
  const docInputRef = useRef<HTMLInputElement>(null);
  const voiceInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [
        jobResult,
        timelineResult,
        workersResult,
        tasksResult,
        materialsResult,
        attachmentsResult,
        invoicesResult,
        jobCostsResult,
        profitabilityResult,
      ] = await Promise.all([
        getJob(token, id),
        getJobTimeline(token, id),
        listWorkers(token),
        listJobTasks(token, id),
        listJobMaterials(token, id),
        listJobAttachments(token, id),
        listInvoices(token, { job_id: id }),
        listJobCosts(token, id),
        getJobProfitability(token, id),
      ]);
      setJob(jobResult.job);
      setTimeline(timelineResult.entries);
      setWorkers(workersResult.workers);
      setTasks(tasksResult.tasks);
      setMaterials(materialsResult.materials);
      setAttachments(attachmentsResult.attachments);
      setInvoices(invoicesResult.invoices);
      setJobCosts(jobCostsResult.job_costs);
      setProfitability(profitabilityResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Job could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function runAction(fn: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const result = await fn();
      if (result && typeof result === "object" && "approval_request_id" in result) {
        setActionNotice("This change requires approval — a request was created and nothing has been applied yet.");
      }
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed. Retry.");
    } finally {
      setBusy(false);
    }
  }

  async function handleNextAction(action: string) {
    if (!token) return;
    if (action === "dispatch" || action === "start" || action === "complete" || action === "close") {
      await runAction(() =>
        action === "close"
          ? closeJob(token, id)
          : transitionJob(token, id, action)
      );
    } else if (action.startsWith("transition:")) {
      const target = action.split(":")[1];
      await runAction(() => transitionJob(token, id, "transition", { target_status: target }));
    }
  }

  async function handleSchedule(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token) return;
    const form = new FormData(e.currentTarget);
    const start = form.get("start") as string;
    const end = form.get("end") as string;
    await runAction(() => scheduleJob(token, id, new Date(start).toISOString(), new Date(end).toISOString()));
  }

  async function handleAssign(workerId: string) {
    if (!token || !workerId) return;
    await runAction(() => assignJob(token, id, workerId));
  }

  async function handleBlock(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token || !blockReason.trim()) return;
    await runAction(() => blockJob(token, id, blockReason.trim()));
    setBlockReason("");
  }

  async function handleUnblock() {
    if (!token) return;
    await runAction(() => unblockJob(token, id, unblockTarget));
  }

  function startEditingJob() {
    if (!job) return;
    setEditTitle(job.title);
    setEditDescription(job.description ?? "");
    setEditPriority(job.priority);
    setEditInternalNotes(job.internal_notes ?? "");
    setEditingJob(true);
  }

  async function handleSaveJob(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token || !editTitle.trim()) return;
    await runAction(() =>
      updateJob(token, id, {
        title: editTitle.trim(),
        description: editDescription.trim() || undefined,
        priority: editPriority,
        internal_notes: editInternalNotes.trim() || undefined,
      })
    );
    setEditingJob(false);
  }

  async function handleUnassign() {
    if (!token) return;
    await runAction(() => transitionJob(token, id, "unassign"));
  }

  async function handleCancelJob() {
    if (!token) return;
    await runAction(() => transitionJob(token, id, "cancel"));
  }

  async function handleReschedule(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token) return;
    const form = new FormData(e.currentTarget);
    const start = form.get("start") as string;
    const end = form.get("end") as string;
    if (!start || !end) return;
    await runAction(() =>
      transitionJob(token, id, "reschedule", {
        start_time: new Date(start).toISOString(),
        end_time: new Date(end).toISOString(),
      })
    );
    setReschedulingJob(false);
  }

  async function handleCreateScopeChange(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token || !scopeDescription.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await createScopeChange(token, id, {
        description: scopeDescription.trim(),
        reason: scopeReason.trim() || undefined,
        estimated_cost: scopeCost ? Number(scopeCost) : undefined,
        estimated_revenue: scopeRevenue ? Number(scopeRevenue) : undefined,
      });
      setScopeChanges((prev) => [result.scope_change, ...prev]);
      setScopeDescription("");
      setScopeReason("");
      setScopeCost("");
      setScopeRevenue("");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Unable to record scope change.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRequestScopeApproval(scopeChangeId: string) {
    if (!token) return;
    setBusy(true);
    setActionError(null);
    try {
      await requestScopeChangeApproval(token, scopeChangeId, "Requested from job detail page");
      setScopeChanges((prev) =>
        prev.map((s) => (s.id === scopeChangeId ? { ...s, status: "PENDING_APPROVAL" } : s))
      );
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Unable to request approval.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSignoff(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token || !signedBy.trim()) return;
    setBusy(true);
    setActionError(null);
    setSignoffNotice(null);
    try {
      await recordJobSignoff(token, id, signedBy.trim());
      setSignoffNotice(`Signed off by ${signedBy.trim()}.`);
      setSignedBy("");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Unable to record sign-off.");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreatePODraft() {
    if (!token) return;
    setBusy(true);
    setActionError(null);
    setPoNotice(null);
    try {
      const result = await createJobPurchaseOrderDraft(token, id, poSupplier.trim() || undefined);
      setPoNotice(`Draft PO created with ${result.items.length} item(s).`);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Unable to create purchase order draft.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSyncMaterialCosts() {
    if (!token) return;
    setSyncingMaterials(true);
    setActionError(null);
    try {
      const result = await syncMaterialCosts(token, id);
      setActionNotice(
        result.created > 0
          ? `Synced ${result.created} material cost(s).`
          : "No new material costs to sync."
      );
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Unable to sync material costs.");
    } finally {
      setSyncingMaterials(false);
    }
  }

  async function handleAddTask(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token) return;
    const formEl = e.currentTarget;
    const title = new FormData(formEl).get("title") as string;
    if (!title) return;
    await runAction(() => createTask(token, id, title));
    formEl.reset();
  }

  async function handleAddMaterial(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token) return;
    const formEl = e.currentTarget;
    const name = new FormData(formEl).get("name") as string;
    if (!name) return;
    await runAction(() => addMaterial(token, id, name));
    formEl.reset();
  }

  async function handleUpload(kind: "documents" | "photos" | "voice-notes", file: File | undefined) {
    if (!token || !file) return;
    await runAction(() => uploadJobFile(token, id, kind, file));
  }

  async function handleViewAttachment(attachmentId: string) {
    if (!token) return;
    try {
      const blobUrl = await downloadJobAttachment(token, id, attachmentId);
      window.open(blobUrl, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to open attachment.");
    }
  }

  const nextActions = job ? NEXT_ACTIONS[job.status] ?? [] : [];

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <Link href="/jobs" className="text-sm text-muted hover:underline">
          ← Back to jobs
        </Link>

        {authLoading || loading ? (
          <Skeleton />
        ) : error ? (
          <div className="mt-4 rounded-md border border-danger/25 bg-danger/[0.06] p-4 text-sm text-danger">
            {error}{" "}
            <button onClick={load} className="ml-2 underline">
              Retry
            </button>
          </div>
        ) : !job ? (
          <p className="mt-4 text-sm text-muted">Job not found.</p>
        ) : (
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <section className="space-y-6 lg:col-span-2">
              <div className="rounded-lg border border-border bg-surface p-6">
                {editingJob ? (
                  <form onSubmit={handleSaveJob} className="space-y-2">
                    <div>
                      <label className="block text-xs text-muted">Title</label>
                      <input
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        required
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-muted">Description</label>
                      <textarea
                        value={editDescription}
                        onChange={(e) => setEditDescription(e.target.value)}
                        rows={2}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-muted">Priority</label>
                      <select
                        value={editPriority}
                        onChange={(e) => setEditPriority(e.target.value)}
                        className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      >
                        {JOB_PRIORITIES.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs text-muted">Internal notes</label>
                      <textarea
                        value={editInternalNotes}
                        onChange={(e) => setEditInternalNotes(e.target.value)}
                        rows={2}
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <div className="flex gap-2 pt-1">
                      <button
                        type="submit"
                        disabled={busy || !editTitle.trim()}
                        className="klaros-btn-primary disabled:opacity-50"
                      >
                        {busy ? "Saving..." : "Save"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingJob(false)}
                        className="rounded-md px-3 py-1.5 text-sm text-muted hover:underline"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                    <div className="flex items-start justify-between">
                      <div>
                        <h1 className="font-display text-2xl text-foreground">
                          {job.job_number} — {job.title}
                        </h1>
                        <p className="text-sm text-muted">
                          {job.status} · {job.priority} · {job.service_type ?? "no service type"}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        {nextActions.map((a) => (
                          <button
                            key={a.action}
                            disabled={busy}
                            onClick={() => handleNextAction(a.action)}
                            className="klaros-btn-primary disabled:opacity-50"
                          >
                            {a.label}
                          </button>
                        ))}
                        <button
                          onClick={startEditingJob}
                          className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                        >
                          Edit
                        </button>
                        {CANCELLABLE_STATUSES.has(job.status) && (
                          <button
                            disabled={busy}
                            onClick={handleCancelJob}
                            className="rounded-md border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
                          >
                            Cancel job
                          </button>
                        )}
                      </div>
                    </div>
                    {actionError && <p className="mt-3 text-sm text-danger">{actionError}</p>}
                    {actionNotice && <p className="mt-3 text-sm text-warning">{actionNotice}</p>}

                    <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                      <div>
                        <dt className="text-muted">Scheduled</dt>
                        <dd>{job.scheduled_start ? new Date(job.scheduled_start).toLocaleString() : "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Assigned worker</dt>
                        <dd className="flex items-center gap-2">
                          {job.assigned_user_id
                            ? workers.find((w) => w.id === job.assigned_user_id)?.name ?? job.assigned_user_id
                            : "unassigned"}
                          {job.assigned_user_id && EDITABLE_STATUSES.has(job.status) && (
                            <button
                              disabled={busy}
                              onClick={handleUnassign}
                              className="text-xs text-danger underline hover:text-foreground disabled:opacity-50"
                            >
                              Unassign
                            </button>
                          )}
                        </dd>
                      </div>
                    </dl>

                    {job.status === "DRAFT" && (
                      <form onSubmit={handleSchedule} className="mt-4 flex items-end gap-2">
                        <div>
                          <label className="block text-xs text-muted">Start</label>
                          <input name="start" type="datetime-local" required className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                        </div>
                        <div>
                          <label className="block text-xs text-muted">End</label>
                          <input name="end" type="datetime-local" required className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                        </div>
                        <button type="submit" className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
                          Schedule
                        </button>
                      </form>
                    )}

                    {job.status !== "DRAFT" && job.status !== "CLOSED" && job.status !== "CANCELLED" && (
                      <div className="mt-4">
                        {reschedulingJob ? (
                          <form onSubmit={handleReschedule} className="flex items-end gap-2">
                            <div>
                              <label className="block text-xs text-muted">New start</label>
                              <input name="start" type="datetime-local" required className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                            </div>
                            <div>
                              <label className="block text-xs text-muted">New end</label>
                              <input name="end" type="datetime-local" required className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-sm" />
                            </div>
                            <button type="submit" className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => setReschedulingJob(false)}
                              className="text-sm text-muted hover:underline"
                            >
                              Cancel
                            </button>
                          </form>
                        ) : (
                          <button
                            onClick={() => setReschedulingJob(true)}
                            className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                          >
                            Reschedule
                          </button>
                        )}
                      </div>
                    )}

                    {EDITABLE_STATUSES.has(job.status) && (
                      <div className="mt-4 flex items-center gap-2">
                        <select
                          onChange={(e) => handleAssign(e.target.value)}
                          defaultValue=""
                          className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                        >
                          <option value="" disabled>
                            {job.assigned_user_id ? "Reassign worker..." : "Assign worker..."}
                          </option>
                          {workers.map((w) => (
                            <option key={w.id} value={w.id}>
                              {w.name}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </>
                )}

                {token && BLOCKABLE_STATUSES.has(job.status) && (
                  <form onSubmit={handleBlock} className="mt-4 flex items-end gap-2 border-t border-border pt-4">
                    <div className="flex-1">
                      <label className="block text-xs text-muted">Block reason</label>
                      <input
                        value={blockReason}
                        onChange={(e) => setBlockReason(e.target.value)}
                        placeholder="Why is this job blocked?"
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={busy || !blockReason.trim()}
                      className="rounded-md border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06] disabled:opacity-50"
                    >
                      Block job
                    </button>
                  </form>
                )}

                {token && job.status === "BLOCKED" && (
                  <div className="mt-4 flex items-end gap-2 border-t border-border pt-4">
                    <div>
                      <label className="block text-xs text-muted">Resume to</label>
                      <select
                        value={unblockTarget}
                        onChange={(e) => setUnblockTarget(e.target.value)}
                        className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      >
                        {UNBLOCK_TARGETS.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      onClick={handleUnblock}
                      disabled={busy}
                      className="klaros-btn-primary disabled:opacity-50"
                    >
                      Unblock job
                    </button>
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Tasks</h2>
                {tasks.length === 0 ? (
                  <EmptyState icon={CheckSquare} title="No tasks yet." compact />
                ) : (
                  <ul className="space-y-2">
                    {tasks.map((t) => (
                      <li key={t.id} className="flex items-center justify-between text-sm">
                        <span className={t.status === "COMPLETED" ? "line-through text-muted-foreground" : ""}>
                          {t.title} {t.required && <span className="text-xs text-muted-foreground">(required)</span>}
                        </span>
                        {t.status !== "COMPLETED" && token && (
                          <button
                            onClick={() => runAction(() => completeTask(token, t.id))}
                            className="text-xs underline text-muted hover:text-foreground"
                          >
                            Complete
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <form onSubmit={handleAddTask} className="mt-3 flex gap-2">
                  <input name="title" placeholder="New task" className="flex-1 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm" />
                  <button type="submit" className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
                    Add
                  </button>
                </form>
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Materials</h2>
                {materials.length === 0 ? (
                  <EmptyState icon={Package} title="No materials recorded." compact />
                ) : (
                  <ul className="space-y-1 text-sm">
                    {materials.map((m) => (
                      <li key={m.id}>
                        {m.name} × {m.quantity} — {m.status}
                      </li>
                    ))}
                  </ul>
                )}
                <form onSubmit={handleAddMaterial} className="mt-3 flex gap-2">
                  <input name="name" placeholder="Material name" className="flex-1 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-sm" />
                  <button type="submit" className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
                    Add
                  </button>
                </form>
                {materials.length > 0 && (
                  <div className="mt-4 flex items-end gap-2 border-t border-border pt-4">
                    <div>
                      <label className="block text-xs text-muted">Supplier (optional)</label>
                      <input
                        value={poSupplier}
                        onChange={(e) => setPoSupplier(e.target.value)}
                        className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <button
                      disabled={busy}
                      onClick={handleCreatePODraft}
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                    >
                      Draft purchase order
                    </button>
                    {poNotice && <span className="text-xs text-success">{poNotice}</span>}
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Photos, documents & voice notes</h2>
                <div className="flex gap-2">
                  <button
                    onClick={() => photoInputRef.current?.click()}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                  >
                    Upload photo
                  </button>
                  <input
                    ref={photoInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={(e) => handleUpload("photos", e.target.files?.[0])}
                  />
                  <button
                    onClick={() => docInputRef.current?.click()}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                  >
                    Upload document
                  </button>
                  <input
                    ref={docInputRef}
                    type="file"
                    accept="application/pdf,text/plain"
                    className="hidden"
                    onChange={(e) => handleUpload("documents", e.target.files?.[0])}
                  />
                  <button
                    onClick={() => voiceInputRef.current?.click()}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                  >
                    Upload voice note
                  </button>
                  <input
                    ref={voiceInputRef}
                    type="file"
                    accept="audio/*"
                    className="hidden"
                    onChange={(e) => handleUpload("voice-notes", e.target.files?.[0])}
                  />
                </div>
                {attachments.length === 0 ? (
                  <div className="mt-3">
                    <EmptyState icon={Paperclip} title="No attachments yet." compact />
                  </div>
                ) : (
                  <ul className="mt-3 space-y-1 text-sm text-muted">
                    {attachments.map((a) => (
                      <li key={a.id} className="flex items-center gap-2">
                        <span>
                          {a.kind}: {a.filename} ({a.size_bytes} bytes, {a.storage_provider})
                        </span>
                        <button
                          onClick={() => handleViewAttachment(a.id)}
                          className="text-xs text-success underline hover:text-foreground"
                        >
                          View
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {EDITABLE_STATUSES.has(job.status) && token && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-3 text-sm font-medium text-muted">Scope changes</h2>
                  {scopeChanges.length === 0 ? (
                    <p className="text-sm text-muted">No scope changes recorded this session.</p>
                  ) : (
                    <ul className="mb-3 space-y-2 text-sm">
                      {scopeChanges.map((s) => (
                        <li key={s.id} className="rounded-md border border-border-strong bg-surface-muted p-3">
                          <div className="flex items-center justify-between">
                            <span>{s.description}</span>
                            <span className="text-xs text-muted">{s.status}</span>
                          </div>
                          {(s.estimated_cost != null || s.estimated_revenue != null) && (
                            <p className="mt-1 text-xs text-muted">
                              {s.estimated_cost != null && `Cost: $${s.estimated_cost} `}
                              {s.estimated_revenue != null && `Revenue: $${s.estimated_revenue}`}
                            </p>
                          )}
                          {s.status === "DETECTED" && (
                            <button
                              disabled={busy}
                              onClick={() => handleRequestScopeApproval(s.id)}
                              className="mt-2 text-xs underline text-muted hover:text-foreground disabled:opacity-50"
                            >
                              Request approval
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <form onSubmit={handleCreateScopeChange} className="space-y-2 border-t border-border pt-3">
                    <input
                      value={scopeDescription}
                      onChange={(e) => setScopeDescription(e.target.value)}
                      placeholder="What changed?"
                      className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    />
                    <input
                      value={scopeReason}
                      onChange={(e) => setScopeReason(e.target.value)}
                      placeholder="Reason (optional)"
                      className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    />
                    <div className="flex gap-2">
                      <input
                        value={scopeCost}
                        onChange={(e) => setScopeCost(e.target.value)}
                        placeholder="Est. cost"
                        className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                      <input
                        value={scopeRevenue}
                        onChange={(e) => setScopeRevenue(e.target.value)}
                        placeholder="Est. revenue"
                        className="w-28 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                      <button
                        type="submit"
                        disabled={busy || !scopeDescription.trim()}
                        className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                      >
                        Record scope change
                      </button>
                    </div>
                  </form>
                </div>
              )}

              {(job.status === "COMPLETED" || job.status === "CLOSED") && token && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-3 text-sm font-medium text-muted">Customer sign-off</h2>
                  <p className="mb-3 text-xs text-muted">
                    Internal record only — not a legally binding e-signature.
                  </p>
                  {signoffNotice && <p className="mb-2 text-sm text-success">{signoffNotice}</p>}
                  <form onSubmit={handleSignoff} className="flex items-end gap-2">
                    <div className="flex-1">
                      <label className="block text-xs text-muted">Signed by</label>
                      <input
                        value={signedBy}
                        onChange={(e) => setSignedBy(e.target.value)}
                        placeholder="Customer name"
                        className="w-full rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                      />
                    </div>
                    <button
                      type="submit"
                      disabled={busy || !signedBy.trim()}
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                    >
                      Record sign-off
                    </button>
                  </form>
                </div>
              )}

              {job.status === "QA_PENDING" && token && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-3 text-sm font-medium text-muted">QA</h2>
                  <div className="flex gap-2">
                    <button onClick={() => runAction(() => startQA(token, id))} className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted">
                      Start QA
                    </button>
                    <button onClick={() => runAction(() => completeQA(token, id))} className="rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-foreground">
                      Pass QA
                    </button>
                    <button
                      onClick={() => runAction(() => failQA(token, id, "Failed from Job detail UI"))}
                      className="rounded-md border border-danger/25 px-3 py-1.5 text-sm text-danger hover:bg-danger/[0.06]"
                    >
                      Fail QA
                    </button>
                  </div>
                </div>
              )}

              {job.status === "COMPLETED" && token && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-3 text-sm font-medium text-muted">Completion packet</h2>
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => runAction(() => generateCompletionPacket(token, id))}
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                    >
                      Generate packet
                    </button>
                    <button
                      onClick={() =>
                        runAction(async () => {
                          const result = await generateContentFromJob(token, id);
                          setActionNotice(`Content idea created: "${result.content.title}".`);
                        })
                      }
                      className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                    >
                      Generate marketing content idea
                    </button>
                  </div>
                </div>
              )}

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Finance</h2>
                {invoices.length === 0 ? (
                  <div className="space-y-3">
                    <p className="text-sm text-muted">
                      Invoice not created. Invoicing is triggered automatically when the job is closed — it
                      hasn&apos;t simply been triggered yet, not because Finance is broken.
                    </p>
                    {job?.status === "CLOSED" && token && (
                      <button
                        onClick={() => runAction(() => triggerInvoiceFromJob(token, id))}
                        className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                      >
                        Trigger invoice now
                      </button>
                    )}
                  </div>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {invoices.map((inv) => (
                      <li key={inv.id} className="flex items-center justify-between">
                        <Link href={`/finance/invoices/${inv.id}`} className="underline hover:text-foreground">
                          {inv.invoice_number}
                        </Link>
                        <span className="text-muted">
                          {inv.status} · ${inv.total} (${inv.amount_due} due)
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {token && <RecordJobCostForm token={token} jobId={id} onRecorded={load} />}

                {profitability && (profitability.estimated_revenue != null || profitability.actual_cost != null) && (
                  <div className="mt-4 border-t border-border pt-4 text-sm">
                    <h3 className="mb-2 text-xs font-medium text-muted">Profitability</h3>
                    <dl className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                      <div>
                        <dt className="text-muted">Est. margin</dt>
                        <dd>{profitability.estimated_margin_pct != null ? `${profitability.estimated_margin_pct}%` : "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Actual cost</dt>
                        <dd>{profitability.actual_cost != null ? `$${profitability.actual_cost}` : "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted">Actual margin</dt>
                        <dd>{profitability.actual_margin_pct != null ? `${profitability.actual_margin_pct}%` : "—"}</dd>
                      </div>
                    </dl>
                  </div>
                )}

                <div className="mt-4 border-t border-border pt-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-xs font-medium text-muted">Job costs</h3>
                    {token && (
                      <button
                        disabled={syncingMaterials}
                        onClick={handleSyncMaterialCosts}
                        className="text-xs underline text-muted hover:text-foreground disabled:opacity-50"
                      >
                        {syncingMaterials ? "Syncing..." : "Sync material costs"}
                      </button>
                    )}
                  </div>
                  {jobCosts.length === 0 ? (
                    <p className="text-xs text-muted">No costs recorded yet.</p>
                  ) : (
                    <ul className="space-y-1 text-xs">
                      {jobCosts.map((c) => (
                        <li key={c.id} className="flex items-center justify-between">
                          <span>
                            {c.category}{c.description ? ` — ${c.description}` : ""}
                          </span>
                          <span className="text-muted">${c.total_cost}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Timeline</h2>
                {timeline.length === 0 ? (
                  <EmptyState icon={Clock} title="No activity recorded yet." compact />
                ) : (
                  <ul className="space-y-2 text-sm">
                    {timeline.map((entry, i) => (
                      <li key={i}>
                        <span className="text-muted-foreground">{new Date(entry.timestamp).toLocaleString()}</span>{" "}
                        — {entry.summary}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>

            <section className="space-y-4">
              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-2 text-sm font-medium text-muted">AI summary</h2>
                {summary ? <p className="text-sm text-muted">{summary}</p> : <p className="text-sm text-muted">Not generated yet.</p>}
                <button
                  onClick={() =>
                    runAction(async () => {
                      if (!token) return;
                      const result = await getJobSummary(token, id);
                      setSummary(result.summary);
                    })
                  }
                  className="mt-4 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                >
                  Generate summary
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function RecordJobCostForm({ token, jobId, onRecorded }: { token: string; jobId: string; onRecorded: () => void }) {
  const [category, setCategory] = useState("MATERIAL");
  const [description, setDescription] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!unitCost) return;
    setSubmitting(true);
    setError(null);
    try {
      await recordJobCost(token, { job_id: jobId, category, description: description || undefined, unit_cost: unitCost });
      setDescription("");
      setUnitCost("");
      onRecorded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to record cost.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-4 flex flex-wrap items-end gap-2 border-t border-border pt-4">
      <div>
        <label className="block text-xs text-muted">Category</label>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
        >
          {["LABOR", "MATERIAL", "SUBCONTRACTOR", "TRAVEL", "EQUIPMENT", "OTHER"].map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-xs text-muted">Description</label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
        />
      </div>
      <div>
        <label className="block text-xs text-muted">Cost</label>
        <input
          required
          value={unitCost}
          onChange={(e) => setUnitCost(e.target.value)}
          placeholder="0.00"
          className="w-24 rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
        />
      </div>
      <button
        type="submit"
        disabled={submitting}
        className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
      >
        Record cost
      </button>
      {error && <p className="w-full text-xs text-danger">{error}</p>}
    </form>
  );
}
