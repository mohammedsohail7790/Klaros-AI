import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listReferrals = vi.fn();
const getReferral = vi.fn();
const approve = vi.fn();
const send = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; code?: string; constructor(status: number, message: string, code?: string) { super(message); this.status = status; this.code = code; } },
  listProviderReferrals: (...a: unknown[]) => listReferrals(...a),
  getProviderReferral: (...a: unknown[]) => getReferral(...a),
  listMedicalTourismProviders: vi.fn().mockResolvedValue({ providers: [{ id: "P1", name: "Apex Hospital", country: "IN", status: "ACTIVE", verification_status: "VERIFIED" }, { id: "P2", name: "Unverified Clinic", country: "TR", status: "ACTIVE", verification_status: "UNVERIFIED" }], total: 2 }),
  getMedicalTourismPipeline: vi.fn().mockResolvedValue({ leads: [{ lead_id: "L1", name: "Amira Test", stage: "PROVIDER_OPTIONS" }], counts: {}, stages: [], definitions: "" }),
  createProviderReferral: vi.fn(),
  recordReferralApproval: (...a: unknown[]) => approve(...a),
  sendProviderReferral: (...a: unknown[]) => send(...a),
  recordClinicResponse: vi.fn(),
  recordReferralOutcome: vi.fn(),
}));
import Page from "@/app/medical-tourism/provider-referrals/page";

const referral = (status: string) => ({ id: "R1", lead_id: "L1", provider_id: "P1", status, permitted_fields: [], sent_at: null, send_attempts: 0, blocked_reason: null, clinic_response_at: null, outcome: null, outcome_reported_at: null, created_at: null });
const detail = (status: string, gate: object) => ({ referral: referral(status), gate, approvals: [], deliveries: [] });

beforeEach(() => { listReferrals.mockReset(); getReferral.mockReset(); approve.mockReset(); send.mockReset(); });

async function openFirst() {
  fireEvent.click(await screen.findByRole("button", { name: "Open" }));
}

describe("Referrals", () => {
  it("shows why a send is blocked, in words, and does not offer to send", async () => {
    listReferrals.mockResolvedValue({ referrals: [referral("DRAFT")] });
    getReferral.mockResolvedValue(detail("DRAFT", { allowed: false, code: "no_share_consent", fields: [] }));
    render(<Page />);
    await openFirst();
    expect(await screen.findByTestId("gate-reason")).toHaveTextContent("The patient has not agreed to share their information with a clinic.");
    expect(screen.queryByRole("button", { name: "Send to clinic" })).not.toBeInTheDocument();
  });

  it("an approval cannot be recorded until the patient was informed, fields are chosen and the wording is named", async () => {
    listReferrals.mockResolvedValue({ referrals: [referral("DRAFT")] });
    getReferral.mockResolvedValue(detail("DRAFT", { allowed: false, code: "no_share_consent", fields: [] }));
    approve.mockResolvedValue({ referral: referral("APPROVED"), approval_id: "A1" });
    render(<Page />);
    await openFirst();
    const record = await screen.findByRole("button", { name: "Record approval" });
    expect(record).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Wording version"), { target: { value: "SHARE-v1" } });
    expect(record).toBeDisabled(); // still: the patient has not been marked informed
    fireEvent.click(screen.getByLabelText(/The patient was told which clinic receives this information/));
    expect(record).toBeEnabled();
    fireEvent.click(record);
    await waitFor(() => expect(approve).toHaveBeenCalledWith("t", "R1", { decision: "GRANTED", permitted_fields: ["name", "phone"], patient_informed: true, wording_version: "SHARE-v1", channel: "phone" }));
  });

  it("when the backend says a send is allowed it lists exactly what will go, and Send calls the backend", async () => {
    listReferrals.mockResolvedValue({ referrals: [referral("APPROVED")] });
    getReferral.mockResolvedValue(detail("APPROVED", { allowed: true, code: null, fields: ["name", "phone"] }));
    send.mockResolvedValue({ referral: referral("SENT"), newly_sent: true });
    render(<Page />);
    await openFirst();
    expect(await screen.findByText(/Only these will go: Name, Phone number/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send to clinic" }));
    await waitFor(() => expect(send).toHaveBeenCalledWith("t", "R1"));
  });

  it("a refusal from the backend is shown in plain words, not as a code", async () => {
    const { ApiError } = await import("@/lib/api");
    listReferrals.mockResolvedValue({ referrals: [referral("APPROVED")] });
    getReferral.mockResolvedValue(detail("APPROVED", { allowed: true, code: null, fields: ["name"] }));
    send.mockRejectedValue(new ApiError(409, "Referral data was not sent: provider not verified.", "provider_not_verified"));
    render(<Page />);
    await openFirst();
    fireEvent.click(await screen.findByRole("button", { name: "Send to clinic" }));
    expect(await screen.findByText("This clinic is not currently verified.")).toBeInTheDocument();
  });

  it("an approved referral is not offered the approval form again, and a sent one asks for the clinic's answer", async () => {
    listReferrals.mockResolvedValue({ referrals: [referral("SENT")] });
    getReferral.mockResolvedValue(detail("SENT", { allowed: false, code: null, fields: [] }));
    render(<Page />);
    await openFirst();
    expect(await screen.findByRole("button", { name: "Clinic accepted" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record approval" })).not.toBeInTheDocument();
  });
});
