import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getLeadOps = vi.fn();
let failLoad = false;
// A plain (non-spied) rejection: the spy wrapper would re-raise it as an unhandled rejection.
const failingLoad = () => new Promise((_, reject) => setTimeout(() => reject(new Error("down")), 0));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  getLeadOperations: (...a: unknown[]) => (failLoad ? failingLoad() : getLeadOps(...a)),
}));
import { LeadOperationsPanel } from "@/components/business/LeadOperationsPanel";

const base = {
  patient: { treatment: "Rhinoplasty", destination_country: "IN", medical_history_summary: null, travel_start: null, travel_end: null, has_insurance: false, insurance_notes: null },
  matching: {
    basis: { procedures: ["Rhinoplasty"], procedure_source: "stated", destination_country: "IN", criteria: [] },
    matches: [
      { provider_id: "p1", name: "Apex Hospital", location: "Delhi, IN", score: 6, fit: "STRONG", reasons: ["Offers Rhinoplasty (est. 3200 USD)", "Located in IN (Delhi)"], offerings: [] },
      { provider_id: "p2", name: "Lotus Clinic", location: "Istanbul, TR", score: 3, fit: "PARTIAL", reasons: ["Offers Rhinoplasty", "Located in TR, not the preferred IN"], offerings: [] },
    ],
    none_reason: null,
  },
  consultations: [],
  timeline: [{ at: new Date().toISOString(), kind: "received", text: "Lead received from your website" }],
  next_action: { text: "Schedule a consultation — Apex Hospital is the closest match.", route: "/medical-tourism/consultations", state: "READY" },
};

beforeEach(() => { getLeadOps.mockReset(); failLoad = false; });

describe("LeadOperationsPanel", () => {
  it("shows the patient's request, each match with its reasons, the timeline and the next action", async () => {
    getLeadOps.mockResolvedValue(base);
    render(<LeadOperationsPanel token="t" leadId="l1" />);
    expect(await screen.findByRole("heading", { name: "Patient enquiry" })).toBeInTheDocument();
    expect(screen.getByText("Rhinoplasty", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("Has insurance").nextSibling).toHaveTextContent("No"); // boolean shown as words
    const apex = screen.getByText("Apex Hospital").closest("li")!;
    expect(apex).toHaveTextContent("Strong match");
    expect(apex).toHaveTextContent("Offers Rhinoplasty (est. 3200 USD)");
    expect(screen.getByText("Lotus Clinic").closest("li")).toHaveTextContent("Partial match");
    expect(screen.getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/medical-tourism/consultations");
    expect(screen.getByRole("list", { name: "Lead timeline" })).toHaveTextContent("Lead received from your website");
    expect(screen.getByText(/Suggestions only — nothing is sent or booked automatically/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bnull\b|\bundefined\b|p1|p2/); // no raw IDs or null leakage
  });

  it("explains why there is no match instead of showing an empty list", async () => {
    getLeadOps.mockResolvedValue({ ...base, matching: { ...base.matching, matches: [], none_reason: "No active provider offers Rhinoplasty yet — add an offering on a provider." }, next_action: { text: "Add a provider.", route: "/medical-tourism/providers", state: "CONFIGURATION_REQUIRED" } });
    render(<LeadOperationsPanel token="t" leadId="l1" />);
    expect(await screen.findByText(/No active provider offers Rhinoplasty yet/)).toBeInTheDocument();
    expect(screen.getByText("Configuration required")).toBeInTheDocument();
  });

  it("a human next step has no link, and says nothing about auto-contact", async () => {
    getLeadOps.mockResolvedValue({ ...base, next_action: { text: "Review this enquiry and contact the patient, then mark the lead as contacted.", route: null, state: "READY" } });
    render(<LeadOperationsPanel token="t" leadId="l1" />);
    expect(await screen.findByText(/contact the patient/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Open/ })).not.toBeInTheDocument();
  });

  it("renders nothing for a lead without patient details (backend 404 -> null)", async () => {
    getLeadOps.mockResolvedValue(null);
    const { container } = render(<LeadOperationsPanel token="t" leadId="l1" />);
    await waitFor(() => expect(getLeadOps).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows an announced error when loading fails", async () => {
    failLoad = true;
    render(<LeadOperationsPanel token="t" leadId="l1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load this lead's operating details/);
  });

  it("does nothing without a token", async () => {
    render(<LeadOperationsPanel token={null} leadId="l1" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(getLeadOps).not.toHaveBeenCalled();
  });

  it("re-fetches when the lead's status changes", async () => {
    getLeadOps.mockResolvedValue(base);
    const { rerender } = render(<LeadOperationsPanel token="t" leadId="l1" refreshKey="NEW" />);
    await screen.findByText("Apex Hospital");
    rerender(<LeadOperationsPanel token="t" leadId="l1" refreshKey="CONTACTED" />);
    await waitFor(() => expect(getLeadOps).toHaveBeenCalledTimes(2));
  });
});
