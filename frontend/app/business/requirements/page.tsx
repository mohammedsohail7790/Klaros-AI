"use client";

/**
 * Requirements: what the business needs in order to run, derived by the
 * backend from the confirmed Business Blueprint (and any industry module
 * the business has opted into). Read-only here — to change a requirement,
 * change the Blueprint; there is deliberately no second place to edit it.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ClipboardList, Quote } from "lucide-react";
import {
  ApiError,
  BuilderRequirement,
  enableIndustryModule,
  generateRecommendationsJourneyStep,
} from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";
import { StageTracker } from "@/components/business/StageTracker";
import { StatusPill } from "@/components/business/StatusPill";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";

export default function RequirementsPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const { overview, error: loadError, reload } = useBuilderOverview(token);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!overview) return;
    const j = overview.journey;
    if (!j) router.replace("/business");
    else if (!isJourneyAtStage(j.status, ["BLUEPRINT_ACTIVE", "RECOMMENDATIONS_READY", "COMPLETED"])) {
      router.replace(getJourneyDestination(j.status));
    }
  }, [overview, router]);

  const groups = useMemo(() => {
    const m = new Map<string, BuilderRequirement[]>();
    for (const r of overview?.requirements ?? []) m.set(r.group, [...(m.get(r.group) ?? []), r]);
    return [...m.entries()];
  }, [overview]);

  async function handleGenerate() {
    if (!token || !overview?.journey || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await generateRecommendationsJourneyStep(token, overview.journey.id);
      router.replace(getJourneyDestination(updated.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) await reload();
      setError(err instanceof ApiError ? err.message : "We couldn't generate recommendations just now. Please try again.");
      setBusy(false);
    }
  }

  async function handleEnable(key: string) {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      await enableIndustryModule(token, key);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't enable that module. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (!overview) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-4xl px-6 py-10">
          {loadError ? <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Retry</Button>}>{loadError}</Alert> : <Skeleton rows={5} />}
        </div>
      </AppShell>
    );
  }

  const moduleActions = overview.next_actions.filter((a) => a.kind === "enable_vertical" || a.id.startsWith("module-planned:"));
  const atBlueprintActive = overview.journey?.status === "BLUEPRINT_ACTIVE";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-8">
        <StageTracker stages={overview.stages} activeKey="requirements" />
        <PageHeader
          icon={ClipboardList}
          title="What your business needs"
          description="Worked out from your Business Blueprint. Each item says why it's here and whether Klaros can run it today."
          actions={
            atBlueprintActive ? (
              <Button onClick={handleGenerate} disabled={busy || overview.requirements.length === 0}>
                {busy ? "Working…" : "Continue to recommendations"}
              </Button>
            ) : (
              <Button onClick={() => router.push(overview.journey?.status === "RECOMMENDATIONS_READY" ? "/business/recommendations" : "/business/map")}>
                {overview.journey?.status === "RECOMMENDATIONS_READY" ? "Continue to recommendations" : "View business map"}
              </Button>
            )
          }
        />

        {(error || loadError) && <div className="mb-4"><Alert variant="danger">{error ?? loadError}</Alert></div>}

        {moduleActions.map((a) => (
          <div key={a.id} className="mb-4">
            <Alert
              variant="info"
              action={
                a.kind === "enable_vertical" && a.vertical_key ? (
                  <Button size="sm" disabled={busy} onClick={() => handleEnable(a.vertical_key!)}>
                    Enable
                  </Button>
                ) : undefined
              }
            >
              <strong className="font-medium">{a.title}.</strong> {a.detail}
            </Alert>
          </div>
        ))}

        {overview.requirements.length === 0 ? (
          <EmptyState
            title="Your Blueprint doesn't list any required capabilities yet. Add them to the “Required Capabilities” section."
            action={<Button variant="secondary" onClick={() => router.push("/business/blueprint")}>Open Blueprint</Button>}
          />
        ) : (
          <div className="space-y-8">
            {groups.map(([group, items]) => (
              <section key={group} aria-labelledby={`g-${group}`}>
                <h2 id={`g-${group}`} className="klaros-label mb-3">{group}</h2>
                <ul className="grid gap-3 md:grid-cols-2">
                  {items.map((r) => (
                    <li key={r.key} className="klaros-card flex flex-col p-4">
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground">{r.label}</h3>
                        <span className={r.required ? "rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-hover" : "rounded-full border border-border px-2 py-0.5 text-[11px] text-muted"}>
                          {r.required ? "Required" : "Suggested"}
                        </span>
                      </div>
                      <p className="text-sm text-muted">{r.description}</p>
                      <p className="mt-2 text-sm text-foreground"><span className="klaros-label mr-1.5">Why</span>{r.why}</p>
                      {r.evidence.filter((e) => e.statement && !r.why.includes(e.statement)).slice(0, 1).map((e, i) => (
                        <blockquote key={e.claim_id ?? `${e.kind}-${i}`} className="mt-2 flex gap-1.5 border-l-2 border-accent/50 pl-2 text-xs italic text-muted">
                          <Quote className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />“{e.statement}”
                        </blockquote>
                      ))}
                      <div className="mt-auto pt-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="klaros-label">Status</span>
                          <StatusPill state={r.readiness} />
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">{r.readiness_detail}</p>
                        <p className="mt-1 text-xs text-foreground">
                          <span className="klaros-label mr-1.5">Next</span>
                          {r.next_step.route ? (
                            <Link href={r.next_step.route} className="underline underline-offset-2">{r.next_step.text}</Link>
                          ) : (
                            r.next_step.text
                          )}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
