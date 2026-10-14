"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Circle, Server } from "lucide-react";
import { ApiError, SetupPlan, activateSetupPlan, getSetupPlan } from "@/lib/api";
import { Button } from "@/components/ui/Button";

/**
 * Getting this business running on Klaros, the same for every business. Klaros starts what it can by itself (the AI crew); the rest is a short list of things
 * only the owner can connect. Each step says what it is for and links to where it is done.
 */
export function SetupPlanCard({ token }: { token: string | null }) {
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setPlan(await getSetupPlan(token));
    } catch {
      setPlan(null); // the card simply stays hidden if the plan cannot be read
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function start() {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      setPlan(await activateSetupPlan(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't start that just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  if (!plan) return null;
  const crewStep = plan.steps.find((s) => s.key === "crew");
  const mine = plan.steps.filter((s) => s.owner === "you");
  const platform = plan.steps.filter((s) => s.owner === "klaros" && s.key !== "crew");
  if (plan.todo === 0 && crewStep?.state === "done" && platform.every((s) => s.state === "done")) return null;
  return (
    <section aria-label="Setup plan" className="mb-12 overflow-hidden rounded-3xl bg-surface-muted">
      <div className="px-6 py-6">
        <h2 className="font-display text-xl font-semibold tracking-tight text-foreground">Getting your business running</h2>
        <p className="mt-0.5 text-[15px] leading-snug text-muted">
          {plan.todo === 0 ? "Everything you need to connect is done." : `${plan.todo} thing${plan.todo === 1 ? "" : "s"} only you can connect. Klaros handles the rest.`}
        </p>
      </div>
      {crewStep && crewStep.state !== "done" && (
        <div className="border-t border-border/70 px-6 py-5">
          <p className="text-[15px] font-medium text-foreground">{crewStep.title}</p>
          <p className="mt-0.5 text-[13px] leading-snug text-muted">{crewStep.why}</p>
          {error && <p className="mt-2 text-[14px] text-danger">{error}</p>}
          <div className="mt-3">
            <Button onClick={start} disabled={busy}>{busy ? "Starting…" : "Start my AI crew"}</Button>
          </div>
        </div>
      )}
      <ul className="divide-y divide-border/70 border-t border-border/70" aria-label="Things to connect">
        {mine.map((s) => (
          <li key={s.key} className="flex items-start gap-3 px-6 py-3.5">
            {s.state === "done" ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" /> : <Circle className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />}
            <div className="min-w-0 flex-1">
              <p className="text-[15px] font-medium text-foreground">{s.title}</p>
              <p className="text-[13px] leading-snug text-muted">{s.why}</p>
            </div>
            {s.state === "todo" && s.href && (
              <Link href={s.href} className="shrink-0 text-[14px] text-accent hover:underline">Connect</Link>
            )}
            {s.state === "done" && <span className="shrink-0 text-[12px] font-medium text-success">Done</span>}
          </li>
        ))}
      </ul>
      {platform.some((s) => s.state === "platform") && (
        <div className="flex items-start gap-3 border-t border-border/70 px-6 py-4">
          <Server className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <p className="text-[13px] leading-snug text-muted">
            {platform.filter((s) => s.state === "platform").map((s) => s.title).join(" and ")} {platform.filter((s) => s.state === "platform").length === 1 ? "isn't" : "aren't"} switched on for Klaros itself yet, so nothing is sent that way. You don&apos;t need to do anything.
          </p>
        </div>
      )}
    </section>
  );
}
