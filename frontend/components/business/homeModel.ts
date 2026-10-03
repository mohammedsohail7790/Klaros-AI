/**
 * Pure view-models for the Business Home command center. Everything is derived from the two
 * backend read-models (overview + operations): nothing here invents a metric, an event or a
 * state, and a thing that isn't true is never shown as true.
 */
import type { BuilderOverview, BusinessOperations, ReadinessState } from "@/lib/api";

export type Severity = "danger" | "warning" | "info";

export interface ActionItem {
  id: string;
  severity: Severity;
  text: string;
  route: string;
  cta: string;
}

const RANK: Record<Severity, number> = { danger: 0, warning: 1, info: 2 };

export function workforceLabel(wf: BuilderOverview["workforce"]): { label: string; state: ReadinessState } {
  if (wf.status === "CONNECTED") return { label: "Connected", state: "CONNECTED" };
  if (!wf.adapter_implemented) return { label: "Integration required", state: "INTEGRATION_REQUIRED" };
  switch (wf.status) {
    case "CONNECTING":
      return { label: "Connecting", state: "CONFIGURATION_REQUIRED" };
    case "NEEDS_ATTENTION":
      return { label: "Needs attention", state: "CONFIGURATION_REQUIRED" };
    case "ERROR":
      return { label: "Connection error", state: "NOT_READY" };
    case "CONFIGURATION_REQUIRED":
      return { label: "Configuration required", state: "CONFIGURATION_REQUIRED" };
    default:
      return { label: "Not connected", state: "NOT_CONNECTED" };
  }
}

/** Providers a requirement needs that are not connected (one per provider name, real adapters only). */
export function unconnectedProviders(overview: BuilderOverview): string[] {
  const names: string[] = [];
  for (const r of overview.requirements) {
    const real = r.providers.filter((p) => !p.adapter_required);
    if (real.length === 0 || real.some((p) => p.state === "CONNECTED")) continue;
    const first = real[0].display_name;
    if (!names.includes(first)) names.push(first);
  }
  return names;
}

export function buildActionCenter(overview: BuilderOverview, ops: BusinessOperations | null): ActionItem[] {
  const items: ActionItem[] = [];
  if (ops) {
    for (const a of ops.attention) {
      items.push({ id: a.id, severity: a.id === "failed-runs" ? "danger" : "warning", text: a.text, route: a.route, cta: "Open" });
    }
  }
  const w = overview.website;
  if (!w.exists) items.push({ id: "website-none", severity: "info", text: "Your website hasn't been generated yet", route: "/website", cta: "Generate" });
  else if (!w.published) items.push({ id: "website-draft", severity: "warning", text: "Your website is a draft — publish it to start receiving enquiries", route: "/website", cta: "Review" });
  else if (w.has_draft) items.push({ id: "website-changes", severity: "info", text: "Your website has unpublished changes", route: "/website", cta: "Review" });

  if (overview.requirements.some((r) => r.workforce_addressable) && overview.workforce.status !== "CONNECTED") {
    items.push({
      id: "workforce",
      severity: "info",
      text: !overview.workforce.adapter_implemented
        ? "Halla needs to be integrated before your AI workforce can talk to customers"
        : overview.workforce.status === "ERROR" || overview.workforce.status === "NEEDS_ATTENTION"
          ? "The connection to Halla needs attention"
          : "Halla isn't connected yet",
      route: "/workforce",
      cta: "Review setup",
    });
  }
  for (const name of unconnectedProviders(overview).slice(0, 3)) {
    items.push({ id: `connect-${name}`, severity: "info", text: `${name} isn't connected`, route: "/business/integrations", cta: "Open" });
  }
  if (ops && !ops.starter_workflow_exists && ops.workflows.length === 0) {
    items.push({ id: "no-workflow", severity: "info", text: "No workflow alerts your team about new leads", route: "/business/workflows", cta: "Create workflow" });
  }
  return items.sort((a, b) => RANK[a.severity] - RANK[b.severity]).slice(0, 8);
}

export interface HealthTile {
  key: string;
  label: string;
  value: string;
  hint: string;
  route: string;
  state?: ReadinessState;
}

export function buildHealth(overview: BuilderOverview, ops: BusinessOperations | null): HealthTile[] {
  const wf = workforceLabel(overview.workforce);
  const w = overview.website;
  const tiles: HealthTile[] = [
    { key: "website", label: "Website", value: w.published ? "Published" : w.exists ? "Draft" : "Not generated", hint: w.published ? "Taking enquiries" : "Not taking enquiries yet", route: "/website", state: w.published ? "READY" : w.exists ? "CONFIGURATION_REQUIRED" : "NOT_READY" },
    { key: "workforce", label: "AI workforce", value: wf.label, hint: ops ? (ops.ai.interactions > 0 ? `${ops.ai.interactions} conversation${ops.ai.interactions === 1 ? "" : "s"} recorded` : "No conversations yet") : "", route: "/workforce", state: wf.state },
  ];
  if (!ops) return tiles;
  const running = ops.workflows.filter((x) => x.status === "ENABLED").length;
  const failed = ops.workflows.reduce((n, x) => n + x.failed_runs, 0);
  const connected = ops.integrations.filter((i) => i.state === "CONNECTED").length;
  const available = ops.integrations.filter((i) => i.state === "AVAILABLE").length;
  const pending = ops.attention.reduce((n, a) => n + (a.count || 0), 0);
  return [
    { key: "leads", label: "Leads", value: String(ops.leads.total), hint: `${ops.leads.new_7d} new this week`, route: "/leads" },
    { key: "opportunities", label: "Active opportunities", value: String(ops.leads.qualified), hint: "Qualified or beyond", route: "/leads" },
    { key: "pending", label: "Pending actions", value: String(pending), hint: pending === 0 ? "Nothing is waiting" : "Waiting on your team", route: "/business/home#action-center" },
    { key: "workflows", label: "Workflows", value: ops.workflows.length === 0 ? "None" : `${running} of ${ops.workflows.length} running`, hint: failed ? `${failed} failed run${failed === 1 ? "" : "s"}` : "No failures", route: "/business/workflows" },
    tiles[1],
    { key: "integrations", label: "Integrations", value: `${connected} connected`, hint: `${available} available to connect`, route: "/business/integrations" },
    tiles[0],
  ];
}

export interface ChainNode {
  key: string;
  label: string;
  detail: string;
  state: ReadinessState;
  route: string;
}

/** Website → AI workforce → Leads → Klaros → (module data / CRM / calendar) → Operations. */
export function buildArchitectureChain(overview: BuilderOverview, ops: BusinessOperations | null): ChainNode[] {
  const wf = workforceLabel(overview.workforce);
  const w = overview.website;
  const moduleData = (ops?.module.data ?? []).filter((d) => d.key !== "patient_leads" && d.key !== "consultations" && d.key !== "referral_commissions").slice(0, 2);
  const calendar = ops?.integrations.find((i) => i.category === "Calendar");
  const running = ops ? ops.workflows.filter((x) => x.status === "ENABLED").length : 0;
  return [
    { key: "website", label: "Website", detail: w.published ? "Published" : w.exists ? "Draft" : "Not generated", state: w.published ? "READY" : w.exists ? "CONFIGURATION_REQUIRED" : "NOT_READY", route: "/website" },
    { key: "workforce", label: "AI workforce", detail: wf.label, state: wf.state, route: "/workforce" },
    { key: "leads", label: "Leads", detail: ops ? `${ops.leads.total} so far` : "…", state: "READY", route: "/leads" },
    { key: "klaros", label: "Klaros", detail: "Qualifies, matches, routes", state: "READY", route: "/business/map" },
    {
      key: "systems",
      label: [...moduleData.map((d) => d.label), "CRM", "Calendar"].join(" · "),
      detail: [...moduleData.map((d) => `${d.label}: ${d.count ?? 0}`), calendar ? (calendar.state === "CONNECTED" ? "Calendar connected" : "Calendar not connected") : null].filter(Boolean).join(" · ") || "CRM ready",
      state: calendar && calendar.state === "CONNECTED" ? "CONNECTED" : "READY",
      route: "/business/data",
    },
    { key: "operations", label: "Operations", detail: ops ? (running ? `${running} workflow${running === 1 ? "" : "s"} running` : "No workflow running") : "…", state: running ? "READY" : "CONFIGURATION_REQUIRED", route: "/business/workflows" },
  ];
}

export interface LaunchRow {
  key: string;
  group: string;
  value: string;
  state: ReadinessState;
  detail: string;
  route: string | null;
  blocking: boolean;
}

/** The seven launch groups, each with a plain verdict. The authoritative readiness verdict is
 * `overview.launch` — this only presents it; it never decides readiness itself. */
export function buildLaunchRows(overview: BuilderOverview, ops: BusinessOperations | null): LaunchRow[] {
  const item = (k: string) => overview.launch.items.find((i) => i.key === k);
  const bp = item("blueprint");
  const reqs = item("requirements");
  const web = item("website");
  const wf = workforceLabel(overview.workforce);
  const dataItems = overview.launch.items.filter((i) => i.key.startsWith("data:"));
  const dataNotReady = dataItems.filter((i) => i.status === "NOT_READY");
  const wanted = new Set<string>();
  const connected = new Set<string>();
  for (const r of overview.requirements) {
    for (const p of r.providers.filter((x) => !x.adapter_required)) {
      wanted.add(p.provider_key);
      if (p.state === "CONNECTED") connected.add(p.provider_key);
    }
  }
  const rows: LaunchRow[] = [
    { key: "business", group: "Business", value: bp?.status === "READY" ? "Defined" : "Not confirmed", state: bp?.status ?? "NOT_READY", detail: bp?.detail ?? "", route: bp?.route ?? "/business/blueprint", blocking: !!bp?.required && !bp?.done },
    { key: "architecture", group: "Architecture", value: overview.requirements.length > 0 ? "Generated" : "Not generated", state: reqs?.status ?? (overview.requirements.length > 0 ? "READY" : "NOT_READY"), detail: reqs?.detail ?? "", route: "/business/map", blocking: !!reqs?.required && !reqs?.done },
    { key: "website", group: "Website", value: overview.website.published ? "Published" : overview.website.exists ? "Draft" : "Not generated", state: web?.status ?? "NOT_READY", detail: web?.detail ?? "", route: "/website", blocking: !!web?.required && !web?.done },
    {
      key: "integrations", group: "Integrations", value: wanted.size === 0 ? "None needed" : `${connected.size}/${wanted.size} connected`,
      state: wanted.size === 0 || connected.size === wanted.size ? "READY" : "NOT_CONNECTED",
      detail: wanted.size === 0 ? "Your business doesn't depend on an external integration." : "Connect the tools your business depends on.", route: "/business/integrations", blocking: false,
    },
    { key: "workforce", group: "AI workforce", value: wf.state === "CONNECTED" ? "Connected" : wf.state === "INTEGRATION_REQUIRED" ? "Requires Halla integration" : "Requires Halla configuration", state: wf.state, detail: "Doesn't block launch — your team handles customers until it is connected.", route: "/workforce", blocking: false },
    { key: "workflows", group: "Workflows", value: ops ? (ops.workflows.length === 0 ? "None configured" : `${ops.workflows.length} configured`) : "…", state: ops && ops.workflows.length > 0 ? "READY" : "CONFIGURATION_REQUIRED", detail: ops && ops.workflows.length > 0 ? "Automation is set up." : "Add a workflow so new leads trigger an alert.", route: "/business/workflows", blocking: false },
    { key: "data", group: "Data", value: dataNotReady.length === 0 ? "Ready" : `${dataNotReady.length} to add`, state: dataNotReady.length === 0 ? "READY" : "NOT_READY", detail: dataNotReady[0]?.detail ?? "Your records are in place.", route: dataNotReady[0]?.route ?? "/business/data", blocking: dataNotReady.some((i) => i.required) },
  ];
  return rows;
}

/**
 * One real figure per Business Map node, where Klaros actually has one: lead counts for the
 * lead-facing nodes, the website's publish state, the workforce's recorded conversations, and an
 * industry module's record counts for any capability that links to that module's own page.
 * Matched on ids and routes only — never on a business or vertical name. A node with no real
 * figure simply has none.
 */
export function buildMapMetrics(overview: BuilderOverview, ops: BusinessOperations | null): Record<string, string> {
  const out: Record<string, string> = {};
  const w = overview.website;
  const wf = workforceLabel(overview.workforce);
  const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  for (const n of overview.business_map.nodes) {
    if (n.id === "cap:website") out[n.id] = w.published ? "Published" : w.exists ? "Draft — not published" : "Not generated";
  }
  if (!ops) return out;
  const records = [...ops.module.data, ...ops.module.metrics];
  const running = ops.workflows.filter((x) => x.status === "ENABLED").length;
  for (const n of overview.business_map.nodes) {
    if (n.id === "actor:customers") out[n.id] = `${plural(ops.leads.total, "lead")} · ${ops.leads.new_7d} new this week`;
    else if (n.id === "workforce:ai") out[n.id] = ops.ai.interactions > 0 ? `${wf.label} · ${plural(ops.ai.interactions, "conversation")}` : `${wf.label} · no conversations yet`;
    else if (n.id === "core:klaros") out[n.id] = running ? plural(running, "workflow") + " running" : "No workflow running";
    else if (n.id === "cap:lead_capture" || n.id === "cap:lead_qualification") out[n.id] = `${plural(ops.leads.total, "lead")} · ${ops.leads.qualified} qualified`;
    else if (n.route && n.id.startsWith("cap:")) {
      const hit = records.find((r) => r.route === n.route && (r.count ?? r.value) !== undefined);
      if (hit) out[n.id] = `${hit.count ?? hit.value} ${hit.label.toLowerCase()}`;
      else if (n.route === "/leads") out[n.id] = plural(ops.leads.total, "lead");
    } else if (n.id === "outcome:revenue") {
      const c = ops.module.metrics.find((m) => m.key.includes("commission"));
      if (c) out[n.id] = `${c.value ?? 0} ${c.label.toLowerCase()}`;
    }
  }
  return out;
}
