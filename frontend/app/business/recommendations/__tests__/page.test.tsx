import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => "/business/recommendations",
}));

const getBuilderOverviewMock = vi.fn();
const listRecommendationsMock = vi.fn();
const acceptRecommendationMock = vi.fn();
const rejectRecommendationMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getBuilderOverview: (...args: unknown[]) => getBuilderOverviewMock(...args),
  listRecommendations: (...args: unknown[]) => listRecommendationsMock(...args),
  acceptRecommendation: (...args: unknown[]) => acceptRecommendationMock(...args),
  rejectRecommendation: (...args: unknown[]) => rejectRecommendationMock(...args),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", tenant_id: "t1", email: "owner@example.com", full_name: "Test Owner", role: "OWNER" },
    loading: false,
    error: null,
  }),
}));

import RecommendationsPage from "@/app/business/recommendations/page";

const STAGES = [{ key: "recommendations", label: "Recommendations", state: "current", route: "/business/recommendations" }];

function overview(status: string, requirements: unknown[] = []) {
  return {
    journey: { id: "j1", status, discovery_session_id: "d1", blueprint_id: "b1", recommendation_run_id: "r1" },
    blueprint: { id: "b1", version: 1, status: "ACTIVE" },
    business: {},
    requirements,
    business_map: { nodes: [], edges: [], lanes: [] },
    next_actions: [],
    launch: { items: [], ready: false, blocking: [], launched: false },
    stages: STAGES,
    website: { exists: false, published: false, has_draft: false, pages: [] },
    workforce: { provider: "halla", status: "NOT_CONNECTED", adapter_implemented: false, message: "", agent_id: null, capabilities: [] },
    operations: { lead_count: 0, modules: [] },
    modules: [],
  };
}

const REQ: Record<string, unknown> = {
  key: "payment_processing",
  label: "Payments",
  group: "Finance",
  description: "",
  required: true,
  why: "You told us this business needs it.",
  technical_why: "",
  source: "blueprint",
  confidence: 0.9,
  evidence: [],
  klaros_support: "INTEGRATION",
  native_route: null,
  readiness: "NOT_CONNECTED",
  readiness_detail: "Klaros can connect a provider for this — not connected yet.",
  workforce_addressable: false,
  providers: [{ provider_key: "stripe", display_name: "Stripe", implementation_status: "REAL", state: "NOT_CONNECTED", adapter_required: false }],
  next_step: { text: "Connect Stripe in Integrations.", route: "/settings/integrations" },
  recommendation_id: "cap1",
  recommendation_ids: ["cap1", "rec1", "rec-tool"],
  recommendation_status: "PROPOSED",
};

function rec(overrides: Record<string, unknown> = {}) {
  return {
    id: "rec1",
    run_id: "r1",
    blueprint_id: "b1",
    blueprint_version: 1,
    type: "INTEGRATION",
    capability_key: "payment_processing",
    provider_key: "stripe",
    provider_implementation_status: "REAL",
    tool_name: null,
    what: "Consider Stripe to satisfy 'payment_processing'.",
    why: "Stripe is catalogued as supporting payments.",
    based_on: null,
    dependencies: ["payment_processing"],
    cost_estimate: null,
    required: false,
    alternatives: ["square"],
    confidence: 0.8,
    source: "BASELINE_RULE",
    source_vertical_key: null,
    status: "PROPOSED",
    decided_by: null,
    decided_at: null,
    rejection_reason: null,
    created_at: "x",
    updated_at: "x",
    ...overrides,
  };
}

const capRec = () => rec({ id: "cap1", type: "CAPABILITY", provider_key: null, provider_implementation_status: null, what: "This business needs the 'payment_processing' capability.", required: true, alternatives: [], dependencies: [] });

describe("Recommendations page", () => {
  beforeEach(() => {
    getBuilderOverviewMock.mockReset();
    listRecommendationsMock.mockReset();
    acceptRecommendationMock.mockReset();
    rejectRecommendationMock.mockReset();
    pushMock.mockReset();
    replaceMock.mockReset();
  });

  it("groups recommendations under the requirement they serve, with its honest state", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([capRec(), rec()]);
    render(<RecommendationsPage />);
    expect(await screen.findByText("Payments")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByText("Integrations that could cover it")).toBeInTheDocument();
    // Plain-language WHAT / WHY — never the engine's internal wording.
    expect(screen.getAllByText("Stripe — covers “Payments”").length).toBeGreaterThan(0);
    expect(screen.getByText(/Your business needs payments, and Stripe is a supported way to provide it/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/catalogued|IntegrationProviderCatalog|implementation_status|Phase 1/);
  });

  it("says a REAL integration is available now but needs the user's own account", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([capRec(), rec()]);
    render(<RecommendationsPage />);
    expect(await screen.findByText(/Integration available — you connect your own account/)).toBeInTheDocument();
    expect(screen.getByText("Available — not connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect it in Integrations." })).toHaveAttribute("href", "/settings/integrations");
  });

  it("shows Connected only when the backend says the provider is connected", async () => {
    const connectedReq = { ...REQ, providers: [{ provider_key: "stripe", display_name: "Stripe", implementation_status: "REAL", state: "CONNECTED", adapter_required: false }] };
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [connectedReq]));
    listRecommendationsMock.mockResolvedValue([capRec(), rec()]);
    render(<RecommendationsPage />);
    expect(await screen.findByText("Connected.")).toBeInTheDocument();
    expect(screen.queryByText(/Integration available/)).not.toBeInTheDocument();
  });

  it("marks a non-REAL provider as Planned/adapter required, never as available", async () => {
    const stubReq = { ...REQ, providers: [{ provider_key: "stripe", display_name: "Stripe", implementation_status: "STUB", state: "PLANNED", adapter_required: true }] };
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [stubReq]));
    listRecommendationsMock.mockResolvedValue([capRec(), rec({ provider_implementation_status: "STUB" })]);
    render(<RecommendationsPage />);
    expect(await screen.findByText(/Integration adapter required/)).toBeInTheDocument();
    expect(screen.getByText("Integration required")).toBeInTheDocument();
    // No fake action: nothing links to Integrations for a provider that cannot be connected.
    expect(screen.queryByRole("link", { name: /Connect/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Integration available/)).not.toBeInTheDocument();
  });

  it("tucks name-matched Klaros tools behind a caveated disclosure instead of recommending them", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([
      capRec(),
      rec(),
      rec({ id: "rec-tool", type: "TOOL", provider_key: null, provider_implementation_status: null, tool_name: "finance.record_payout" }),
    ]);
    render(<RecommendationsPage />);
    expect(await screen.findByText(/1 related Klaros tool \(matched by name only/)).toBeInTheDocument();
    expect(screen.getAllByText("Accept")).toHaveLength(1); // only the integration is a decision
  });

  it("does not ask the user to accept a capability they themselves stated as required", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([capRec()]);
    render(<RecommendationsPage />);
    await screen.findByText("Payments");
    expect(screen.queryByText("Accept")).not.toBeInTheDocument();
  });

  it("asks about a merely-suggested capability", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [{ ...REQ, required: false, source: "industry_module" }]));
    listRecommendationsMock.mockResolvedValue([capRec()]);
    render(<RecommendationsPage />);
    expect((await screen.findAllByText("Include “Payments” in your business")).length).toBeGreaterThan(0);
  });

  it("shows required vs optional and never invents a cost", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([capRec(), rec()]);
    render(<RecommendationsPage />);
    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(screen.queryByText(/\$|cost/i)).not.toBeInTheDocument();
  });

  it("accepts a recommendation through the backend and reconciles its displayed status", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([rec()]);
    acceptRecommendationMock.mockResolvedValue(rec({ status: "ACCEPTED", decided_by: "u1" }));
    render(<RecommendationsPage />);
    fireEvent.click(await screen.findByText("Accept"));
    await waitFor(() => expect(acceptRecommendationMock).toHaveBeenCalledWith("test-token", "rec1"));
    expect(await screen.findByText("Accepted")).toBeInTheDocument();
  });

  it("rejects a recommendation through the backend", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([rec()]);
    rejectRecommendationMock.mockResolvedValue(rec({ status: "REJECTED" }));
    render(<RecommendationsPage />);
    fireEvent.click(await screen.findByText("Not for me"));
    await waitFor(() => expect(rejectRecommendationMock).toHaveBeenCalledWith("test-token", "rec1"));
  });

  it("continues to the business map without completing the journey", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("RECOMMENDATIONS_READY", [REQ]));
    listRecommendationsMock.mockResolvedValue([rec()]);
    render(<RecommendationsPage />);
    fireEvent.click(await screen.findByText(/Continue to business map/));
    expect(pushMock).toHaveBeenCalledWith("/business/map");
  });

  it("redirects away when recommendations haven't actually been generated yet", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview("BLUEPRINT_ACTIVE"));
    render(<RecommendationsPage />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/business/requirements"));
    expect(listRecommendationsMock).not.toHaveBeenCalled();
  });

  it("shows a loading state while the overview is being fetched", async () => {
    getBuilderOverviewMock.mockReturnValue(new Promise(() => {}));
    render(<RecommendationsPage />);
    expect(screen.getByLabelText("Loading")).toBeInTheDocument();
  });

  it("explains a load failure and offers a retry", async () => {
    const { ApiError } = await import("@/lib/api");
    getBuilderOverviewMock.mockRejectedValue(new ApiError(500, "Internal server error"));
    render(<RecommendationsPage />);
    expect(await screen.findByText("Internal server error")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
