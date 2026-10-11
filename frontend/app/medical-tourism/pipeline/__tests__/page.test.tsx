import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getPipeline = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class ApiError extends Error {}, getMedicalTourismPipeline: (...a: unknown[]) => getPipeline(...a) }));
import Page from "@/app/medical-tourism/pipeline/page";

const counts = { NEW_ENQUIRY: 0, CONSENT: 1, QUALIFICATION: 1, PROVIDER_OPTIONS: 0, PATIENT_APPROVAL: 0, REFERRED: 1, CLINIC_RESPONSE: 0, OUTCOME: 0 };
const intake = (procedure: string | null, destination: string | null, timeframe: string | null = null) => ({ procedure, destination, timeframe, language: null, contact_time: null });
const lead = (over: object) => ({ lead_id: "L", name: "Amira Test", stage: "QUALIFICATION", stage_number: 3, next_action: "", held_for_person: false, consent_frozen: false, intake: intake("Dental veneers", "Turkey", "Within 2 months"), referrals: [], open_tasks: 0, assigned_user_id: null, created_at: null, ...over });
const data = {
  stages: Object.keys(counts), counts, definitions: "",
  leads: [
    lead({ lead_id: "L1" }),
    lead({ lead_id: "L2", name: null, stage: "CONSENT", stage_number: 2, consent_frozen: true, intake: intake(null, null) }),
    lead({ lead_id: "L3", name: "Omar Test", stage: "REFERRED", stage_number: 6, referrals: [{ id: "R1", provider_id: "P1", provider_name: "Lotus Institute", status: "SENT", sent_at: new Date().toISOString(), clinic_response_at: null, outcome_reported_at: null }], open_tasks: 2, held_for_person: true }),
  ],
};

beforeEach(() => { getPipeline.mockReset(); search = ""; });

describe("Patients board", () => {
  it("shows a column for each of the eight stages with the patients in them", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    const board = await screen.findByLabelText("Patient board");
    expect(within(board).getAllByRole("region")).toHaveLength(8);
    const qualification = within(within(board).getByRole("region", { name: "Qualification" }));
    expect(qualification.getByText("Amira Test")).toBeInTheDocument();
    expect(qualification.getByText("Dental veneers · Turkey")).toBeInTheDocument();
    expect(qualification.getByText("Within 2 months")).toBeInTheDocument();
    expect(within(board).getAllByText("No one here").length).toBeGreaterThan(0);
  });

  it("every card opens that patient's case file, and shows the clinic, tasks and a held flag", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    const card = await screen.findByRole("link", { name: "Open Omar Test" });
    expect(card).toHaveAttribute("href", "/medical-tourism/patients/L3");
    expect(card).toHaveTextContent("With Lotus Institute");
    expect(card).toHaveTextContent("2 tasks");
    expect(card).toHaveTextContent("Held for a person");
    expect(screen.getByRole("link", { name: "Open Amira Test" })).toHaveTextContent("Review enquiry");
  });

  it("shows a patient who withdrew consent WITHOUT a name or any detail", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    const card = await screen.findByRole("link", { name: "Open Patient (details withheld)" });
    expect(card).toHaveTextContent("Nothing to do now");
    expect(document.body.textContent).not.toMatch(/\+\d{6}|@example/);
  });

  it("switches to a list with stage filters", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    await screen.findByLabelText("Patient board");
    fireEvent.click(screen.getByRole("tab", { name: /List/ }));
    expect(screen.getAllByRole("tab", { name: /\(\d\)/ })).toHaveLength(9); // All + eight stages
    fireEvent.click(screen.getByRole("tab", { name: "6. Referred (1)" }));
    expect(screen.queryByRole("link", { name: "Amira Test" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Omar Test" })).toHaveAttribute("href", "/medical-tourism/patients/L3");
  });

  it("opens straight into the list for a stage chosen from Today", async () => {
    search = "stage=REFERRED";
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    expect(await screen.findByRole("link", { name: "Omar Test" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Amira Test" })).not.toBeInTheDocument();
  });

  it("explains an empty pipeline and shows a retryable error", async () => {
    getPipeline.mockResolvedValue({ stages: [], counts, leads: [], definitions: "" });
    const { unmount } = render(<Page />);
    expect(await screen.findByText(/No patient enquiries yet/)).toBeInTheDocument();
    unmount();
    getPipeline.mockRejectedValue(new Error("boom"));
    render(<Page />);
    expect(await screen.findByText("Unable to load your patients.")).toBeInTheDocument();
  });
});
