import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ reportOrderProblem: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import ProblemReport from "@/components/store/ProblemReport";
import { StoreApiError } from "@/lib/storeApi";

beforeEach(() => api.reportOrderProblem.mockReset());

const GOOD = "The jar of honey arrived broken.";

describe("something wrong with this order", () => {
  it("sends the report once and thanks the shopper", async () => {
    api.reportOrderProblem.mockResolvedValue({ received: true });
    renderInStore(<ProblemReport token="tok123" canReport status={null} />);
    await userEvent.type(screen.getByLabelText("What happened?"), GOOD);
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("status")).toHaveTextContent("We have your report");
    expect(api.reportOrderProblem).toHaveBeenCalledTimes(1);
    expect(api.reportOrderProblem).toHaveBeenCalledWith("tok123", GOOD);
  });

  it("asks for more words and sends nothing when it is too short", async () => {
    renderInStore(<ProblemReport token="t" canReport status={null} />);
    await userEvent.type(screen.getByLabelText("What happened?"), "broken");
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 10 characters");
    expect(api.reportOrderProblem).not.toHaveBeenCalled();
  });

  it("shows the problem in plain words and keeps what was typed when sending fails", async () => {
    api.reportOrderProblem.mockRejectedValueOnce(new StoreApiError(409, "This order already has a report. We will reply by email.")).mockResolvedValue({ received: true });
    renderInStore(<ProblemReport token="t" canReport status={null} />);
    await userEvent.type(screen.getByLabelText("What happened?"), GOOD);
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has a report");
    expect(screen.getByLabelText("What happened?")).toHaveValue(GOOD);
  });

  it("offers nothing when the order cannot be reported, and says where an open or approved report stands", () => {
    const none = renderInStore(<ProblemReport token="t" canReport={false} status={null} />);
    expect(none.container).toBeEmptyDOMElement();
    none.unmount();
    const open = renderInStore(<ProblemReport token="t" canReport={false} status="PENDING" />);
    expect(open.container).toHaveTextContent("We have your report");
    open.unmount();
    const ok = renderInStore(<ProblemReport token="t" canReport={false} status="APPROVED" />);
    expect(ok.container).toHaveTextContent("Your report was approved");
    expect(screen.queryByRole("button", { name: "Send report" })).not.toBeInTheDocument();
  });

  it("lets the shopper write again after a report was not approved", () => {
    renderInStore(<ProblemReport token="t" canReport status="REJECTED" />);
    expect(screen.getByText(/was not approved; you can write to us again/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send report" })).toBeEnabled();
  });
});
