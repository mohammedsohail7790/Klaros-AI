"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, GitBranch } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, MedicalTourismPipeline, PipelineStage, getMedicalTourismPipeline } from "@/lib/api";
import { STAGE_LABEL, STAGE_ORDER } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";

/**
 * The owner's pipeline: every patient enquiry at the stage the recorded facts put it in (consent, qualification, the approved referral, the clinic's
 * answer, the reported outcome). A stage is derived by the backend and cannot be set here. A patient who has not agreed to us keeping their details is
 * listed without a name.
 */
export default function MedicalTourismPipelinePage() {
  const { token, user, loading: authLoading } = useAuth();
  const [data, setData] = useState<MedicalTourismPipeline | null>(null);
  const [filter, setFilter] = useState<PipelineStage | "ALL">("ALL");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setData(await getMedicalTourismPipeline(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load the pipeline.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = (data?.leads ?? []).filter((l) => filter === "ALL" || l.stage === filter);

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <PageHeader
          title="Pipeline"
          description="Where every patient enquiry stands, from the first call to the clinic's reported outcome."
          icon={GitBranch}
          actions={<Button size="sm" variant="secondary" onClick={() => load()}>Refresh</Button>}
        />

        {authLoading || (loading && !data) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
        ) : data ? (
          <>
            <div role="tablist" aria-label="Stage" className="mb-4 flex flex-wrap gap-2">
              <button role="tab" aria-selected={filter === "ALL"} onClick={() => setFilter("ALL")} className={`rounded-full border px-3 py-1 text-xs ${filter === "ALL" ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"}`}>
                All ({data.leads.length})
              </button>
              {STAGE_ORDER.map((s, i) => (
                <button key={s} role="tab" aria-selected={filter === s} onClick={() => setFilter(s)} className={`rounded-full border px-3 py-1 text-xs ${filter === s ? "border-foreground bg-surface text-foreground" : "border-border-strong text-muted"}`}>
                  {i + 1}. {STAGE_LABEL[s]} ({data.counts[s] ?? 0})
                </button>
              ))}
            </div>

            {rows.length === 0 ? (
              <EmptyState icon={GitBranch} title={filter === "ALL" ? "No patient enquiries yet." : "No patients at this stage."} />
            ) : (
              <div className="klaros-table-wrap">
                <table className="klaros-table">
                  <thead className="bg-surface text-muted">
                    <tr>
                      <th className="px-4 py-2">Patient</th>
                      <th className="px-4 py-2">Stage</th>
                      <th className="px-4 py-2">Next step</th>
                      <th className="px-4 py-2">Referrals</th>
                      <th className="px-4 py-2">Tasks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((l) => (
                      <tr key={l.lead_id} className="border-t border-border hover:bg-surface">
                        <td className="px-4 py-2 text-foreground">
                          {l.name ? (
                            <Link href={`/leads/${l.lead_id}`} className="font-medium underline-offset-2 hover:underline">{l.name}</Link>
                          ) : (
                            <Link href={`/leads/${l.lead_id}`} className="italic text-muted underline-offset-2 hover:underline">Name withheld (no consent to keep details)</Link>
                          )}
                          {l.held_for_person && (
                            <span className="ml-2 inline-flex items-center gap-1 text-xs text-warning">
                              <AlertTriangle className="h-3 w-3" aria-hidden="true" /> held for a person
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2 whitespace-nowrap">{l.stage_number}. {STAGE_LABEL[l.stage]}</td>
                        <td className="px-4 py-2 text-muted">{l.next_action}</td>
                        <td className="px-4 py-2">
                          {l.referrals.length === 0 ? <span className="text-muted">—</span> : l.referrals.map((r) => <Badge key={r.id} status={r.status}>{r.status}</Badge>)}
                        </td>
                        <td className="px-4 py-2 text-muted">{l.open_tasks > 0 ? `${l.open_tasks} open` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="mt-4 max-w-3xl text-xs text-muted-foreground">{data.definitions}</p>
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
