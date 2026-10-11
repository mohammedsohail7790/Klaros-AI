import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getPipeline = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class ApiError extends Error {}, getMedicalTourismPipeline: (...a: unknown[]) => getPipeline(...a) }));
import Page from "@/app/medical-tourism/pipeline/page";

const counts = { NEW_ENQUIRY: 0, CONSENT: 1, QUALIFICATION: 1, PROVIDER_OPTIONS: 0, PATIENT_APPROVAL: 0, REFERRED: 1, CLINIC_RESPONSE: 0, OUTCOME: 0 };
const lead = (over: object) => ({ lead_id: "L", name: "Amira Test", stage: "QUALIFICATION", stage_number: 3, next_action: "Complete the administrative qualification.", held_for_person: false, consent_frozen: false, referrals: [], open_tasks: 0, assigned_user_id: null, created_at: null, ...over });
const data = {
  stages: Object.keys(counts), counts, definitions: "Derived from recorded facts.",
  leads: [lead({ lead_id: "L1" }), lead({ lead_id: "L2", name: null, stage: "CONSENT", stage_number: 2, consent_frozen: true }), lead({ lead_id: "L3", name: "Omar Test", stage: "REFERRED", stage_number: 6, referrals: [{ id: "R1", provider_id: "P1", status: "SENT" }], open_tasks: 2, held_for_person: true })],
};

beforeEach(() => { getPipeline.mockReset(); });

describe("Medical Tourism pipeline", () => {
  it("shows every patient at the stage the backend derived, with the eight stage tabs and their counts", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    expect(await screen.findByRole("link", { name: "Amira Test" })).toHaveAttribute("href", "/leads/L1");
    expect(screen.getAllByRole("tab")).toHaveLength(9); // All + eight stages
    expect(screen.getByRole("tab", { name: "6. Referred (1)" })).toBeInTheDocument();
    expect(screen.getByText("2 open")).toBeInTheDocument();
    expect(screen.getByText(/held for a person/)).toBeInTheDocument();
  });

  it("lists a patient who has not agreed to us keeping their details WITHOUT a name", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    expect(await screen.findByText(/Name withheld/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\+1555|@example/);
  });

  it("filters by stage", async () => {
    getPipeline.mockResolvedValue(data);
    render(<Page />);
    await screen.findByRole("link", { name: "Amira Test" });
    fireEvent.click(screen.getByRole("tab", { name: "6. Referred (1)" }));
    expect(screen.queryByRole("link", { name: "Amira Test" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Omar Test" })).toBeInTheDocument();
  });

  it("shows a retryable error", async () => {
    getPipeline.mockRejectedValue(new Error("boom"));
    render(<Page />);
    expect(await screen.findByText("Unable to load the pipeline.")).toBeInTheDocument();
  });
});
