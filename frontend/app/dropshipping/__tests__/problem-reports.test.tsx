import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listProblemReports: vi.fn(), decideProblemReport: vi.fn() }));
vi.mock("@/lib/dropshippingApi", async () => ({ ...(await vi.importActual<object>("@/lib/dropshippingApi")), ...api }));
import ProblemReports from "@/components/dropshipping/ProblemReports";

const entry = (over = {}) => ({ id: "r1", order_id: "o1", order_number: "DS-HONEY123", status: "PENDING" as const, description: "The jar of honey arrived broken.", decision_note: null, created_at: null, decided_at: null, ...over });

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.listProblemReports.mockResolvedValue({ problem_reports: [entry()] });
  api.decideProblemReport.mockResolvedValue({ order: {} });
});

describe("shoppers' reports on the store page", () => {
  it("lists what is waiting, and nothing at all when nothing is", async () => {
    const first = render(<ProblemReports token="t" canDecide />);
    const row = await screen.findByLabelText("Report on DS-HONEY123");
    expect(row).toHaveTextContent("The jar of honey arrived broken.");
    first.unmount();
    api.listProblemReports.mockResolvedValue({ problem_reports: [] });
    const empty = render(<ProblemReports token="t" canDecide />);
    await vi.waitFor(() => expect(api.listProblemReports).toHaveBeenCalledTimes(2));
    expect(empty.container).toBeEmptyDOMElement();
  });

  it("approves after a confirmation, then reloads", async () => {
    render(<ProblemReports token="t" canDecide />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve and flag for refund" }));
    api.listProblemReports.mockResolvedValue({ problem_reports: [] });
    await userEvent.click(screen.getByRole("button", { name: "Yes, approve" }));
    expect(api.decideProblemReport).toHaveBeenCalledWith("t", "r1", "approve", "");
    await vi.waitFor(() => expect(screen.queryByLabelText("Report on DS-HONEY123")).not.toBeInTheDocument());
  });

  it("will not reject without a reason, and sends the reason when given", async () => {
    render(<ProblemReports token="t" canDecide />);
    await userEvent.click(await screen.findByRole("button", { name: "Not approved" }));
    const confirm = screen.getAllByRole("button", { name: "Not approved" }).at(-1)!;
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why not"), "The photos show it was whole.");
    await userEvent.click(confirm);
    expect(api.decideProblemReport).toHaveBeenCalledWith("t", "r1", "reject", "The photos show it was whole.");
  });

  it("shows the reports but no buttons to someone who may only look", async () => {
    render(<ProblemReports token="t" canDecide={false} />);
    await screen.findByLabelText("Report on DS-HONEY123");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("stays quiet when the list cannot be loaded", async () => {
    api.listProblemReports.mockRejectedValue(new Error("down"));
    const r = render(<ProblemReports token="t" canDecide />);
    await vi.waitFor(() => expect(api.listProblemReports).toHaveBeenCalled());
    expect(r.container).toBeEmptyDOMElement();
  });
});
