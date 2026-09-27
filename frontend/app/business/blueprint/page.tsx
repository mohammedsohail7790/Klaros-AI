"use client";

/**
 * Phase 14: Business Blueprint review experience.
 *
 * Renders the actual 20 fixed BlueprintSection keys (app/models/
 * business_blueprint.py's BlueprintSectionKey) returned by the existing
 * Phase 2 Blueprint API — never a re-invented schema, never raw JSON as
 * the primary presentation. Section editing and claim confirm/reject go
 * straight through the existing backend endpoints; nothing is accepted as
 * final until the server confirms it. "Confirm Blueprint" is the explicit
 * human checkpoint and calls the Phase 13 journey action
 * `confirm-blueprint` — never the raw `/business-blueprint/activate`
 * endpoint directly (that stays an internal implementation detail the
 * journey service calls on the server side).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { FileText } from "lucide-react";
import {
  ApiError,
  BLUEPRINT_SECTION_LABELS,
  BlueprintClaim,
  BlueprintSection,
  BusinessJourney,
  FullBlueprintResponse,
  confirmBlueprintClaim,
  confirmBlueprintJourneyStep,
  getActiveBlueprint,
  getCurrentBusinessJourney,
  getDraftBlueprint,
  generateRecommendationsJourneyStep,
  rejectBlueprintClaim,
  updateBlueprintSection,
} from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";

export default function BlueprintPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [journey, setJourney] = useState<BusinessJourney | null | undefined>(undefined);
  const [data, setData] = useState<FullBlueprintResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const confirming = useRef(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const j = await getCurrentBusinessJourney(token);
      if (!j) {
        router.replace("/business");
        return;
      }
      if (!isJourneyAtStage(j.status, ["BLUEPRINT_REVIEW", "BLUEPRINT_ACTIVE"])) {
        router.replace(getJourneyDestination(j.status));
        return;
      }
      setJourney(j);
      const full = j.status === "BLUEPRINT_ACTIVE" ? await getActiveBlueprint(token) : await getDraftBlueprint(token);
      setData(full);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Unable to load your Business Blueprint.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleConfirmBlueprint() {
    if (!token || !journey || confirming.current) return;
    confirming.current = true;
    setBusy(true);
    setError(null);
    try {
      const updated = await confirmBlueprintJourneyStep(token, journey.id);
      router.replace(getJourneyDestination(updated.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Already active / incomplete blueprint / stale — reconcile.
        await load();
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to confirm your Blueprint. Please try again.");
      }
    } finally {
      setBusy(false);
      confirming.current = false;
    }
  }

  async function handleGenerateRecommendations() {
    if (!token || !journey || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await generateRecommendationsJourneyStep(token, journey.id);
      router.replace(getJourneyDestination(updated.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        await load();
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to generate recommendations. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleClaimDecision(claim: BlueprintClaim, decision: "confirm" | "reject") {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (decision === "confirm") await confirmBlueprintClaim(token, claim.id);
      else await rejectBlueprintClaim(token, claim.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update this claim.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSectionSave(section: BlueprintSection, nextData: Record<string, unknown>) {
    if (!token || !data || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateBlueprintSection(token, section.section_key, data.blueprint.id, nextData);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save this section.");
    } finally {
      setBusy(false);
    }
  }

  if (journey === undefined || (journey && !data)) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <Skeleton rows={6} />
        </div>
      </AppShell>
    );
  }

  if (!data || !journey) return null;

  const isDraft = data.blueprint.status === "DRAFT";
  const claimsBySection = data.claims.reduce<Record<string, BlueprintClaim[]>>((acc, c) => {
    (acc[c.section_key] ??= []).push(c);
    return acc;
  }, {});

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          icon={FileText}
          title="Business Blueprint"
          description="Your business, structured from what you told Klaros during Discovery."
          actions={
            isDraft ? (
              <Button onClick={handleConfirmBlueprint} disabled={busy}>
                {busy ? "Confirming..." : "Confirm Blueprint"}
              </Button>
            ) : (
              <Button onClick={handleGenerateRecommendations} disabled={busy}>
                {busy ? "Generating..." : "Generate recommendations"}
              </Button>
            )
          }
        />

        {error && (
          <div className="mb-4">
            <Alert variant="danger">{error}</Alert>
          </div>
        )}

        {!isDraft && (
          <Alert variant="success" className="mb-4">
            Your Business Blueprint is confirmed. Generate recommendations to see what Klaros suggests next.
          </Alert>
        )}

        <div className="space-y-4">
          {data.sections.map((section) => (
            <SectionCard
              key={section.id}
              section={section}
              claims={claimsBySection[section.section_key] ?? []}
              editable={isDraft}
              busy={busy}
              onSave={(next) => handleSectionSave(section, next)}
              onClaimDecision={handleClaimDecision}
            />
          ))}
        </div>
      </div>
    </AppShell>
  );
}

function SectionCard({
  section,
  claims,
  editable,
  busy,
  onSave,
  onClaimDecision,
}: {
  section: BlueprintSection;
  claims: BlueprintClaim[];
  editable: boolean;
  busy: boolean;
  onSave: (data: Record<string, unknown>) => void;
  onClaimDecision: (claim: BlueprintClaim, decision: "confirm" | "reject") => void;
}) {
  const label = BLUEPRINT_SECTION_LABELS[section.section_key as keyof typeof BLUEPRINT_SECTION_LABELS] ?? section.section_key;
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState(() => JSON.stringify(section.data, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const hasData = section.data && Object.keys(section.data).length > 0;
  const proposedClaims = claims.filter((c) => c.status === "PROPOSED");

  function handleSaveClick() {
    try {
      const parsed = JSON.parse(draftText);
      setParseError(null);
      onSave(parsed);
      setEditing(false);
    } catch {
      setParseError("This isn't valid JSON — fix the formatting and try again.");
    }
  }

  return (
    <div className="klaros-card p-5">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-base font-semibold text-foreground">{label}</h2>
        <div className="flex items-center gap-2">
          <Badge status={section.status}>{section.status}</Badge>
          {editable && !editing && (
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
        </div>
      </div>

      {editing ? (
        <div className="space-y-2">
          {parseError && <Alert variant="danger">{parseError}</Alert>}
          <textarea
            className="klaros-input min-h-[120px] font-mono text-xs"
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
            aria-label={`Edit ${label} data`}
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSaveClick} disabled={busy}>
              Save section
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraftText(JSON.stringify(section.data, null, 2));
                setParseError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : hasData ? (
        <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
          {Object.entries(section.data).map(([k, v]) => (
            <div key={k}>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{k}</dt>
              <dd className="text-foreground">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-sm text-muted">Not filled in yet.</p>
      )}

      {proposedClaims.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-border pt-3">
          <p className="klaros-label">Claims to review</p>
          {proposedClaims.map((claim) => (
            <div key={claim.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm">
              <div className="min-w-0">
                <span className="font-medium text-foreground">{claim.key}: </span>
                <span className="text-muted">{typeof claim.value === "object" ? JSON.stringify(claim.value) : String(claim.value)}</span>
                <span className="ml-2 text-xs text-muted-foreground">({claim.claim_type})</span>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => onClaimDecision(claim, "confirm")}>
                  Confirm
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onClaimDecision(claim, "reject")}>
                  Reject
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
