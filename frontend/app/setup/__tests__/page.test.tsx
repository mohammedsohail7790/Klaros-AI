import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getTypes = vi.fn();
const getSetup = vi.fn();
const submit = vi.fn();
const confirm = vi.fn();
const listWordings = vi.fn().mockResolvedValue({ wordings: [], suggested_text: "A draft wording that is long enough to be approved by the owner.", min_length: 40, max_length: 2000 });
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  getBusinessTypes: (...a: unknown[]) => getTypes(...a), getMedicalTourismSetup: (...a: unknown[]) => getSetup(...a),
  submitMedicalTourismSetup: (...a: unknown[]) => submit(...a), confirmSetupItem: (...a: unknown[]) => confirm(...a),
  listConsentWordings: (...a: unknown[]) => listWordings(...a), approveConsentWording: vi.fn(),
}));
import Page from "@/app/setup/page";

const types = { types: [{ key: "medical_tourism", name: "Medical Tourism", available: true, description: "Connect patients abroad with verified clinics." }] };
const item = (key: string, status: string, route: string | null = null) => ({ key, label: key + " label", status, why: key + " why", route });
const configured = {
  configured: true, ready_to_launch: false, open_items: 3, note: "Everything under drafts is a draft for your approval.",
  profile: { business_name: "Atlas Care", patient_markets: ["GB"], destinations: ["IN"], business_model: "both", business_model_label: "Referral and coordination", procedures: ["Dental implants"] },
  checklist: [item("profile", "DONE"), item("providers", "TODO", "/medical-tourism/providers"), item("agreements_signed", "NEEDS_APPROVAL")],
  drafts: { intake_questions: { status: "DRAFT", needs: "owner approval", items: ["What treatment are you interested in?"] } },
};

beforeEach(() => { getTypes.mockReset(); getSetup.mockReset(); submit.mockReset(); confirm.mockReset(); getTypes.mockResolvedValue(types); sessionStorage.clear(); });

describe("Business setup", () => {
  it("offers the one-form setup to a business that has not started, and keeps Create disabled until every answer is given", async () => {
    getSetup.mockResolvedValue({ configured: false });
    render(<Page />);
    const create = await screen.findByRole("button", { name: "Create my business workspace" });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Atlas Care" } });
    fireEvent.change(screen.getByLabelText("Patient markets"), { target: { value: "United Kingdom, UAE" } });
    fireEvent.change(screen.getByLabelText("Destinations"), { target: { value: "India" } });
    fireEvent.change(screen.getByLabelText("Procedures"), { target: { value: "Dental implants\nHair transplant" } });
    expect(create).toBeDisabled(); // no business model chosen yet
    fireEvent.click(screen.getByLabelText(/Both/));
    expect(create).toBeEnabled();
  });

  it("sends the answers as lists and shows the backend's message when something is wrong", async () => {
    const { ApiError } = await import("@/lib/api");
    getSetup.mockResolvedValue({ configured: false });
    submit.mockRejectedValue(new ApiError(422, "'Atlantis' is not a country Klaros recognises; use a name or a two-letter code."));
    render(<Page />);
    fireEvent.change(await screen.findByLabelText("Business name"), { target: { value: "Atlas Care" } });
    fireEvent.change(screen.getByLabelText("Patient markets"), { target: { value: "UK" } });
    fireEvent.change(screen.getByLabelText("Destinations"), { target: { value: "Atlantis" } });
    fireEvent.change(screen.getByLabelText("Procedures"), { target: { value: "Dental implants, Hair transplant" } });
    fireEvent.click(screen.getByLabelText(/Referral and coordination|Both/));
    fireEvent.click(screen.getByRole("button", { name: "Create my business workspace" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith("t", { business_name: "Atlas Care", patient_markets: ["UK"], destinations: ["Atlantis"], procedures: ["Dental implants", "Hair transplant"], business_model: "both" }));
    expect(await screen.findByText(/not a country Klaros recognises/)).toBeInTheDocument();
  });

  it("also sends the optional channels, currencies and goals when they are answered (they fill Blueprint sections that would be Missing)", async () => {
    getSetup.mockResolvedValue({ configured: false });
    submit.mockResolvedValue({ configured: true, profile: { business_name: "Atlas Care", patient_markets: ["GB"], destinations: ["IN"], business_model: "both", business_model_label: "x", procedures: ["Dental implants"] }, checklist: [], drafts: {}, open_items: 0, ready_to_launch: false, note: "" });
    render(<Page />);
    fireEvent.change(await screen.findByLabelText("Business name"), { target: { value: "Atlas Care" } });
    fireEvent.change(screen.getByLabelText("Patient markets"), { target: { value: "UK" } });
    fireEvent.change(screen.getByLabelText("Destinations"), { target: { value: "India" } });
    fireEvent.change(screen.getByLabelText("Procedures"), { target: { value: "Dental implants" } });
    fireEvent.change(screen.getByLabelText("Channels"), { target: { value: "Website enquiries\nWhatsApp" } });
    fireEvent.change(screen.getByLabelText("Currencies"), { target: { value: "AED, GBP" } });
    fireEvent.change(screen.getByLabelText("Goals"), { target: { value: " 50 patients in 6 months " } });
    fireEvent.click(screen.getByLabelText(/Referral and coordination|Both/));
    fireEvent.click(screen.getByRole("button", { name: "Create my business workspace" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith("t", expect.objectContaining({ channels: ["Website enquiries", "WhatsApp"], currencies: ["AED", "GBP"], goals: "50 patients in 6 months" })));
  });

  it("shows the launch checklist honestly: what is done, what needs real information, and what only the owner can confirm", async () => {
    getSetup.mockResolvedValue(configured);
    render(<Page />);
    expect(await screen.findByTestId("checklist-summary")).toHaveTextContent("3 items still open");
    expect(screen.getByText(/Needs your confirmation · agreements_signed why/)).toBeInTheDocument();
    expect(screen.getByText(/To do · providers why/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/medical-tourism/providers");
    expect(screen.getByLabelText("Confirm: agreements_signed label")).not.toBeChecked();
  });

  it("an owner confirmation is saved through the backend", async () => {
    getSetup.mockResolvedValue(configured);
    confirm.mockResolvedValue({ ...configured, checklist: [item("profile", "DONE"), item("providers", "TODO", "/medical-tourism/providers"), item("agreements_signed", "DONE")] });
    render(<Page />);
    fireEvent.click(await screen.findByLabelText("Confirm: agreements_signed label"));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("t", "agreements_signed", true));
    await waitFor(() => expect(screen.getByLabelText("Confirm: agreements_signed label")).toBeChecked());
  });

  it("labels every draft as a draft", async () => {
    getSetup.mockResolvedValue(configured);
    render(<Page />);
    expect(await screen.findByText("Patient intake questions")).toBeInTheDocument();
    expect(screen.getByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText(/draft for your approval/)).toBeInTheDocument();
  });
});
