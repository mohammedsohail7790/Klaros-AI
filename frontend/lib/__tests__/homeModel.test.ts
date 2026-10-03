import { describe, expect, it } from "vitest";
import { opsFixture, overviewFixture } from "./opsFixtures";
import {
  buildActionCenter,
  buildArchitectureChain,
  buildHealth,
  buildLaunchRows,
  buildMapMetrics,
  unconnectedProviders,
  workforceLabel,
} from "@/components/business/homeModel";
import type { BuilderOverview, BusinessOperations } from "@/lib/api";

const ov = (o: Record<string, unknown> = {}) => overviewFixture(o) as unknown as BuilderOverview;
const op = (o: Record<string, unknown> = {}) => opsFixture(o) as unknown as BusinessOperations;

describe("workforceLabel", () => {
  it("says Integration required while no adapter exists, never Connected", () => {
    expect(workforceLabel(ov().workforce)).toEqual({ label: "Integration required", state: "INTEGRATION_REQUIRED" });
  });
  it("says Connected only for CONNECTED", () => {
    expect(workforceLabel({ ...ov().workforce, status: "CONNECTED", adapter_implemented: true }).state).toBe("CONNECTED");
    expect(workforceLabel({ ...ov().workforce, status: "NOT_CONNECTED", adapter_implemented: true }).label).toBe("Not connected");
  });
});

describe("workforceLabel — the real adapter's states", () => {
  const live = (status: string) => ({ ...ov().workforce, status, adapter_implemented: true, mode: "live" }) as unknown as BuilderOverview["workforce"];
  it("says exactly what the backend reported, and nothing is Connected but CONNECTED", () => {
    expect(workforceLabel(live("CONNECTING"))).toEqual({ label: "Connecting", state: "CONFIGURATION_REQUIRED" });
    expect(workforceLabel(live("NEEDS_ATTENTION"))).toEqual({ label: "Needs attention", state: "CONFIGURATION_REQUIRED" });
    expect(workforceLabel(live("ERROR"))).toEqual({ label: "Connection error", state: "NOT_READY" });
    expect(workforceLabel(live("NOT_CONNECTED"))).toEqual({ label: "Not connected", state: "NOT_CONNECTED" });
    expect(workforceLabel(live("CONNECTED")).state).toBe("CONNECTED");
  });
  it("turns a connection error into an action, not a silent state", () => {
    const o = ov({ workforce: live("ERROR") });
    expect(buildActionCenter(o, op()).find((a) => a.id === "workforce")?.text).toBe("The connection to Halla needs attention");
  });
});

describe("unconnectedProviders", () => {
  it("lists only real-adapter providers of requirements with nothing connected, once each", () => {
    expect(unconnectedProviders(ov())).toEqual(["Google Calendar"]); // Stripe is connected; PayPal is planned
  });
});

describe("buildActionCenter", () => {
  it("turns real attention items into actions, most urgent first", () => {
    const items = buildActionCenter(
      ov(),
      op({ attention: [{ id: "new-leads", text: "2 new leads waiting", route: "/leads?status=NEW", count: 2 }, { id: "failed-runs", text: "1 workflow run failed", route: "/business/workflows", count: 1 }] })
    );
    expect(items[0]).toMatchObject({ id: "failed-runs", severity: "danger" });
    expect(items[1]).toMatchObject({ id: "new-leads", severity: "warning" });
  });
  it("adds Halla, disconnected integrations and a missing workflow from real state", () => {
    const ids = buildActionCenter(ov(), op({ starter_workflow_exists: false, workflows: [] })).map((a) => a.id);
    expect(ids).toEqual(expect.arrayContaining(["workforce", "connect-Google Calendar", "no-workflow"]));
    const halla = buildActionCenter(ov(), op()).find((a) => a.id === "workforce")!;
    expect(halla.text).toMatch(/needs to be integrated/);
  });
  it("flags a draft website and unpublished changes, and says nothing when all is well", () => {
    expect(buildActionCenter(ov({ website: { exists: true, published: false, has_draft: true, pages: [] } }), op()).some((a) => a.id === "website-draft")).toBe(true);
    expect(buildActionCenter(ov({ website: { exists: true, published: true, has_draft: true, pages: [] } }), op()).some((a) => a.id === "website-changes")).toBe(true);
    const clean = buildActionCenter(
      ov({ requirements: [], workforce: { ...ov().workforce, status: "CONNECTED", adapter_implemented: true } }),
      op()
    );
    expect(clean).toEqual([]);
  });
  it("works before operations load", () => {
    expect(() => buildActionCenter(ov(), null)).not.toThrow();
  });
});

describe("buildHealth", () => {
  it("uses real counts and never invents a metric", () => {
    const tiles = Object.fromEntries(buildHealth(ov(), op()).map((t) => [t.key, t]));
    expect(tiles.leads).toMatchObject({ value: "4", hint: "3 new this week" });
    expect(tiles.opportunities.value).toBe("2");
    expect(tiles.workflows.value).toBe("1 of 1 running");
    expect(tiles.workforce).toMatchObject({ value: "Integration required", hint: "No conversations yet" });
    expect(tiles.integrations).toMatchObject({ value: "1 connected", hint: "1 available to connect" });
    expect(tiles.website.value).toBe("Published");
  });
  it("reports recorded AI conversations and pending actions from real counts", () => {
    const tiles = Object.fromEntries(
      buildHealth(ov(), op({ ai: { events: {}, interactions: 2, qualified: 1, escalated: 1, needs_person: 1 }, attention: [{ id: "a", text: "t", route: "/x", count: 3 }] })).map((t) => [t.key, t])
    );
    expect(tiles.workforce.hint).toBe("2 conversations recorded");
    expect(tiles.pending.value).toBe("3");
  });
  it("shows only website and workforce before operations load", () => {
    expect(buildHealth(ov(), null).map((t) => t.key)).toEqual(["website", "workforce"]);
  });
});

describe("buildArchitectureChain", () => {
  it("is Website → AI workforce → Leads → Klaros → systems → Operations with real states", () => {
    const chain = buildArchitectureChain(ov(), op());
    expect(chain.map((n) => n.key)).toEqual(["website", "workforce", "leads", "klaros", "systems", "operations"]);
    expect(chain[1]).toMatchObject({ detail: "Integration required", state: "INTEGRATION_REQUIRED" });
    expect(chain[2].detail).toBe("4 so far");
    expect(chain[4].label).toContain("Providers");
    expect(chain[4].detail).toContain("Providers: 2");
    expect(chain[4].detail).toContain("Calendar connected");
    expect(chain[5].detail).toBe("1 workflow running");
  });
  it("says Calendar not connected when it is not", () => {
    const o = op();
    o.integrations = o.integrations.map((i) => (i.category === "Calendar" ? { ...i, state: "AVAILABLE" } : i));
    expect(buildArchitectureChain(ov(), o)[4].detail).toContain("Calendar not connected");
  });
});

describe("buildLaunchRows", () => {
  it("presents the backend verdict without deciding readiness itself", () => {
    const rows = Object.fromEntries(buildLaunchRows(ov(), op()).map((r) => [r.key, r]));
    expect(rows.business).toMatchObject({ value: "Defined", state: "READY", blocking: false });
    expect(rows.architecture.value).toBe("Generated");
    expect(rows.website.value).toBe("Published");
    expect(rows.integrations.value).toBe("1/2 connected");
    expect(rows.workforce).toMatchObject({ value: "Requires Halla integration", state: "INTEGRATION_REQUIRED", blocking: false });
    expect(rows.workflows.value).toBe("1 configured");
    expect(rows.data).toMatchObject({ value: "1 to add", state: "NOT_READY", blocking: true });
  });
  it("marks required unfinished items as blocking", () => {
    const rows = buildLaunchRows(
      ov({ launch: { ...ov().launch, items: ov().launch.items.map((i) => (i.key === "blueprint" ? { ...i, status: "NOT_READY", done: false } : i)) } }),
      op()
    );
    expect(rows.find((r) => r.key === "business")).toMatchObject({ value: "Not confirmed", blocking: true });
  });
});

describe("buildMapMetrics", () => {
  it("gives real figures to the nodes that have one — matched on ids and routes", () => {
    const m = buildMapMetrics(ov(), op());
    expect(m["actor:customers"]).toBe("4 leads · 3 new this week");
    expect(m["cap:website"]).toBe("Published");
    expect(m["workforce:ai"]).toBe("Integration required · no conversations yet");
    expect(m["core:klaros"]).toBe("1 workflow running");
    expect(m["cap:lead_capture"]).toBe("4 leads · 2 qualified");
    expect(m["cap:provider_directory"]).toBe("2 providers"); // matched through the module's own route
    expect(m["cap:analytics"]).toBeUndefined(); // no real figure → none shown
  });
  it("shows only the website state before operations load", () => {
    expect(buildMapMetrics(ov(), null)).toEqual({ "cap:website": "Published" });
  });
  it("counts recorded conversations for the workforce node", () => {
    expect(buildMapMetrics(ov(), op({ ai: { events: {}, interactions: 1, qualified: 0, escalated: 0, needs_person: 0 } }))["workforce:ai"]).toBe("Integration required · 1 conversation");
  });
});
