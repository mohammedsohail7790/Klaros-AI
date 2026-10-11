import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getFunnel = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getMedicalTourismFunnel: (...a: unknown[]) => getFunnel(...a),
}));
import Page from "@/app/medical-tourism/overview/page";

const base = {
  received: 12,
  consent: { no_evidence: 1, contact_granted: 9, store_personal_data_granted: 8, store_medical_information_granted: 0, frozen: 2 },
  qualification: { qualified: 6, unqualified: 0, held_for_person: 0, pending: 4 },
  appointments: { confirmed: 2, confirmed_by_halla: 2, tentative_not_a_booking: 3, cancelled: 1, completed: 0, no_show: 0 },
  consultations: {},
  escalations_requested: { klaros_safety_screen: 0, halla: 0 },
  delivery: { halla_events_processed: 30, halla_events_failed: 0, halla_events_in_flight: 0, halla_events_stuck_past_lease: 0, outbound_blocked_by_consent: 0 },
  drop_off: { no_consent_evidence: 1, frozen_after_withdrawal_or_decline: 2, awaiting_qualification: 4, held_for_person: 0 },
  definitions: "Counts only.",
};

beforeEach(() => { getFunnel.mockReset(); });

describe("Medical Tourism overview", () => {
  it("shows the counts and says nothing needs attention when nothing does", async () => {
    getFunnel.mockResolvedValue(base);
    render(<Page />);
    expect(await screen.findByText("Nothing needs attention right now.")).toBeInTheDocument();
    expect(screen.getByText("Received").closest("div")?.parentElement?.textContent).toContain("12");
    expect(screen.getByText("Requested, not booked")).toBeInTheDocument(); // a request is never presented as a booking
    expect(screen.getByText("Details withheld")).toBeInTheDocument();
  });

  it("warns when enquiries are held for a person and links to them", async () => {
    getFunnel.mockResolvedValue({ ...base, qualification: { ...base.qualification, held_for_person: 2 } });
    render(<Page />);
    expect(await screen.findByText(/2 enquiries are held for a person/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open patient enquiries" })).toHaveAttribute("href", "/medical-tourism/leads");
    expect(screen.queryByText("Nothing needs attention right now.")).not.toBeInTheDocument();
  });

  it("raises a danger alert when messages from the AI receptionist failed or are stuck", async () => {
    getFunnel.mockResolvedValue({ ...base, delivery: { ...base.delivery, halla_events_failed: 1, halla_events_stuck_past_lease: 2 } });
    render(<Page />);
    expect(await screen.findByText(/3 messages from the AI receptionist did not finish arriving \(1 failed, 2 stuck\)/)).toBeInTheDocument();
  });

  it("describes an escalation as a request to a person, not as something done", async () => {
    getFunnel.mockResolvedValue({ ...base, escalations_requested: { klaros_safety_screen: 1, halla: 1 } });
    render(<Page />);
    expect(await screen.findByText(/2 escalations were requested\. This means a person was asked to act, not that anyone has yet\./)).toBeInTheDocument();
  });

  it("shows no patient names or contact details, and a retryable error when the funnel cannot load", async () => {
    getFunnel.mockRejectedValue(new Error("boom"));
    render(<Page />);
    expect(await screen.findByText("Unable to load the overview.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
