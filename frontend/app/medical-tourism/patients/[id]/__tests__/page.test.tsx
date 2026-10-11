import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pipeline = vi.fn();
const tasks = vi.fn();
const providers = vi.fn();
const getLeadMock = vi.fn();
const accept = vi.fn();
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "L1" }) }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/business/LeadConsentPanel", () => ({ LeadConsentPanel: ({ leadId }: { leadId: string }) => <div>consent panel for {leadId}</div> }));
vi.mock("@/components/mt/ReferralDialogs", () => ({
  NewReferral: ({ initialLeadId }: { initialLeadId?: string }) => <div>new referral dialog for {initialLeadId}</div>,
  ReferralDetail: ({ id }: { id: string }) => <div>referral dialog {id}</div>,
}));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getMedicalTourismPipeline: (...a: unknown[]) => pipeline(...a),
  listHandoffTasks: (...a: unknown[]) => tasks(...a),
  listMedicalTourismProviders: (...a: unknown[]) => providers(...a),
  getLead: (...a: unknown[]) => getLeadMock(...a),
  acceptHandoffTask: (...a: unknown[]) => accept(...a),
  completeHandoffTask: vi.fn(),
}));
import Page from "@/app/medical-tourism/patients/[id]/page";

const intake = { procedure: "Knee replacement", destination: "India", timeframe: "Within 6 weeks", language: "English", contact_time: null };
const row = (over: object = {}) => ({
  lead_id: "L1", name: "Fatima Rahman", stage: "PROVIDER_OPTIONS", stage_number: 4, next_action: "", held_for_person: false, consent_frozen: false, intake,
  referrals: [], open_tasks: 0, assigned_user_id: null, created_at: new Date().toISOString(), ...over,
});
const referral = (status: string) => ({ id: "R1", provider_id: "P1", provider_name: "Lotus Institute", status, sent_at: status === "DRAFT" ? null : new Date().toISOString(), clinic_response_at: null, outcome_reported_at: null });

beforeEach(() => {
  [pipeline, tasks, providers, getLeadMock, accept].forEach((m) => m.mockReset());
  pipeline.mockResolvedValue({ stages: [], counts: {}, leads: [row()], definitions: "" });
  tasks.mockResolvedValue({ tasks: [], total: 0 });
  providers.mockResolvedValue({ providers: [{ id: "P1", name: "Lotus Institute", country: "IN", status: "ACTIVE", verification_status: "VERIFIED" }] });
  getLeadMock.mockResolvedValue({ lead: { id: "L1", phone: "+971500000000", email: "fatima@example.com" } });
});

describe("Patient case file", () => {
  it("puts who, what, where and how far along at the top, with one clear next step", async () => {
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: "Fatima Rahman" })).toBeInTheDocument();
    expect(screen.getByText(/Knee replacement · India/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Progress" })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Provider options");
    const next = within(screen.getByRole("region", { name: "Next step" }));
    expect(next.getByText(/Pick a verified clinic/)).toBeInTheDocument();
    fireEvent.click(next.getByRole("button", { name: "Choose a clinic" }));
    expect(screen.getByText("new referral dialog for L1")).toBeInTheDocument();
  });

  it("shows what the patient told us and their contact details, and the consent panel", async () => {
    render(<Page />);
    const about = within(await screen.findByRole("region", { name: "About the enquiry" }));
    expect(about.getByText("Within 6 weeks")).toBeInTheDocument();
    expect(about.getByText("+971500000000")).toBeInTheDocument();
    expect(about.getByText("fatima@example.com")).toBeInTheDocument();
    expect(screen.getByText("consent panel for L1")).toBeInTheDocument();
  });

  it("shows NO details and does not even request contact data for a patient who withdrew consent", async () => {
    pipeline.mockResolvedValue({ stages: [], counts: {}, leads: [row({ name: null, stage: "CONSENT", consent_frozen: true, intake: { procedure: null, destination: null, timeframe: null, language: null, contact_time: null } })], definitions: "" });
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: "Patient (details withheld)" })).toBeInTheDocument();
    expect(screen.getByText(/has not agreed to us keeping their details/)).toBeInTheDocument();
    expect(getLeadMock).not.toHaveBeenCalled();
    expect(screen.queryByText("+971500000000")).not.toBeInTheDocument();
  });

  it("opens the live referral for the approval step and lists every clinic with its status", async () => {
    pipeline.mockResolvedValue({ stages: [], counts: {}, leads: [row({ stage: "PATIENT_APPROVAL", referrals: [referral("DRAFT")] })], definitions: "" });
    render(<Page />);
    const next = within(await screen.findByRole("region", { name: "Next step" }));
    fireEvent.click(next.getByRole("button", { name: "Record decision" }));
    expect(screen.getByText("referral dialog R1")).toBeInTheDocument();
    const clinics = within(screen.getByRole("region", { name: "Referrals" }));
    expect(clinics.getByText("Lotus Institute")).toBeInTheDocument();
    expect(clinics.getByText("not sent")).toBeInTheDocument();
  });

  it("lets the owner take a task and says nothing is waiting when there is none", async () => {
    tasks.mockResolvedValue({ tasks: [{ id: "T1", lead_id: "L1", kind: "escalation", reason_code: "clinical_question", status: "OPEN", owner_user_id: null, accepted_at: null, completed_at: null, note: null, created_at: null }], total: 1 });
    accept.mockResolvedValue({});
    render(<Page />);
    const tasksRegion = within(await screen.findByRole("region", { name: "Tasks" }));
    expect(tasksRegion.getByText("Needs a person")).toBeInTheDocument();
    expect(tasksRegion.getByText(/medical question/)).toBeInTheDocument();
    fireEvent.click(tasksRegion.getByRole("button", { name: "I'll take it" }));
    await waitFor(() => expect(accept).toHaveBeenCalledWith("t", "T1"));
  });

  it("says plainly when the patient cannot be found", async () => {
    pipeline.mockResolvedValue({ stages: [], counts: {}, leads: [], definitions: "" });
    render(<Page />);
    expect(await screen.findByText(/could not be found/)).toBeInTheDocument();
  });
});
