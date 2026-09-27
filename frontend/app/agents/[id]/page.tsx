"use client";

/**
 * Phase 15: Agent detail / configuration page.
 *
 * The backend (Phases 4-8, unchanged here) is the sole authority for
 * validation, authorization, versioning, autonomy enforcement, tool
 * execution, approvals, and tenant isolation. This page only displays
 * state returned by the existing `/api/v1/agents` endpoints, collects
 * configuration, and reconciles whatever the backend hands back — it
 * never computes autonomy/policy/permission decisions itself.
 *
 * Two independent, backend-enforced lifecycles are rendered explicitly so
 * they're never conflated:
 *   - Agent.status: DRAFT -> ACTIVE -> PAUSED -> ARCHIVED (activate/pause/
 *     archive endpoints). Identity/purpose/autonomy are only editable
 *     while DRAFT (backend rule, see agent_service.py::update_draft_agent).
 *   - AgentVersion.status: DRAFT -> PUBLISHED -> DEPRECATED (create/publish
 *     endpoints). A version's executable configuration (instructions,
 *     tool-permission snapshot, limits, triggers) is immutable once
 *     PUBLISHED — a new draft version is required for further change.
 *
 * `AgentToolPermission` is the LIVE, mutable grant table (editable at any
 * time, not gated by version status) — a version only takes a frozen COPY
 * of it at create/publish time. The UI keeps these visually distinct
 * ("Live tool grants" vs. a specific version's immutable snapshot) per the
 * Phase 15 spec's "Tool Permission Snapshot UX" requirement.
 *
 * `AgentToolPermission.constraint_config` is stored but has no execution-
 * time consumer (confirmed against agent_execution_service.py /
 * agent_reasoning_service.py — same finding as PHASE_8_TOOL_IDEMPOTENCY_
 * AUDIT.md) — this page therefore does not expose a constraint editor that
 * would imply enforcement that doesn't exist.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Bot, Play, Rocket, ChevronDown, ChevronRight } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Alert } from "@/components/ui/Alert";
import { Skeleton } from "@/components/ui/Skeleton";
import { PageHeader } from "@/components/ui/PageHeader";
import { Field, Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import {
  Agent,
  AgentExecution,
  AgentExecutionStep,
  AgentToolPermissionGrant,
  AgentVersion,
  ApiError,
  ToolCatalogEntry,
  activateAgent,
  archiveAgent,
  createAgentVersion,
  executeAgent,
  getAgent,
  getToolCatalog,
  grantAgentToolPermission,
  listAgentExecutionSteps,
  listAgentExecutions,
  listAgentToolPermissions,
  listAgentVersions,
  pauseAgent,
  publishAgentVersion,
  revokeAgentToolPermission,
  updateAgent,
} from "@/lib/api";

const AUTONOMY_OPTIONS: { value: string; label: string; note: string }[] = [
  { value: "OBSERVE", label: "Observe only", note: "Cannot call any tool through this system." },
  { value: "RECOMMEND", label: "Recommend only", note: "Cannot call any tool through this system." },
  {
    value: "EXECUTE_WITH_APPROVAL",
    label: "Execute with approval",
    note: "Every tool call is held for human approval before it runs.",
  },
  {
    value: "EXECUTE_AUTONOMOUS",
    label: "Execute autonomously",
    note: "Follows each tool's own configured policy — never bypasses an existing approval requirement or block.",
  },
];

const EXECUTION_STATUS_HELP: Record<string, string> = {
  PENDING: "Queued — governance checks are in progress.",
  RUNNING: "In progress.",
  WAITING_APPROVAL: "Waiting for a human to approve this action.",
  COMPLETED: "Finished successfully.",
  FAILED: "Finished with an error.",
  HALTED: "Stopped before completing (see termination reason).",
};

export default function AgentDetailPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const agentId = params?.id;
  const toast = useToast();

  const [agent, setAgent] = useState<Agent | null>(null);
  const [versions, setVersions] = useState<AgentVersion[] | null>(null);
  const [grants, setGrants] = useState<AgentToolPermissionGrant[] | null>(null);
  const [catalog, setCatalog] = useState<ToolCatalogEntry[] | null>(null);
  const [executions, setExecutions] = useState<AgentExecution[] | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token || !agentId) return;
    setError(null);
    try {
      const [a, v, g, c, e] = await Promise.all([
        getAgent(token, agentId),
        listAgentVersions(token, agentId),
        listAgentToolPermissions(token, agentId),
        getToolCatalog(token),
        listAgentExecutions(token, agentId),
      ]);
      setAgent(a);
      setVersions(v);
      setGrants(g);
      setCatalog(c);
      setExecutions(e);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        setNotFound(true);
        return;
      }
      if (err instanceof ApiError && err.status === 403) {
        setError("You don't have permission to view this agent.");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Unable to load this agent.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, agentId]);

  useEffect(() => {
    load();
  }, [load]);

  async function doTransition(action: "activate" | "pause" | "archive") {
    if (!token || !agentId || lifecycleBusy) return;
    setLifecycleBusy(true);
    setError(null);
    try {
      const fn = action === "activate" ? activateAgent : action === "pause" ? pauseAgent : archiveAgent;
      const updated = await fn(token, agentId);
      setAgent(updated);
      toast.success(`Agent ${action}d.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.warning(err.message);
        await load();
      } else {
        setError(err instanceof ApiError ? err.message : `Unable to ${action} this agent.`);
      }
    } finally {
      setLifecycleBusy(false);
    }
  }

  if (notFound) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-3xl px-6 py-10">
          <Alert variant="danger">This agent doesn&apos;t exist, or isn&apos;t visible to your account.</Alert>
          <Button className="mt-4" variant="secondary" onClick={() => router.push("/agents")}>
            Back to Agents
          </Button>
        </div>
      </AppShell>
    );
  }

  if (!agent || !versions || !grants || !catalog || !executions) {
    return (
      <AppShell user={user}>
        <div className="mx-auto max-w-3xl px-6 py-10">
          {error ? <Alert variant="danger">{error}</Alert> : <Skeleton rows={5} stats={0} />}
        </div>
      </AppShell>
    );
  }

  const publishedVersion = versions.find((v) => v.id === agent.current_version_id) ?? null;
  const draftVersions = versions.filter((v) => v.status === "DRAFT").sort((a, b) => b.version - a.version);
  const latestDraft = draftVersions[0] ?? null;
  const canRun = agent.status === "ACTIVE" && publishedVersion?.status === "PUBLISHED";

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
        <PageHeader
          icon={Bot}
          title={agent.name}
          description={agent.purpose || "No description provided."}
          actions={
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Badge status={agent.status}>{agent.status}</Badge>
              {agent.status !== "ARCHIVED" && (agent.status === "DRAFT" || agent.status === "PAUSED") && (
                <Button size="sm" variant="secondary" disabled={lifecycleBusy} onClick={() => doTransition("activate")}>
                  Activate
                </Button>
              )}
              {agent.status === "ACTIVE" && (
                <Button size="sm" variant="secondary" disabled={lifecycleBusy} onClick={() => doTransition("pause")}>
                  Pause
                </Button>
              )}
              {agent.status !== "ARCHIVED" && (
                <Button size="sm" variant="ghost" disabled={lifecycleBusy} onClick={() => doTransition("archive")}>
                  Archive
                </Button>
              )}
              <RunAgentButton
                agentId={agentId}
                token={token}
                canRun={canRun}
                grants={grants}
                onRan={(exec) => setExecutions((prev) => (prev ? [exec, ...prev] : [exec]))}
              />
            </div>
          }
        />

        {error && <Alert variant="danger">{error}</Alert>}

        {!canRun && (
          <Alert variant="info">
            {agent.status !== "ACTIVE"
              ? "This agent must be Activated, with a Published version, before it can run."
              : "This agent has no Published version yet — publish a version below before running it."}
          </Alert>
        )}

        <IdentitySection agent={agent} token={token} onSaved={(a) => setAgent(a)} toast={toast} />

        <RoleAutonomySection agent={agent} />

        <ToolPermissionsSection
          agentId={agentId}
          token={token}
          grants={grants}
          catalog={catalog}
          onChanged={setGrants}
          toast={toast}
        />

        <VersionsSection
          agentId={agentId}
          token={token}
          versions={versions}
          currentVersionId={agent.current_version_id}
          latestDraft={latestDraft}
          onVersionsChanged={setVersions}
          onAgentChanged={setAgent}
          toast={toast}
        />

        <ExecutionsSection agentId={agentId} token={token} executions={executions} />
      </div>
    </AppShell>
  );
}

// ---------------------------------------------------------------- Identity

function IdentitySection({
  agent,
  token,
  onSaved,
  toast,
}: {
  agent: Agent;
  token: string | null;
  onSaved: (a: Agent) => void;
  toast: ReturnType<typeof useToast>;
}) {
  const editable = agent.status === "DRAFT";
  const [purpose, setPurpose] = useState(agent.purpose);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = purpose !== agent.purpose;

  async function save() {
    if (!token || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateAgent(token, agent.id, { purpose });
      onSaved(updated);
      toast.success("Draft saved.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save this draft.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="klaros-card p-5">
      <h2 className="mb-3 text-sm font-semibold text-foreground">Identity &amp; purpose</h2>
      {error && (
        <div className="mb-3">
          <Alert variant="danger">{error}</Alert>
        </div>
      )}
      <Field label="What should this agent do?">
        <textarea
          className="klaros-input min-h-[80px] resize-y disabled:opacity-60"
          value={purpose}
          disabled={!editable}
          onChange={(e) => setPurpose(e.target.value)}
        />
      </Field>
      {!editable && (
        <p className="mt-2 text-xs text-muted-foreground">
          Only a DRAFT agent&apos;s identity can be edited directly. Create a new version to change this agent&apos;s
          behavior going forward.
        </p>
      )}
      {editable && (
        <div className="mt-3 flex justify-end">
          <Button size="sm" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving..." : "Save draft"}
          </Button>
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------ Role/autonomy

function RoleAutonomySection({ agent }: { agent: Agent }) {
  const editable = agent.status === "DRAFT";
  return (
    <section className="klaros-card p-5">
      <h2 className="mb-3 text-sm font-semibold text-foreground">Role &amp; autonomy</h2>
      <dl className="mb-4 text-sm">
        <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Acting role</dt>
        <dd className="mt-0.5 text-foreground">{agent.acting_role}</dd>
        <p className="mt-1 text-xs text-muted-foreground">
          The role this agent's actions are authorized under, set from its creator's own role when it was created.
          It cannot be changed.
        </p>
      </dl>
      <div>
        <div className="klaros-label mb-2">Autonomy tier</div>
        <div className="space-y-2">
          {AUTONOMY_OPTIONS.map((opt) => (
            <div
              key={opt.value}
              className={`rounded-lg border p-3 text-sm ${
                agent.autonomy_tier === opt.value ? "border-accent bg-accent-soft" : "border-border opacity-60"
              }`}
            >
              <span className="block font-medium text-foreground">{opt.label}</span>
              <span className="block text-xs text-muted">{opt.note}</span>
            </div>
          ))}
        </div>
        {!editable && (
          <p className="mt-2 text-xs text-muted-foreground">
            Autonomy can only be changed while this agent is a DRAFT.
          </p>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------- Tool permissions

function ToolPermissionsSection({
  agentId,
  token,
  grants,
  catalog,
  onChanged,
  toast,
}: {
  agentId: string;
  token: string | null;
  grants: AgentToolPermissionGrant[];
  catalog: ToolCatalogEntry[];
  onChanged: (g: AgentToolPermissionGrant[]) => void;
  toast: ReturnType<typeof useToast>;
}) {
  const [query, setQuery] = useState("");
  const [busyTool, setBusyTool] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const grantedNames = new Set(grants.map((g) => g.tool_name));
  const filtered = catalog.filter((t) => t.name.toLowerCase().includes(query.trim().toLowerCase()));

  async function toggle(tool: ToolCatalogEntry) {
    if (!token || busyTool) return;
    setBusyTool(tool.name);
    setError(null);
    try {
      if (grantedNames.has(tool.name)) {
        await revokeAgentToolPermission(token, agentId, tool.name);
        onChanged(grants.filter((g) => g.tool_name !== tool.name));
      } else {
        const grant = await grantAgentToolPermission(token, agentId, { tool_name: tool.name });
        onChanged([...grants, grant]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update this tool's permission.");
    } finally {
      setBusyTool(null);
    }
  }

  return (
    <section className="klaros-card p-5">
      <h2 className="text-sm font-semibold text-foreground">Tool permissions</h2>
      <p className="mt-1 text-xs text-muted">
        Live, deny-by-default grants for this agent. Only granted tools can ever be called through it, and only when
        this business's own permissions/approval policy for that tool allows it. A new version freezes a copy of
        these grants when it's created and again when it's published — changing grants here never alters an already
        published version.
      </p>
      {error && (
        <div className="mt-3">
          <Alert variant="danger">{error}</Alert>
        </div>
      )}
      {catalog.length > 5 && (
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search tools..."
          aria-label="Search tools"
          className="klaros-input mt-3"
        />
      )}
      <ul className="mt-3 max-h-96 divide-y divide-border overflow-y-auto rounded-lg border border-border">
        {filtered.map((tool) => {
          const granted = grantedNames.has(tool.name);
          return (
            <li key={tool.name} className="flex items-start justify-between gap-3 px-3 py-2.5">
              <label className="flex min-w-0 flex-1 items-start gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={granted}
                  disabled={busyTool === tool.name}
                  onChange={() => toggle(tool)}
                  aria-label={`Grant ${tool.name}`}
                />
                <span className="min-w-0">
                  <span className="block font-medium text-foreground">{tool.name}</span>
                  <span className="block truncate text-xs text-muted">{tool.description}</span>
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    {tool.required_permission && (
                      <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        Requires {tool.required_permission}
                      </span>
                    )}
                    {tool.counts_toward_ai_usage && (
                      <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        Counts toward AI usage
                      </span>
                    )}
                  </span>
                </span>
              </label>
              {granted && <Badge status="ACTIVE">Granted</Badge>}
            </li>
          );
        })}
        {filtered.length === 0 && <li className="px-3 py-4 text-center text-xs text-muted-foreground">No tools match.</li>}
      </ul>
    </section>
  );
}

// -------------------------------------------------------------- Versions

function VersionsSection({
  agentId,
  token,
  versions,
  currentVersionId,
  latestDraft,
  onVersionsChanged,
  onAgentChanged,
  toast,
}: {
  agentId: string;
  token: string | null;
  versions: AgentVersion[];
  currentVersionId: string | null;
  latestDraft: AgentVersion | null;
  onVersionsChanged: (v: AgentVersion[]) => void;
  onAgentChanged: (a: Agent) => void;
  toast: ReturnType<typeof useToast>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [maxPerHour, setMaxPerHour] = useState(20);
  const [maxConcurrent, setMaxConcurrent] = useState(1);
  const [maxChainDepth, setMaxChainDepth] = useState(1);
  const [approvalOverride, setApprovalOverride] = useState("");
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleFrequency, setScheduleFrequency] = useState("DAILY");
  const [scheduleTime, setScheduleTime] = useState("09:00");
  const [scheduleGoal, setScheduleGoal] = useState("");
  const [creating, setCreating] = useState(false);
  const [publishingId, setPublishingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewingId, setReviewingId] = useState<string | null>(null);

  async function submitVersion() {
    if (!token || creating) return;
    if (!instructions.trim()) {
      setError("Give this version instructions to run under.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const triggers: AgentVersion["triggers"] = {};
      if (scheduleEnabled) {
        triggers.schedule = { enabled: true, frequency: scheduleFrequency, time: scheduleTime, goal: scheduleGoal };
      }
      const version = await createAgentVersion(token, agentId, {
        instructions: instructions.trim(),
        max_executions_per_hour: maxPerHour,
        max_concurrent_executions: maxConcurrent,
        max_tool_chain_depth: maxChainDepth,
        approval_policy_override: approvalOverride || null,
        triggers,
      });
      onVersionsChanged([version, ...versions]);
      setShowForm(false);
      setInstructions("");
      toast.success(`Draft version ${version.version} created.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create a new version.");
    } finally {
      setCreating(false);
    }
  }

  async function publish(version: AgentVersion) {
    if (!token || publishingId) return;
    setPublishingId(version.id);
    setError(null);
    try {
      const published = await publishAgentVersion(token, agentId, version.id);
      onVersionsChanged(versions.map((v) => (v.id === published.id ? published : v)));
      const refreshedAgent = await getAgent(token, agentId);
      onAgentChanged(refreshedAgent);
      toast.success(`Version ${published.version} published.`);
      setReviewingId(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.warning(err.message);
        // Reconcile with authoritative server state instead of leaving a
        // stale DRAFT badge on a version the backend already published
        // (or otherwise transitioned) out from under this tab — mirrors
        // the reconciliation `doTransition` already performs on 409.
        const [freshVersions, freshAgent] = await Promise.all([
          listAgentVersions(token, agentId),
          getAgent(token, agentId),
        ]);
        onVersionsChanged(freshVersions);
        onAgentChanged(freshAgent);
      } else {
        setError(err instanceof ApiError ? err.message : "Unable to publish this version.");
      }
    } finally {
      setPublishingId(null);
    }
  }

  const sorted = [...versions].sort((a, b) => b.version - a.version);

  return (
    <section className="klaros-card p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">Versions</h2>
        {!showForm && (
          <Button size="sm" variant="secondary" onClick={() => setShowForm(true)}>
            Create new version
          </Button>
        )}
      </div>
      <p className="mb-3 text-xs text-muted">
        A version's instructions, tool-permission snapshot, limits, and triggers are frozen once Published — change
        behavior going forward by creating and publishing a new version.
      </p>
      {error && (
        <div className="mb-3">
          <Alert variant="danger">{error}</Alert>
        </div>
      )}

      {showForm && (
        <div className="mb-4 space-y-4 rounded-lg border border-border p-4">
          <Field label="Instructions">
            <textarea
              className="klaros-input min-h-[100px] resize-y"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="What this version of the agent should do and how."
            />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Max executions/hour">
              <Input type="number" min={1} value={maxPerHour} onChange={(e) => setMaxPerHour(Number(e.target.value))} />
            </Field>
            <Field label="Max concurrent">
              <Input type="number" min={1} value={maxConcurrent} onChange={(e) => setMaxConcurrent(Number(e.target.value))} />
            </Field>
            <Field label="Max tool chain depth">
              <Input type="number" min={1} value={maxChainDepth} onChange={(e) => setMaxChainDepth(Number(e.target.value))} />
            </Field>
          </div>
          <Field label="Approval policy override (optional)">
            <select
              className="klaros-input"
              value={approvalOverride}
              onChange={(e) => setApprovalOverride(e.target.value)}
            >
              <option value="">No override — use each tool's own policy</option>
              <option value="APPROVAL_REQUIRED">Force every tool call to require approval</option>
              <option value="BLOCKED">Block every tool call under this version</option>
            </select>
          </Field>

          <div className="rounded-lg border border-border p-3">
            <label className="flex items-center gap-2 text-sm font-medium text-foreground">
              <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} />
              Run on a schedule
            </label>
            {scheduleEnabled && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <Field label="Frequency">
                  <select className="klaros-input" value={scheduleFrequency} onChange={(e) => setScheduleFrequency(e.target.value)}>
                    <option value="DAILY">Daily</option>
                    <option value="WEEKLY">Weekly</option>
                  </select>
                </Field>
                <Field label="Time (24-hour, business-local)">
                  <Input type="time" value={scheduleTime} onChange={(e) => setScheduleTime(e.target.value)} />
                </Field>
                <div className="col-span-2">
                  <Field label="Goal for the scheduled run">
                    <Input value={scheduleGoal} onChange={(e) => setScheduleGoal(e.target.value)} placeholder="What should the agent do each time this fires?" />
                  </Field>
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
            <Button size="sm" disabled={creating} onClick={submitVersion}>
              {creating ? "Creating..." : "Save draft version"}
            </Button>
          </div>
        </div>
      )}

      {sorted.length === 0 ? (
        <p className="text-sm text-muted">No versions yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {sorted.map((v) => (
            <li key={v.id} className="p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-medium text-foreground">Version {v.version}</span>
                  <Badge status={v.status}>{v.status}</Badge>
                  {v.id === currentVersionId && <Badge status="ACTIVE">Current</Badge>}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="text-xs text-accent hover:text-accent-hover"
                    onClick={() => setReviewingId(reviewingId === v.id ? null : v.id)}
                  >
                    {reviewingId === v.id ? "Hide" : "Review"}
                  </button>
                  {v.status === "DRAFT" && (
                    <Button size="sm" disabled={publishingId === v.id} onClick={() => publish(v)}>
                      <Rocket className="h-3.5 w-3.5" strokeWidth={2} />
                      {publishingId === v.id ? "Publishing..." : "Publish"}
                    </Button>
                  )}
                </div>
              </div>
              {reviewingId === v.id && (
                <div className="mt-3 space-y-2 rounded-lg bg-surface-muted p-3 text-xs">
                  <div>
                    <span className="font-medium text-foreground">Instructions: </span>
                    <span className="text-muted">{v.instructions_snapshot || "(none)"}</span>
                  </div>
                  <div>
                    <span className="font-medium text-foreground">Tools ({v.tool_permissions_snapshot.length}): </span>
                    <span className="text-muted">
                      {v.tool_permissions_snapshot.length > 0
                        ? v.tool_permissions_snapshot.map((t) => t.tool_name).join(", ")
                        : "None"}
                    </span>
                  </div>
                  <div>
                    <span className="font-medium text-foreground">Limits: </span>
                    <span className="text-muted">
                      {v.max_executions_per_hour}/hour, {v.max_concurrent_executions} concurrent, chain depth {v.max_tool_chain_depth}
                    </span>
                  </div>
                  <div>
                    <span className="font-medium text-foreground">Approval override: </span>
                    <span className="text-muted">{v.approval_policy_override ?? "None"}</span>
                  </div>
                  <div>
                    <span className="font-medium text-foreground">Triggers: </span>
                    <span className="text-muted">
                      {v.triggers?.schedule?.enabled
                        ? `Schedule (${v.triggers.schedule.frequency} at ${v.triggers.schedule.time})`
                        : v.triggers?.event?.enabled
                          ? `Event (${v.triggers.event.event_type})`
                          : "None configured"}
                    </span>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ------------------------------------------------------------- Executions

function ExecutionsSection({
  agentId,
  token,
  executions,
}: {
  agentId: string;
  token: string | null;
  executions: AgentExecution[];
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [steps, setSteps] = useState<AgentExecutionStep[] | null>(null);
  const [loadingSteps, setLoadingSteps] = useState(false);

  async function toggle(exec: AgentExecution) {
    if (expandedId === exec.id) {
      setExpandedId(null);
      setSteps(null);
      return;
    }
    setExpandedId(exec.id);
    setSteps(null);
    if (!token) return;
    setLoadingSteps(true);
    try {
      const rows = await listAgentExecutionSteps(token, agentId, exec.id);
      setSteps(rows);
    } catch {
      setSteps([]);
    } finally {
      setLoadingSteps(false);
    }
  }

  const sorted = [...executions].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  return (
    <section className="klaros-card p-5">
      <h2 className="mb-3 text-sm font-semibold text-foreground">Recent executions</h2>
      {sorted.length === 0 ? (
        <p className="text-sm text-muted">No executions yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {sorted.map((exec) => (
            <li key={exec.id}>
              <button
                type="button"
                onClick={() => toggle(exec)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-surface-muted"
              >
                <span className="flex items-center gap-2 text-sm">
                  {expandedId === exec.id ? (
                    <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={2} />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" strokeWidth={2} />
                  )}
                  <Badge status={exec.status}>{exec.status}</Badge>
                  <span className="text-muted-foreground">{exec.trigger_source}</span>
                  <span className="text-foreground">{exec.tool_name ?? exec.goal ?? "(reasoning run)"}</span>
                </span>
                <span className="text-xs text-muted-foreground">
                  {exec.started_at ? new Date(exec.started_at).toLocaleString() : new Date(exec.created_at).toLocaleString()}
                </span>
              </button>
              {expandedId === exec.id && (
                <div className="space-y-2 border-t border-border bg-surface-muted p-3 text-xs">
                  <p className="text-muted">{EXECUTION_STATUS_HELP[exec.status] ?? ""}</p>
                  {exec.error_message && <p className="text-danger">{exec.error_message}</p>}
                  {exec.termination_reason && (
                    <p>
                      <span className="font-medium text-foreground">Stopped because: </span>
                      <span className="text-muted">{exec.termination_reason}</span>
                    </p>
                  )}
                  {exec.final_response && (
                    <p>
                      <span className="font-medium text-foreground">Result: </span>
                      <span className="text-muted">{exec.final_response}</span>
                    </p>
                  )}
                  {exec.status === "WAITING_APPROVAL" && (
                    <p>
                      This run is waiting for a human decision.{" "}
                      <a href="/approvals" className="text-accent hover:text-accent-hover">
                        Review in Approvals
                      </a>
                      .
                    </p>
                  )}
                  {loadingSteps && <p className="text-muted-foreground">Loading steps...</p>}
                  {steps && steps.length > 0 && (
                    <ul className="space-y-1.5">
                      {steps.map((s) => (
                        <li key={s.id} className="rounded border border-border bg-surface p-2">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-foreground">Step {s.step_number}</span>
                            <Badge status={s.status}>{s.status}</Badge>
                            {s.tool_name && <span className="text-muted-foreground">{s.tool_name}</span>}
                          </div>
                          {s.decision_summary && <p className="mt-1 text-muted">{s.decision_summary}</p>}
                          {s.error_code && <p className="mt-1 text-danger">{s.error_code}</p>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ------------------------------------------------------------- Run agent

function RunAgentButton({
  agentId,
  token,
  canRun,
  grants,
  onRan,
}: {
  agentId: string;
  token: string | null;
  canRun: boolean;
  grants: AgentToolPermissionGrant[];
  onRan: (e: AgentExecution) => void;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"goal" | "tool">("goal");
  const [goal, setGoal] = useState("");
  const [toolName, setToolName] = useState(grants[0]?.tool_name ?? "");
  const [toolInput, setToolInput] = useState("{}");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!token || running) return;
    setError(null);
    if (mode === "goal" && !goal.trim()) {
      setError("Describe what you want this agent to do.");
      return;
    }
    if (mode === "tool" && !toolName) {
      setError("Choose a granted tool to run.");
      return;
    }
    let parsedInput: Record<string, unknown> = {};
    if (mode === "tool") {
      try {
        parsedInput = toolInput.trim() ? JSON.parse(toolInput) : {};
      } catch {
        setError("Tool input must be valid JSON.");
        return;
      }
    }
    setRunning(true);
    try {
      const execution = await executeAgent(
        token,
        agentId,
        mode === "goal" ? { goal: goal.trim() } : { tool_name: toolName, tool_input: parsedInput }
      );
      onRan(execution);
      toast.info(`Execution ${execution.status.toLowerCase()}.`);
      setOpen(false);
      setGoal("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to run this agent.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <Button size="sm" disabled={!canRun} onClick={() => setOpen(true)}>
        <Play className="h-3.5 w-3.5" strokeWidth={2} />
        Run Agent
      </Button>
      {open && (
        <Modal title="Run Agent" onClose={() => setOpen(false)}>
          {error && (
            <div className="mb-3">
              <Alert variant="danger">{error}</Alert>
            </div>
          )}
          <div className="mb-3 flex gap-2 text-xs">
            <button
              type="button"
              className={`rounded-full border px-3 py-1 ${mode === "goal" ? "border-accent bg-accent-soft text-accent" : "border-border text-muted"}`}
              onClick={() => setMode("goal")}
            >
              Give it a goal
            </button>
            <button
              type="button"
              className={`rounded-full border px-3 py-1 ${mode === "tool" ? "border-accent bg-accent-soft text-accent" : "border-border text-muted"}`}
              onClick={() => setMode("tool")}
            >
              Run one granted tool
            </button>
          </div>
          {mode === "goal" ? (
            <Field label="Goal">
              <textarea
                className="klaros-input min-h-[80px] resize-y"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="What should this agent accomplish right now?"
              />
            </Field>
          ) : (
            <div className="space-y-3">
              <Field label="Tool">
                <select className="klaros-input" value={toolName} onChange={(e) => setToolName(e.target.value)}>
                  {grants.length === 0 && <option value="">No tools granted</option>}
                  {grants.map((g) => (
                    <option key={g.tool_name} value={g.tool_name}>
                      {g.tool_name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Tool input (JSON)">
                <textarea
                  className="klaros-input min-h-[80px] resize-y font-mono text-xs"
                  value={toolInput}
                  onChange={(e) => setToolInput(e.target.value)}
                />
              </Field>
            </div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" disabled={running} onClick={run}>
              {running ? "Running..." : "Run"}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
