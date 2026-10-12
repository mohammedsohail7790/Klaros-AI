import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getRef = vi.fn();
const outcome = vi.fn();
vi.mock("@/components/ui/Modal", () => ({ Modal: ({ title, children }: { title: string; children: React.ReactNode }) => <div role="dialog" aria-label={title}>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getProviderReferral: (...a: unknown[]) => getRef(...a),
  recordReferralOutcome: (...a: unknown[]) => outcome(...a),
  createProviderReferral: vi.fn(),
  recordClinicResponse: vi.fn(),
  recordReferralApproval: vi.fn(),
  sendProviderReferral: vi.fn(),
}));
import { ReferralDetail } from "@/components/mt/ReferralDialogs";

const detail = (status: string, outcomeValue: string | null = null) => ({
  referral: { id: "R1", lead_id: "L1", provider_id: "P1", status, permitted_fields: ["name"], sent_at: "2026-10-12T00:00:00Z", send_attempts: 1, blocked_reason: null, clinic_response_at: null, outcome: outcomeValue, outcome_reported_at: null, created_at: null },
  gate: { allowed: true, code: null, fields: ["name"] },
  approvals: [],
  deliveries: [],
});

beforeEach(() => { getRef.mockReset(); outcome.mockReset(); });

describe("Referral outcome", () => {
  it("records the first outcome only with a reference to where the clinic reported it", async () => {
    getRef.mockResolvedValue(detail("ACCEPTED"));
    outcome.mockResolvedValue({});
    render(<ReferralDetail token="t" id="R1" providerName={{ P1: "Anatolia" }} onClose={vi.fn()} />);
    const button = await screen.findByRole("button", { name: "Record outcome" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Where the clinic reported it"), { target: { value: "Clinic email" } });
    fireEvent.click(button);
    await waitFor(() => expect(outcome).toHaveBeenCalledWith("t", "R1", { outcome: "TREATMENT_COMPLETED", evidence: "Clinic email" }));
  });

  it("lets the owner update an outcome later, because a consultation usually comes before the treatment", async () => {
    getRef.mockResolvedValue(detail("OUTCOME_REPORTED", "CONSULTATION_ATTENDED"));
    outcome.mockResolvedValue({});
    render(<ReferralDetail token="t" id="R1" providerName={{ P1: "Anatolia" }} onClose={vi.fn()} />);
    expect(await screen.findByText("Update the outcome")).toBeInTheDocument();
    expect(screen.getByText(/Reported so far: Consultation attended/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Where the clinic reported it"), { target: { value: "Clinic invoice 1042" } });
    fireEvent.click(screen.getByRole("button", { name: "Update outcome" }));
    await waitFor(() => expect(outcome).toHaveBeenCalledWith("t", "R1", { outcome: "TREATMENT_COMPLETED", evidence: "Clinic invoice 1042" }));
    expect(await screen.findByText("Outcome updated.")).toBeInTheDocument();
  });
});
