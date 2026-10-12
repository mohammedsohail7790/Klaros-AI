"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ClipboardList } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, HandoffTask, PipelineLead, acceptHandoffTask, cancelHandoffTask, completeHandoffTask, getMedicalTourismPipeline, listHandoffTasks } from "@/lib/api";
import { ageLabel, caseHref, contextLine, patientName } from "@/lib/mtCase";
import { TASK_KIND_LABEL, TASK_REASON_LABEL } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

const FILTERS = ["OPEN", "ACCEPTED", "DONE", "CANCELLED", "ALL"] as const;
const REFUSED: Record<string, string> = {
  not_open: "Someone has already taken this task.",
  not_accepted: "Accept the task before completing it.",
  not_the_owner: "Only the person who accepted this task (or a manager) can finish it.",
  already_closed: "This task is already closed.",
};

/**
 * Cases that need a person: handed over by the AI receptionist or raised by a safety rule. Each has one owner, and every change is audited by the backend.
 * The page only shows a closed reason and a note a PERSON wrote: nothing from the call itself.
 */
export default function MedicalTourismTasksPage() {
  const { token, user, loading: authLoading } = useAuth();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("OPEN");
  const [tasks, setTasks] = useState<HandoffTask[]>([]);
  const [patients, setPatients] = useState<PipelineLead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setTasks((await listHandoffTasks(token, { status: filter === "ALL" ? undefined : filter })).tasks);
      // Names are a convenience: if they cannot be loaded the tasks still show, with a generic label.
      getMedicalTourismPipeline(token).then((p) => setPatients(p.leads ?? [])).catch(() => setPatients([]));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load tasks.");
    } finally {
      setLoading(false);
    }
  }, [token, filter]);

  useEffect(() => {
    load();
  }, [load]);

  const patientOf = useMemo(() => Object.fromEntries(patients.map((p) => [p.lead_id, p])), [patients]);

  async function act(task: HandoffTask, action: "accept" | "complete" | "cancel") {
    if (!token) return;
    setBusy(task.id);
    setNotice(null);
    setError(null);
    try {
      if (action === "accept") await acceptHandoffTask(token, task.id);
      else if (action === "complete") await completeHandoffTask(token, task.id);
      else await cancelHandoffTask(token, task.id);
      await load();
    } catch (err) {
      await load(); // refresh first (it clears the banner), then say why this one was refused
      setError(err instanceof ApiError ? (err.code && REFUSED[err.code]) || err.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell user={user}>
      <div className="px-6 py-8 sm:px-8">
        <PageHeader title="Tasks" description="Patients who need a person. Take one to own it; every change is recorded." icon={ClipboardList} />
        <div role="tablist" aria-label="Status" className="mb-4 flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)} className={`rounded-full border px-3 py-1 text-xs ${filter === f ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"}`}>
              {f === "ALL" ? "All" : f.charAt(0) + f.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
        {notice && <Alert variant="success" className="mb-3">{notice}</Alert>}
        {error && <Alert variant="danger" className="mb-3">{error}</Alert>}

        {authLoading || (loading && tasks.length === 0) ? (
          <Skeleton />
        ) : tasks.length === 0 ? (
          <EmptyState icon={ClipboardList} title={filter === "OPEN" ? "Nothing is waiting for a person." : "No tasks here."} />
        ) : (
          <div className="klaros-table-wrap">
            <table className="klaros-table">
              <thead className="bg-surface text-muted">
                <tr>
                  <th className="px-4 py-2">Patient</th>
                  <th className="px-4 py-2">What and why</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <Link href={caseHref(t.lead_id)} className="font-medium text-foreground underline-offset-2 hover:underline">
                        {patientOf[t.lead_id] ? patientName(patientOf[t.lead_id]) : "Open patient"}
                      </Link>
                      {patientOf[t.lead_id] && contextLine(patientOf[t.lead_id]) && <div className="text-xs text-muted">{contextLine(patientOf[t.lead_id])}</div>}
                    </td>
                    <td className="px-4 py-2">
                      <div className="font-medium text-foreground">{TASK_KIND_LABEL[t.kind] ?? "Task"}</div>
                      <div className="text-muted">{(t.reason_code && TASK_REASON_LABEL[t.reason_code]) || ""}</div>
                      {t.created_at && <div className="text-xs text-muted-foreground">opened {ageLabel(t.created_at)}</div>}
                    </td>
                    <td className="px-4 py-2"><Badge status={t.status}>{t.status === "ACCEPTED" ? "In progress" : t.status.charAt(0) + t.status.slice(1).toLowerCase()}</Badge></td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      {t.status === "OPEN" && <Button size="sm" disabled={busy === t.id} onClick={() => act(t, "accept")}>Accept</Button>}
                      {t.status === "ACCEPTED" && <Button size="sm" disabled={busy === t.id} onClick={() => act(t, "complete")}>Mark done</Button>}
                      {(t.status === "OPEN" || t.status === "ACCEPTED") && (
                        <Button size="sm" variant="ghost" disabled={busy === t.id} onClick={() => act(t, "cancel")}>Cancel</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
