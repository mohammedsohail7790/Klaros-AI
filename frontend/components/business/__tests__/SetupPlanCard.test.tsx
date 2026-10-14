import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getPlan = vi.fn();
const activate = vi.fn();
vi.mock("@/lib/api", () => ({ ApiError: class extends Error {}, getSetupPlan: (...a: unknown[]) => getPlan(...a), activateSetupPlan: (...a: unknown[]) => activate(...a) }));
import { SetupPlanCard } from "@/components/business/SetupPlanCard";

const step = (key: string, over: Record<string, unknown> = {}) => ({ key, title: `${key} title`, why: `${key} why`, owner: "you", state: "todo", href: `/${key}`, ...over });
const plan = (over: Record<string, unknown> = {}) => ({ verticals: [], crew: { running: 0, total: 11 }, todo: 2, steps: [step("crew", { owner: "klaros", href: "/business/home" }), step("receptionist"), step("calendar"), step("email", { owner: "klaros", state: "platform", href: null })], ...over });

describe("SetupPlanCard", () => {
  beforeEach(() => {
    getPlan.mockReset();
    activate.mockReset();
  });

  it("offers to start the crew, lists what only the owner can connect, and says what Klaros itself has not switched on", async () => {
    getPlan.mockResolvedValue(plan());
    render(<SetupPlanCard token="t" />);
    expect(await screen.findByText("2 things only you can connect. Klaros handles the rest.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Connect" })).toHaveLength(2);
    expect(screen.getByText(/email title isn't switched on for Klaros itself/)).toBeInTheDocument();
    getPlan.mockResolvedValue(plan({ crew: { running: 11, total: 11 }, steps: [step("crew", { owner: "klaros", state: "done" }), step("receptionist"), step("calendar")] }));
    activate.mockResolvedValue(plan({ crew: { running: 11, total: 11 }, steps: [step("crew", { owner: "klaros", state: "done" }), step("receptionist"), step("calendar")] }));
    fireEvent.click(screen.getByRole("button", { name: "Start my AI crew" }));
    await waitFor(() => expect(activate).toHaveBeenCalledWith("t"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Start my AI crew" })).not.toBeInTheDocument());
  });

  it("disappears once everything is done, and stays hidden if the plan cannot be read", async () => {
    getPlan.mockResolvedValue(plan({ todo: 0, steps: [step("crew", { owner: "klaros", state: "done" }), step("receptionist", { state: "done" }), step("email", { owner: "klaros", state: "done", href: null })] }));
    const { container } = render(<SetupPlanCard token="t" />);
    await waitFor(() => expect(getPlan).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    getPlan.mockRejectedValue(new Error("down"));
    const second = render(<SetupPlanCard token="t" />);
    await waitFor(() => expect(getPlan).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });
});
