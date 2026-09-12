"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CheckSquare, Clock, Package, Paperclip } from "lucide-react";
import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/ui/EmptyState";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  Invoice,
  Job,
  JobAttachment,
  JobMaterial,
  JobTask,
  TimelineEntry,
  Worker,
  addMaterial,
  assignJob,
  closeJob,
  completeQA,
  completeTask,
  createTask,
  downloadJobAttachment,
  failQA,
  generateCompletionPacket,
  getJob,
  getJobSummary,
  getJobTimeline,
  listInvoices,
  listJobAttachments,
  listJobMaterials,
  listJobTasks,
  listWorkers,
  recordJobCost,
  scheduleJob,
  startQA,
  transitionJob,
  triggerInvoiceFromJob,
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
  const [busy, setBusy] = useState(false);

  const photoInputRef = useRef<HTMLInputElement>(null);
  const docInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [jobResult, timelineResult, workersResult, tasksResult, materialsResult, attachmentsResult, invoicesResult] =
        await Promise.all([
          getJob(token, id),
          getJobTimeline(token, id),
          listWorkers(token),
          listJobTasks(token, id),
          listJobMaterials(token, id),
          listJobAttachments(token, id),
          listInvoices(token, { job_id: id }),
        ]);
      setJob(jobResult.job);
      setTimeline(timelineResult.entries);
      setWorkers(workersResult.workers);
      setTasks(tasksResult.tasks);
      setMaterials(materialsResult.materials);
      setAttachments(attachmentsResult.attachments);
      setInvoices(invoicesResult.invoices);
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
    try {
      await fn();
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

  async function handleUpload(kind: "documents" | "photos", file: File | undefined) {
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
          <p className="mt-4 text-sm text-muted">Loading job...</p>
        ) : error ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50/30 p-4 text-sm text-red-700">
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
                  </div>
                </div>
                {actionError && <p className="mt-3 text-sm text-red-600">{actionError}</p>}

                <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <dt className="text-muted">Scheduled</dt>
                    <dd>{job.scheduled_start ? new Date(job.scheduled_start).toLocaleString() : "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted">Assigned worker</dt>
                    <dd>{job.assigned_user_id ? workers.find((w) => w.id === job.assigned_user_id)?.name ?? job.assigned_user_id : "unassigned"}</dd>
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

                {job.status === "SCHEDULED" && !job.assigned_user_id && (
                  <div className="mt-4 flex items-center gap-2">
                    <select
                      onChange={(e) => handleAssign(e.target.value)}
                      defaultValue=""
                      className="rounded-md border border-border-strong bg-surface-muted px-2 py-1.5 text-sm"
                    >
                      <option value="" disabled>
                        Assign worker...
                      </option>
                      {workers.map((w) => (
                        <option key={w.id} value={w.id}>
                          {w.name}
                        </option>
                      ))}
                    </select>
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
              </div>

              <div className="rounded-lg border border-border bg-surface p-6">
                <h2 className="mb-3 text-sm font-medium text-muted">Photos & documents</h2>
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
                          className="text-xs text-emerald-600 underline hover:text-foreground"
                        >
                          View
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

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
                      className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50/30"
                    >
                      Fail QA
                    </button>
                  </div>
                </div>
              )}

              {job.status === "COMPLETED" && token && (
                <div className="rounded-lg border border-border bg-surface p-6">
                  <h2 className="mb-3 text-sm font-medium text-muted">Completion packet</h2>
                  <button
                    onClick={() => runAction(() => generateCompletionPacket(token, id))}
                    className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted"
                  >
                    Generate packet
                  </button>
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
      {error && <p className="w-full text-xs text-red-600">{error}</p>}
    </form>
  );
}
