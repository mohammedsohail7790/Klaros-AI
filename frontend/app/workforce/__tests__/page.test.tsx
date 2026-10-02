import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/workforce" }));
const getWorkforceStatusMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getWorkforceStatus: (...a: unknown[]) => getWorkforceStatusMock(...a),
  getBuilderOverview: vi.fn().mockResolvedValue({ journey: null, requirements: [] }),
  getBusinessOperations: vi.fn().mockResolvedValue({ agents: [] }),
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

const base = {
  provider: "halla",
  agent_id: null,
  capabilities: [
    { key: "voice", label: "Voice", description: "Spoken conversations." },
    { key: "incoming_calls", label: "Incoming calls", description: "Answer calls." },
  ],
};

describe("AI workforce page", () => {
  beforeEach(() => getWorkforceStatusMock.mockReset());

  it("explains the Integration-required state and shows no connect controls when no adapter exists", async () => {
    getWorkforceStatusMock.mockResolvedValue({ ...base, status: "NOT_CONNECTED", adapter_implemented: false, message: "No AI workforce is connected." });
    render(<WorkforcePage />);
    expect(await screen.findByText("Connect Halla to enable AI workforce capabilities.")).toBeInTheDocument();
    expect(screen.getAllByText("Integration required").length).toBeGreaterThan(1);
    expect(screen.getByText(/connection itself isn't built yet/)).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /connect|deploy|configure|sign in/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /About Halla AI/ })).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("lists the capabilities it will provide and the parts of this business it would serve", async () => {
    getWorkforceStatusMock.mockResolvedValue({ ...base, status: "NOT_CONNECTED", adapter_implemented: false, message: "m" });
    const api = await import("@/lib/api");
    (api.getBuilderOverview as ReturnType<typeof vi.fn>).mockResolvedValue({
      journey: { id: "j" },
      stages: [],
      requirements: [{ label: "Lead qualification", workforce_addressable: true }, { label: "Website", workforce_addressable: false }],
    });
    render(<WorkforcePage />);
    expect(await screen.findByText("Voice")).toBeInTheDocument();
    expect(await screen.findByText("Lead qualification")).toBeInTheDocument();
    const needs = screen.getByRole("heading", { name: /Parts of your business/ }).closest("section")!;
    expect(needs).not.toHaveTextContent("Website");
  });

  it("shows Connected only when the backend says CONNECTED", async () => {
    getWorkforceStatusMock.mockResolvedValue({ ...base, status: "CONNECTED", adapter_implemented: true, message: "Healthy." });
    render(<WorkforcePage />);
    expect((await screen.findAllByText("Connected")).length).toBeGreaterThan(0);
  });

  it("explains a failure and offers a retry", async () => {
    getWorkforceStatusMock.mockReset();
    getWorkforceStatusMock.mockRejectedValueOnce(new Error("network down"));
    getWorkforceStatusMock.mockResolvedValue({ ...base, status: "NOT_CONNECTED", adapter_implemented: false, message: "No AI workforce is connected." });
    render(<WorkforcePage />);
    expect(await screen.findByText(/couldn't check your AI workforce/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("No AI workforce is connected.")).toBeInTheDocument();
  });
});

describe("AI workforce — Klaros agents next to the Halla boundary", () => {
  it("shows each Klaros agent in plain language with its real permissions and history", async () => {
    getWorkforceStatusMock.mockResolvedValue({ ...base, status: "NOT_CONNECTED", adapter_implemented: false, message: "m" });
    const api = await import("@/lib/api");
    (api as unknown as { getBusinessOperations: unknown }).getBusinessOperations = vi.fn().mockResolvedValue({
      agents: [
        { id: "a1", name: "Patient Qualification Agent", purpose: "Qualify new enquiries.", status: "ACTIVE", autonomy_tier: "RECOMMEND", tools: ["crm.qualify_lead"], executions: 2, last_execution: { status: "SUCCEEDED", at: new Date().toISOString() } },
        { id: "a2", name: "Draft agent", purpose: "", status: "DRAFT", autonomy_tier: "OBSERVE", tools: [], executions: 0, last_execution: null },
      ],
    });
    render(<WorkforcePage />);
    expect(await screen.findByRole("link", { name: "Patient Qualification Agent" })).toHaveAttribute("href", "/agents/a1");
    expect(screen.getByText("Suggests — a person decides")).toBeInTheDocument();
    expect(screen.getByText("Record qualification")).toBeInTheDocument(); // not "crm.qualify_lead"
    expect(screen.getByText(/2 runs · last succeeded/)).toBeInTheDocument();
    expect(screen.getByText("No tools granted — it cannot act.")).toBeInTheDocument();
    expect(screen.getByText("Hasn't run yet.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/crm\.qualify_lead|RECOMMEND\b/);
  });
});
