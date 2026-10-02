"use client";

/**
 * Integration Center. Status is derived from the platform's integration catalog and the
 * tenant's own connections: CONNECTED only for a real adapter with a live connection;
 * AVAILABLE for a real adapter you haven't connected; PLANNED / INTEGRATION REQUIRED when no
 * adapter exists. A connect action is offered only where connecting is actually possible.
 */

import Link from "next/link";
import { Plug } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { useBuilderOverview } from "@/components/business/useBuilderOverview";
import { useOperations } from "@/components/business/useOperations";
import { StatusPill } from "@/components/business/StatusPill";
import { ConsoleSection } from "@/components/business/console";
import type { OpsIntegration } from "@/lib/api";

function Card({ i, needed }: { i: OpsIntegration; needed: boolean }) {
  return (
    <li className={`klaros-card flex flex-col gap-2 p-4 ${i.state === "PLANNED" || i.state === "INTEGRATION_REQUIRED" ? "border-dashed" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{i.name}</h3>
        <StatusPill state={i.state} />
      </div>
      {needed && <p className="text-xs font-medium text-accent-hover">Your business needs this</p>}
      {i.purpose && <p className="text-xs text-muted">{i.purpose}</p>}
      {i.capabilities.length > 0 && <p className="text-xs text-muted-foreground">Covers: {i.capabilities.join(", ")}</p>}
      {i.last_error && <p className="text-xs text-danger">Last error: {i.last_error}</p>}
      <div className="mt-auto pt-1 text-xs">
        {i.state === "AVAILABLE" || i.state === "CONFIGURATION_REQUIRED" ? (
          <Link href="/settings/integrations" className="klaros-btn-secondary text-sm">{i.state === "AVAILABLE" ? "Connect" : "Fix connection"}<span className="sr-only"> {i.name}</span></Link>
        ) : i.state === "CONNECTED" ? (
          <Link href="/settings/integrations" className="klaros-btn-secondary text-sm">Manage<span className="sr-only"> {i.name}</span></Link>
        ) : i.state === "INTEGRATION_REQUIRED" ? (
          <span className="text-muted">Integration required — it is a separate platform and no connection exists yet.</span>
        ) : (
          <span className="text-muted">Planned — an adapter is required before this can be connected.</span>
        )}
      </div>
    </li>
  );
}

export default function IntegrationCenterPage() {
  const { token, user } = useAuth();
  const { ops, error, reload } = useOperations(token);
  const { overview } = useBuilderOverview(token);
  const neededKeys = new Set((overview?.requirements ?? []).filter((r) => r.required).flatMap((r) => r.providers.map((p) => p.provider_key)));
  const counts = ops ? { connected: ops.integrations.filter((i) => i.state === "CONNECTED").length, available: ops.integrations.filter((i) => i.state === "AVAILABLE").length } : null;

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-5xl px-6 py-8">
        <PageHeader icon={Plug} title="Integrations" description="What Klaros can connect to, what is connected, and what isn't possible yet." />
        {error && <div className="mb-4"><Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => reload()}>Try again</Button>}>{error}</Alert></div>}
        {!ops && !error && <Skeleton rows={5} />}
        {ops && counts && (
          <>
            <p className="mb-6 text-sm text-muted">{counts.connected} connected · {counts.available} available to connect.</p>
            {ops.integration_groups.map((g) => {
              const items = ops.integrations.filter((i) => i.category === g);
              return (
                <ConsoleSection key={g} id={`g-${g}`} title={g}>
                  {items.length === 0 ? (
                    <p className="klaros-card flex items-center justify-between gap-2 border-dashed p-3 text-sm text-muted">Nothing available yet<StatusPill state="PLANNED" /></p>
                  ) : (
                    <ul className="grid gap-3 md:grid-cols-2">{items.map((i) => <Card key={i.provider_key} i={i} needed={neededKeys.has(i.provider_key)} />)}</ul>
                  )}
                </ConsoleSection>
              );
            })}
          </>
        )}
      </div>
    </AppShell>
  );
}
