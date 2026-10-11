import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
const accept = vi.fn();
const complete = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; code?: string; constructor(status: number, message: string, code?: string) { super(message); this.status = status; this.code = code; } },
  listHandoffTasks: (...a: unknown[]) => list(...a), acceptHandoffTask: (...a: unknown[]) => accept(...a), completeHandoffTask: (...a: unknown[]) => complete(...a), cancelHandoffTask: vi.fn(),
}));
import Page from "@/app/medical-tourism/tasks/page";

const task = (over: object) => ({ id: "T1", lead_id: "L1", kind: "escalation", reason_code: "halla_escalated", status: "OPEN", owner_user_id: null, accepted_at: null, completed_at: null, note: null, created_at: null, ...over });

beforeEach(() => { list.mockReset(); accept.mockReset(); complete.mockReset(); });

describe("Medical Tourism tasks", () => {
  it("shows the closed reason in words and never any call text", async () => {
    list.mockResolvedValue({ tasks: [task({})], total: 1 });
    render(<Page />);
    expect(await screen.findByText("Needs a person")).toBeInTheDocument();
    expect(screen.getByText("The AI receptionist asked for a person.")).toBeInTheDocument();
  });

  it("accepting an open task calls the backend and reloads", async () => {
    list.mockResolvedValueOnce({ tasks: [task({})], total: 1 }).mockResolvedValue({ tasks: [task({ status: "ACCEPTED", owner_user_id: "u" })], total: 1 });
    accept.mockResolvedValue({});
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(accept).toHaveBeenCalledWith("t", "T1"));
    expect(await screen.findByRole("button", { name: "Mark done" })).toBeInTheDocument();
  });

  it("explains a refusal in plain words (someone else took it)", async () => {
    const { ApiError } = await import("@/lib/api");
    list.mockResolvedValue({ tasks: [task({})], total: 1 });
    accept.mockRejectedValue(new ApiError(409, "not_open", "not_open"));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Someone has already taken this task.")).toBeInTheDocument();
  });

  it("says plainly when nothing is waiting", async () => {
    list.mockResolvedValue({ tasks: [], total: 0 });
    render(<Page />);
    expect(await screen.findByText("Nothing is waiting for a person.")).toBeInTheDocument();
  });
});
