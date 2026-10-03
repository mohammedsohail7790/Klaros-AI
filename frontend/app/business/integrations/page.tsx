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

const GROUP_LABEL: Record<string, string> = { "CRM & operations": "CRM", Accounting: "Finance" };
const groupLabel = (g: string) => GROUP_LABEL[g] ?? g;

// What each empty category means today — said plainly, with where to look instead.
const EMPTY_GROUP: Record<string, { text: string; href: string; link: string }> = {
  Communication: { text: "No email or messaging provider is catalogued yet. Calls and conversations arrive with the AI workforce once Halla is integrated.", href: "/workforce", link: "AI workforce" },
  Analytics: { text: "No analytics integration yet — Klaros reports on your own leads and operations.", href: "/business/analytics", link: "Analytics" },
  Ecommerce: { text: "No storefront or supplier integration exists yet.", href: "/business/map", link: "Business map" },
};

function purposeLine(i: OpsIntegration): string {
  return i.purpose ?? (i.capabilities.length ? `Covers ${i.capabilities.join(", ")}.` : "");
}

/** What still has to happen before this integration works — stated, never implied. */
function requiredConfiguration(i: OpsIntegration): string {
  switch (i.state) {
    case "CONNECTED":
      return "Nothing to configure.";
    case "AVAILABLE":
      return "Needs your account — you sign in on the connection page.";
    case "CONFIGURATION_REQUIRED":
      return i.last_error ? `The connection needs attention: ${i.last_error}` : "The connection needs attention.";
    case "INTEGRATION_REQUIRED":
      return "Needs the integration to be built — Klaros has defined the contract only.";
    default:
      return "Needs an adapter — none exists yet.";
  }
}

function Card({ i, needed }: { i: OpsIntegration; needed: boolean }) {
  const workforce = i.category === "AI Workforce";
  return (
    <li className={`klaros-card flex flex-col gap-2 p-4 ${i.state === "PLANNED" || i.state === "INTEGRATION_REQUIRED" ? "border-dashed" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{i.name}</h3>
        <StatusPill state={i.state} />
      </div>
      {needed && <p className="text-xs font-medium text-accent-hover">Your business needs this</p>}
      {purposeLine(i) && <p className="text-xs text-muted">{purposeLine(i)}</p>}
      <dl className="grid gap-1 text-xs">
        <div className="flex gap-2"><dt className="shrink-0 text-muted">Connection</dt><dd className="text-foreground">{i.state === "CONNECTED" ? "Connected" : i.state === "AVAILABLE" || i.state === "CONFIGURATION_REQUIRED" ? "Not connected" : "Not possible yet"}</dd></div>
        <div className="flex gap-2"><dt className="shrink-0 text-muted">Needs</dt><dd className="text-foreground">{requiredConfiguration(i)}</dd></div>
      </dl>
      <div className="mt-auto pt-1 text-xs">
        {workforce ? (
          <Link href="/workforce" className="klaros-btn-secondary text-sm">Review setup<span className="sr-only"> for {i.name}</span></Link>
        ) : i.state === "AVAILABLE" || i.state === "CONFIGURATION_REQUIRED" ? (
          <Link href="/settings/integrations" className="klaros-btn-secondary text-sm">{i.state === "AVAILABLE" ? "Connect" : "Fix connection"}<span className="sr-only"> {i.name}</span></Link>
        ) : i.state === "CONNECTED" ? (
          <Link href="/settings/integrations" className="klaros-btn-secondary text-sm">Manage<span className="sr-only"> {i.name}</span></Link>
        ) : (
          <span className="text-muted">Planned — can&apos;t be connected yet.</span>
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
                <ConsoleSection key={g} id={`g-${g}`} title={groupLabel(g)}>
                  {items.length === 0 ? (
                    <p className="klaros-card flex flex-wrap items-center justify-between gap-2 border-dashed p-3 text-sm text-muted">
                      <span>{EMPTY_GROUP[g]?.text ?? "Nothing available yet."}{EMPTY_GROUP[g] && <> <Link href={EMPTY_GROUP[g].href} className="underline underline-offset-2">{EMPTY_GROUP[g].link}</Link></>}</span>
                      <StatusPill state="PLANNED" />
                    </p>
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
