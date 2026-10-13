"use client";

/**
 * Phase 15: Agent list. Renders actual `Agent` rows from the existing,
 * fully-built Agent backend (Phases 4-8) via `GET /api/v1/agents` — no
 * fabricated health/quality/success metrics, since the backend returns
 * none. See PHASE_15_AGENT_CONFIGURATION_FRONTEND_IMPLEMENTATION_LOG.md.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Plus, Search } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Alert } from "@/components/ui/Alert";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { Agent, ApiError, listAgents } from "@/lib/api";

const AUTONOMY_LABELS: Record<string, string> = {
  OBSERVE: "Observe only",
  RECOMMEND: "Recommend only",
  EXECUTE_WITH_APPROVAL: "Executes with approval",
  EXECUTE_AUTONOMOUS: "Executes autonomously",
};

export default function AgentsPage() {
  const { token, user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const rows = await listAgents(token);
      setAgents(rows);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Unable to load agents.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  if (authLoading || (agents === null && !error)) {
    return (
      <AppShell user={user}>
        <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
          <Skeleton rows={4} stats={0} />
        </div>
      </AppShell>
    );
  }

  if (agents === null) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8 sm:py-12">
          <Alert variant="danger">{error}</Alert>
        </div>
      </AppShell>
    );
  }

  const filtered = query.trim()
    ? agents.filter((a) => a.name.toLowerCase().includes(query.trim().toLowerCase()))
    : agents;

  return (
    <AppShell user={user}>
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 sm:py-12">
        <PageHeader
          icon={Bot}
          title="Agents"
          description="Governed, tool-executing agents configured for this business."
          actions={
            <Button onClick={() => router.push("/agents/new")}>
              <Plus className="h-4 w-4" strokeWidth={2} />
              Create Agent
            </Button>
          }
        />

        {error && (
          <div className="mb-4">
            <Alert variant="danger">{error}</Alert>
          </div>
        )}

        {agents.length > 0 && (
          <div className="relative mb-8 max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" strokeWidth={2} />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search agents..."
              aria-label="Search agents"
              className="klaros-input !rounded-full pl-9"
            />
          </div>
        )}

        {agents.length === 0 ? (
          <EmptyState
            icon={Bot}
            title="No agents yet. Create an agent to automate a governed business task."
            action={
              <Button onClick={() => router.push("/agents/new")} size="sm">
                Create Agent
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState compact title={`No agents match "${query}".`} />
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2" aria-label="Agents">
            {filtered.map((agent) => (
              <li key={agent.id}>
                <button
                  type="button"
                  onClick={() => router.push(`/agents/${agent.id}`)}
                  className="group flex h-full w-full flex-col rounded-3xl bg-surface-muted p-6 text-left transition-all duration-500 ease-apple hover:-translate-y-0.5 hover:bg-surface hover:shadow-raised"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="flex h-11 w-11 items-center justify-center rounded-full bg-surface text-accent">
                      <Bot className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
                    </span>
                    <Badge status={agent.status}>{agent.status}</Badge>
                  </div>
                  <span className="mt-5 text-[19px] font-semibold tracking-tight text-foreground">{agent.name}</span>
                  <p className="mt-1.5 flex-1 text-[14px] leading-snug text-muted">{agent.purpose || "No description provided."}</p>
                  <div className="mt-5 flex flex-wrap gap-2">
                    <span className="rounded-full bg-surface px-3 py-1 text-xs font-medium text-muted">{AUTONOMY_LABELS[agent.autonomy_tier] ?? agent.autonomy_tier}</span>
                    <span className={`rounded-full px-3 py-1 text-xs font-medium ${agent.current_version_id ? "bg-success/10 text-success" : "bg-surface text-muted"}`}>
                      {agent.current_version_id ? "Has published version" : "No published version"}
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
