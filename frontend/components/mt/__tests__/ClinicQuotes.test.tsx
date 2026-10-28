import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getQuotes = vi.fn();
const record = vi.fn();
const withdraw = vi.fn();
const providers = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getLeadQuotes: (...a: unknown[]) => getQuotes(...a),
  recordLeadQuote: (...a: unknown[]) => record(...a),
  withdrawLeadQuote: (...a: unknown[]) => withdraw(...a),
  listMedicalTourismProviders: (...a: unknown[]) => providers(...a),
}));
import { ClinicQuotes } from "@/components/mt/ClinicQuotes";

const quote = (over: object) => ({ id: "Q", provider_id: "P", provider_name: "Clinic", country: "TR", verified: true, procedure: null, amount: "3000.00", currency: "USD", includes: "Surgery and 2 nights", excludes: null, valid_until: null, expired: false, reference: null, recorded_at: null, lowest_in_currency: false, disclosure: "We have no commission agreement with this clinic.", ...over });
const data = (quotes: object[]) => ({ lead_id: "L", quotes, order_note: "Clinics are listed by price, lowest first. They are not ordered by commission.", general_disclosure: "We are paid a commission by the clinics we refer patients to. Clinics are listed by price, lowest first. They are not ordered by commission.", currencies: ["USD"] });

beforeEach(() => {
  [getQuotes, record, withdraw, providers].forEach((m) => m.mockReset());
  providers.mockResolvedValue({ providers: [{ id: "P1", name: "Cheap Clinic", status: "ACTIVE" }, { id: "P2", name: "Old Clinic", status: "INACTIVE" }] });
});

describe("ClinicQuotes", () => {
  it("lists quotes as the backend ordered them, flags the lowest price, and always shows the commission disclosure", async () => {
    getQuotes.mockResolvedValue(data([
      quote({ id: "A", provider_name: "Cheap Clinic", amount: "2500.00", lowest_in_currency: true, disclosure: "We receive a commission from this clinic.", commission: { agreed: true, basis: "PERCENTAGE", percentage: "5.00", flat_amount: null, currency: "USD", estimated: "125.00", estimated_currency: "USD" } }),
      quote({ id: "B", provider_name: "Pricey Clinic", amount: "4000.00", verified: false }),
    ]));
    render(<ClinicQuotes token="t" leadId="L" />);
    const rows = within(await screen.findByRole("list", { name: "Quotes, cheapest first" })).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Cheap Clinic");
    expect(rows[0]).toHaveTextContent("Lowest price");
    expect(rows[0]).toHaveTextContent("We receive a commission from this clinic.");
    expect(rows[0]).toHaveTextContent("Only you see this: About $125 (5%)");
    expect(rows[1]).toHaveTextContent("Not verified yet");
    expect(rows[1]).toHaveTextContent("We have no commission agreement with this clinic.");
    expect(rows[1]).not.toHaveTextContent("Only you see this");
    expect(screen.getByText(/not ordered by commission/i)).toBeInTheDocument();
  });

  it("says what to do when there are no quotes yet", async () => {
    getQuotes.mockResolvedValue(data([]));
    render(<ClinicQuotes token="t" leadId="L" />);
    expect(await screen.findByText(/No quotes yet/)).toBeInTheDocument();
  });

  it("records a quote for the chosen clinic, with what it includes, and shows the refreshed list", async () => {
    getQuotes.mockResolvedValue(data([]));
    record.mockResolvedValue(data([quote({ id: "N", provider_name: "Cheap Clinic", amount: "2500.00", lowest_in_currency: true })]));
    render(<ClinicQuotes token="t" leadId="L" />);
    await screen.findByText(/No quotes yet/);
    fireEvent.click(screen.getByRole("button", { name: "Add a quote" }));
    const save = await screen.findByRole("button", { name: "Save quote" });
    expect(save).toBeDisabled(); // no clinic chosen yet
    await waitFor(() => expect(screen.getByRole("option", { name: "Cheap Clinic" })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: "Old Clinic" })).not.toBeInTheDocument(); // an inactive clinic cannot be quoted
    fireEvent.change(screen.getByLabelText("Clinic"), { target: { value: "P1" } });
    fireEvent.change(screen.getByLabelText("Quoted price"), { target: { value: "2500" } });
    fireEvent.change(screen.getByLabelText("What the price includes"), { target: { value: "Surgery, 2 nights" } });
    fireEvent.click(screen.getByRole("button", { name: "Save quote" }));
    await waitFor(() => expect(record).toHaveBeenCalledWith("t", "L", expect.objectContaining({ provider_id: "P1", amount: "2500", currency: "USD", includes: "Surgery, 2 nights" })));
    expect(await screen.findByText("Cheap Clinic", { selector: "span" })).toBeInTheDocument();
  });

  it("withdraws a quote on request and says there are none left", async () => {
    getQuotes.mockResolvedValue(data([quote({ id: "A", provider_name: "Cheap Clinic" })]));
    withdraw.mockResolvedValue(data([]));
    render(<ClinicQuotes token="t" leadId="L" />);
    fireEvent.click(await screen.findByRole("button", { name: "Withdraw this quote" }));
    expect(await screen.findByText(/No quotes yet/)).toBeInTheDocument();
    expect(withdraw).toHaveBeenCalledWith("t", "A");
  });
});
