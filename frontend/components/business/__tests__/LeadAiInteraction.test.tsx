import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getInteraction = vi.fn();
const syncLead = vi.fn();
const callLead = vi.fn();
// A rejection made outside the spy: a spied rejected promise is re-raised by the mock wrapper as an unhandled rejection.
let failWith: Error | null = null;
const fail = () => new Promise((_, reject) => setTimeout(() => reject(failWith), 0));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  getLeadInteraction: (...a: unknown[]) => (failWith ? fail() : getInteraction(...a)),
  syncLeadToHalla: (...a: unknown[]) => syncLead(...a),
  askHallaToCall: (...a: unknown[]) => callLead(...a),
}));
import { GenericNextAction, LeadAiInteraction } from "@/components/business/LeadAiInteraction";
import { AI_STATE_META, AiStatePill } from "@/components/business/AiState";

const base = (over: Record<string, unknown> = {}) => ({
  state: "NOT_CONNECTED", label: "Halla not connected",
  workforce: { status: "NOT_CONNECTED", mode: "none", adapter_implemented: false, message: "m" },
  events: [], summary: null, next_action: { text: "Make first contact", route: "/leads/l1" },
  ...over,
});

beforeEach(() => {
  syncLead.mockReset();
  callLead.mockReset();
  getInteraction.mockReset();
  failWith = null;
});

describe("LeadAiInteraction", () => {
  it("never implies a conversation that did not happen", async () => {
    getInteraction.mockResolvedValue(base());
    render(<LeadAiInteraction token="t" leadId="l1" />);
    expect(await screen.findByText("Halla not connected")).toBeInTheDocument();
    expect(screen.getByText(/No AI conversation has taken place with this customer/)).toBeInTheDocument();
    expect(screen.queryByText(/Conversation summary/)).not.toBeInTheDocument();
  });

  it("with a connected workforce and no events it says Halla has not spoken yet", async () => {
    getInteraction.mockResolvedValue(base({ state: "WAITING_FOR_HALLA", workforce: { status: "CONNECTED", mode: "live", adapter_implemented: true, message: "m" } }));
    render(<LeadAiInteraction token="t" leadId="l1" />);
    expect(await screen.findByText("Waiting for Halla")).toBeInTheDocument();
    expect(screen.getByText(/Halla hasn't spoken with this customer yet/)).toBeInTheDocument();
  });

  it("shows the recorded events, the summary, and marks simulated ones", async () => {
    getInteraction.mockResolvedValue(
      base({
        state: "QUALIFIED", label: "Qualified", summary: "Wants a hair transplant in Turkey.",
        workforce: { status: "NOT_CONNECTED", mode: "development", adapter_implemented: false, message: "m" },
        events: [
          { type: "halla.interaction.started", text: "Halla started a conversation", at: new Date().toISOString(), channel: "voice", summary: null, simulated: true },
          { type: "halla.lead.qualified", text: "Halla qualified the lead", at: new Date().toISOString(), channel: "voice", summary: "Wants a hair transplant in Turkey.", simulated: true },
        ],
      })
    );
    render(<LeadAiInteraction token="t" leadId="l1" />);
    expect(await screen.findByText("Wants a hair transplant in Turkey.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "AI interaction events" })).toHaveTextContent("Halla qualified the lead");
    expect(screen.getAllByText("simulated").length).toBe(2);
    expect(screen.getByText(/Development simulator — these events are simulated\. This is not a live Halla connection\./)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/halla\.lead\.qualified|undefined|null/);
  });

  it("explains a failure without crashing", async () => {
    failWith = new Error("down");
    render(<LeadAiInteraction token="t" leadId="l1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("We couldn't load the AI interaction just now.");
  });

  it("explains a permission failure", async () => {
    const { ApiError } = await import("@/lib/api");
    failWith = new (ApiError as unknown as new (s: number, m: string) => Error)(403, "no");
    render(<LeadAiInteraction token="t" leadId="l1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have permission to view AI interactions.");
  });

  it("does nothing without a token", async () => {
    render(<LeadAiInteraction token={null} leadId="l1" />);
    await new Promise((r) => setTimeout(r, 30));
    expect(getInteraction).not.toHaveBeenCalled();
  });
});

describe("AI state labels", () => {
  it("covers every state with a plain label", () => {
    expect(Object.keys(AI_STATE_META).sort()).toEqual(
      ["APPOINTMENT_CONFIRMED", "APPOINTMENT_REQUESTED", "CONFIGURATION_REQUIRED", "ESCALATED", "IN_PROGRESS", "NOT_CONNECTED", "QUALIFICATION_PENDING", "QUALIFIED", "WAITING_FOR_HALLA"].sort()
    );
    render(<AiStatePill state="ESCALATED" />);
    expect(screen.getByText("Escalated to a person")).toBeInTheDocument();
  });
});

describe("GenericNextAction", () => {
  it("shows the next action as a link, or says the lead is closed", () => {
    const { rerender } = render(<GenericNextAction action={{ text: "Qualify the lead", route: "/leads/l1" }} />);
    expect(screen.getByText("Qualify the lead")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/leads/l1");
    rerender(<GenericNextAction action={null} />);
    expect(screen.getByText(/Nothing to do — this lead is closed/)).toBeInTheDocument();
  });
});


describe("LeadAiInteraction — real Halla actions", () => {
  const live = (status: string) => base({ workforce: { status, mode: "live", adapter_implemented: true, message: "m" } });

  it("offers no Halla action unless the backend reports a real, connected Halla", async () => {
    for (const w of [base(), live("ERROR"), live("NOT_CONNECTED"), base({ workforce: { status: "CONNECTED", mode: "development", adapter_implemented: false, message: "m" } })]) {
      getInteraction.mockResolvedValue(w);
      const { unmount } = render(<LeadAiInteraction token="t" leadId="l1" />);
      await screen.findByText(/AI interaction/);
      expect(screen.queryByRole("button", { name: /Send to Halla|Ask Halla to call/ })).not.toBeInTheDocument();
      unmount();
    }
  });

  it("sends the lead to Halla and says what happened", async () => {
    getInteraction.mockResolvedValue(live("CONNECTED"));
    syncLead.mockResolvedValue({ synced: true, created: true });
    render(<LeadAiInteraction token="t" leadId="l1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Send to Halla/ }));
    expect(await screen.findByText("Sent to Halla.")).toBeInTheDocument();
    expect(syncLead).toHaveBeenCalledWith("t", "l1");
  });

  it("asks for confirmation before Halla calls a customer, and never calls without it", async () => {
    getInteraction.mockResolvedValue(live("CONNECTED"));
    callLead.mockResolvedValue({ requested: true, call_id: "CA1" });
    render(<LeadAiInteraction token="t" leadId="l1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Ask Halla to call/ }));
    expect(callLead).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(callLead).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Ask Halla to call/ }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, call" }));
    await waitFor(() => expect(callLead).toHaveBeenCalledWith("t", "l1", { reason: "follow_up" }));
    expect(await screen.findByText("Halla has been asked to call this customer.")).toBeInTheDocument();
  });

  it("shows the backend's reason when an action fails", async () => {
    getInteraction.mockResolvedValue(live("CONNECTED"));
    const { ApiError } = await import("@/lib/api");
    syncLead.mockImplementation(() => ({ then: (_ok: unknown, bad: (e: Error) => void) => bad(new (ApiError as unknown as new (s: number, m: string) => Error)(502, "Halla could not be reached")) }));
    render(<LeadAiInteraction token="t" leadId="l1" />);
    fireEvent.click(await screen.findByRole("button", { name: /Send to Halla/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Halla could not be reached");
  });
});
