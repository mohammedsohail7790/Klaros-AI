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
        <div className="px-8 py-8">
          <Skeleton rows={4} stats={0} />
        </div>
      </AppShell>
    );
  }

  if (agents === null) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-4xl px-6 py-10">
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
      <div className="mx-auto max-w-4xl px-6 py-10">
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
          <div className="mb-4 relative max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" strokeWidth={2} />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search agents..."
              aria-label="Search agents"
              className="klaros-input pl-8"
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
          <div className="klaros-card divide-y divide-border">
            {filtered.map((agent) => (
              <button
                key={agent.id}
                type="button"
                onClick={() => router.push(`/agents/${agent.id}`)}
                className="flex w-full items-center justify-between gap-4 px-4 py-3.5 text-left hover:bg-surface-muted"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{agent.name}</span>
                    <Badge status={agent.status}>{agent.status}</Badge>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted">
                    {agent.purpose || "No description provided."}
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1 text-right">
                  <span className="text-xs text-muted-foreground">
                    {AUTONOMY_LABELS[agent.autonomy_tier] ?? agent.autonomy_tier}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {agent.current_version_id ? "Has published version" : "No published version"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
