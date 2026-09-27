"use client";

/**
 * Phase 14: Recommendations experience.
 *
 * Recommendations are generated server-side by the Phase 3 Recommendation
 * Engine, triggered only via the Phase 13 journey action
 * `generate-recommendations` (never recomputed or matched in the
 * browser). This page lists the tenant's actual Recommendation rows for
 * the confirmed blueprint and lets the user Accept/Reject each one — a
 * pure status write, never an execution: accepting a recommendation here
 * never connects a provider, creates an agent, or publishes a website.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ListChecks } from "lucide-react";
import {
  ApiError,
  BusinessJourney,
  Recommendation,
  acceptRecommendation,
  completeBusinessJourney,
  getCurrentBusinessJourney,
  listRecommendations,
  rejectRecommendation,
} from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";

export default function RecommendationsPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [journey, setJourney] = useState<BusinessJourney | null | undefined>(undefined);
  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const decisionInFlight = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const j = await getCurrentBusinessJourney(token);
      if (!j) {
        router.replace("/business");
        return;
      }
      if (!isJourneyAtStage(j.status, ["RECOMMENDATIONS_READY"])) {
        router.replace(getJourneyDestination(j.status));
        return;
      }
      setJourney(j);
      const recs = await listRecommendations(token, j.blueprint_id ? { blueprintId: j.blueprint_id } : undefined);
      setRecommendations(recs);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Unable to load recommendations.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDecision(rec: Recommendation, decision: "accept" | "reject") {
    if (!token || decisionInFlight.current.has(rec.id)) return;
    decisionInFlight.current.add(rec.id);
    setBusyId(rec.id);
    setError(null);
    try {
      const updated = decision === "accept" ? await acceptRecommendation(token, rec.id) : await rejectRecommendation(token, rec.id);
      setRecommendations((prev) => (prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : prev));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        await load();
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to update this recommendation.");
      }
    } finally {
      setBusyId(null);
      decisionInFlight.current.delete(rec.id);
    }
  }

  async function handleFinish() {
    if (!token || !journey || finishing) return;
    setFinishing(true);
    setError(null);
    try {
      const updated = await completeBusinessJourney(token, journey.id);
      router.replace(getJourneyDestination(updated.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        await load();
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to complete your business journey right now.");
      }
    } finally {
      setFinishing(false);
    }
  }

  if (journey === undefined || recommendations === null) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <Skeleton rows={4} />
        </div>
      </AppShell>
    );
  }

  const decided = recommendations.filter((r) => r.status === "ACCEPTED" || r.status === "REJECTED").length;
  const allDecided = recommendations.length > 0 && decided === recommendations.length;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-3xl px-6 py-10">
        <PageHeader
          icon={ListChecks}
          title="Recommendations"
          description="Based on your business blueprint, here's what Klaros suggests."
          actions={
            <Button onClick={handleFinish} disabled={finishing}>
              {finishing ? "Finishing..." : "Finish setup"}
            </Button>
          }
        />

        {error && (
          <div className="mb-4">
            <Alert variant="danger">{error}</Alert>
          </div>
        )}

        {allDecided && (
          <Alert variant="info" className="mb-4">
            You've reviewed every recommendation. Click &ldquo;Finish setup&rdquo; when you're ready to complete your
            business foundation.
          </Alert>
        )}

        {recommendations.length === 0 ? (
          <EmptyState title="No recommendations were generated for your business yet." />
        ) : (
          <div className="space-y-4">
            {recommendations.map((rec) => (
              <RecommendationCard key={rec.id} rec={rec} busy={busyId === rec.id} onDecision={handleDecision} />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}

function RecommendationCard({
  rec,
  busy,
  onDecision,
}: {
  rec: Recommendation;
  busy: boolean;
  onDecision: (rec: Recommendation, decision: "accept" | "reject") => void;
}) {
  const decided = rec.status !== "PROPOSED";
  return (
    <div className="klaros-card p-5">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge status={rec.required ? undefined : undefined} className={rec.required ? "border-accent/30 bg-accent-soft text-accent" : ""}>
          {rec.required ? "Required" : "Optional"}
        </Badge>
        <Badge status={rec.status}>{rec.status}</Badge>
        <span className="text-xs text-muted-foreground">{rec.type}</span>
      </div>

      <h2 className="text-base font-semibold text-foreground">{rec.what}</h2>
      <p className="mt-1 text-sm text-muted">{rec.why}</p>

      <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Dependencies</dt>
          <dd className="text-foreground">
            {rec.dependencies && rec.dependencies.length > 0 ? rec.dependencies.join(", ") : "None"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Cost</dt>
          <dd className="text-foreground">
            {rec.cost_estimate != null ? JSON.stringify(rec.cost_estimate) : "Cost estimate unavailable"}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Confidence</dt>
          <dd className="text-foreground">{rec.confidence != null ? rec.confidence : "Not provided"}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Source</dt>
          <dd className="text-foreground">{rec.source}</dd>
        </div>
        {rec.alternatives != null && (
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Alternatives</dt>
            <dd className="text-foreground">{JSON.stringify(rec.alternatives)}</dd>
          </div>
        )}
      </dl>

      {!decided && (
        <div className="mt-4 flex gap-2">
          <Button size="sm" disabled={busy} onClick={() => onDecision(rec, "accept")}>
            {busy ? "Saving..." : "Accept"}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onDecision(rec, "reject")}>
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}
