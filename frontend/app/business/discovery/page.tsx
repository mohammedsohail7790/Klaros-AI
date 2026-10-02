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
import { Check, MessageCircleQuestion } from "lucide-react";
import {
  ApiError,
  BusinessJourney,
  DiscoverySession,
  DiscoveryTurn,
  answerDiscoveryQuestion,
  completeDiscoveryJourneyStep,
  finishDiscoverySession,
  getCurrentBusinessJourney,
  getDiscoverySession,
} from "@/lib/api";
import { getJourneyDestination, isJourneyAtStage } from "@/lib/businessJourneyController";
import { StageTracker } from "@/components/business/StageTracker";
import type { BuilderStage } from "@/lib/api";

// Discovery runs before the backend has a Blueprint to derive the full tracker
// from, so the early part of the journey is a fixed, static strip.
const DISCOVERY_STAGES: BuilderStage[] = [
  { key: "idea", label: "Idea", state: "done", route: "/business" },
  { key: "discovery", label: "Discovery", state: "current", route: "/business/discovery" },
  { key: "blueprint", label: "Blueprint", state: "todo", route: "/business/blueprint" },
  { key: "requirements", label: "Requirements", state: "todo", route: "/business/requirements" },
  { key: "recommendations", label: "Recommendations", state: "todo", route: "/business/recommendations" },
  { key: "map", label: "Business Map", state: "todo", route: "/business/map" },
  { key: "website", label: "Website", state: "todo", route: "/website" },
  { key: "launch", label: "Launch", state: "todo", route: "/business/home" },
];

export default function DiscoveryPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [journey, setJourney] = useState<BusinessJourney | null | undefined>(undefined);
  const [session, setSession] = useState<DiscoverySession | null>(null);
  const [turns, setTurns] = useState<DiscoveryTurn[]>([]);
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
        const { session: s, turns: t } = await getDiscoverySession(token, j.discovery_session_id);
        setSession(s);
        setTurns(t);
        if (s.status === "COMPLETED") {
          // The Discovery session already reached COMPLETED server-side —
          // this happens on a refresh/direct navigation after the final
          // answer, or if the in-flight advance after that answer was
          // interrupted (tab closed, network error, etc). The journey
          // itself is still DISCOVERY_ACTIVE (we only got here because the
          // earlier stage guard let us render this page), so drive the
          // same server-authoritative handoff used right after the last
          // answer is submitted: call complete-discovery, refresh the
          // journey, and navigate on. This must never be skipped — a
          // COMPLETED session with no further action is exactly the "stuck
          // on Loading your next question..." bug.
          setQuestion(null);
          await advanceToBlueprint(j);
          return;
        }
        // The backend stores the currently-pending question on the LATEST
        // turn's `question` field alongside that same turn's already-filled
        // `answer` (the answer to the PREVIOUS question) — a turn with
        // `question` set and `answer` unset never exists in this data model
        // (see BusinessDiscoveryService._process_turn/submit_answer). The
        // most recent turn (turns are returned in ascending sequence order)
        // is therefore always the source of truth for "what to ask next."
        const latestTurn = t[t.length - 1];
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
        const { session: s, turns: t } = await getDiscoverySession(token, journey.discovery_session_id);
        setSession(s);
        setTurns(t);
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

  async function handleFinishEarly() {
    if (!token || !journey?.discovery_session_id || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await finishDiscoverySession(token, journey.discovery_session_id);
      setQuestion(null);
      await advanceToBlueprint();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't finish just now. Please try again.");
    } finally {
      setBusy(false);
      submitting.current = false;
    }
  }

  async function advanceToBlueprint(journeyOverride?: BusinessJourney) {
    // Accepts an explicit journey rather than only reading the `journey`
    // state variable: when called from `load()` right after `setJourney(j)`,
    // the state update has not yet committed (React batches it to the next
    // render), so the closed-over `journey` would still be the stale
    // pre-load value (often `undefined` on first load) and this would
    // silently no-op — the exact shape of the "stuck on Loading your next
    // question..." bug. Callers that already have a fresh journey object
    // (namely `load()`) must pass it explicitly.
    const activeJourney = journeyOverride ?? journey;
    if (!token || !activeJourney) return;
    setAdvancing(true);
    setError(null);
    try {
      const updated = await completeDiscoveryJourneyStep(token, activeJourney.id);
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

  // Everything the user has already told us, oldest first: the opening idea
  // plus each answered question. Read-only here — anything that needs
  // correcting can be edited on the Blueprint screen next, before it's final.
  // A turn stores the answer to the PREVIOUS turn's question (the turn's own
  // `question` is the next one asked), so each answer is paired with the
  // question on the turn before it. Turn 0 is the opening idea, shown above.
  const answered = turns
    .map((t, i) => ({ id: t.id, question: i > 0 ? turns[i - 1].question : null, answer: t.answer }))
    .filter((_, i) => i > 0)
    .filter((t) => t.answer && t.answer.trim());
  const asked = session?.questions_asked ?? 0;
  const max = session?.max_questions ?? 0;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-2xl px-6 py-8">
        <StageTracker stages={DISCOVERY_STAGES} activeKey="discovery" />
        <PageHeader
          icon={MessageCircleQuestion}
          title="Tell us about your business"
          description="A few quick questions so Klaros understands what you're building. Plain answers are fine."
        />

        {session?.business_idea && (
          <div className="mb-4 rounded-lg border border-border bg-surface-muted px-4 py-3 text-sm">
            <p className="klaros-label mb-0.5">Your idea</p>
            <p className="text-foreground">{session.business_idea}</p>
          </div>
        )}

        {error && (
          <div className="mb-4">
            <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>{error}</Alert>
          </div>
        )}

        <div className="klaros-card space-y-4 p-5 sm:p-6">
          {advancing ? (
            <p className="text-sm text-muted" aria-live="polite">
              That's everything we need — pulling your business together…
            </p>
          ) : question ? (
            <>
              {max > 0 && (
                <div aria-live="polite">
                  <p className="text-xs text-muted-foreground">
                    Question {Math.min(asked + 1, max)} — at most {max} in total
                  </p>
                  <div
                    className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-muted"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={max}
                    aria-valuenow={asked}
                    aria-label="Discovery progress"
                  >
                    <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.min(100, (asked / max) * 100)}%` }} />
                  </div>
                </div>
              )}
              <label htmlFor="discovery-answer" className="block text-base font-medium text-foreground">{question}</label>
              <textarea
                id="discovery-answer"
                className="klaros-input min-h-[110px]"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Type your answer…"
                disabled={busy}
              />
              <Button onClick={handleSubmitAnswer} disabled={busy || !answer.trim()} className="w-full sm:w-auto">
                {busy ? "Saving…" : "Continue"}
              </Button>
              <p className="text-xs text-muted-foreground">
                Your progress is saved — you can leave and come back any time. You'll be able to review and correct everything before it's final.
              </p>
            </>
          ) : (
            <div className="space-y-3" aria-live="polite">
              <p className="text-sm text-muted">
                {answered.length > 0
                  ? "We don't have another question ready. If you've told us enough, you can move on and fill in or correct anything on the next screen."
                  : "Getting your next question ready…"}
              </p>
              {answered.length > 0 && (
                <Button onClick={handleFinishEarly} disabled={busy}>
                  {busy ? "Working…" : "That's enough — build my Blueprint"}
                </Button>
              )}
            </div>
          )}
          {question && !advancing && answered.length > 0 && (
            <button
              type="button"
              onClick={handleFinishEarly}
              disabled={busy}
              className="text-sm text-muted underline underline-offset-2 hover:text-foreground disabled:opacity-50"
            >
              That's enough — build my Blueprint
            </button>
          )}
        </div>

        {answered.length > 0 && (
          <section className="mt-8" aria-labelledby="answers-so-far">
            <h2 id="answers-so-far" className="klaros-label mb-3">What you've told us so far</h2>
            <ol className="space-y-3">
              {answered.map((t) => (
                <li key={t.id} className="flex gap-3 text-sm">
                  <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                  <div className="min-w-0">
                    {t.question && <p className="text-muted-foreground">{t.question}</p>}
                    <p className="text-foreground">{t.answer}</p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
    </AppShell>
  );
}
