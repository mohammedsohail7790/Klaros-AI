import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pipeline = vi.fn();
const tasks = vi.fn();
const revenue = vi.fn();
const funnel = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", full_name: "Sam Owner" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getMedicalTourismPipeline: (...a: unknown[]) => pipeline(...a),
  listHandoffTasks: (...a: unknown[]) => tasks(...a),
  getMedicalTourismRevenue: (...a: unknown[]) => revenue(...a),
  getMedicalTourismFunnel: (...a: unknown[]) => funnel(...a),
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
  [pipeline, tasks, revenue, funnel].forEach((m) => m.mockReset());
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
});
