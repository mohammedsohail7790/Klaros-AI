"use client";

/**
 * Business Builder entry — "What are you building?"
 *
 * Loads the tenant's current BusinessJourney (the frontend's source of
 * truth) and either resumes an active one at its authoritative stage
 * (never re-deriving that mapping — see lib/businessJourneyController.ts),
 * sends a finished one to the operating home, or — if there is none —
 * asks the one question the whole product starts from. The example ideas
 * are only shortcuts that fill the text box; they carry no logic.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { ArrowRight, Sparkles } from "lucide-react";
import { ApiError, BusinessJourney, getCurrentBusinessJourney, listBusinessJourneys, startBusinessJourney } from "@/lib/api";
import { getJourneyDestination } from "@/lib/businessJourneyController";

const EXAMPLES = [
  "I want to build a medical tourism company connecting international patients with hospitals in India.",
  "I want to start a dropshipping business using a supplier catalog.",
  "I want to build an online consulting business.",
];

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
      let found = await getCurrentBusinessJourney(token);
      if (!found) {
        // "Current" excludes finished journeys: a business that is already set up goes to its
        // operating home, not back to a blank "What are you building?" form.
        const latest = (await listBusinessJourneys(token))?.[0];
        if (latest?.status === "COMPLETED") found = latest;
      }
      // An abandoned journey is over: the visitor starts a fresh one here
      // (redirecting it to "/business" would just loop back to this page).
      const j = found && found.status !== "ABANDONED" ? found : null;
      setJourney(j);
      if (j) router.replace(getJourneyDestination(j.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "We couldn't check your business just now. Please try again.");
      setJourney(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  // An idea typed on the marketing site travels here through sign-up.
  useEffect(() => {
    try {
      const pending = sessionStorage.getItem("klaros_pending_idea");
      if (pending) {
        setBusinessIdea((cur) => cur || pending);
        sessionStorage.removeItem("klaros_pending_idea");
      }
    } catch {
      // storage unavailable — nothing to carry over
    }
  }, []);

  async function handleStart() {
    if (!token || busy || !businessIdea.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const j = await startBusinessJourney(token, businessIdea.trim());
      router.replace(getJourneyDestination(j.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("You don't have permission to start a business. Ask an owner or admin to do this.");
      } else {
        setError(err instanceof ApiError ? err.message : "We couldn't start your business just now. Please try again.");
      }
      setBusy(false);
    }
  }

  // Resuming/redirecting: show a skeleton rather than flashing the form.
  if (journey === undefined || journey) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-2xl px-6 py-12">
          <Skeleton rows={3} />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-2xl px-6 py-10 sm:py-16">
        <div className="mb-8 text-center">
          <span className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <Sparkles className="h-5 w-5" strokeWidth={2} aria-hidden="true" />
          </span>
          <h1 className="font-display text-3xl text-foreground sm:text-4xl">What are you building?</h1>
          <p className="mx-auto mt-3 max-w-md text-sm text-muted sm:text-base">
            Describe your business in your own words. Klaros will ask a few questions, work out what the business needs, and help you
            build it.
          </p>
        </div>

        <div className="klaros-card space-y-4 p-5 sm:p-6">
          {error && <Alert variant="danger">{error}</Alert>}
          <label htmlFor="business-idea" className="klaros-label">
            Your business idea
          </label>
          <textarea
            id="business-idea"
            className="klaros-input min-h-[140px]"
            value={businessIdea}
            onChange={(e) => setBusinessIdea(e.target.value)}
            placeholder="Tell us what you want to build, who it's for, and how it makes money."
            disabled={busy}
            maxLength={4000}
          />
          <Button onClick={handleStart} disabled={busy || !businessIdea.trim()} className="w-full gap-2 sm:w-auto">
            {busy ? "Starting…" : "Start building"}
            {!busy && <ArrowRight className="h-4 w-4" aria-hidden="true" />}
          </Button>
        </div>

        <div className="mt-6">
          <p className="klaros-label mb-2">Need a nudge? Try one of these</p>
          <ul className="flex flex-col gap-2">
            {EXAMPLES.map((ex) => (
              <li key={ex}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setBusinessIdea(ex)}
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-left text-sm text-muted transition-colors hover:border-accent hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {ex}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </AppShell>
  );
}
