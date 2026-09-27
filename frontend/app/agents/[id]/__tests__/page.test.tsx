import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock }),
  usePathname: () => "/agents/a1",
  useParams: () => ({ id: "a1" }),
}));

const getAgentMock = vi.fn();
const listAgentVersionsMock = vi.fn();
const listAgentToolPermissionsMock = vi.fn();
const getToolCatalogMock = vi.fn();
const listAgentExecutionsMock = vi.fn();
const updateAgentMock = vi.fn();
const activateAgentMock = vi.fn();
const pauseAgentMock = vi.fn();
const archiveAgentMock = vi.fn();
const grantAgentToolPermissionMock = vi.fn();
const revokeAgentToolPermissionMock = vi.fn();
const createAgentVersionMock = vi.fn();
const publishAgentVersionMock = vi.fn();
const executeAgentMock = vi.fn();
const listAgentExecutionStepsMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getAgent: (...args: unknown[]) => getAgentMock(...args),
  listAgentVersions: (...args: unknown[]) => listAgentVersionsMock(...args),
  listAgentToolPermissions: (...args: unknown[]) => listAgentToolPermissionsMock(...args),
  getToolCatalog: (...args: unknown[]) => getToolCatalogMock(...args),
  listAgentExecutions: (...args: unknown[]) => listAgentExecutionsMock(...args),
  updateAgent: (...args: unknown[]) => updateAgentMock(...args),
  activateAgent: (...args: unknown[]) => activateAgentMock(...args),
  pauseAgent: (...args: unknown[]) => pauseAgentMock(...args),
  archiveAgent: (...args: unknown[]) => archiveAgentMock(...args),
  grantAgentToolPermission: (...args: unknown[]) => grantAgentToolPermissionMock(...args),
  revokeAgentToolPermission: (...args: unknown[]) => revokeAgentToolPermissionMock(...args),
  createAgentVersion: (...args: unknown[]) => createAgentVersionMock(...args),
  publishAgentVersion: (...args: unknown[]) => publishAgentVersionMock(...args),
  executeAgent: (...args: unknown[]) => executeAgentMock(...args),
  listAgentExecutionSteps: (...args: unknown[]) => listAgentExecutionStepsMock(...args),
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

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), danger: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

import AgentDetailPage from "@/app/agents/[id]/page";
import { ApiError } from "@/lib/api";

function agent(overrides: Record<string, unknown> = {}) {
  return {
    id: "a1",
    name: "Invoice Follow-up Agent",
    purpose: "Chases unpaid invoices",
    status: "DRAFT",
    autonomy_tier: "OBSERVE",
    acting_role: "OWNER",
    current_version_id: null,
    source_blueprint_id: null,
    source_blueprint_version: null,
    source_recommendation_id: null,
    created_by: "u1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function version(overrides: Record<string, unknown> = {}) {
  return {
    id: "v1",
    agent_id: "a1",
    version: 1,
    status: "DRAFT",
    instructions_snapshot: "Chase unpaid invoices politely.",
    tool_permissions_snapshot: [],
    memory_refs: [],
    triggers: {},
    max_executions_per_hour: 20,
    max_concurrent_executions: 1,
    max_tool_chain_depth: 1,
    approval_policy_override: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function toolEntry(overrides: Record<string, unknown> = {}) {
  return {
    name: "send_invoice_reminder",
    description: "Sends a reminder email for an unpaid invoice",
    required_permission: "SEND_INVOICE",
    tenant_scoped: true,
    counts_toward_ai_usage: false,
    ...overrides,
  };
}

function execution(overrides: Record<string, unknown> = {}) {
  return {
    id: "e1",
    agent_id: "a1",
    agent_version_id: "v1",
    trigger_source: "MANUAL",
    status: "COMPLETED",
    mode: "SINGLE_ACTION",
    tool_name: "send_invoice_reminder",
    tool_input_summary: {},
    result_summary: { ok: true },
    error_message: null,
    goal: null,
    final_response: null,
    termination_reason: null,
    step_count: 1,
    approval_request_id: null,
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:01:00Z",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

async function setup(overrides: {
  agent?: Record<string, unknown>;
  versions?: Record<string, unknown>[];
  grants?: Record<string, unknown>[];
  catalog?: Record<string, unknown>[];
  executions?: Record<string, unknown>[];
} = {}) {
  getAgentMock.mockResolvedValue(agent(overrides.agent));
  listAgentVersionsMock.mockResolvedValue(overrides.versions ?? []);
  listAgentToolPermissionsMock.mockResolvedValue(overrides.grants ?? []);
  getToolCatalogMock.mockResolvedValue(overrides.catalog ?? [toolEntry()]);
  listAgentExecutionsMock.mockResolvedValue(overrides.executions ?? []);
  render(<AgentDetailPage />);
  await screen.findByText("Invoice Follow-up Agent");
}

describe("Agent detail / configuration page", () => {
  beforeEach(() => {
    for (const m of [
      getAgentMock,
      listAgentVersionsMock,
      listAgentToolPermissionsMock,
      getToolCatalogMock,
      listAgentExecutionsMock,
      updateAgentMock,
      activateAgentMock,
      pauseAgentMock,
      archiveAgentMock,
      grantAgentToolPermissionMock,
      revokeAgentToolPermissionMock,
      createAgentVersionMock,
      publishAgentVersionMock,
      executeAgentMock,
      listAgentExecutionStepsMock,
    ]) {
      m.mockReset();
    }
    pushMock.mockReset();
  });

  it("renders identity, acting role, and autonomy from real backend fields", async () => {
    await setup();
    expect(screen.getAllByText("Chases unpaid invoices").length).toBeGreaterThan(0);
    expect(screen.getByText("OWNER")).toBeInTheDocument();
    expect(screen.getAllByText("Observe only").length).toBeGreaterThan(0);
  });

  it("renders the tool catalog from the backend, not a hardcoded list", async () => {
    await setup({ catalog: [toolEntry({ name: "custom_tool_xyz", description: "A dynamically returned tool" })] });
    expect(screen.getByText("custom_tool_xyz")).toBeInTheDocument();
    expect(screen.getByText("A dynamically returned tool")).toBeInTheDocument();
  });

  it("grants a tool permission through the backend when checked", async () => {
    grantAgentToolPermissionMock.mockResolvedValue({
      id: "g1",
      agent_id: "a1",
      tool_name: "send_invoice_reminder",
      constraint: null,
      created_at: "x",
    });
    await setup();
    fireEvent.click(screen.getByLabelText("Grant send_invoice_reminder"));
    await waitFor(() =>
      expect(grantAgentToolPermissionMock).toHaveBeenCalledWith("test-token", "a1", { tool_name: "send_invoice_reminder" })
    );
  });

  it("revokes a granted tool permission through the backend when unchecked", async () => {
    revokeAgentToolPermissionMock.mockResolvedValue(undefined);
    await setup({
      grants: [{ id: "g1", agent_id: "a1", tool_name: "send_invoice_reminder", constraint: null, created_at: "x" }],
    });
    fireEvent.click(screen.getByLabelText("Grant send_invoice_reminder"));
    await waitFor(() =>
      expect(revokeAgentToolPermissionMock).toHaveBeenCalledWith("test-token", "a1", "send_invoice_reminder")
    );
  });

  it("saves a draft agent's identity via Save draft", async () => {
    updateAgentMock.mockResolvedValue(agent({ purpose: "Chases unpaid invoices politely" }));
    await setup();
    const el = screen.getByDisplayValue("Chases unpaid invoices");
    fireEvent.change(el, { target: { value: "Chases unpaid invoices politely" } });
    fireEvent.click(screen.getByText("Save draft"));
    await waitFor(() => expect(updateAgentMock).toHaveBeenCalledWith("test-token", "a1", { purpose: "Chases unpaid invoices politely" }));
  });

  it("does not allow editing identity once the agent is no longer DRAFT", async () => {
    await setup({ agent: { status: "ACTIVE" } });
    const el = screen.getByDisplayValue("Chases unpaid invoices") as HTMLTextAreaElement;
    expect(el).toBeDisabled();
    expect(screen.queryByText("Save draft")).not.toBeInTheDocument();
  });

  it("publishes a draft version through the backend publish endpoint", async () => {
    publishAgentVersionMock.mockResolvedValue(version({ status: "PUBLISHED" }));
    getAgentMock.mockResolvedValueOnce(agent()).mockResolvedValueOnce(agent({ current_version_id: "v1" }));
    await setup({ versions: [version()] });
    fireEvent.click(screen.getByText("Publish"));
    await waitFor(() => expect(publishAgentVersionMock).toHaveBeenCalledWith("test-token", "a1", "v1"));
  });

  it("shows a Published badge and does not let a published version look editable", async () => {
    await setup({ agent: { current_version_id: "v1" }, versions: [version({ status: "PUBLISHED" })] });
    expect(screen.getByText("PUBLISHED")).toBeInTheDocument();
    expect(screen.queryByText("Publish")).not.toBeInTheDocument();
  });

  it("disables Run Agent when the agent has no active, published version", async () => {
    await setup();
    expect(screen.getByText("Run Agent")).toBeDisabled();
    expect(
      screen.getByText("This agent must be Activated, with a Published version, before it can run.")
    ).toBeInTheDocument();
  });

  it("enables Run Agent once ACTIVE with a published current version, and runs via goal", async () => {
    executeAgentMock.mockResolvedValue(execution({ status: "COMPLETED" }));
    await setup({
      agent: { status: "ACTIVE", current_version_id: "v1" },
      versions: [version({ status: "PUBLISHED" })],
    });
    fireEvent.click(screen.getByText("Run Agent"));
    fireEvent.change(screen.getByPlaceholderText("What should this agent accomplish right now?"), {
      target: { value: "Follow up on invoice 123" },
    });
    fireEvent.click(screen.getByText("Run"));
    await waitFor(() =>
      expect(executeAgentMock).toHaveBeenCalledWith("test-token", "a1", { goal: "Follow up on invoice 123" })
    );
  });

  it("shows WAITING_APPROVAL truthfully and links to Approvals, never auto-approving", async () => {
    await setup({ executions: [execution({ status: "WAITING_APPROVAL", approval_request_id: "ar1" })], catalog: [] });
    fireEvent.click(screen.getByText("send_invoice_reminder"));
    expect(await screen.findByText(/Review in Approvals/)).toBeInTheDocument();
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
  });

  it("shows FAILED executions with the backend's sanitized error message", async () => {
    await setup({ executions: [execution({ status: "FAILED", error_message: "Tool call rejected: policy" })], catalog: [] });
    fireEvent.click(screen.getByText("send_invoice_reminder"));
    expect(await screen.findByText("Tool call rejected: policy")).toBeInTheDocument();
  });

  it("does not claim unlimited autonomy or full system access anywhere on the page", async () => {
    await setup();
    expect(screen.queryByText(/unlimited autonomy/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no restrictions/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/full system access/i)).not.toBeInTheDocument();
  });

  it("reconciles state on a 409 from a lifecycle transition instead of showing a false success", async () => {
    activateAgentMock.mockRejectedValue(new ApiError(409, "Agent is not DRAFT or PAUSED"));
    getAgentMock.mockResolvedValueOnce(agent()).mockResolvedValueOnce(agent({ status: "ARCHIVED" }));
    await setup();
    fireEvent.click(screen.getByText("Activate"));
    await waitFor(() => expect(getAgentMock).toHaveBeenCalledTimes(2));
  });

  it("shows a 404-appropriate message for a nonexistent or inaccessible agent", async () => {
    getAgentMock.mockRejectedValue(new ApiError(404, "Agent not found"));
    listAgentVersionsMock.mockResolvedValue([]);
    listAgentToolPermissionsMock.mockResolvedValue([]);
    getToolCatalogMock.mockResolvedValue([]);
    listAgentExecutionsMock.mockResolvedValue([]);
    render(<AgentDetailPage />);
    expect(await screen.findByText("This agent doesn't exist, or isn't visible to your account.")).toBeInTheDocument();
  });
});
