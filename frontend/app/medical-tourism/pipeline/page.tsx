"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, GitBranch, LayoutGrid, List } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { ApiError, MedicalTourismPipeline, PipelineLead, PipelineStage, getMedicalTourismPipeline } from "@/lib/api";
import { ageLabel, caseHref, contextLine, nextStepFor, patientName } from "@/lib/mtCase";
import { STAGE_LABEL, STAGE_ORDER } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Hero, Page } from "@/components/ui/layout";
import { Skeleton } from "@/components/ui/Skeleton";

type View = "board" | "list";

const clinicOf = (l: PipelineLead) => l.referrals.find((r) => r.status !== "CANCELLED")?.provider_name ?? null;

function PatientCard({ l }: { l: PipelineLead }) {
  const step = nextStepFor(l);
  const context = contextLine(l);
  const clinic = clinicOf(l);
  return (
    <li>
      <Link
        href={caseHref(l.lead_id)}
        className="block rounded-2xl bg-surface p-4 shadow-card transition-all duration-500 ease-apple hover:-translate-y-0.5 hover:shadow-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        aria-label={`Open ${patientName(l)}`}
      >
        <div className="flex items-start justify-between gap-2">
          <span className={`font-medium ${l.name ? "text-foreground" : "italic text-muted"}`}>{patientName(l)}</span>
          {l.open_tasks > 0 && <span className="shrink-0 rounded-full bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">{l.open_tasks} task{l.open_tasks === 1 ? "" : "s"}</span>}
        </div>
        {context && <p className="mt-1 text-[13px] leading-snug text-muted">{context}</p>}
        {l.intake?.timeframe && <p className="text-xs text-muted-foreground">{l.intake.timeframe}</p>}
        {clinic && <p className="mt-1.5 text-xs text-muted">With {clinic}</p>}
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-border/70 pt-3 text-xs">
          {step.cta ? <span className={`font-medium ${step.priority === 1 ? "text-danger" : "text-accent"}`}>{step.cta}</span> : <span className="text-muted-foreground">Nothing to do now</span>}
          <span className="text-muted-foreground">{ageLabel(l.created_at)}</span>
        </div>
        {l.held_for_person && (
          <p className="mt-1.5 inline-flex items-center gap-1 text-xs text-warning">
            <AlertTriangle className="h-3 w-3" aria-hidden="true" /> Held for a person
          </p>
        )}
      </Link>
    </li>
  );
}

function Board({ data }: { data: MedicalTourismPipeline }) {
  return (
    <div className="-mx-5 overflow-x-auto px-5 pb-6 sm:-mx-8 sm:px-8" aria-label="Patient board">
      <div className="flex min-w-max gap-4">
        {STAGE_ORDER.map((s, i) => {
          const rows = data.leads.filter((l) => l.stage === s);
          return (
            <section key={s} aria-label={STAGE_LABEL[s]} className={`shrink-0 rounded-3xl bg-surface-muted p-3 ${rows.length === 0 ? "w-44" : "w-72"}`}>
              <header className="mb-3 flex items-center justify-between px-2 pt-1">
                <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
                  <span className="mr-1.5 text-muted-foreground">{i + 1}</span>
                  {STAGE_LABEL[s]}
                </h2>
                <span className="rounded-full bg-surface px-2.5 py-0.5 text-xs font-medium tabular-nums text-muted">{rows.length}</span>
              </header>
              {rows.length === 0 ? (
                <div className="px-3 py-6 text-center text-[13px] text-muted-foreground">No one here</div>
              ) : (
                <ul className="space-y-2">
                  {rows.map((l) => (
                    <PatientCard key={l.lead_id} l={l} />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function ListView({ rows }: { rows: PipelineLead[] }) {
  return (
    <div className="klaros-table-wrap">
      <table className="klaros-table">
        <thead>
          <tr>
            <th className="px-4 py-2">Patient</th>
            <th className="px-4 py-2">Stage</th>
            <th className="px-4 py-2">Next step</th>
            <th className="px-4 py-2">Clinic</th>
            <th className="px-4 py-2">Referral</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((l) => {
            const step = nextStepFor(l);
            return (
              <tr key={l.lead_id} className="border-t border-border hover:bg-surface">
                <td className="px-4 py-2">
                  <Link href={caseHref(l.lead_id)} className={`font-medium underline-offset-2 hover:underline ${l.name ? "text-foreground" : "italic text-muted"}`}>
                    {patientName(l)}
                  </Link>
                  {contextLine(l) && <div className="text-xs text-muted">{contextLine(l)}</div>}
                  {l.held_for_person && (
                    <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-warning">
                      <AlertTriangle className="h-3 w-3" aria-hidden="true" /> held for a person
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-2">
                  {l.stage_number}. {STAGE_LABEL[l.stage]}
                </td>
                <td className="px-4 py-2 text-muted">
                  {step.cta ? <span className="font-medium text-accent">{step.cta}</span> : <span>Nothing to do now</span>}
                  <div className="text-xs">{step.hint}</div>
                </td>
                <td className="px-4 py-2 text-muted">{clinicOf(l) ?? "—"}</td>
                <td className="px-4 py-2">{l.referrals.length === 0 ? <span className="text-muted">—</span> : l.referrals.map((r) => <Badge key={r.id} status={r.status}>{r.status.replace("_", " ")}</Badge>)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PipelineBody() {
  const { token, user, loading: authLoading } = useAuth();
  const params = useSearchParams();
  const stageParam = params.get("stage");
  const initialStage = (STAGE_ORDER as string[]).includes(stageParam ?? "") ? (stageParam as PipelineStage) : null;
  const [view, setView] = useState<View>(initialStage ? "list" : "board");
  const [filter, setFilter] = useState<PipelineStage | "ALL">(initialStage ?? "ALL");
  const [data, setData] = useState<MedicalTourismPipeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setData(await getMedicalTourismPipeline(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load your patients.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = (data?.leads ?? []).filter((l) => filter === "ALL" || l.stage === filter);
  const toggle = (v: View, Icon: typeof List, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={view === v}
      onClick={() => setView(v)}
      className={`inline-flex items-center gap-1.5 rounded-full px-4 py-1.5 text-[13px] font-medium transition-all duration-300 ease-apple ${view === v ? "bg-surface text-foreground shadow-card" : "text-muted"}`}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
      {label}
    </button>
  );

  return (
    <AppShell user={user}>
      <Page className="!max-w-none">
        <Hero
          title="Patients"
          lede="Every enquiry, from the first call to the clinic's reported outcome. Open a patient to see everything and take the next step."
          actions={
            <div className="flex items-center gap-2">
              <div role="tablist" aria-label="View" className="flex rounded-full bg-surface-muted p-1">
                {toggle("board", LayoutGrid, "Board")}
                {toggle("list", List, "List")}
              </div>
              <Link href="/leads?new=1" className="klaros-btn-primary">
                Add patient
              </Link>
              <Button size="sm" variant="secondary" onClick={() => load()}>
                Refresh
              </Button>
            </div>
          }
        />

        {authLoading || (loading && !data) ? (
          <Skeleton />
        ) : error ? (
          <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>
            {error}
          </Alert>
        ) : data ? (
          data.leads.length === 0 ? (
            <EmptyState icon={GitBranch} title="No patient enquiries yet. They appear here the moment the AI receptionist or your website records one." />
          ) : view === "board" ? (
            <Board data={data} />
          ) : (
            <>
              <div role="tablist" aria-label="Stage" className="mb-4 flex flex-wrap gap-2">
                <button role="tab" aria-selected={filter === "ALL"} onClick={() => setFilter("ALL")} className={`klaros-filter-tab ${filter === "ALL" ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}>
                  All ({data.leads.length})
                </button>
                {STAGE_ORDER.map((s, i) => (
                  <button key={s} role="tab" aria-selected={filter === s} onClick={() => setFilter(s)} className={`klaros-filter-tab ${filter === s ? "klaros-filter-tab-active" : "klaros-filter-tab-inactive"}`}>
                    {i + 1}. {STAGE_LABEL[s]} ({data.counts[s] ?? 0})
                  </button>
                ))}
              </div>
              {rows.length === 0 ? <EmptyState icon={GitBranch} title="No patients at this stage." /> : <ListView rows={rows} />}
            </>
          )
        ) : null}
        {data && <p className="mt-4 max-w-3xl text-xs text-muted-foreground">A patient's stage is worked out from what is on record; it cannot be moved by hand or skipped.</p>}
      </Page>
    </AppShell>
  );
}

export default function MedicalTourismPipelinePage() {
  return (
    <Suspense fallback={null}>
      <PipelineBody />
    </Suspense>
  );
}
