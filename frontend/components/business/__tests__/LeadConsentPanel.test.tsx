import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getConsent = vi.fn();
const attest = vi.fn();
const erase = vi.fn();
let pilot = true;
vi.mock("@/lib/usePilotMode", () => ({ usePilotMode: () => pilot }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  getLeadConsent: (...a: unknown[]) => getConsent(...a),
  attestLeadConsent: (...a: unknown[]) => attest(...a),
  eraseLeadPersonalData: (...a: unknown[]) => erase(...a),
}));
import { LeadConsentPanel } from "@/components/business/LeadConsentPanel";

const granted = {
  evidence_recorded: true, granted_scopes: ["contact", "store_personal_data"], contact: true, store_personal_data: true, store_medical_information: false,
  as_of: "2026-10-09T10:00:00Z", wording_version: "MT-CONSENT-v1", method: "voice_ai_verbal", history_count: 2, note: null,
};
const withdrawn = { ...granted, granted_scopes: [], contact: false, store_personal_data: false, history_count: 3 };
const none = { evidence_recorded: false, granted_scopes: [], contact: false, store_personal_data: false, store_medical_information: false, as_of: null, wording_version: null, method: null, history_count: 0, note: "none" };

beforeEach(() => { getConsent.mockReset(); attest.mockReset(); erase.mockReset(); pilot = true; });

describe("LeadConsentPanel", () => {
  it("renders nothing for an organisation that is not a consent-gated pilot, and does not call the backend", () => {
    pilot = false;
    const { container } = render(<LeadConsentPanel token="t" leadId="L1" />);
    expect(container).toBeEmptyDOMElement();
    expect(getConsent).not.toHaveBeenCalled();
  });

  it("shows what the newest decision allows, how it was given, and that history is kept", async () => {
    getConsent.mockResolvedValue(granted);
    render(<LeadConsentPanel token="t" leadId="L1" />);
    expect(await screen.findByLabelText("May be contacted: yes")).toBeInTheDocument();
    expect(screen.getByLabelText("May keep personal details: yes")).toBeInTheDocument();
    expect(screen.getByLabelText("May keep health information: no")).toBeInTheDocument();
    expect(screen.getByText(/Said to the AI receptionist/)).toBeInTheDocument();
    expect(screen.getByText(/wording MT-CONSENT-v1/)).toBeInTheDocument();
    expect(screen.getByText(/2 decisions on record \(history is kept\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Erase personal details/)).not.toBeInTheDocument(); // consent still given: no erase
  });

  it("says plainly that nothing is agreed when there is no record", async () => {
    getConsent.mockResolvedValue(none);
    render(<LeadConsentPanel token="t" leadId="L1" />);
    expect(await screen.findByText(/No consent has been recorded for this patient, so nothing is treated as agreed/)).toBeInTheDocument();
    expect(screen.getByLabelText("May be contacted: no")).toBeInTheDocument();
  });

  it("records a team member's decision as a COMPLETE set, and only when it differs from the current one", async () => {
    getConsent.mockResolvedValue(granted);
    attest.mockResolvedValue({ ...granted, granted_scopes: ["contact"], store_personal_data: false });
    const onChanged = vi.fn();
    render(<LeadConsentPanel token="t" leadId="L1" onChanged={onChanged} />);
    const save = await screen.findByRole("button", { name: "Record decision" });
    expect(save).toBeDisabled(); // nothing changed yet
    fireEvent.click(screen.getByLabelText("May keep personal details", { selector: "input" }));
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(attest).toHaveBeenCalledWith("t", "L1", ["contact"]));
    expect(await screen.findByText("Recorded.")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("ticking nothing records a withdrawal", async () => {
    getConsent.mockResolvedValue(granted);
    attest.mockResolvedValue(withdrawn);
    render(<LeadConsentPanel token="t" leadId="L1" />);
    await screen.findByRole("button", { name: "Record decision" });
    fireEvent.click(screen.getByLabelText("May be contacted", { selector: "input" }));
    fireEvent.click(screen.getByLabelText("May keep personal details", { selector: "input" }));
    fireEvent.click(screen.getByRole("button", { name: "Record withdrawal" }));
    await waitFor(() => expect(attest).toHaveBeenCalledWith("t", "L1", []));
    expect(await screen.findByText("Recorded: the patient has withdrawn consent.")).toBeInTheDocument();
  });

  it("offers erasure only after personal-data consent is gone, asks to confirm, then erases", async () => {
    getConsent.mockResolvedValue(withdrawn);
    erase.mockResolvedValue({ erased: true });
    const onChanged = vi.fn();
    render(<LeadConsentPanel token="t" leadId="L1" onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: "Erase personal details…" }));
    expect(erase).not.toHaveBeenCalled(); // not until confirmed
    expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Erase" }));
    await waitFor(() => expect(erase).toHaveBeenCalledWith("t", "L1"));
    expect(await screen.findByText("Personal details have been erased.")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows the backend's refusal instead of claiming success", async () => {
    const { ApiError } = await import("@/lib/api");
    getConsent.mockResolvedValue(withdrawn);
    erase.mockRejectedValue(new ApiError(409, "Personal-data consent has not been withdrawn or declined for this lead."));
    render(<LeadConsentPanel token="t" leadId="L1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Erase personal details…" }));
    fireEvent.click(screen.getByRole("button", { name: "Erase" }));
    expect(await screen.findByText(/has not been withdrawn or declined/)).toBeInTheDocument();
    expect(screen.queryByText("Personal details have been erased.")).not.toBeInTheDocument();
  });
});
