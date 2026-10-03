"use client";

/**
 * The Business Map: "this is how your business operates." Everything shown
 * is derived server-side from the Blueprint, requirements, recommendations
 * and live connection state — see backend business_builder_service.py.
 * Below it, the Next Actions generated from that same state.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ArrowRight, Network } from "lucide-react";
import { ApiError, completeBusinessJourney, enableIndustryModule } from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";
import { StageTracker } from "@/components/business/StageTracker";
import { StatusPill } from "@/components/business/StatusPill";
import { WebsiteActions } from "@/components/business/WebsiteActions";
import { BusinessMap } from "@/components/business/BusinessMap";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";
import { useOperations } from "@/components/business/useOperations";
import { buildMapMetrics } from "@/components/business/homeModel";

export default function BusinessMapPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const { overview, error: loadError, reload } = useBuilderOverview(token);
  const { ops } = useOperations(token);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!overview) return;
    const j = overview.journey;
    if (!j) router.replace("/business");
    else if (!isJourneyAtStage(j.status, ["RECOMMENDATIONS_READY", "COMPLETED"])) router.replace(getJourneyDestination(j.status));
  }, [overview, router]);

  async function handleFinish() {
    if (!token || !overview?.journey || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (overview.journey.status !== "COMPLETED") await completeBusinessJourney(token, overview.journey.id);
      router.push("/website");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't finish setting up just now. Please try again.");
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
        <div className="mx-auto max-w-6xl px-6 py-10">
          {loadError ? <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Retry</Button>}>{loadError}</Alert> : <Skeleton rows={6} />}
        </div>
      </AppShell>
    );
  }

  const completed = overview.journey?.status === "COMPLETED";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-6xl px-6 py-8">
        <StageTracker stages={overview.stages} activeKey="map" />
        <PageHeader
          icon={Network}
          title="How your business operates"
          description="Your customers, the capabilities behind them, and the systems and partners they depend on — all built from your Blueprint."
          actions={
            <Button onClick={handleFinish} disabled={busy || overview.business_map.nodes.length === 0} className="gap-2">
              {busy
                ? "Working…"
                : overview.website.exists
                  ? "Open Website"
                  : "Looks right — generate my website"}
              {!busy && <ArrowRight className="h-4 w-4" aria-hidden="true" />}
            </Button>
          }
        />

        {(error || loadError) && <div className="mb-4"><Alert variant="danger">{error ?? loadError}</Alert></div>}
        {overview.website.published && (
          <div className="mb-4 flex flex-wrap items-center gap-3 text-sm text-muted">
            <span>Your website is live.</span>
            <WebsiteActions website={overview.website} tenantId={user?.tenant_id} />
          </div>
        )}

        {overview.business_map.nodes.length === 0 ? (
          <EmptyState title="There's nothing to map yet — confirm your Blueprint and its required capabilities first." />
        ) : (
          <BusinessMap map={overview.business_map} metrics={buildMapMetrics(overview, ops)} />
        )}

        <section className="mt-10" aria-labelledby="next-actions">
          <h2 id="next-actions" className="font-display text-xl text-foreground">Next actions</h2>
          <p className="mt-1 text-sm text-muted">Generated from the current state of your business — they update as you make progress.</p>
          {overview.next_actions.length === 0 ? (
            <p className="mt-4 text-sm text-muted">Nothing left to do right now.</p>
          ) : (
            <ul className="mt-4 grid gap-3 md:grid-cols-2">
              {overview.next_actions.map((a) => (
                <li key={a.id} className="klaros-card flex flex-col gap-2 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-foreground">{a.title}</h3>
                    <StatusPill state={a.state} />
                  </div>
                  <p className="text-sm text-muted">{a.detail}</p>
                  {a.kind === "enable_vertical" && a.vertical_key ? (
                    <Button size="sm" className="self-start" disabled={busy} onClick={() => handleEnable(a.vertical_key!)}>Enable module</Button>
                  ) : a.route ? (
                    <Link href={a.route} className="klaros-btn-secondary self-start text-sm">Open<span className="sr-only"> {a.title}</span></Link>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AppShell>
  );
}
