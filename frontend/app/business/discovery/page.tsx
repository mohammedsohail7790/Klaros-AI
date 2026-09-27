"use client";

/**
 * Phase 14: Discovery experience.
 *
 * Built entirely around the existing Phase 2 Discovery API
 * (app/api/v1/business_discovery.py) via the journey's
 * `discovery_session_id` — this page never generates questions itself and
 * never decides when Discovery is "done"; it just renders whatever
 * question/turn state the backend currently reports and submits answers.
 *
 * Completion is a two-step, server-authoritative handoff: the Discovery
 * session reaching COMPLETED does NOT by itself move the journey to
 * Blueprint review — this page then calls the named Phase 13 action
 * `complete-discovery`, refetches the journey, and only then routes on.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { MessageCircleQuestion } from "lucide-react";
import {
  ApiError,
  BusinessJourney,
  DiscoverySession,
  answerDiscoveryQuestion,
  completeDiscoveryJourneyStep,
  getCurrentBusinessJourney,
  getDiscoverySession,
} from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";

export default function DiscoveryPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [journey, setJourney] = useState<BusinessJourney | null | undefined>(undefined);
  const [session, setSession] = useState<DiscoverySession | null>(null);
  const [question, setQuestion] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  // Guards against a double-click firing two in-flight submissions.
  const submitting = useRef(false);

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const j = await getCurrentBusinessJourney(token);
      if (!j) {
        router.replace("/business");
        return;
      }
      // Direct-URL / stale-state guard: only DISCOVERY_ACTIVE renders here.
      if (!isJourneyAtStage(j.status, ["DISCOVERY_ACTIVE"])) {
        router.replace(getJourneyDestination(j.status));
        return;
      }
      setJourney(j);
      if (j.discovery_session_id) {
        const { session: s, turns } = await getDiscoverySession(token, j.discovery_session_id);
        setSession(s);
        // The backend stores the currently-pending question on the LATEST
        // turn's `question` field alongside that same turn's already-filled
        // `answer` (the answer to the PREVIOUS question) — a turn with
        // `question` set and `answer` unset never exists in this data model
        // (see BusinessDiscoveryService._process_turn/submit_answer). The
        // most recent turn (turns are returned in ascending sequence order)
        // is therefore always the source of truth for "what to ask next."
        const latestTurn = turns[turns.length - 1];
        setQuestion(s.status === "ACTIVE" ? latestTurn?.question ?? null : null);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Unable to load Discovery.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSubmitAnswer() {
    if (!token || !journey?.discovery_session_id || submitting.current || !answer.trim()) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await answerDiscoveryQuestion(token, journey.discovery_session_id, answer.trim());
      setAnswer("");
      if (result.session_status === "COMPLETED") {
        setQuestion(null);
        await advanceToBlueprint();
      } else {
        setQuestion(result.next_question);
        // Reconcile the session's own counters from the server rather than
        // incrementing local state.
        const { session: s } = await getDiscoverySession(token, journey.discovery_session_id);
        setSession(s);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Session already completed elsewhere — reconcile against the
        // backend rather than getting stuck.
        await load();
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to submit your answer. Please try again.");
      }
    } finally {
      setBusy(false);
      submitting.current = false;
    }
  }

  async function advanceToBlueprint() {
    if (!token || !journey) return;
    setAdvancing(true);
    setError(null);
    try {
      const updated = await completeDiscoveryJourneyStep(token, journey.id);
      router.replace(getJourneyDestination(updated.status));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Idempotent/stale-transition — reconcile with the current journey.
        await load();
      } else {
        setError(err instanceof ApiError ? err.message : "Discovery finished, but we couldn't advance to your Blueprint yet.");
      }
    } finally {
      setAdvancing(false);
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

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-2xl px-6 py-10">
        <PageHeader
          icon={MessageCircleQuestion}
          title="Discovery"
          description="Answer a few questions so Klaros can build your business profile."
        />

        {error && (
          <div className="mb-4">
            <Alert variant="danger">{error}</Alert>
          </div>
        )}

        <div className="klaros-card space-y-4 p-6">
          {advancing ? (
            <p className="text-sm text-muted" aria-live="polite">
              Discovery complete — building your Business Blueprint...
            </p>
          ) : question ? (
            <>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground" aria-live="polite">
                Building your business profile{session ? ` — ${session.questions_asked} question${session.questions_asked === 1 ? "" : "s"} answered so far` : ""}
              </p>
              <p className="text-base font-medium text-foreground">{question}</p>
              <textarea
                className="klaros-input min-h-[100px]"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Your answer..."
                disabled={busy}
                aria-label="Your answer"
              />
              <Button onClick={handleSubmitAnswer} disabled={busy || !answer.trim()}>
                {busy ? "Saving..." : "Continue"}
              </Button>
              <p className="text-xs text-muted-foreground">
                Your progress is saved automatically — you can leave and come back any time.
              </p>
            </>
          ) : (
            <p className="text-sm text-muted" aria-live="polite">
              Loading your next question...
            </p>
          )}
        </div>
      </div>
    </AppShell>
  );
}
