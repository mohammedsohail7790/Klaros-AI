import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCrew = vi.fn();
const update = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  getOperatingCrew: (...a: unknown[]) => getCrew(...a),
  provisionOperatingCrew: vi.fn(),
  updateOperatingCrewSettings: (...a: unknown[]) => update(...a),
}));
import { CrewCard } from "@/components/business/CrewCard";

const member = (key: string, name: string, over: Record<string, unknown> = {}) => ({ key, name, purpose: `${name} purpose`, trigger: "Every day", status: "ACTIVE", running: true, switched_off: false, agent_id: key, ...over });

describe("CrewCard", () => {
  beforeEach(() => {
    getCrew.mockReset();
    update.mockReset().mockResolvedValue({ auto_provision: true, disabled_members: [] });
  });

  it("switches a running member off and reloads", async () => {
    getCrew.mockResolvedValue({ verticals: [], running: 2, total: 2, members: [member("a", "Alpha"), member("b", "Beta")] });
    render(<CrewCard token="t" />);
    expect(await screen.findByText("Your AI crew is running the business.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Switch off" })[0]);
    await waitFor(() => expect(update).toHaveBeenCalledWith("t", { disabled_members: ["a"] }));
    expect(getCrew).toHaveBeenCalledTimes(2);
  });

  it("shows a switched-off member as such, counts the crew as in order, and can switch it back on", async () => {
    getCrew.mockResolvedValue({ verticals: [], running: 1, total: 2, members: [member("a", "Alpha"), member("b", "Beta", { running: false, switched_off: true, status: "PAUSED" })] });
    render(<CrewCard token="t" />);
    expect(await screen.findByText("Switched off")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Turn on autopilot" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Switch on" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("t", { disabled_members: [] }));
  });
});
