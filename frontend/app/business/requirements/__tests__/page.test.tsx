import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock }),
  usePathname: () => "/business/requirements",
}));
const getBuilderOverviewMock = vi.fn();
const enableMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getBuilderOverview: (...a: unknown[]) => getBuilderOverviewMock(...a),
  enableIndustryModule: (...a: unknown[]) => enableMock(...a),
  generateRecommendationsJourneyStep: vi.fn(),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "t1", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));

import RequirementsPage from "@/app/business/requirements/page";

const req = (o: Record<string, unknown>) => ({
  key: "k", label: "Label", group: "Sales & customers", description: "Does a thing.", required: true,
  why: "You listed “patient leads” as something the business needs.", technical_why: "", source: "blueprint", confidence: 0.9,
  evidence: [], klaros_support: "NATIVE", native_route: "/leads", readiness: "READY", readiness_detail: "Klaros has a built-in module for this.",
  next_step: { text: "Open Label.", route: "/leads" }, workforce_addressable: false, providers: [], recommendation_id: null, recommendation_ids: [], recommendation_status: null, ...o,
});
const overview = (requirements: unknown[], next_actions: unknown[] = []) => ({
  journey: { id: "j", status: "BLUEPRINT_ACTIVE" }, requirements, next_actions,
  stages: [{ key: "requirements", label: "Requirements", state: "current", route: "/business/requirements" }],
});

describe("Requirements page", () => {
  beforeEach(() => {
    getBuilderOverviewMock.mockReset();
    enableMock.mockReset();
    replaceMock.mockReset();
  });

  it("shows why each requirement exists, its honest status, and the real next step", async () => {
    getBuilderOverviewMock.mockResolvedValue(
      overview([
        req({ key: "lead_capture", label: "Lead capture" }),
        req({ key: "inventory", label: "Inventory", group: "Catalog & supply", readiness: "PLANNED", readiness_detail: "No module yet.", next_step: { text: "Integration adapter required — Klaros has no module or integration for this yet.", route: null } }),
      ])
    );
    render(<RequirementsPage />);
    expect((await screen.findAllByText(/You listed/)).length).toBe(2);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Label." })).toHaveAttribute("href", "/leads");
    // PLANNED: plain text, no link — never a fake action.
    const text = screen.getByText(/Integration adapter required/);
    expect(text.closest("a")).toBeNull();
    expect(screen.getAllByText("Planned").length).toBeGreaterThan(0);
  });

  it("offers an explicit enable action for an industry module and calls the backend only on click", async () => {
    getBuilderOverviewMock.mockResolvedValue(
      overview([req({})], [{ id: "enable-module:x", title: "Enable the X module", detail: "Adds ready-made records.", state: "READY", route: null, kind: "enable_vertical", vertical_key: "x" }])
    );
    enableMock.mockResolvedValue({ key: "x", name: "X", enabled: true });
    render(<RequirementsPage />);
    const btn = await screen.findByRole("button", { name: "Enable" });
    expect(enableMock).not.toHaveBeenCalled();
    fireEvent.click(btn);
    await waitFor(() => expect(enableMock).toHaveBeenCalledWith("t", "x"));
  });

  it("tells the user what to do when there are no requirements", async () => {
    getBuilderOverviewMock.mockResolvedValue(overview([]));
    render(<RequirementsPage />);
    expect(await screen.findByText(/doesn't list any required capabilities/)).toBeInTheDocument();
  });
});
