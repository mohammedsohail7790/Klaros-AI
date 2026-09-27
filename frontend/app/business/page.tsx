"use client";

/**
 * Phase 14: the Business Journey entry point / resume point.
 *
 * This page is the smallest possible controller: it loads the tenant's
 * current BusinessJourney (Phase 13's `GET /business-journey`, the
 * frontend's source of truth), and either
 *   - shows "Build your business" if there is none yet,
 *   - redirects to the authoritative stage route for an active journey
 *     (never re-deriving that mapping itself — see
 *     lib/businessJourneyController.ts), or
 *   - shows a completion / abandoned summary for a terminal journey.
 *
 * It never infers journey state independently and never mutates status
 * itself except via the named `startBusinessJourney` action.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Input";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { Sparkles } from "lucide-react";
import { ApiError, BusinessJourney, getCurrentBusinessJourney, startBusinessJourney } from "@/lib/api";
import { getJourneyDestination } from "@/lib/businessJourneyController";

export default function BusinessJourneyEntryPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [journey, setJourney] = useState<BusinessJourney | null | undefined>(undefined);
  const [businessIdea, setBusinessIdea] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const j = await getCurrentBusinessJourney(token);
      setJourney(j);
      // Active (non-terminal) journeys never render this page's content —
      // resume them at their authoritative stage immediately.
      if (j && j.status !== "COMPLETED" && j.status !== "ABANDONED") {
        router.replace(getJourneyDestination(j.status));
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Unable to load your business journey.");
      setJourney(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStart() {
    if (!token || busy || !businessIdea.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const j = await startBusinessJourney(token, businessIdea.trim());
      setJourney(j);
      router.replace(getJourneyDestination(j.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("You don't have permission to start a business journey.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to start your business journey. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (journey === undefined) {
    return (
      <AppShell user={user}>
        <div className="px-8 py-8">
          <Skeleton />
        </div>
      </AppShell>
    );
  }

  // Terminal states render inline here rather than redirecting away.
  if (journey && journey.status === "COMPLETED") {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-2xl px-6 py-12">
          <PageHeader icon={Sparkles} title="Your Klaros business foundation is ready" description="Discovery, your Business Blueprint, and recommendations are complete." />
          <Alert variant="success" className="mb-6">
            Completed foundation: Discovery, Business Blueprint confirmation, and recommendation review are done for
            this business.
          </Alert>
          <p className="mb-4 text-sm text-muted">
            Future operating setup — connecting integrations, creating agents, publishing a website — is handled
            elsewhere in Klaros as you're ready for it; nothing was created automatically as part of this process.
          </p>
          <Button variant="secondary" onClick={() => router.push("/dashboard")}>
            Go to dashboard
          </Button>
        </div>
      </AppShell>
    );
  }

  if (journey && journey.status === "ABANDONED") {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-2xl px-6 py-12">
          <PageHeader icon={Sparkles} title="Build your business with Klaros" description="Your previous business journey was abandoned. Start a new one below." />
          <StartForm
            businessIdea={businessIdea}
            setBusinessIdea={setBusinessIdea}
            onStart={handleStart}
            busy={busy}
            error={error}
          />
        </div>
      </AppShell>
    );
  }

  // No journey yet.
  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-2xl px-6 py-12">
        <PageHeader icon={Sparkles} title="Build your business with Klaros" description="Tell us your business idea. Klaros will guide you through Discovery, a Business Blueprint, and tailored recommendations." />
        <StartForm businessIdea={businessIdea} setBusinessIdea={setBusinessIdea} onStart={handleStart} busy={busy} error={error} />
      </div>
    </AppShell>
  );
}

function StartForm({
  businessIdea,
  setBusinessIdea,
  onStart,
  busy,
  error,
}: {
  businessIdea: string;
  setBusinessIdea: (v: string) => void;
  onStart: () => void;
  busy: boolean;
  error: string | null;
}) {
  return (
    <div className="klaros-card space-y-4 p-6">
      {error && <Alert variant="danger">{error}</Alert>}
      <Field label="What's your business idea?">
        <textarea
          className="klaros-input min-h-[120px]"
          value={businessIdea}
          onChange={(e) => setBusinessIdea(e.target.value)}
          placeholder="e.g. A subscription meal-prep service for busy professionals in Austin, TX"
          disabled={busy}
        />
      </Field>
      <Button onClick={onStart} disabled={busy || !businessIdea.trim()}>
        {busy ? "Starting..." : "Start Discovery"}
      </Button>
    </div>
  );
}
