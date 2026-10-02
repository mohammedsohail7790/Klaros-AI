"use client";

/**
 * Workflows — how a lead is handled, and the automations behind it. This is a view of the
 * EXISTING automation engine (events → conditions → governed actions), not a second one.
 * Only actions that really execute can be added here; anything that needs an external
 * integration is listed as "Integration required" and never pretends to run.
 */

import { useState } from "react";
import Link from "next/link";
import { Workflow } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, createStarterWorkflow } from "@/lib/api";
import { useOperations } from "@/components/business/useOperations";
import { ConsoleSection, LeadPipeline, WorkflowList } from "@/components/business/console";
import { StatusPill } from "@/components/business/StatusPill";

const NEEDS_INTEGRATION = ["Send a WhatsApp message", "Call the patient or customer", "Book on a connected calendar", "Send an email sequence"];

export default function WorkflowsPage() {
  const { token, user } = useAuth();
  const { ops, error, reload } = useOperations(token);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);

  async function addStarter() {
    if (!token || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const r = await createStarterWorkflow(token);
      setCreated(r.created);
      await reload();
    } catch (err) {
      setActionError(
        err instanceof ApiError && err.status === 403
          ? "You don't have permission to create workflows."
          : err instanceof ApiError
            ? err.message
            : "We couldn't create the workflow just now. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-4xl px-6 py-8">
        <PageHeader icon={Workflow} title="Workflows" description="How an enquiry becomes a customer — which steps are built in, which are automated, and which your team does." />
        {(error || actionError) && (
          <div className="mb-4"><Alert variant="danger" action={error ? <Button size="sm" variant="secondary" onClick={() => reload()}>Try again</Button> : undefined}>{actionError ?? error}</Alert></div>
        )}
        {created && <div className="mb-4"><Alert variant="success">The “New lead alert” workflow is created and running.</Alert></div>}
        {!ops && !error && <Skeleton rows={5} />}
        {ops && (
          <>
            <ConsoleSection id="how" title="How a lead is handled" hint="The real path an enquiry takes today.">
              <LeadPipeline stages={ops.lead_pipeline} />
            </ConsoleSection>

            <ConsoleSection
              id="mine"
              title="Your workflows"
              hint="Automations that run on their own when something happens."
              action={
                !ops.starter_workflow_exists ? (
                  <Button size="sm" onClick={addStarter} disabled={busy}>{busy ? "Creating…" : "Add “New lead alert”"}</Button>
                ) : undefined
              }
            >
              {ops.workflows.length === 0 && (
                <p className="mb-3 text-sm text-muted">
                  You have no workflows yet. The starter workflow notifies your team the moment a lead arrives — it uses the existing automation engine and runs for real.
                </p>
              )}
              <WorkflowList workflows={ops.workflows} />
              <p className="mt-4 text-sm text-muted">
                Need something more specific? Build it in the <Link href="/automations" className="underline underline-offset-2">full workflow builder</Link>. Each step runs through Klaros's normal permission checks and audit trail.
              </p>
            </ConsoleSection>

            <ConsoleSection id="later" title="Not available yet" hint="These need an external integration, so a workflow cannot do them today.">
              <ul className="grid gap-2 sm:grid-cols-2">
                {NEEDS_INTEGRATION.map((a) => (
                  <li key={a} className="klaros-card flex items-center justify-between gap-2 border-dashed p-3 text-sm text-muted">
                    {a}
                    <StatusPill state="INTEGRATION_REQUIRED" />
                  </li>
                ))}
              </ul>
            </ConsoleSection>
          </>
        )}
      </div>
    </AppShell>
  );
}
