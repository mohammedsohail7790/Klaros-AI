import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = { getHallaPhone: vi.fn(), searchHallaPhone: vi.fn(), buyHallaPhone: vi.fn(), setHallaBusinessNumber: vi.fn() };
let failNext: Error | null = null;
const maybeFail = <T,>(fn: (...a: unknown[]) => T) => (...a: unknown[]) => (failNext ? new Promise<never>((_, rej) => setTimeout(() => rej(failNext), 0)) : fn(...a));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  getHallaPhone: (...a: unknown[]) => maybeFail(api.getHallaPhone)(...a),
  searchHallaPhone: (...a: unknown[]) => maybeFail(api.searchHallaPhone)(...a),
  buyHallaPhone: (...a: unknown[]) => maybeFail(api.buyHallaPhone)(...a),
  setHallaBusinessNumber: (...a: unknown[]) => maybeFail(api.setHallaBusinessNumber)(...a),
}));
import { HallaPhonePanel, formatPhone } from "@/components/business/HallaPhone";
import { ApiError, type HallaPhoneStatus } from "@/lib/api";

const status = (over: Partial<HallaPhoneStatus> = {}): HallaPhoneStatus => ({
  numbers: [], business_number: null, human_handoff: false, can_buy: true, monthly_cost_usd: 1.15,
  plan: { plan: "professional", status: "trialing", trial_expires_at: "2026-10-26T00:00:00Z", trial_minutes_used: 0 }, ...over,
});

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  failNext = null;
});

describe("formatPhone", () => {
  it("formats a US number and leaves anything else alone", () => {
    expect(formatPhone("+19193715609")).toBe("+1 (919) 371-5609");
    expect(formatPhone("+971501234567")).toBe("+971501234567");
    expect(formatPhone(null)).toBe("");
  });
});

describe("HallaPhonePanel", () => {
  it("shows nothing until the receptionist is connected, and asks for nothing then", () => {
    const { container } = render(<HallaPhonePanel token="t" connected={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(api.getHallaPhone).not.toHaveBeenCalled();
  });

  it("searches by area code and will not buy until a number is chosen AND the price is accepted", async () => {
    api.getHallaPhone.mockResolvedValue(status());
    api.searchHallaPhone.mockResolvedValue({ numbers: [{ phone_number: "+14155550123", friendly_name: "x" }, { phone_number: "+14155550124", friendly_name: "y" }], monthly_cost_usd: 1.15 });
    api.buyHallaPhone.mockResolvedValue({ phone_number: "+14155550123", monthly_cost_usd: 1.15, receptionist_pinned: true });
    render(<HallaPhonePanel token="t" connected />);
    const searchBtn = await screen.findByRole("button", { name: /Search numbers/ });
    expect(searchBtn).toBeDisabled(); // no area code yet
    fireEvent.change(screen.getByLabelText("Area code"), { target: { value: "415" } });
    fireEvent.click(searchBtn);
    await screen.findByText("+1 (415) 555-0123");
    expect(api.searchHallaPhone).toHaveBeenCalledWith("t", "415");
    const buy = screen.getByRole("button", { name: "Buy this number" });
    expect(buy).toBeDisabled(); // nothing picked
    fireEvent.click(screen.getByLabelText("+1 (415) 555-0123"));
    expect(buy).toBeDisabled(); // picked, price not accepted
    expect(screen.getByText(/costs about \$1\.15 a month/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(buy).toBeEnabled();
    api.getHallaPhone.mockResolvedValue(status({ numbers: [{ phone_number: "+14155550123", status: "active", agent: "Receptionist / Intake" }], can_buy: false }));
    fireEvent.click(buy);
    await waitFor(() => expect(api.buyHallaPhone).toHaveBeenCalledWith("t", { phone_number: "+14155550123", accepted_monthly_cost_usd: 1.15 }));
    await screen.findByText(/is now your AI number/);
    expect(await screen.findByText(/Answered by Receptionist \/ Intake/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Buy this number" })).toBeNull(); // one number per business
  });

  it("changing the chosen number clears the price acceptance", async () => {
    api.getHallaPhone.mockResolvedValue(status());
    api.searchHallaPhone.mockResolvedValue({ numbers: [{ phone_number: "+14155550123", friendly_name: "x" }, { phone_number: "+14155550124", friendly_name: "y" }], monthly_cost_usd: 1.15 });
    render(<HallaPhonePanel token="t" connected />);
    fireEvent.change(await screen.findByLabelText("Area code"), { target: { value: "415" } });
    fireEvent.click(screen.getByRole("button", { name: /Search numbers/ }));
    fireEvent.click(await screen.findByLabelText("+1 (415) 555-0123"));
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Buy this number" })).toBeEnabled();
    fireEvent.click(screen.getByLabelText("+1 (415) 555-0124"));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Buy this number" })).toBeDisabled();
  });

  it("says plainly when the purchase fails and that nothing was charged", async () => {
    api.getHallaPhone.mockResolvedValue(status());
    api.searchHallaPhone.mockResolvedValue({ numbers: [{ phone_number: "+14155550123", friendly_name: "x" }], monthly_cost_usd: 1.15 });
    api.buyHallaPhone.mockRejectedValue(new ApiError(409, "That number was just taken. Search again for another."));
    render(<HallaPhonePanel token="t" connected />);
    fireEvent.change(await screen.findByLabelText("Area code"), { target: { value: "415" } });
    fireEvent.click(screen.getByRole("button", { name: /Search numbers/ }));
    fireEvent.click(await screen.findByLabelText("+1 (415) 555-0123"));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Buy this number" }));
    await screen.findByText("That number was just taken. Search again for another.");
  });

  it("shows the existing number, the trial and how to test it, and offers no second purchase", async () => {
    api.getHallaPhone.mockResolvedValue(status({ numbers: [{ phone_number: "+19193715609", status: "active", agent: "Receptionist / Intake" }], can_buy: false }));
    render(<HallaPhonePanel token="t" connected />);
    expect(await screen.findByText("+1 (919) 371-5609")).toBeInTheDocument();
    expect(screen.getByText(/Trial plan until/)).toBeInTheDocument();
    expect(screen.getByText(/call this number from another phone/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Search numbers/ })).toBeNull();
  });

  it("explains when a number can't be added (limit reached or unavailable)", async () => {
    api.getHallaPhone.mockResolvedValue(status({ can_buy: false }));
    render(<HallaPhonePanel token="t" connected />);
    expect(await screen.findByText(/can't be added right now/)).toBeInTheDocument();
  });

  it("saves the business's own number, shows how to forward it, and can remove it", async () => {
    api.getHallaPhone.mockResolvedValue(status({ numbers: [{ phone_number: "+19193715609", status: "active", agent: null }], can_buy: false }));
    api.setHallaBusinessNumber.mockResolvedValue({ business_number: "+14155550199", human_handoff: true });
    render(<HallaPhonePanel token="t" connected />);
    const input = await screen.findByLabelText("Business phone number");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "415 555 0199" } });
    api.getHallaPhone.mockResolvedValue(status({ numbers: [{ phone_number: "+19193715609", status: "active", agent: null }], can_buy: false, business_number: "+14155550199", human_handoff: true }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setHallaBusinessNumber).toHaveBeenCalledWith("t", "415 555 0199"));
    expect(await screen.findByText(/How to forward \+1 \(415\) 555-0199 to your AI number/)).toBeInTheDocument();
    api.setHallaBusinessNumber.mockResolvedValue({ business_number: null, human_handoff: false });
    api.getHallaPhone.mockResolvedValue(status({ numbers: [{ phone_number: "+19193715609", status: "active", agent: null }], can_buy: false }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.setHallaBusinessNumber).toHaveBeenLastCalledWith("t", ""));
    await screen.findByText(/Removed/);
  });

  it("shows a load failure as a failure", async () => {
    failNext = new (class extends Error {})("boom");
    render(<HallaPhonePanel token="t" connected />);
    await screen.findByText(/couldn't load your phone details/);
  });
});
