import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock }),
  usePathname: () => "/business/home",
}));
const getBuilderOverviewMock = vi.fn();
const getBusinessOperationsMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getBuilderOverview: (...a: unknown[]) => getBuilderOverviewMock(...a),
  getBusinessOperations: (...a: unknown[]) => getBusinessOperationsMock(...a),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "tenant-1", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));

import BusinessHomePage from "@/app/business/home/page";

function overview(over: Record<string, unknown> = {}) {
  return {
    journey: { id: "j", status: "COMPLETED" },
    blueprint: { id: "b", version: 1, status: "ACTIVE" },
    business: { name: "Gulf Care Connect", industry: "Medical tourism", summary: "Connects patients with hospitals", customers: null, business_model: "Referral commission" },
    requirements: [{ key: "communication", label: "Customer communication", workforce_addressable: true, providers: [] }],
    business_map: { nodes: [], edges: [], lanes: [] },
    next_actions: [{ id: "website:publish", title: "Review and publish your website", detail: "A draft exists.", state: "CONFIGURATION_REQUIRED", route: "/website", kind: "link" }],
    launch: {
      launched: false,
      ready: false,
      verdict: "NOT_READY",
      blocking: ["Website: A draft exists but isn't published — publish it to go live."],
      items: [
        { key: "website", label: "Website", status: "CONFIGURATION_REQUIRED", detail: "A draft exists but isn't published — publish it to go live.", required: true, route: "/website", done: false },
        { key: "workforce", label: "AI workforce", status: "INTEGRATION_REQUIRED", detail: "Connect Halla to enable AI customer communication — the integration isn't available yet.", required: false, route: "/workforce", done: false },
      ],
    },
    progress: [
      { key: "discovery", label: "Discovery", status: "READY", detail: "Completed.", route: "/business/discovery" },
      { key: "website", label: "Website", status: "CONFIGURATION_REQUIRED", detail: "Draft, not published.", route: "/website" },
      { key: "workforce", label: "AI workforce", status: "INTEGRATION_REQUIRED", detail: "Integration required — Halla adapter not available yet.", route: "/workforce" },
      { key: "integrations", label: "Integrations", status: "NOT_CONNECTED", detail: "0 connected.", route: "/settings/integrations" },
      { key: "launch", label: "Launch", status: "NOT_READY", detail: "1 thing left.", route: "/business/home#launch" },
    ],
    stages: [],
    website: { exists: true, published: false, has_draft: true, pages: ["home", "contact"] },
    workforce: { provider: "halla", status: "NOT_CONNECTED", adapter_implemented: false, message: "No AI workforce is connected.", agent_id: null, capabilities: [] },
    operations: { lead_count: 3, modules: [{ key: "provider_directory", label: "Provider / partner directory", count: 2, route: "/medical-tourism/providers" }] },
    modules: [],
    ...over,
  };
}

function ops(over: Record<string, unknown> = {}) {
  return {
    leads: {
      total: 3, new_7d: 2, by_status: { NEW: 2, QUALIFIED: 1 }, by_source: { WEB: 2, CHAT: 1 }, qualified: 1, website_enquiries: 2,
      waiting: [{ id: "l1", name: "Layla Patient", source: "WEB", created_at: new Date().toISOString() }],
      recent: [{ id: "l1", name: "Layla Patient", status: "NEW", source: "WEB", created_at: new Date().toISOString() }],
    },
    customers: 1,
    workflows: [],
    lead_pipeline: [
      { key: "form", label: "Website form submitted", kind: "SYSTEM", state: "READY", detail: "Your published website accepts enquiries.", route: "/website" },
      { key: "notify", label: "Team notified", kind: "AUTOMATED", state: "CONFIGURATION_REQUIRED", detail: "No workflow alerts your team yet — add the starter workflow.", route: "/business/workflows" },
    ],
    agents: [],
    activity: [{ at: new Date().toISOString(), kind: "lead", text: "Lead received: Layla Patient", route: "/leads/l1" }],
    integrations: [
      { provider_key: "stripe", name: "Stripe", category: "Payments", purpose: null, capabilities: [], state: "AVAILABLE", implementation: "REAL", last_error: null },
      { provider_key: "x", name: "X", category: "Marketing", purpose: null, capabilities: [], state: "PLANNED", implementation: "STUB", last_error: null },
    ],
    integration_groups: ["Payments", "Marketing"],
    attention: [{ id: "new-leads", text: "2 new leads waiting for a first response", route: "/leads?status=NEW", count: 2 }],
    module: { metrics: [{ key: "patient_leads", label: "Patient enquiries", value: 1, route: "/medical-tourism/leads" }], data: [{ key: "providers", label: "Providers", count: 2, route: "/medical-tourism/providers" }], breakdowns: [] },
    starter_workflow_exists: false,
    ...over,
  };
}

describe("Business Home — the operating center", () => {
  beforeEach(() => {
    getBuilderOverviewMock.mockReset();
    getBusinessOperationsMock.mockReset();
    getBusinessOperationsMock.mockResolvedValue(ops());
    replaceMock.mockReset();
  });

  it("before the website is live it is 'Prepare your business': progress and blockers, no operating console", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview());
    render(<BusinessHomePage />);
    expect(await screen.findByRole("heading", { level: 1, name: "Gulf Care Connect" })).toBeInTheDocument();
    expect(screen.getByText("Prepare your business")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Launch progress" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Needs your attention" })).not.toBeInTheDocument();
    const ladder = screen.getByRole("list", { name: "Launch progress" });
    expect(within(ladder).getByText("Integration required — Halla adapter not available yet.")).toBeInTheDocument();
    expect(within(ladder).getByText("0 connected.")).toBeInTheDocument();
    expect(screen.getAllByText("Review and publish your website").length).toBeGreaterThan(0);
  });

  it("once the website is live it is 'Operate your business': attention, real lead numbers, pipeline, activity", async () => {
    getBuilderOverviewMock.mockResolvedValue(
      overview({ website: { exists: true, published: true, has_draft: false, pages: ["home"] }, launch: { ...overview().launch, launched: true } })
    );
    render(<BusinessHomePage />);
    expect(await screen.findByText("Operate your business")).toBeInTheDocument();
    for (const h of ["Needs your attention", "Leads", "From website to customer", "Operations", "Recent activity"]) {
      expect(screen.getByRole("heading", { level: 2, name: h })).toBeInTheDocument();
    }
    expect(await screen.findByText("2 new leads waiting for a first response")).toBeInTheDocument();
    expect(screen.getByText("Total leads").nextSibling).toHaveTextContent("3");
    expect(screen.getByText("From your website").nextSibling).toHaveTextContent("2");
    const waiting = screen.getByRole("link", { name: "Layla Patient" });
    expect(waiting).toHaveAttribute("href", "/leads/l1");
    expect(screen.getByRole("list", { name: "How a lead is handled" })).toHaveTextContent("No workflow alerts your team yet");
    expect(screen.getByText("Patient enquiries").closest("a")).toHaveAttribute("href", "/medical-tourism/leads");
    expect(screen.getByRole("list", { name: "Recent activity" })).toHaveTextContent("Lead received: Layla Patient");
    // Honest even while operating: the checklist is incomplete, so it is not plain "Live".
    expect(screen.getByText("Website published — launch checklist incomplete")).toBeInTheDocument();
  });

  it("shows empty-state wording, not invented numbers, for an operating business with no leads", async () => {
    getBuilderOverviewMock.mockResolvedValue(
      overview({ website: { exists: true, published: true, has_draft: false, pages: ["home"] }, launch: { ...overview().launch, launched: true } })
    );
    getBusinessOperationsMock.mockResolvedValue(
      ops({ leads: { total: 0, new_7d: 0, by_status: {}, by_source: {}, qualified: 0, website_enquiries: 0, waiting: [], recent: [] }, attention: [], activity: [] })
    );
    render(<BusinessHomePage />);
    expect(await screen.findByText("Nothing is waiting on you right now.")).toBeInTheDocument();
    expect(screen.getByText(/No leads yet\. They appear here/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing has happened yet/)).toBeInTheDocument();
  });

  it("explains an operations failure without blanking the page", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview());
    getBusinessOperationsMock.mockImplementation(async () => {
      throw new Error("down");
    });
    render(<BusinessHomePage />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Gulf Care Connect" })).toBeInTheDocument();
  });

  it("states why launch is not ready, and optional items never claim to block", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview());
    render(<BusinessHomePage />);
    await screen.findByText("Launch readiness");
    expect(screen.getAllByText("Not ready").length).toBeGreaterThan(0);
    expect(screen.getByText(/A draft exists but isn't published/)).toBeInTheDocument();
    expect(screen.getByText("(doesn't block launch)")).toBeInTheDocument();
    expect(screen.queryByText("Live — website published")).not.toBeInTheDocument();
  });

  it("offers the website action that matches the real state: draft → Publish, published → View Live", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview());
    const { unmount } = render(<BusinessHomePage />);
    expect(await screen.findByRole("link", { name: "Publish Website" })).toHaveAttribute("href", "/website");
    unmount();
    getBuilderOverviewMock.mockResolvedValue(
      overview({ website: { exists: true, published: true, has_draft: false, pages: ["home"] }, launch: { ...overview().launch, launched: true } })
    );
    render(<BusinessHomePage />);
    const live = await screen.findByRole("link", { name: /View Live Website/ });
    expect(live).toHaveAttribute("href", "/w/tenant-1");
    expect(live).toHaveAttribute("target", "_blank");
    // Published, but the checklist is not complete: never plain "Live".
    expect(screen.getByText("Website published — launch checklist incomplete")).toBeInTheDocument();
    expect(screen.queryByText("Live — website published")).not.toBeInTheDocument();
  });

  it("offers Generate Website when none exists, and hides the workforce card when nothing needs one", async () => {
    getBuilderOverviewMock.mockResolvedValue(
      overview({ website: { exists: false, published: false, has_draft: false, pages: [] }, requirements: [{ key: "crm", label: "CRM", workforce_addressable: false, providers: [] }] })
    );
    render(<BusinessHomePage />);
    expect(await screen.findByRole("link", { name: "Generate Website" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "AI workforce" })).not.toBeInTheDocument();
  });

  it("sends a tenant with no business journey to the builder", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview({ journey: null }));
    render(<BusinessHomePage />);
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business"));
  });

  it("explains a load failure and offers to retry", async () => {
    getBuilderOverviewMock.mockImplementation(async () => {
      throw new Error("down");
    });
    render(<BusinessHomePage />);
    expect(await screen.findByText(/couldn't load your business/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
