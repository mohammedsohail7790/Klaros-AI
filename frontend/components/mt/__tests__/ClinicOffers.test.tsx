import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const offers = vi.fn();
const providers = vi.fn();
const procedures = vi.fn();
const create = vi.fn();
vi.mock("@/components/ui/Modal", () => ({ Modal: ({ title, children }: { title: string; children: React.ReactNode }) => <div role="dialog" aria-label={title}>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  listMedicalTourismOfferings: (...a: unknown[]) => offers(...a),
  listMedicalTourismProviders: (...a: unknown[]) => providers(...a),
  listMedicalTourismProcedures: (...a: unknown[]) => procedures(...a),
  createMedicalTourismOffering: (...a: unknown[]) => create(...a),
}));
import { ClinicOffers } from "@/components/mt/ClinicOffers";

beforeEach(() => {
  [offers, providers, procedures, create].forEach((m) => m.mockReset());
  offers.mockResolvedValue({ offerings: [] });
  providers.mockResolvedValue({ providers: [{ id: "P1", name: "Anatolia Dental Clinic", status: "ACTIVE", verification_status: "VERIFIED" }] });
  procedures.mockResolvedValue({ procedures: [{ id: "X1", name: "Dental implants" }] });
});

describe("What clinics offer", () => {
  it("says what to do first when there is no clinic, and disables adding", async () => {
    providers.mockResolvedValue({ providers: [] });
    render(<ClinicOffers token="t" />);
    expect(await screen.findByText(/Add a clinic first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add an offer" })).toBeDisabled();
  });

  it("lists offers by clinic and procedure name, and says when no price was given", async () => {
    offers.mockResolvedValue({ offerings: [{ id: "O1", provider_id: "P1", procedure_id: "X1", estimated_price: null, currency: null, status: "ACTIVE" }, { id: "O2", provider_id: "P1", procedure_id: "X1", estimated_price: "1800.00", currency: "USD", status: "ACTIVE" }] });
    render(<ClinicOffers token="t" />);
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1]).getByText("Anatolia Dental Clinic")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Not given")).toBeInTheDocument();
    expect(within(rows[2]).getByText("1800.00 USD")).toBeInTheDocument();
  });

  it("adds an offer with no price by default, and never invents one", async () => {
    create.mockResolvedValue({ offering: {}, deduplicated: false });
    render(<ClinicOffers token="t" />);
    const open = await screen.findByRole("button", { name: "Add an offer" });
    await waitFor(() => expect(open).toBeEnabled()); // it renders at once but stays disabled until the clinics and procedures have loaded
    fireEvent.click(open);
    const submit = screen.getByRole("button", { name: "Add offer" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Clinic"), { target: { value: "P1" } });
    fireEvent.change(screen.getByLabelText("Procedure"), { target: { value: "X1" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(create).toHaveBeenCalledWith("t", { provider_id: "P1", procedure_id: "X1" }));
  });

  it("sends a price only when one was typed, and refuses a malformed one", async () => {
    create.mockResolvedValue({ offering: {}, deduplicated: false });
    render(<ClinicOffers token="t" />);
    const open = await screen.findByRole("button", { name: "Add an offer" });
    await waitFor(() => expect(open).toBeEnabled()); // it renders at once but stays disabled until the clinics and procedures have loaded
    fireEvent.click(open);
    fireEvent.change(screen.getByLabelText("Clinic"), { target: { value: "P1" } });
    fireEvent.change(screen.getByLabelText("Procedure"), { target: { value: "X1" } });
    fireEvent.change(screen.getByLabelText("Approved price"), { target: { value: "about 2k" } });
    expect(screen.getByText(/Enter the price as a number/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add offer" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Approved price"), { target: { value: "1800" } });
    fireEvent.click(screen.getByRole("button", { name: "Add offer" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("t", { provider_id: "P1", procedure_id: "X1", estimated_price: "1800", currency: "USD" }));
  });

  it("enables adding an offer as soon as the page reports a new procedure, without a reload", async () => {
    procedures.mockResolvedValue({ procedures: [] });
    const { rerender } = render(<ClinicOffers token="t" refreshKey={0} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Add an offer" })).toBeDisabled());

    procedures.mockResolvedValue({ procedures: [{ id: "X1", name: "Dental implants" }] });
    rerender(<ClinicOffers token="t" refreshKey={1} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Add an offer" })).toBeEnabled());
  });
});
