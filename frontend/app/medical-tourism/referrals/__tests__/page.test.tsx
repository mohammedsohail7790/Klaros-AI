import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const desk = vi.fn();
const pipeline = vi.fn();
const create = vi.fn();
const transition = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/ui/Modal", () => ({ Modal: ({ title, children }: { title: string; children: React.ReactNode }) => <div role="dialog" aria-label={title}>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; code?: string; constructor(s: number, m: string, c?: string) { super(m); this.status = s; this.code = c; } },
  getCommissionDesk: (...a: unknown[]) => desk(...a),
  getMedicalTourismPipeline: (...a: unknown[]) => pipeline(...a),
  createProviderCommission: (...a: unknown[]) => create(...a),
  transitionCommission: (...a: unknown[]) => transition(...a),
}));
import Page from "@/app/medical-tourism/referrals/page";

const com = (over: object = {}) => ({ id: "C1", lead_id: "L1", provider_name: "Anatolia Dental", provider_referral_id: "R1", basis: "FLAT", commission_percentage: null, flat_amount: "650.00", computed_amount: "650.00", currency: "USD", status: "PENDING", qualifying_outcome: "TREATMENT_COMPLETED", outcome: "CONSULTATION_ATTENDED", outcome_evidence: "Clinic email", status_note: null, ...over });

beforeEach(() => {
  [desk, pipeline, create, transition].forEach((m) => m.mockReset());
  desk.mockResolvedValue({ commissions: [], awaiting: [] });
  pipeline.mockResolvedValue({ leads: [{ lead_id: "L1", name: "Layla Test", stage: "OUTCOME", intake: {}, referrals: [] }] });
});

describe("Commissions", () => {
  it("lists referrals whose clinic reported an outcome and lets the owner set up the commission with the agreed outcome", async () => {
    desk.mockResolvedValue({ commissions: [], awaiting: [{ referral_id: "R1", lead_id: "L1", provider_name: "Anatolia Dental", outcome: "CONSULTATION_ATTENDED", outcome_evidence: "Clinic email" }] });
    create.mockResolvedValue({ commission: com() });
    render(<Page />);
    const ready = within(await screen.findByRole("region", { name: "Ready for a commission" }));
    expect(ready.getByText(/Consultation attended/)).toBeInTheDocument();
    fireEvent.click(ready.getByRole("button", { name: "Set up commission" }));
    expect(screen.getByRole("button", { name: "Record commission" })).toBeDisabled(); // nothing typed: nothing guessed
    fireEvent.change(screen.getByLabelText("Fee"), { target: { value: "650" } });
    fireEvent.click(screen.getByRole("button", { name: "Record commission" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("t", "R1", { currency: "USD", basis: "FLAT", qualifying_outcome: "TREATMENT_COMPLETED", flat_amount: "650" }));
  });

  it("a percentage commission needs only the percentage; the treatment cost is optional", async () => {
    desk.mockResolvedValue({ commissions: [], awaiting: [{ referral_id: "R1", lead_id: "L1", provider_name: "A", outcome: "TREATMENT_COMPLETED", outcome_evidence: "x" }] });
    create.mockResolvedValue({ commission: com() });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Set up commission" }));
    fireEvent.click(screen.getByLabelText(/Percentage of treatment cost/));
    fireEvent.change(screen.getByLabelText("Percentage"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Record commission" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("t", "R1", { currency: "USD", basis: "PERCENTAGE", qualifying_outcome: "TREATMENT_COMPLETED", commission_percentage: "10" }));
  });

  it("shows potential money as potential, with the patient's name, the outcome and the evidence", async () => {
    desk.mockResolvedValue({ commissions: [com()], awaiting: [] });
    render(<Page />);
    const row = (await screen.findByText("Layla Test")).closest("tr")!;
    expect(row).toHaveTextContent("Anatolia Dental");
    expect(row).toHaveTextContent("Consultation attended");
    expect(row).toHaveTextContent("Clinic email");
    expect(row).toHaveTextContent("Potential");
    expect(row).toHaveTextContent("Not earned yet");
    expect(within(row).getByRole("button", { name: "Mark earned" })).toBeInTheDocument();
  });

  it("offers the one next step per status, in order", async () => {
    desk.mockResolvedValue({ commissions: [com({ id: "A", status: "CONFIRMED" }), com({ id: "B", status: "INVOICED" }), com({ id: "C", status: "CANCELLED" })], awaiting: [] });
    render(<Page />);
    await screen.findAllByRole("row");
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByRole("button", { name: "Mark invoiced" })).toBeInTheDocument();
    expect(within(rows[1]).getByRole("button", { name: "Mark paid" })).toBeInTheDocument();
    expect(within(rows[2]).queryByRole("button")).not.toBeInTheDocument();
  });

  it("explains in words why a commission cannot be marked earned without the outcome evidence", async () => {
    const { ApiError } = await import("@/lib/api");
    desk.mockResolvedValue({ commissions: [com()], awaiting: [] });
    transition.mockRejectedValue(new ApiError(409, "no_outcome_evidence", "no_outcome_evidence"));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark earned" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Mark earned" })).getByRole("button", { name: "Mark earned" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("has not reported the outcome your agreement names");
  });

  it("moves a commission one step and reloads", async () => {
    desk.mockResolvedValue({ commissions: [com({ status: "CONFIRMED" })], awaiting: [] });
    transition.mockResolvedValue({ commission: com({ status: "INVOICED" }) });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark invoiced" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Mark invoiced" })).getByRole("button", { name: "Mark invoiced" }));
    await waitFor(() => expect(transition).toHaveBeenCalledWith("t", "C1", "INVOICED", undefined));
    expect(await screen.findByText(/Commission is now invoiced/)).toBeInTheDocument();
  });
});
