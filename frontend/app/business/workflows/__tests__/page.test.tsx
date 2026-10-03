import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { emptyLeads, opsFixture } from "@/lib/__tests__/opsFixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/business/workflows" }));
const getOps = vi.fn();
const getOverview = vi.fn();
const createStarter = vi.fn();
const getDetail = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  getBusinessOperations: (...a: unknown[]) => getOps(...a),
  getBuilderOverview: (...a: unknown[]) => getOverview(...a),
  createStarterWorkflow: (...a: unknown[]) => createStarter(...a),
  getWorkflowDetail: (...a: unknown[]) => getDetail(...a),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(), markAllNotificationsRead: vi.fn(), dismissNotification: vi.fn(), logout: vi.fn(),
}));
vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "t", user: { id: "u", tenant_id: "t1", email: "a@b.c", full_name: "A", role: "OWNER" }, loading: false, error: null }),
}));
const overview = (requirements: unknown[] = []) => ({ journey: { id: "j", status: "COMPLETED" }, requirements, stages: [], next_actions: [] });
import Page from "@/app/business/workflows/page";

beforeEach(() => {
  getOps.mockReset();
  getOverview.mockReset();
  createStarter.mockReset();
  getDetail.mockReset();
  getOverview.mockResolvedValue(overview());
});

describe("Workflows", () => {
  it("shows how a lead is handled and the real workflows, in plain language", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: "Workflows" })).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "How a lead is handled" })).getByText("Team notified")).toBeInTheDocument();
    const wf = screen.getByRole("list", { name: "Workflows" });
    expect(within(wf).getByText("New lead alert")).toBeInTheDocument();
    expect(within(wf).getByText("A new lead arrives")).toBeInTheDocument(); // not "lead.created"
    expect(within(wf).getByText("Notify the team")).toBeInTheDocument(); // not "notifications.create_notification"
    expect(document.body.textContent).not.toMatch(/lead\.created|notifications\.create_notification|undefined|null/);
    expect(screen.getByText(/Hasn't run yet/)).toBeInTheDocument(); // never claims a run that didn't happen
  });

  it("offers the starter workflow only when none exists, and creating it calls the real endpoint", async () => {
    getOps.mockResolvedValue(opsFixture({ workflows: [], starter_workflow_exists: false }));
    createStarter.mockResolvedValue({ id: "w1", name: "New lead alert", status: "ENABLED", created: true });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add “New lead alert”/ }));
    await waitFor(() => expect(createStarter).toHaveBeenCalledWith("t"));
    expect(await screen.findByText(/is created and running/)).toBeInTheDocument();
  });

  it("does not offer it again once it exists", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    await screen.findByText("New lead alert");
    expect(screen.queryByRole("button", { name: /Add “New lead alert”/ })).not.toBeInTheDocument();
  });

  it("lists what needs an integration as Integration required, never as runnable", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    const item = (await screen.findByText("Send a WhatsApp message")).closest("li")!;
    expect(item).toHaveTextContent("Integration required");
    expect(within(item).queryByRole("button")).not.toBeInTheDocument();
  });

  it("explains a permission failure on creating a workflow", async () => {
    const { ApiError } = await import("@/lib/api");
    getOps.mockResolvedValue(opsFixture({ workflows: [], starter_workflow_exists: false }));
    createStarter.mockImplementation(async () => { throw new ApiError(403, "forbidden"); });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add “New lead alert”/ }));
    expect(await screen.findByText("You don't have permission to create workflows.")).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    getOps.mockImplementation(async () => { throw new Error("down"); });
    render(<Page />);
    expect(await screen.findByText(/couldn't load your operations/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});


describe("Workflows — what happens next and the real runs", () => {
  const detail = (over: Record<string, unknown> = {}) => ({
    id: "w1", name: "New lead alert", description: null, status: "ENABLED", trigger: "EVENT", trigger_event: "lead.created",
    steps: [{ action: "notifications.create_notification" }], published: true,
    totals: { runs: 2, completed: 1, failed: 1 },
    runs: [
      { id: "r2", status: "FAILED", at: new Date().toISOString(), error: null, steps: [{ index: 0, action: "notifications.create_notification", status: "FAILED", error: "No recipient configured" }] },
      { id: "r1", status: "COMPLETED", at: new Date().toISOString(), error: null, steps: [{ index: 0, action: "notifications.create_notification", status: "COMPLETED", error: null }] },
    ],
    ...over,
  });

  it("says what each workflow does next", async () => {
    getOps.mockResolvedValue(opsFixture());
    render(<Page />);
    expect(await screen.findByText(/Waits for its trigger — A new lead arrives\./)).toBeInTheDocument();
  });

  it("flags a failed last run and points at it", async () => {
    getOps.mockResolvedValue(opsFixture({ workflows: [{ ...opsFixture().workflows[0], runs: 2, failed_runs: 1, last_run: { status: "FAILED", at: new Date().toISOString() } }] }));
    render(<Page />);
    expect(await screen.findByText("Last run failed")).toBeInTheDocument();
    expect(screen.getByText(/Check why the last run failed/)).toBeInTheDocument();
  });

  it("requests nothing until details are opened, then shows real runs step by step", async () => {
    getOps.mockResolvedValue(opsFixture());
    getDetail.mockResolvedValue(detail());
    render(<Page />);
    const summary = await screen.findByText("View details");
    expect(getDetail).not.toHaveBeenCalled();
    fireEvent.click(summary);
    const details = summary.closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(await screen.findByText("No recipient configured", { exact: false })).toBeInTheDocument();
    expect(getDetail).toHaveBeenCalledWith("t", "w1");
    expect(screen.getByText("2 total · 1 completed · 1 failed")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/notifications\.create_notification|undefined|null/);
  });

  it("explains a detail that cannot be loaded", async () => {
    getOps.mockResolvedValue(opsFixture());
    getDetail.mockRejectedValue(new Error("down"));
    render(<Page />);
    const summary = await screen.findByText("View details");
    const details = summary.closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(await screen.findByText("We couldn't load this workflow's runs just now.")).toBeInTheDocument();
  });
});


describe("Workflows — escalation starter", () => {
  it("offers the escalation workflow only when it does not exist, and creates it through the real endpoint", async () => {
    getOps.mockResolvedValue(opsFixture({ escalation_workflow_exists: false }));
    createStarter.mockResolvedValue({ id: "w2", name: "Escalated lead alert", status: "ENABLED", created: true });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add “Escalated lead alert”/ }));
    await waitFor(() => expect(createStarter).toHaveBeenCalledWith("t", "escalation"));
    expect(await screen.findByText(/The “Escalated lead alert” workflow is created and running/)).toBeInTheDocument();
  });

  it("does not offer it once it exists", async () => {
    getOps.mockResolvedValue(opsFixture({ escalation_workflow_exists: true }));
    render(<Page />);
    await screen.findByRole("heading", { level: 1, name: "Workflows" });
    expect(screen.queryByRole("button", { name: /Escalated lead alert/ })).not.toBeInTheDocument();
  });

  it("describes an escalation-triggered workflow in plain language", async () => {
    getOps.mockResolvedValue(opsFixture({ workflows: [{ ...opsFixture().workflows[0], name: "Escalated lead alert", trigger_event: "halla.lead.escalated" }] }));
    render(<Page />);
    expect(await screen.findByText("The AI workforce hands a lead to a person")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/halla\.lead\.escalated/);
  });
});
