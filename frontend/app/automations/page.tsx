"use client";

import { useCallback, useEffect, useState } from "react";
import { History, Workflow } from "lucide-react";
import AppShell from "@/components/AppShell";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/useAuth";
import { useToast } from "@/components/ui/Toast";
import {
  ApiError,
  AutomationExecutionDetail,
  AutomationExecutionRow,
  AutomationRow,
  AutomationStep,
  AutomationVersionRow,
  ConditionNode,
  createAutomation,
  dispatchScheduledTick,
  getAutomation,
  getAutomationExecution,
  getAutomationTimezone,
  listAutomationExecutions,
  listAutomationVersions,
  listAutomations,
  publishAutomation,
  setAutomationEnabled,
  setAutomationTimezone,
  triggerAutomation,
  updateAutomation,
} from "@/lib/api";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Every action here is checked against the backend's own ACTION_ALLOWLIST
// (app/services/automation_service.py) before it can ever run — this list
// exists only to drive the step editor's per-action param fields, not to
// grant anything the backend wouldn't already enforce.
const ACTIONS = [
  {
    value: "notifications.create_notification",
    label: "Send a notification",
    fields: [
      { key: "title", label: "Title", type: "text" as const },
      { key: "body", label: "Body", type: "text" as const },
    ],
  },
  {
    value: "crm.update_lead",
    label: "Update a lead",
    fields: [
      { key: "lead_id", label: "Lead ID (or {{lead.id}})", type: "text" as const },
      { key: "status", label: "New status", type: "text" as const },
    ],
  },
  {
    value: "crm.create_note",
    label: "Add a customer note",
    fields: [
      { key: "customer_id", label: "Customer ID (or {{lead.customer_id}})", type: "text" as const },
      { key: "body", label: "Note", type: "text" as const },
    ],
  },
  {
    // Phase 18: the AI Next Action decision layer's own governed entry
    // point. This action never mutates anything directly itself — it
    // observes the given quote, consults Company Memory, and lets the AI
    // propose at most one action from its own separate, narrower
    // allowlist, validated deterministically and executed only through
    // the same ActionPolicy/ApprovalRequest boundary every other action
    // here uses.
    value: "ai.propose_quote_followup",
    label: "AI: decide a quote follow-up",
    fields: [
      { key: "quote_id", label: "Quote ID (or {{event.entity_id}})", type: "text" as const },
    ],
  },
  {
    // Phase 20: the second AI Next Action scenario — same governed shape
    // as ai.propose_quote_followup above, proving the pattern generalizes.
    value: "ai.propose_invoice_followup",
    label: "AI: decide an invoice follow-up",
    fields: [
      { key: "invoice_id", label: "Invoice ID (or {{event.entity_id}})", type: "text" as const },
    ],
  },
];

const COMPARISON_OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "is_null", "is_not_null"];

interface ConditionRow {
  field: string;
  op: string;
  value: string;
}

interface FormState {
  name: string;
  description: string;
  triggerType: "MANUAL" | "EVENT" | "SCHEDULE";
  eventType: string;
  scheduleFrequency: "DAILY" | "WEEKLY";
  scheduleTime: string;
  scheduleWeekdays: number[];
  hasWait: boolean;
  waitSeconds: string;
  conditions: ConditionRow[];
  steps: { action: string; params: Record<string, string> }[];
}

function emptyForm(): FormState {
  return {
    name: "",
    description: "",
    triggerType: "MANUAL",
    eventType: "lead.created",
    scheduleFrequency: "DAILY",
    scheduleTime: "09:00",
    scheduleWeekdays: [0],
    hasWait: false,
    waitSeconds: "900",
    conditions: [],
    steps: [{ action: ACTIONS[0].value, params: {} }],
  };
}

function formToPayload(form: FormState): {
  trigger_type: string;
  trigger_config: Record<string, unknown>;
  condition: ConditionNode | null;
  steps: AutomationStep[];
} {
  const condition: ConditionNode | null =
    form.conditions.length === 0
      ? null
      : form.conditions.length === 1
      ? { field: form.conditions[0].field, op: form.conditions[0].op, value: parseConditionValue(form.conditions[0].value) }
      : {
          and: form.conditions.map((c) => ({ field: c.field, op: c.op, value: parseConditionValue(c.value) })),
        };

  const steps: AutomationStep[] = [];
  if (form.hasWait) {
    steps.push({ action: "wait", params: { seconds: Number(form.waitSeconds) || 0 } });
  }
  for (const s of form.steps) {
    steps.push({ action: s.action, params: s.params });
  }

  let trigger_config: Record<string, unknown> = {};
  if (form.triggerType === "EVENT") {
    trigger_config = { event_type: form.eventType };
  } else if (form.triggerType === "SCHEDULE") {
    trigger_config =
      form.scheduleFrequency === "WEEKLY"
        ? { frequency: "WEEKLY", time: form.scheduleTime, weekdays: form.scheduleWeekdays }
        : { frequency: "DAILY", time: form.scheduleTime };
  }

  return {
    trigger_type: form.triggerType,
    trigger_config,
    condition,
    steps,
  };
}

function parseConditionValue(raw: string): unknown {
  if (raw === "") return null;
  if (raw === "true") return true;
  if (raw === "false") return false;
  const num = Number(raw);
  if (!Number.isNaN(num) && raw.trim() !== "") return num;
  return raw;
}

export default function AutomationsPage() {
  const toast = useToast();
  const { token, user, loading: authLoading } = useAuth();
  const [automations, setAutomations] = useState<AutomationRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [mode, setMode] = useState<"list" | "create" | "edit">("list");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedAutomation, setSelectedAutomation] = useState<AutomationRow | null>(null);
  const [versions, setVersions] = useState<AutomationVersionRow[] | null>(null);
  const [executions, setExecutions] = useState<AutomationExecutionRow[] | null>(null);
  const [selectedExecution, setSelectedExecution] = useState<AutomationExecutionDetail | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  const [triggerContext, setTriggerContext] = useState("{}");
  const [tenantTimezone, setTenantTimezone] = useState<string | null>(null);
  const [timezoneInput, setTimezoneInput] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await listAutomations(token);
      setAutomations(result.automations);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load automations.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!token) return;
    getAutomationTimezone(token)
      .then((result) => {
        setTenantTimezone(result.timezone);
        setTimezoneInput(result.timezone);
      })
      .catch(() => {});
  }, [token]);

  async function handleSaveTimezone() {
    if (!token || !timezoneInput) return;
    setBusy(true);
    setError(null);
    try {
      const result = await setAutomationTimezone(token, timezoneInput);
      setTenantTimezone(result.timezone);
      toast.success(`Business timezone set to ${result.timezone}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to set timezone — check the IANA timezone name.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDispatchTickNow() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const result = await dispatchScheduledTick(token);
      toast.success(
        result.dispatched_execution_ids.length > 0
          ? `Scheduler tick ran — ${result.dispatched_execution_ids.length} automation(s) dispatched.`
          : "Scheduler tick ran — nothing was due."
      );
      if (selectedId) {
        const executionList = await listAutomationExecutions(token, selectedId);
        setExecutions(executionList.executions);
        const automation = await getAutomation(token, selectedId);
        setSelectedAutomation(automation);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to run the scheduler tick.");
    } finally {
      setBusy(false);
    }
  }

  async function openDetail(id: string) {
    if (!token) return;
    setError(null);
    setSelectedExecution(null);
    try {
      const [automation, versionList, executionList] = await Promise.all([
        getAutomation(token, id),
        listAutomationVersions(token, id),
        listAutomationExecutions(token, id),
      ]);
      setSelectedId(id);
      setSelectedAutomation(automation);
      setVersions(versionList.versions);
      setExecutions(executionList.executions);
      setMode("edit");

      const latest = versionList.versions[versionList.versions.length - 1];
      if (latest) {
        const waitStep = latest.steps[0]?.action === "wait" ? latest.steps[0] : null;
        const restSteps = waitStep ? latest.steps.slice(1) : latest.steps;
        const triggerType: FormState["triggerType"] =
          latest.trigger_type === "EVENT" ? "EVENT" : latest.trigger_type === "SCHEDULE" ? "SCHEDULE" : "MANUAL";
        setForm({
          name: automation.name,
          description: automation.description ?? "",
          triggerType,
          eventType: (latest.trigger_config.event_type as string) ?? "lead.created",
          scheduleFrequency: (latest.trigger_config.frequency as "DAILY" | "WEEKLY") ?? "DAILY",
          scheduleTime: (latest.trigger_config.time as string) ?? "09:00",
          scheduleWeekdays: (latest.trigger_config.weekdays as number[]) ?? [0],
          hasWait: !!waitStep,
          waitSeconds: waitStep ? String((waitStep.params as { seconds?: number }).seconds ?? 900) : "900",
          conditions: conditionToRows(latest.condition),
          steps: restSteps.map((s) => ({
            action: s.action,
            params: Object.fromEntries(Object.entries(s.params).map(([k, v]) => [k, String(v ?? "")])),
          })),
        });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load automation detail.");
    }
  }

  function conditionToRows(condition: ConditionNode | null): ConditionRow[] {
    if (!condition) return [];
    if (condition.and) {
      return condition.and
        .filter((c) => c.field)
        .map((c) => ({ field: c.field ?? "", op: c.op ?? "eq", value: String(c.value ?? "") }));
    }
    if (condition.field) {
      return [{ field: condition.field, op: condition.op ?? "eq", value: String(condition.value ?? "") }];
    }
    return [];
  }

  function startCreate() {
    setForm(emptyForm());
    setSelectedId(null);
    setSelectedAutomation(null);
    setVersions(null);
    setExecutions(null);
    setSelectedExecution(null);
    setMode("create");
  }

  async function handleSave() {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const payload = formToPayload(form);
      if (mode === "create") {
        const automation = await createAutomation(token, { name: form.name, description: form.description || null, ...payload });
        toast.success("Automation created as a draft.");
        await load();
        await openDetail(automation.id);
      } else if (selectedId) {
        await updateAutomation(token, selectedId, payload);
        toast.success("Saved as a new version.");
        await load();
        await openDetail(selectedId);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save automation.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish() {
    if (!token || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await publishAutomation(token, selectedId);
      toast.success("Published — this version is now live.");
      await load();
      await openDetail(selectedId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to publish.");
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleEnabled(enabled: boolean) {
    if (!token || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await setAutomationEnabled(token, selectedId, enabled);
      toast.success(enabled ? "Enabled." : "Disabled.");
      await load();
      await openDetail(selectedId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to change enabled state.");
    } finally {
      setBusy(false);
    }
  }

  async function handleManualTrigger() {
    if (!token || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      let context: Record<string, unknown> = {};
      try {
        context = JSON.parse(triggerContext || "{}");
      } catch {
        setError("Trigger context must be valid JSON.");
        setBusy(false);
        return;
      }
      const result = await triggerAutomation(token, selectedId, context);
      if ("deduplicated" in result) {
        toast.success("Deduplicated — an execution with this exact context already ran.");
      } else {
        toast.success(`Triggered — execution ${result.status.toLowerCase()}.`);
      }
      const executionList = await listAutomationExecutions(token, selectedId);
      setExecutions(executionList.executions);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to trigger automation.");
    } finally {
      setBusy(false);
    }
  }

  async function openExecution(id: string) {
    if (!token) return;
    setError(null);
    try {
      const detail = await getAutomationExecution(token, id);
      setSelectedExecution(detail);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load execution detail.");
    }
  }

  function updateCondition(index: number, field: keyof ConditionRow, value: string) {
    setForm((f) => ({
      ...f,
      conditions: f.conditions.map((c, i) => (i === index ? { ...c, [field]: value } : c)),
    }));
  }

  function updateStepAction(index: number, action: string) {
    setForm((f) => ({
      ...f,
      steps: f.steps.map((s, i) => (i === index ? { action, params: {} } : s)),
    }));
  }

  function updateStepParam(index: number, key: string, value: string) {
    setForm((f) => ({
      ...f,
      steps: f.steps.map((s, i) => (i === index ? { ...s, params: { ...s.params, [key]: value } } : s)),
    }));
  }

  return (
    <AppShell user={user}>
      <div className="px-8 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="font-display text-2xl text-foreground">Automations</h1>
          {mode !== "list" ? (
            <button
              onClick={() => setMode("list")}
              className="rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-surface-muted"
            >
              Back to list
            </button>
          ) : (
            <button onClick={startCreate} className="klaros-btn-primary text-xs">
              New automation
            </button>
          )}
        </div>

        <p className="mb-4 text-xs text-muted">
          Event → condition → action, running against the exact same governed Tool Registry pipeline every
          other part of Klaros uses — no automation can call an action outside a fixed, hardcoded allowlist.
          Editing an automation always creates a new version; an execution already in flight keeps running
          against the version it started with.
        </p>

        <div className="mb-6 flex items-center gap-2 rounded-md border border-border bg-surface p-3 text-xs">
          <span className="text-muted">Business timezone (used by all Schedule triggers):</span>
          <input
            value={timezoneInput}
            onChange={(e) => setTimezoneInput(e.target.value)}
            placeholder="America/New_York"
            className="w-48 rounded-md border border-border-strong bg-background px-2 py-1 text-xs"
          />
          <button
            onClick={handleSaveTimezone}
            disabled={busy || timezoneInput === tenantTimezone}
            className="rounded-md border border-border-strong bg-surface-muted px-2 py-1 text-xs hover:bg-surface-muted disabled:opacity-50"
          >
            Save
          </button>
          {tenantTimezone && <span className="text-muted-foreground">Current: {tenantTimezone}</span>}
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-sm text-danger">{error}</div>
        )}

        {mode === "list" && (
          <div>
            {authLoading || loading ? (
              <Skeleton />
            ) : !automations || automations.length === 0 ? (
              <EmptyState
                icon={Workflow}
                title="No automations yet."
                action={
                  <button onClick={startCreate} className="klaros-btn-primary text-xs">
                    New automation
                  </button>
                }
              />
            ) : (
              <div className="space-y-2">
                {automations.map((a) => (
                  <button
                    key={a.id}
                    onClick={() => openDetail(a.id)}
                    className="block w-full rounded-lg border border-border p-3 text-left text-sm hover:bg-surface-muted"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{a.name}</span>
                      <Badge status={a.status} className="text-[10px]">
                        {a.status}
                      </Badge>
                    </div>
                    {a.description && <p className="mt-1 text-muted">{a.description}</p>}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {(mode === "create" || mode === "edit") && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="space-y-4 rounded-lg border border-border bg-surface p-5">
              <div>
                <label className="mb-1 block text-xs text-muted">Name</label>
                <input
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  disabled={mode === "edit"}
                  className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm disabled:opacity-60"
                  placeholder="New Lead Follow-up"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted">Description</label>
                <input
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                  placeholder="Optional"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs text-muted">Trigger</label>
                <div className="flex gap-2">
                  {(["MANUAL", "EVENT", "SCHEDULE"] as const).map((t) => (
                    <button
                      key={t}
                      onClick={() => setForm((f) => ({ ...f, triggerType: t }))}
                      className={`rounded-md border px-3 py-1.5 text-xs ${
                        form.triggerType === t ? "border-border-strong bg-surface-muted" : "border-border-strong hover:bg-surface-muted"
                      }`}
                    >
                      {t === "MANUAL" ? "Manual (run on demand)" : t === "EVENT" ? "Event" : "Schedule"}
                    </button>
                  ))}
                </div>
              </div>

              {form.triggerType === "EVENT" && (
                <div>
                  <label className="mb-1 block text-xs text-muted">Event type</label>
                  <input
                    value={form.eventType}
                    onChange={(e) => setForm((f) => ({ ...f, eventType: e.target.value }))}
                    className="w-full rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                    placeholder="lead.created"
                  />
                </div>
              )}

              {form.triggerType === "SCHEDULE" && (
                <div className="space-y-2">
                  <div>
                    <label className="mb-1 block text-xs text-muted">Frequency</label>
                    <div className="flex gap-2">
                      {(["DAILY", "WEEKLY"] as const).map((f) => (
                        <button
                          key={f}
                          onClick={() => setForm((prev) => ({ ...prev, scheduleFrequency: f }))}
                          className={`rounded-md border px-3 py-1.5 text-xs ${
                            form.scheduleFrequency === f
                              ? "border-border-strong bg-surface-muted"
                              : "border-border-strong hover:bg-surface-muted"
                          }`}
                        >
                          {f === "DAILY" ? "Daily" : "Weekly"}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted">Time (business timezone)</label>
                    <input
                      type="time"
                      value={form.scheduleTime}
                      onChange={(e) => setForm((f) => ({ ...f, scheduleTime: e.target.value }))}
                      className="w-40 rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                    />
                  </div>
                  {form.scheduleFrequency === "WEEKLY" && (
                    <div>
                      <label className="mb-1 block text-xs text-muted">Days</label>
                      <div className="flex gap-1">
                        {WEEKDAY_LABELS.map((label, i) => (
                          <button
                            key={label}
                            onClick={() =>
                              setForm((f) => ({
                                ...f,
                                scheduleWeekdays: f.scheduleWeekdays.includes(i)
                                  ? f.scheduleWeekdays.filter((d) => d !== i)
                                  : [...f.scheduleWeekdays, i].sort(),
                              }))
                            }
                            className={`rounded-md border px-2 py-1 text-[10px] ${
                              form.scheduleWeekdays.includes(i)
                                ? "border-border-strong bg-surface-muted"
                                : "border-border-strong hover:bg-surface-muted"
                            }`}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <p className="text-[10px] text-muted-foreground">
                    Runs in the business timezone set below — currently {tenantTimezone ?? "loading..."}.
                  </p>
                </div>
              )}

              <div>
                <label className="mb-1 flex items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={form.hasWait}
                    onChange={(e) => setForm((f) => ({ ...f, hasWait: e.target.checked }))}
                  />
                  Wait before running (durable, survives restarts)
                </label>
                {form.hasWait && (
                  <input
                    type="number"
                    value={form.waitSeconds}
                    onChange={(e) => setForm((f) => ({ ...f, waitSeconds: e.target.value }))}
                    className="mt-1 w-32 rounded-md border border-border-strong bg-background px-3 py-2 text-sm"
                  />
                )}
              </div>

              <div>
                <div className="mb-1 flex items-center justify-between">
                  <label className="text-xs text-muted">Conditions (all must match — AND)</label>
                  <button
                    onClick={() => setForm((f) => ({ ...f, conditions: [...f.conditions, { field: "", op: "eq", value: "" }] }))}
                    className="text-[10px] text-muted underline hover:text-foreground"
                  >
                    + add condition
                  </button>
                </div>
                {form.conditions.length === 0 && <p className="text-[10px] text-muted-foreground">No conditions — always runs.</p>}
                {form.conditions.map((c, i) => (
                  <div key={i} className="mb-2 flex gap-2">
                    <input
                      value={c.field}
                      onChange={(e) => updateCondition(i, "field", e.target.value)}
                      placeholder="lead.score"
                      className="w-1/3 rounded-md border border-border-strong bg-background px-2 py-1.5 text-xs"
                    />
                    <select
                      value={c.op}
                      onChange={(e) => updateCondition(i, "op", e.target.value)}
                      className="rounded-md border border-border-strong bg-background px-2 py-1.5 text-xs"
                    >
                      {COMPARISON_OPS.map((op) => (
                        <option key={op} value={op}>
                          {op}
                        </option>
                      ))}
                    </select>
                    <input
                      value={c.value}
                      onChange={(e) => updateCondition(i, "value", e.target.value)}
                      placeholder="70"
                      className="flex-1 rounded-md border border-border-strong bg-background px-2 py-1.5 text-xs"
                    />
                    <button
                      onClick={() => setForm((f) => ({ ...f, conditions: f.conditions.filter((_, j) => j !== i) }))}
                      className="text-xs text-danger hover:text-danger"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>

              <div>
                <div className="mb-1 flex items-center justify-between">
                  <label className="text-xs text-muted">Actions (run in order)</label>
                  <button
                    onClick={() => setForm((f) => ({ ...f, steps: [...f.steps, { action: ACTIONS[0].value, params: {} }] }))}
                    className="text-[10px] text-muted underline hover:text-foreground"
                  >
                    + add action
                  </button>
                </div>
                {form.steps.map((s, i) => {
                  const def = ACTIONS.find((a) => a.value === s.action) ?? ACTIONS[0];
                  return (
                    <div key={i} className="mb-2 rounded-md border border-border p-2">
                      <div className="mb-2 flex items-center justify-between">
                        <select
                          value={s.action}
                          onChange={(e) => updateStepAction(i, e.target.value)}
                          className="rounded-md border border-border-strong bg-background px-2 py-1.5 text-xs"
                        >
                          {ACTIONS.map((a) => (
                            <option key={a.value} value={a.value}>
                              {a.label}
                            </option>
                          ))}
                        </select>
                        {form.steps.length > 1 && (
                          <button
                            onClick={() => setForm((f) => ({ ...f, steps: f.steps.filter((_, j) => j !== i) }))}
                            className="text-xs text-danger hover:text-danger"
                          >
                            remove
                          </button>
                        )}
                      </div>
                      {def.fields.map((field) => (
                        <input
                          key={field.key}
                          value={s.params[field.key] ?? ""}
                          onChange={(e) => updateStepParam(i, field.key, e.target.value)}
                          placeholder={field.label}
                          className="mb-1 w-full rounded-md border border-border-strong bg-background px-2 py-1.5 text-xs"
                        />
                      ))}
                      <p className="text-[10px] text-muted-foreground">
                        Use {"{{"}field.path{"}}"} to reference the trigger context (e.g. {"{{"}lead.name{"}}"}).
                      </p>
                    </div>
                  );
                })}
              </div>

              <button
                onClick={handleSave}
                disabled={busy || !form.name}
                className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm hover:bg-surface-muted disabled:opacity-50"
              >
                {mode === "create" ? "Create draft" : "Save as new version"}
              </button>
            </div>

            <div className="space-y-4">
              {selectedAutomation && (
                <div className="rounded-lg border border-border bg-surface p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="font-medium">{selectedAutomation.name}</h2>
                    <Badge status={selectedAutomation.status} className="text-[10px]">
                      {selectedAutomation.status}
                    </Badge>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {selectedAutomation.status === "DRAFT" && (
                      <button
                        onClick={handlePublish}
                        disabled={busy}
                        className="rounded-md border border-success/20 px-3 py-1.5 text-xs text-success hover:bg-success/[0.06]"
                      >
                        Publish
                      </button>
                    )}
                    {selectedAutomation.published_version_id && selectedAutomation.status !== "ENABLED" && (
                      <button
                        onClick={() => handleToggleEnabled(true)}
                        disabled={busy}
                        className="rounded-md border border-success/20 px-3 py-1.5 text-xs text-success hover:bg-success/[0.06]"
                      >
                        Enable
                      </button>
                    )}
                    {selectedAutomation.status === "ENABLED" && (
                      <button
                        onClick={() => handleToggleEnabled(false)}
                        disabled={busy}
                        className="rounded-md border border-warning/25 px-3 py-1.5 text-xs text-warning hover:bg-warning/[0.07]"
                      >
                        Disable
                      </button>
                    )}
                  </div>

                  {versions && (
                    <p className="mt-3 text-[10px] text-muted-foreground">
                      {versions.length} version{versions.length === 1 ? "" : "s"} — currently editing the latest
                    </p>
                  )}

                  {form.triggerType === "MANUAL" && selectedAutomation.status === "ENABLED" && (
                    <div className="mt-4 border-t border-border pt-4">
                      <label className="mb-1 block text-xs text-muted">Trigger context (JSON, optional)</label>
                      <textarea
                        value={triggerContext}
                        onChange={(e) => setTriggerContext(e.target.value)}
                        rows={3}
                        className="w-full rounded-md border border-border-strong bg-background px-2 py-1.5 font-mono text-xs"
                      />
                      <button
                        onClick={handleManualTrigger}
                        disabled={busy}
                        className="mt-2 rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-xs hover:bg-surface-muted disabled:opacity-50"
                      >
                        Run now
                      </button>
                    </div>
                  )}

                  {form.triggerType === "SCHEDULE" && selectedAutomation.status === "ENABLED" && (
                    <div className="mt-4 border-t border-border pt-4">
                      <p className="mb-2 text-xs text-muted">
                        Next scheduled run:{" "}
                        <span className="text-muted">
                          {selectedAutomation.next_scheduled_run
                            ? new Date(selectedAutomation.next_scheduled_run).toLocaleString()
                            : "—"}
                        </span>
                      </p>
                      <p className="mb-2 text-[10px] text-muted-foreground">
                        The background scheduler checks every automation once per poll tick automatically. Use
                        this only to force an immediate check (e.g. for testing) rather than waiting.
                      </p>
                      <button
                        onClick={handleDispatchTickNow}
                        disabled={busy}
                        className="rounded-md border border-border-strong bg-surface-muted px-3 py-1.5 text-xs hover:bg-surface-muted disabled:opacity-50"
                      >
                        Run scheduler tick now
                      </button>
                    </div>
                  )}
                </div>
              )}

              {executions && (
                <div className="rounded-lg border border-border bg-surface p-5">
                  <h3 className="mb-3 text-sm font-medium">Execution history</h3>
                  {executions.length === 0 ? (
                    <EmptyState icon={History} title="No executions yet." compact />
                  ) : (
                    <div className="space-y-2">
                      {executions.map((e) => (
                        <button
                          key={e.id}
                          onClick={() => openExecution(e.id)}
                          className={`block w-full rounded-md border p-2 text-left text-xs hover:bg-surface-muted ${
                            selectedExecution?.id === e.id ? "border-border-strong" : "border-border"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span>{e.trigger_type}</span>
                            <Badge status={e.status} className="text-[10px]">
                              {e.status}
                            </Badge>
                          </div>
                          <span className="text-muted-foreground">{e.started_at ? new Date(e.started_at).toLocaleString() : "—"}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {selectedExecution && (
                <div className="rounded-lg border border-border bg-surface p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-sm font-medium">Execution detail</h3>
                    <Badge status={selectedExecution.status} className="text-[10px]">
                      {selectedExecution.status}
                    </Badge>
                  </div>
                  {selectedExecution.error && (
                    <p className="mb-2 rounded-md border border-danger/25 bg-danger/[0.06] p-2 text-xs text-danger">
                      {selectedExecution.error}
                    </p>
                  )}
                  <div className="space-y-2">
                    {selectedExecution.steps.map((s) => (
                      <div key={s.id} className="rounded-md border border-border p-2 text-xs">
                        <div className="flex items-center justify-between">
                          <span>
                            {s.step_index}. {s.action}
                          </span>
                          <Badge status={s.status} className="text-[10px]">
                            {s.status}
                          </Badge>
                        </div>
                        {s.error && <p className="mt-1 text-danger">{s.error}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
