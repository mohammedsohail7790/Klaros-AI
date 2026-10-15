import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const save = vi.fn();
vi.mock("@/components/ui/Modal", () => ({ Modal: ({ title, children }: { title: string; children: React.ReactNode }) => <div role="dialog" aria-label={title}>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  getClinicTerms: (...a: unknown[]) => get(...a),
  saveClinicTerms: (...a: unknown[]) => save(...a),
}));
import { ClinicTerms } from "@/components/mt/ClinicTerms";

const provider = { id: "P1", name: "Anatolia Dental Clinic" } as never;
const empty = { set: false, commission_basis: null, commission_percentage: null, flat_amount: null, currency: null, qualifying_outcome: null, agreement_reference: null };
const onSaved = vi.fn();

describe("Clinic terms", () => {
  beforeEach(() => { [get, save, onSaved].forEach((m) => m.mockReset()); get.mockResolvedValue({ terms: empty }); save.mockResolvedValue({ terms: empty }); });

  it("saves nothing as 'none agreed' without inventing a commission", async () => {
    render(<ClinicTerms token="t" provider={provider} onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.click(await screen.findByRole("button", { name: "Save terms" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("t", "P1", expect.objectContaining({ commission_basis: null, commission_percentage: null, flat_amount: null, currency: null })));
    expect(onSaved).toHaveBeenCalled();
  });

  it("saves a percentage commission with its currency and agreement reference", async () => {
    render(<ClinicTerms token="t" provider={provider} onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.change(await screen.findByLabelText("Commission type"), { target: { value: "PERCENTAGE" } });
    fireEvent.change(screen.getByLabelText("Commission percentage"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("Agreement reference"), { target: { value: "Folder A" } });
    fireEvent.click(screen.getByRole("button", { name: "Save terms" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("t", "P1", expect.objectContaining({ commission_basis: "PERCENTAGE", commission_percentage: "15", flat_amount: null, currency: "USD", agreement_reference: "Folder A" })));
  });

  it("shows what the server refused and keeps the dialog open", async () => {
    const { ApiError } = await import("@/lib/api");
    save.mockRejectedValue(new ApiError(422, "Enter the commission percentage."));
    render(<ClinicTerms token="t" provider={provider} onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.click(await screen.findByRole("button", { name: "Save terms" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the commission percentage.");
    expect(onSaved).not.toHaveBeenCalled();
  });
});
