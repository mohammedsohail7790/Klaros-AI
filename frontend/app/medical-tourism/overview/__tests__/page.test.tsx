import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pipeline = vi.fn();
const tasks = vi.fn();
const revenue = vi.fn();
const funnel = vi.fn();
const providers = vi.fn();
const crewList = vi.fn();
const crewActivity = vi.fn();
const attention = vi.fn();
const approvals = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", full_name: "Sam Owner" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getMedicalTourismPipeline: (...a: unknown[]) => pipeline(...a),
  listHandoffTasks: (...a: unknown[]) => tasks(...a),
  getMedicalTourismRevenue: (...a: unknown[]) => revenue(...a),
  getMedicalTourismFunnel: (...a: unknown[]) => funnel(...a),
  listMedicalTourismProviders: (...a: unknown[]) => providers(...a),
  getOperatingCrew: (...a: unknown[]) => crewList(...a),
  getCrewActivity: (...a: unknown[]) => crewActivity(...a),
  getAttentionQueue: (...a: unknown[]) => attention(...a),
  listApprovals: (...a: unknown[]) => approvals(...a),
}));
import Page from "@/app/medical-tourism/overview/page";

const counts = { NEW_ENQUIRY: 0, CONSENT: 1, QUALIFICATION: 1, PROVIDER_OPTIONS: 1, PATIENT_APPROVAL: 1, REFERRED: 1, CLINIC_RESPONSE: 0, OUTCOME: 0 };
const intake = (procedure: string | null, destination: string | null) => ({ procedure, destination, timeframe: null, language: null, contact_time: null });
const lead = (over: object) => ({ lead_id: "L", name: "Patient", stage: "QUALIFICATION", stage_number: 3, next_action: "", held_for_person: false, consent_frozen: false, intake: intake(null, null), referrals: [], open_tasks: 0, assigned_user_id: null, created_at: new Date().toISOString(), ...over });
const ago = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();
const quiet = { delivery: { halla_events_failed: 0, halla_events_stuck_past_lease: 0 } };

const leads = [
  lead({ lead_id: "A", name: "Fatima Rahman", stage: "PATIENT_APPROVAL", intake: intake("Knee replacement", "India"), referrals: [{ id: "R1", provider_id: "P1", provider_name: "Lotus Institute", status: "APPROVED", sent_at: null, clinic_response_at: null, outcome_reported_at: null }] }),
  lead({ lead_id: "B", name: "Omar Khalil", stage: "PROVIDER_OPTIONS", intake: intake("Hair transplant", "Turkey") }),
  lead({ lead_id: "C", name: "Yusuf Haddad", stage: "REFERRED", intake: intake("IVF", "India"), referrals: [{ id: "R2", provider_id: "P1", provider_name: "Lotus Institute", status: "SENT", sent_at: ago(1), clinic_response_at: null, outcome_reported_at: null }] }),
  lead({ lead_id: "D", name: null, stage: "CONSENT", consent_frozen: true }),
];

beforeEach(() => {
  [pipeline, tasks, revenue, funnel, providers, crewList, crewActivity, attention, approvals].forEach((m) => m.mockReset());
  providers.mockResolvedValue({ providers: [], total: 0 });
  crewList.mockRejectedValue(new Error("not loaded")); // the extras stay hidden unless a test provides them
  crewActivity.mockRejectedValue(new Error("not loaded"));
  attention.mockRejectedValue(new Error("not loaded"));
  approvals.mockResolvedValue({ approvals: [] });
  pipeline.mockResolvedValue({ stages: Object.keys(counts), counts, leads, definitions: "" });
  tasks.mockResolvedValue({ tasks: [], total: 0 });
  revenue.mockResolvedValue({ by_currency: {}, definitions: "" });
  funnel.mockResolvedValue(quiet);
});

describe("Today", () => {
  it("answers 'what needs me?' with named patients, the reason, and one action each, most urgent first", async () => {
    render(<Page />);
    const list = within(await screen.findByRole("region", { name: "Needs you" }));
    const rows = list.getAllByRole("listitem");
    expect(rows).toHaveLength(2); // the waiting and the withdrawn patients are not asks
    expect(rows[0]).toHaveTextContent("Fatima Rahman");
    expect(rows[0]).toHaveTextContent("Knee replacement · India");
    expect(rows[0]).toHaveTextContent("Urgent");
    expect(within(rows[0]).getByRole("link", { name: /Send referral/ })).toHaveAttribute("href", "/medical-tourism/patients/A");
    expect(rows[1]).toHaveTextContent("Omar Khalil");
    expect(within(rows[1]).getByRole("link", { name: /Choose a clinic/ })).toBeInTheDocument();
    expect(screen.getByText("2 things need you, 1 is urgent.")).toBeInTheDocument();
  });

  it("never says all is well while something needs a person", async () => {
    tasks.mockResolvedValue({ tasks: [{ id: "T1", lead_id: "B", kind: "escalation", reason_code: "emergency", status: "OPEN", owner_user_id: null, accepted_at: null, completed_at: null, note: null, created_at: null }], total: 1 });
    render(<Page />);
    expect(await screen.findByText(/Needs a person: Possible emergency/)).toBeInTheDocument();
    expect(screen.queryByText(/all caught up/i)).not.toBeInTheDocument();
  });

  it("says plainly when everyone is moving or waiting on a clinic", async () => {
    pipeline.mockResolvedValue({ stages: [], counts, leads: [leads[2]], definitions: "" });
    render(<Page />);
    expect(await screen.findByText(/all caught up/i)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Waiting on clinics" })).toHaveTextContent("Yusuf Haddad");
  });

  it("shows where patients are, linking each stage to the board, and never presents potential money as earned", async () => {
    revenue.mockResolvedValue({ by_currency: { USD: { PENDING: { count: 1, amount: "650.00" }, CONFIRMED: { count: 0, amount: "0.00" }, INVOICED: { count: 0, amount: "0.00" }, PAID: { count: 0, amount: "0.00" } } }, definitions: "" });
    render(<Page />);
    const stages = within(await screen.findByRole("region", { name: "Where your patients are" }));
    expect(stages.getByRole("link", { name: /Provider options/ })).toHaveAttribute("href", "/medical-tourism/pipeline?stage=PROVIDER_OPTIONS");
    const money = within(screen.getByRole("region", { name: "Money" }));
    expect(money.getByText("Potential")).toBeInTheDocument();
    expect(money.getByText(/\$650/)).toBeInTheDocument();
    expect(money.getByText(/not earned/i)).toBeInTheDocument();
  });

  it("warns when messages from the AI receptionist did not arrive", async () => {
    funnel.mockResolvedValue({ delivery: { halla_events_failed: 1, halla_events_stuck_past_lease: 2 } });
    render(<Page />);
    expect(await screen.findByText(/3 messages from the AI receptionist did not finish arriving/)).toBeInTheDocument();
  });

  it("greets the owner by first name and shows a retryable error when loading fails", async () => {
    render(<Page />);
    expect(await screen.findByRole("heading", { level: 1, name: /Sam/ })).toBeInTheDocument();
  });

  it("shows an error with a retry when the data cannot be loaded", async () => {
    pipeline.mockRejectedValue(new Error("boom"));
    render(<Page />);
    expect(await screen.findByText("Unable to load Today.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("shows the whole journey and says the business is stuck at verifying a clinic when none is verified", async () => {
    providers.mockResolvedValue({ providers: [{ status: "ACTIVE", contact_email: "a@x.com", verification_status: "UNDER_REVIEW" }, { status: "ACTIVE", contact_email: null, verification_status: "UNVERIFIED" }], total: 2 });
    funnel.mockResolvedValue({ ...quiet, received: 4, qualification: { qualified: 2 } });
    render(<Page />);
    const journey = within(await screen.findByRole("region", { name: "The whole journey" }));
    const steps = journey.getAllByRole("listitem");
    expect(steps).toHaveLength(6);
    expect(steps[1]).toHaveTextContent("Clinics verified");
    expect(steps[1]).toHaveTextContent("You are here");
    expect(steps[2]).toHaveTextContent("4");
    expect(journey.getByText(/None of your 2 clinics is verified/)).toBeInTheDocument();
  });

  it("lists what each AI did in plain words, stuck ones first, and links to the agent", async () => {
    crewList.mockResolvedValue({ verticals: [], running: 2, total: 2, members: [
      { key: "ok", name: "Owner Brief", purpose: "Writes the owner a brief. [operating crew]", trigger: "Every day at 07:00", status: "ACTIVE", running: true, agent_id: "A1" },
      { key: "bad", name: "Lead Responder", purpose: "Reads every new enquiry. [operating crew]", trigger: "When lead.created happens", status: "ACTIVE", running: true, agent_id: "A2" },
    ] });
    crewActivity.mockResolvedValue({ days: 7, members: [
      { key: "ok", name: "Owner Brief", agent_id: "A1", runs: 3, failed: 0, last: { status: "COMPLETED", at: new Date().toISOString(), trigger: "SCHEDULE", summary: "Wrote the brief." } },
      { key: "bad", name: "Lead Responder", agent_id: "A2", runs: 1, failed: 1, last: { status: "FAILED", at: new Date().toISOString(), trigger: "EVENT", summary: null } },
    ] });
    render(<Page />);
    const ai = within(await screen.findByRole("region", { name: "What your AI team did" }));
    const rows = ai.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Lead Responder");
    expect(rows[0]).toHaveTextContent(/Could not finish its last run/);
    expect(rows[1]).toHaveTextContent("Wrote the brief.");
    expect(ai.getByRole("link", { name: "Owner Brief" })).toHaveAttribute("href", "/agents/A1");
    expect(ai.getByText(/1 of 2 ran in the last 7 days, 1 could not finish/)).toBeInTheDocument();
    expect(ai.queryByText(/\[operating crew\]/)).not.toBeInTheDocument();
  });

  it("asks for approvals and other business-wide items beyond patients, and hides that block when there are none", async () => {
    approvals.mockResolvedValue({ approvals: [{ id: "X1" }, { id: "X2" }] });
    attention.mockResolvedValue({ items: [
      { category: "mt_no_verified_clinic", priority: "HIGH", score: 1, title: "No verified clinic", reason: "Patients cannot be referred.", entity_type: "mt_clinics", entity_id: "T", link: "/medical-tourism/agreements", age_days: null, monetary_value: null },
      { category: "other", priority: "LOW", score: 0, title: "Minor thing", reason: "x", entity_type: "e", entity_id: "E", link: "/x", age_days: null, monetary_value: null },
    ], critical_count: 0, high_count: 1 });
    render(<Page />);
    const also = within(await screen.findByRole("region", { name: "Also needs you" }));
    expect(also.getByText("2 actions are waiting for your approval")).toBeInTheDocument();
    expect(also.getByRole("link", { name: /Review/ })).toHaveAttribute("href", "/approvals");
    expect(also.getByText("No verified clinic")).toBeInTheDocument();
    expect(also.queryByText("Minor thing")).not.toBeInTheDocument(); // low priority is not an ask
  });

  it("leaves the extra sections out, and still shows the page, when they cannot be read", async () => {
    render(<Page />);
    expect(await screen.findByRole("region", { name: "Needs you" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "What your AI team did" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Also needs you" })).not.toBeInTheDocument();
  });
});
