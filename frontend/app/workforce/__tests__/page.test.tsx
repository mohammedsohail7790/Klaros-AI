import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/workforce" }));
const getWorkforceSetupMock = vi.fn();
const getOpsMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getWorkforceSetup: (...a: unknown[]) => getWorkforceSetupMock(...a),
  getBuilderOverview: vi.fn().mockResolvedValue({ journey: null, requirements: [] }),
  getBusinessOperations: (...a: unknown[]) => getOpsMock(...a),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "t", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));

import WorkforcePage from "@/app/workforce/page";

const members = [
  { key: "receptionist", name: "AI Receptionist", purpose: "Answers incoming enquiries.", status: "AVAILABLE_THROUGH_HALLA", status_label: "Available through Halla", capabilities: ["Incoming calls", "Lead qualification"] },
  { key: "followup", name: "AI Follow-up Agent", purpose: "Follows up with leads.", status: "AVAILABLE_THROUGH_HALLA", status_label: "Available through Halla", capabilities: ["Outbound communication"] },
  { key: "scheduling", name: "AI Scheduling Agent", purpose: "Runs the booking conversation.", status: "AVAILABLE_THROUGH_HALLA", status_label: "Available through Halla", capabilities: ["Appointment booking"] },
];
const steps = [
  { key: "connect", label: "Connect Halla", state: "BLOCKED", detail: "Integration required — the Halla adapter has not been built." },
  { key: "agent", label: "Select and configure an agent", state: "BLOCKED", detail: "Needs a connected Halla account first." },
  { key: "context", label: "Define business context", state: "READY", detail: "Prepared by Klaros from your business — review it below." },
  { key: "test", label: "Test", state: "BLOCKED", detail: "Needs a connected Halla account first." },
  { key: "activate", label: "Activate", state: "BLOCKED", detail: "Needs a connected Halla account first." },
];
const setup = (over: Record<string, unknown> = {}, status: Record<string, unknown> = {}) => ({
  status: { provider: "halla", status: "NOT_CONNECTED", adapter_implemented: false, mode: "none", message: "No AI workforce is connected.", agent_id: null, ...status },
  channels: [],
  members,
  steps,
  context: {
    business_name: "Aurora Medical Travel", industry: "Medical tourism", summary: "s", customers: "Patients from the Gulf",
    services: ["Hair transplant", "Dental implants"], markets: ["IN", "TR"],
    qualification_fields: ["Treatment of interest", "Budget"], escalation_triggers: ["Complex medical questions", "A complaint"], booking_rules: ["Book only into your connected calendar"],
  },
  dev_simulator: false,
  ...over,
});
const ops = (over: Record<string, unknown> = {}) => ({ agents: [], ai: { events: {}, interactions: 0, qualified: 0, escalated: 0, needs_person: 0 }, ...over });

describe("AI workforce page", () => {
  beforeEach(() => {
    getWorkforceSetupMock.mockReset();
    getOpsMock.mockReset();
    getOpsMock.mockResolvedValue(ops());
  });

  it("explains Integration required and offers no control that cannot work", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    expect(await screen.findByText("Connect Halla to enable AI workforce capabilities.")).toBeInTheDocument();
    expect(screen.getAllByText("Integration required").length).toBeGreaterThan(1);
    expect(screen.getByText(/connection itself isn.t built yet/)).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /connect|deploy|configure|sign in|activate|test/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /About Halla AI/ })).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("separates Klaros (the business) from Halla (the interaction layer)", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    expect(await screen.findByText(/Klaros — the business/)).toBeInTheDocument();
    expect(screen.getByText(/Halla AI — the interaction layer/)).toBeInTheDocument();
    expect(screen.getByText(/A separate platform that speaks with customers/)).toBeInTheDocument();
  });

  it("shows the three workforce members as Available through Halla — none active", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    const list = (await screen.findByRole("heading", { name: "Workforce members" })).closest("section")!;
    expect(within(list).getAllByText("Available through Halla")).toHaveLength(3);
    expect(within(list).getByText("AI Receptionist")).toBeInTheDocument();
    expect(within(list).queryByText("Active")).not.toBeInTheDocument();
  });

  it("locks the steps that need Halla and prepares the Klaros side", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    const section = (await screen.findByRole("heading", { name: "Set up your AI workforce" })).closest("section")!;
    expect(within(section).getAllByText("Locked").length).toBe(4);
    expect(within(section).getByText("Prepared")).toBeInTheDocument();
    expect(within(section).getAllByText("Needs a connected Halla account first.").length).toBe(3);
  });

  it("previews the business context, qualification and escalation rules Klaros prepared", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    expect(await screen.findByText("Hair transplant")).toBeInTheDocument();
    expect(screen.getByText("TR")).toBeInTheDocument();
    expect(screen.getByText("Budget")).toBeInTheDocument();
    expect(screen.getByText("Complex medical questions")).toBeInTheDocument();
    expect(screen.getByText("Patients from the Gulf")).toBeInTheDocument();
  });

  it("labels the development simulator as not live and still never says Connected", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup({ dev_simulator: true }, { mode: "development", message: "Development simulator — not a live Halla connection." }));
    render(<WorkforcePage />);
    expect(await screen.findByText("Development simulator — not live")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
  });

  it("reports recorded AI conversations only from real counts", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    getOpsMock.mockResolvedValue(ops({ ai: { events: {}, interactions: 3, qualified: 2, escalated: 1, needs_person: 1 } }));
    render(<WorkforcePage />);
    expect(await screen.findByText(/3 conversations recorded · 2 qualified · 1 escalated to a person/)).toBeInTheDocument();
  });

  it("shows no conversations when none were recorded", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    expect(await screen.findByText("No AI conversations have been recorded.")).toBeInTheDocument();
  });

  it("shows Connected only when the backend says CONNECTED", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup({}, { status: "CONNECTED", adapter_implemented: true, mode: "live", message: "Healthy." }));
    render(<WorkforcePage />);
    expect((await screen.findAllByText("Connected")).length).toBeGreaterThan(0);
  });

  it("lists the parts of the business it would serve", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    const api = await import("@/lib/api");
    (api.getBuilderOverview as ReturnType<typeof vi.fn>).mockResolvedValue({
      journey: { id: "j" }, stages: [],
      requirements: [{ label: "Lead qualification", workforce_addressable: true }, { label: "Website", workforce_addressable: false }],
    });
    render(<WorkforcePage />);
    const needs = (await screen.findByRole("heading", { name: /Parts of your business/ })).closest("section")!;
    expect(needs).toHaveTextContent("Lead qualification");
    expect(needs).not.toHaveTextContent("Website");
  });

  it("explains a failure and offers a retry", async () => {
    getWorkforceSetupMock.mockRejectedValueOnce(new Error("network down"));
    getWorkforceSetupMock.mockResolvedValue(setup());
    render(<WorkforcePage />);
    expect(await screen.findByText(/couldn.t check your AI workforce/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("No AI workforce is connected.")).toBeInTheDocument();
  });

  it("explains a permission failure", async () => {
    const { ApiError } = await import("@/lib/api");
    getWorkforceSetupMock.mockRejectedValue(new (ApiError as unknown as new (s: number, m: string) => Error)(403, "Forbidden"));
    render(<WorkforcePage />);
    expect(await screen.findByText("You don't have permission to view the AI workforce.")).toBeInTheDocument();
  });
});

describe("AI workforce — Klaros agents next to the Halla boundary", () => {
  it("shows each Klaros agent in plain language with its real permissions and history", async () => {
    getWorkforceSetupMock.mockResolvedValue(setup());
    getOpsMock.mockResolvedValue(
      ops({
        agents: [
          { id: "a1", name: "Patient Qualification Agent", purpose: "Qualify new enquiries.", status: "ACTIVE", autonomy_tier: "RECOMMEND", tools: ["crm.qualify_lead"], executions: 2, last_execution: { status: "SUCCEEDED", at: new Date().toISOString() } },
          { id: "a2", name: "Draft agent", purpose: "", status: "DRAFT", autonomy_tier: "OBSERVE", tools: [], executions: 0, last_execution: null },
        ],
      })
    );
    render(<WorkforcePage />);
    expect(await screen.findByRole("link", { name: "Patient Qualification Agent" })).toHaveAttribute("href", "/agents/a1");
    expect(screen.getByText("Suggests — a person decides")).toBeInTheDocument();
    expect(screen.getByText("Record qualification")).toBeInTheDocument();
    expect(screen.getByText(/2 runs · last succeeded/)).toBeInTheDocument();
    expect(screen.getByText("No tools granted — it cannot act.")).toBeInTheDocument();
    expect(screen.getByText("Hasn't run yet.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/crm\.qualify_lead|RECOMMEND\b/);
  });
});


describe("AI workforce — real Halla adapter enabled", () => {
  const live = (status: string, halla: Record<string, unknown> = {}) =>
    setup({ halla: { halla_tenant_id: null, has_credential: false, has_signing_secret: false, last_verified_at: null, last_error: null, webhook_url: "https://klaros.test/api/v1/webhooks/halla/t", ...halla } }, { status, adapter_implemented: true, mode: "live", message: "Halla message" });

  it("shows the connect form (not the 'integration required' notice) and never claims Connected before it is true", async () => {
    getWorkforceSetupMock.mockResolvedValue(live("NOT_CONNECTED"));
    render(<WorkforcePage />);
    expect(await screen.findByRole("form", { name: "Connect Halla" })).toBeInTheDocument();
    expect(screen.queryByText(/connection itself isn.t built yet/)).not.toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not connected").length).toBeGreaterThan(0);
  });

  it("labels a failing connection as an error, with the backend's own message", async () => {
    getWorkforceSetupMock.mockResolvedValue(live("ERROR", { has_credential: true, halla_tenant_id: "ht", last_error: "Halla rejected the credential" }));
    render(<WorkforcePage />);
    expect(await screen.findByText("Connection error")).toBeInTheDocument();
    expect(screen.getByText("Halla rejected the credential")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
  });

  it("shows Connected only for CONNECTED", async () => {
    getWorkforceSetupMock.mockResolvedValue(live("CONNECTED", { has_credential: true, halla_tenant_id: "ht", has_signing_secret: true }));
    const api = await import("@/lib/api");
    (api as unknown as { listHallaAgents: unknown }).listHallaAgents = vi.fn().mockResolvedValue({ agents: [] });
    render(<WorkforcePage />);
    expect((await screen.findAllByText("Connected")).length).toBeGreaterThan(0);
  });
});
