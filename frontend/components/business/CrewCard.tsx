"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot } from "lucide-react";
import { ApiError, OperatingCrew, getOperatingCrew, provisionOperatingCrew } from "@/lib/api";
import { ProgressRing } from "@/components/ui/layout";
import { Button } from "@/components/ui/Button";

/**
 * Autopilot: the standing AI crew that runs this business from its playbook. It shows who is on the team and what starts each of them,
 * and one button sets up whoever is missing. Every action they take still goes through the same approvals, audit log and AI kill switch.
 */
export function CrewCard({ token }: { token: string | null }) {
  const [crew, setCrew] = useState<OperatingCrew | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setCrew(await getOperatingCrew(token));
    } catch {
      setCrew(null); // the card simply stays hidden if the crew cannot be read
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function turnOn() {
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await provisionOperatingCrew(token);
      if (Object.keys(r.failed).length > 0) setError(`Couldn't set up: ${Object.keys(r.failed).join(", ")}.`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "We couldn't turn autopilot on just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }

  if (!crew) return null;
  const allOn = crew.total > 0 && crew.running === crew.total;
  return (
    <section aria-label="Autopilot" className="mb-12 overflow-hidden rounded-3xl bg-surface-muted">
      <div className="flex items-center gap-5 px-6 py-6">
        <ProgressRing done={crew.running} total={crew.total} />
        <div className="min-w-0">
          <h2 className="font-display text-xl font-semibold tracking-tight text-foreground">Autopilot</h2>
          <p className="mt-0.5 text-[15px] leading-snug text-muted">
            {allOn ? "Your AI crew is running the business." : `${crew.running} of ${crew.total} crew members are running.`}
          </p>
        </div>
      </div>
      <ul className="divide-y divide-border/70 border-t border-border/70" aria-label="Crew">
        {crew.members.map((m) => (
          <li key={m.key} className="flex items-start gap-3 px-6 py-3.5">
            <span className={`mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${m.running ? "bg-success/10 text-success" : "bg-surface text-muted-foreground"}`}>
              <Bot className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[15px] font-medium text-foreground">{m.name}</p>
              <p className="text-[13px] leading-snug text-muted">{m.purpose}</p>
              <p className="mt-0.5 text-[12px] text-muted-foreground">{m.trigger}</p>
            </div>
            <span className={`shrink-0 text-[12px] font-medium ${m.running ? "text-success" : "text-muted-foreground"}`}>{m.running ? "Running" : "Not set up"}</span>
          </li>
        ))}
      </ul>
      {!allOn && (
        <div className="border-t border-border/70 px-6 py-5">
          {error && <p className="mb-3 text-[14px] text-danger">{error}</p>}
          <Button onClick={turnOn} disabled={busy}>
            {busy ? "Setting up…" : "Turn on autopilot"}
          </Button>
          <p className="mt-3 text-[13px] leading-snug text-muted-foreground">
            Each member acts only through Klaros&apos;s governed tools. Actions that need approval still wait for you, and you can pause all AI at any time.
          </p>
        </div>
      )}
    </section>
  );
}
