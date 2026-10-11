"use client";

/**
 * Recommendations: what Klaros suggests, organised by the requirement each
 * suggestion serves. Generated server-side (never matched in the browser)
 * by the Recommendation Engine; accepting or rejecting is a pure status
 * write — it never connects a provider, creates an agent or publishes a
 * website. Availability is shown exactly as the catalog states it: only a
 * REAL adapter is "available now"; everything else says an adapter is
 * still required.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ArrowRight, ListChecks } from "lucide-react";
import {
  ApiError,
  BuilderRequirement,
  ReadinessState,
  Recommendation,
  acceptRecommendation,
  listRecommendations,
  rejectRecommendation,
} from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";
import { StageTracker } from "@/components/business/StageTracker";
import { StatusPill } from "@/components/business/StatusPill";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";

const label = (k: string) => k.replace(/[._]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function availability(
  rec: Recommendation,
  provider?: { state: ReadinessState; adapter_required: boolean }
): { state: ReadinessState | null; text: string; next: { text: string; href?: string } | null } {
  if (rec.type === "INTEGRATION") {
    const real = provider ? !provider.adapter_required : rec.provider_implementation_status === "REAL";
    if (!real) {
      return {
        state: "INTEGRATION_REQUIRED",
        text: "Integration adapter required — Klaros can't connect this yet.",
        next: { text: "Nothing to connect today." },
      };
    }
    if (provider?.state === "CONNECTED") return { state: "CONNECTED", text: "Connected.", next: null };
    return {
      state: "AVAILABLE",
      text: "Integration available — you connect your own account.",
      next: { text: "Connect it in Integrations.", href: "/settings/integrations" },
    };
  }
  if (rec.type === "TOOL") return { state: null, text: "Built into Klaros and available now.", next: null };
  return { state: null, text: "", next: null };
}

export default function RecommendationsPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const { overview, error: loadError, reload } = useBuilderOverview(token);
  const [recs, setRecs] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const inFlight = useRef<Set<string>>(new Set());
  const fetchedRecs = useRef(false);

  const loadRecs = useCallback(async () => {
    if (!token || !overview?.journey) return;
    try {
      setRecs(await listRecommendations(token, overview.journey.blueprint_id ? { blueprintId: overview.journey.blueprint_id } : undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't load your recommendations. Please try again.");
    }
  }, [token, overview?.journey]);

  useEffect(() => {
    if (!overview) return;
    const j = overview.journey;
    if (!j) router.replace("/business");
    else if (!isJourneyAtStage(j.status, ["RECOMMENDATIONS_READY", "COMPLETED"])) router.replace(getJourneyDestination(j.status));
    else if (!fetchedRecs.current) {
      fetchedRecs.current = true;
      loadRecs();
    }
  }, [overview, router, loadRecs]);

  // Group by REQUIREMENT, not by the raw key each source happened to use: the
  // backend tells us which recommendation rows belong to which requirement.
  const sections = useMemo(() => {
    if (!recs) return [];
    const reqByRecId = new Map<string, BuilderRequirement>();
    for (const r of overview?.requirements ?? []) for (const id of r.recommendation_ids ?? []) reqByRecId.set(id, r);
    const groups = new Map<string, { requirement?: BuilderRequirement; cap: string; items: Recommendation[] }>();
    for (const rec of recs) {
      if (rec.status === "SUPERSEDED") continue;
      const requirement = reqByRecId.get(rec.id);
      const gk = requirement?.key ?? `raw:${rec.capability_key ?? "other"}`;
      const g = groups.get(gk) ?? { requirement, cap: rec.capability_key ?? "other", items: [] };
      g.items.push(rec);
      groups.set(gk, g);
    }
    const order = new Map((overview?.requirements ?? []).map((r, i) => [r.key, i]));
    return [...groups.entries()]
      .sort(([a], [b]) => (order.get(a) ?? 1e6) - (order.get(b) ?? 1e6))
      .map(([key, g]) => ({
        key,
        requirement: g.requirement,
        cap: g.cap,
        capRecs: g.items.filter((i) => i.type === "CAPABILITY"),
        integrations: g.items.filter((i) => i.type === "INTEGRATION"),
        tools: g.items.filter((i) => i.type === "TOOL"),
      }));
  }, [recs, overview]);

  async function decide(rec: Recommendation, decision: "accept" | "reject") {
    if (!token || inFlight.current.has(rec.id)) return;
    inFlight.current.add(rec.id);
    setBusyId(rec.id);
    setError(null);
    try {
      const updated = decision === "accept" ? await acceptRecommendation(token, rec.id) : await rejectRecommendation(token, rec.id);
      setRecs((prev) => (prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : prev));
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) await loadRecs();
      else setError(err instanceof ApiError ? err.message : "We couldn't save that decision. Please try again.");
    } finally {
      setBusyId(null);
      inFlight.current.delete(rec.id);
    }
  }

  if (!overview || recs === null) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-4xl px-6 py-10">
          {loadError || error ? <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Retry</Button>}>{loadError ?? error}</Alert> : <Skeleton rows={5} />}
        </div>
      </AppShell>
    );
  }

  // What actually needs a decision: ways to cover a need, plus capabilities that are only
  // suggested. Required capabilities are the user's own words — nothing to accept.
  const pending = sections.reduce(
    (n, g) =>
      n +
      g.integrations.filter((i) => i.status === "PROPOSED").length +
      (g.requirement?.required === false ? g.capRecs.filter((c) => c.status === "PROPOSED").length : 0),
    0
  );

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-8">
        <StageTracker stages={overview.stages} activeKey="recommendations" />
        <PageHeader
          icon={ListChecks}
          title="What Klaros recommends"
          description="Grouped by what your business needs. Accepting a suggestion records your choice — nothing is connected or switched on automatically."
          actions={
            <Button onClick={() => router.push("/business/map")} className="gap-2">
              Continue to business map <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          }
        />
        {error && <div className="mb-4"><Alert variant="danger">{error}</Alert></div>}
        {pending > 0 && <p className="mb-4 text-sm text-muted">{pending} still to review — you can also come back to this later.</p>}

        {sections.length === 0 ? (
          <EmptyState title="No recommendations were generated. Your Blueprint may not list any required capabilities yet." />
        ) : (
          <div className="space-y-8">
            {sections.map(({ key, requirement, cap, capRecs, integrations, tools }) => {
              const optionalCap = requirement ? requirement.required === false : capRecs.some((c) => !c.required);
              return (
                <section key={key} aria-labelledby={`rec-${key}`}>
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <h2 id={`rec-${key}`} className="text-base font-semibold text-foreground">{requirement?.label ?? label(cap)}</h2>
                    {requirement && <StatusPill state={requirement.readiness} />}
                    <span className="text-xs text-muted-foreground">{optionalCap ? "Suggested" : "Required"}</span>
                  </div>
                  {requirement && (
                    <p className="mb-3 text-sm text-muted">
                      {requirement.why} {requirement.readiness_detail}
                    </p>
                  )}
                  {optionalCap && capRecs.slice(0, 1).map((c) => (
                    <Card key={c.id} rec={c} busy={busyId === c.id} onDecide={decide} requirement={requirement} />
                  ))}
                  {integrations.length > 0 && (
                    <div className="mt-3 border-l-2 border-border pl-4">
                      <p className="klaros-label mb-2">Integrations that could cover it</p>
                      <div className="space-y-3">
                        {integrations.map((o) => (
                          <Card
                            key={o.id}
                            rec={o}
                            busy={busyId === o.id}
                            onDecide={decide}
                            provider={requirement?.providers.find((p) => p.provider_key === o.provider_key)}
                            requirement={requirement}
                            allProviders={requirement?.providers}
                          />
                        ))}
                      </div>
                    </div>
                  )}
                  {integrations.length === 0 && requirement && requirement.readiness === "PLANNED" && (
                    <p className="mt-2 text-sm text-muted">No integration is available for this yet.</p>
                  )}
                  {tools.length > 0 && (
                    <details className="mt-3 text-sm">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {tools.length} related Klaros tool{tools.length === 1 ? "" : "s"} (matched by name only — check they fit)
                      </summary>
                      <ul className="mt-2 space-y-1 pl-4 text-xs text-muted">
                        {tools.map((t) => (
                          <li key={t.id}>
                            <span className="font-mono">{t.tool_name}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </div>
    </AppShell>
  );
}

type ProviderView = { provider_key: string; display_name: string; state: ReadinessState; adapter_required: boolean };

function Card({
  rec,
  busy,
  onDecide,
  provider,
  requirement,
  allProviders,
}: {
  rec: Recommendation;
  busy: boolean;
  onDecide: (r: Recommendation, d: "accept" | "reject") => void;
  provider?: ProviderView;
  requirement?: BuilderRequirement;
  allProviders?: ProviderView[];
}) {
  const decided = rec.status !== "PROPOSED";
  const avail = availability(rec, provider);
  const forWhat = requirement?.label ?? label(rec.capability_key ?? "this need");
  const isIntegration = rec.type === "INTEGRATION";
  // Plain-language WHAT / WHY — never the engine's internal wording.
  const name = isIntegration ? provider?.display_name ?? label(rec.provider_key ?? "this integration") : null;
  const what = isIntegration ? `${name} — covers “${forWhat}”` : `Include “${forWhat}” in your business`;
  const why = isIntegration
    ? `Your business needs ${forWhat.toLowerCase()}, and ${name} is a supported way to provide it.`
    : requirement?.description ?? "";
  const altNames = (Array.isArray(rec.alternatives) ? (rec.alternatives as unknown[]).map(String) : []).map(
    (k) => allProviders?.find((p) => p.provider_key === k)?.display_name ?? label(k)
  );
  return (
    <div className="klaros-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium text-foreground">{what}</p>
        <span className={rec.status === "ACCEPTED" ? "text-xs font-medium text-success" : rec.status === "REJECTED" ? "text-xs text-muted-foreground" : "sr-only"}>
          {rec.status === "ACCEPTED" ? "Accepted" : rec.status === "REJECTED" ? "Not for me" : "Awaiting your decision"}
        </span>
      </div>
      {why && <p className="mt-1 text-sm text-muted"><span className="klaros-label mr-1.5">Why</span>{why}</p>}
      {avail.text && (
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <span className="klaros-label">Status</span>
          <StatusPill state={avail.state} />
          <p className="text-xs text-foreground">{avail.text}</p>
        </div>
      )}
      {avail.next && (
        <p className="mt-1 text-xs text-muted">
          <span className="klaros-label mr-1.5">Next</span>
          {avail.next.href ? <Link href={avail.next.href} className="underline underline-offset-2">{avail.next.text}</Link> : avail.next.text}
        </p>
      )}
      {altNames.length > 0 && <p className="mt-1 text-xs text-muted"><span className="klaros-label mr-1.5">Alternatives</span>{altNames.join(", ")}</p>}
      {!decided && (
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={busy} onClick={() => onDecide(rec, "accept")}>{busy ? "Saving…" : "Accept"}<span className="sr-only"> {what}</span></Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecide(rec, "reject")}>Not for me<span className="sr-only"> — {what}</span></Button>
        </div>
      )}
    </div>
  );
}
