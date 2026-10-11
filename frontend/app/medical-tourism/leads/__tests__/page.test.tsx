import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listPatientLeads = vi.fn();
const searchLeads = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  createPatientLead: vi.fn(),
  listMedicalTourismProcedures: vi.fn().mockResolvedValue({ procedures: [] }),
  listPatientLeads: (...a: unknown[]) => listPatientLeads(...a),
  searchLeads: (...a: unknown[]) => searchLeads(...a),
}));
import Page from "@/app/medical-tourism/leads/page";

const LEAD_ID = "4d9d1dfa-0fe6-444e-98f3-c76772837978";
const pl = { id: "pl1", lead_id: LEAD_ID, procedure_id: null, preferred_destination_country: "IN", travel_start_date: null, travel_end_date: null, has_insurance: false, created_at: "2026-10-01T00:00:00Z" };

beforeEach(() => { listPatientLeads.mockReset(); searchLeads.mockReset(); });

describe("Medical Tourism patient leads", () => {
  it("shows the patient's name as a link to the lead — never the raw id", async () => {
    listPatientLeads.mockResolvedValue({ patient_leads: [pl], total: 1 });
    searchLeads.mockResolvedValue({ leads: [{ id: LEAD_ID, name: "Pat Ient" }], total: 1 });
    render(<Page />);
    const link = await screen.findByRole("link", { name: "Pat Ient" });
    expect(link).toHaveAttribute("href", `/leads/${LEAD_ID}`);
    expect(document.body.textContent).not.toContain(LEAD_ID);
  });

  it("falls back to a plain label when names cannot be loaded", async () => {
    listPatientLeads.mockResolvedValue({ patient_leads: [pl], total: 1 });
    searchLeads.mockRejectedValue(new Error("nope"));
    render(<Page />);
    expect(await screen.findByRole("link", { name: "Patient enquiry" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(LEAD_ID);
  });

  it("shows an empty state", async () => {
    listPatientLeads.mockResolvedValue({ patient_leads: [], total: 0 });
    searchLeads.mockResolvedValue({ leads: [], total: 0 });
    render(<Page />);
    expect(await screen.findByText("No Medical Tourism patient leads yet.")).toBeInTheDocument();
  });
});
