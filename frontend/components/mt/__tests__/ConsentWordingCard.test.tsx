import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = { listConsentWordings: vi.fn(), approveConsentWording: vi.fn() };
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  listConsentWordings: (...a: unknown[]) => api.listConsentWordings(...a),
  approveConsentWording: (...a: unknown[]) => api.approveConsentWording(...a),
}));
import { ConsentWordingCard } from "@/components/mt/ConsentWordingCard";
import { ApiError } from "@/lib/api";

const DRAFT = "I agree that Verdant Care may keep the details I enter on this form, and may contact me about my enquiry.";
const list = (wordings: object[] = []) => ({ wordings, suggested_text: DRAFT, min_length: 40, max_length: 2000 });
const approved = (version: string, text: string, language = "en") => ({ version, text, language, approved_at: "2026-10-13T10:00:00Z" });

beforeEach(() => {
  api.listConsentWordings.mockReset();
  api.approveConsentWording.mockReset();
});

describe("ConsentWordingCard", () => {
  it("starts from a draft, says it is not approved, and will not approve until the owner has read it", async () => {
    api.listConsentWordings.mockResolvedValue(list());
    render(<ConsentWordingCard token="t" />);
    const box = (await screen.findByLabelText("Wording")) as HTMLTextAreaElement;
    expect(box.value).toBe(DRAFT);
    expect(screen.getByText(/draft to start from, not an approved wording/)).toBeInTheDocument();
    expect(screen.getByText(/enquiry form on your website stays closed/)).toBeInTheDocument();
    const approve = screen.getByRole("button", { name: "Approve this wording" });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(approve).toBeEnabled();
  });

  it("will not approve wording that is too short", async () => {
    api.listConsentWordings.mockResolvedValue(list());
    render(<ConsentWordingCard token="t" />);
    fireEvent.change(await screen.findByLabelText("Wording"), { target: { value: "too short" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Approve this wording" })).toBeDisabled();
    expect(screen.getByText(/at least 40/)).toBeInTheDocument();
  });

  it("approves exactly what is in the box, in the chosen language, then shows the approved version", async () => {
    api.listConsentWordings.mockResolvedValueOnce(list());
    api.approveConsentWording.mockResolvedValue(approved("WEB-v1", DRAFT));
    api.listConsentWordings.mockResolvedValue(list([approved("WEB-v1", DRAFT)]));
    const onChanged = vi.fn();
    render(<ConsentWordingCard token="t" onChanged={onChanged} />);
    await screen.findByLabelText("Wording");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Approve this wording" }));
    await waitFor(() => expect(api.approveConsentWording).toHaveBeenCalledWith("t", { text: DRAFT, language: "en" }));
    await screen.findByText(/WEB-v1 is approved/);
    expect(onChanged).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Write a new version" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Wording")).toBeNull();
  });

  it("shows the approved wording read-only, and a new version needs the text to actually change", async () => {
    api.listConsentWordings.mockResolvedValue(list([approved("WEB-v2", DRAFT + " Extra."), approved("WEB-v1", DRAFT)]));
    render(<ConsentWordingCard token="t" />);
    expect(await screen.findByText(DRAFT + " Extra.")).toBeInTheDocument();
    expect(screen.getByText(/Earlier versions stay on record: WEB-v1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Write a new version" }));
    expect(screen.getByText(/creates a new version. The old one is kept/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Approve this wording" })).toBeDisabled(); // unchanged
    fireEvent.change(screen.getByLabelText("Wording"), { target: { value: DRAFT + " Extra. And a little more." } });
    // editing the words clears "I have read this": the owner must read the new text before approving it
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Approve this wording" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Approve this wording" })).toBeEnabled();
  });

  it("says plainly when approval fails and that nothing was changed", async () => {
    api.listConsentWordings.mockResolvedValue(list());
    api.approveConsentWording.mockRejectedValue(new ApiError(422, "The wording can't contain markup or links to scripts. Use plain text."));
    render(<ConsentWordingCard token="t" />);
    await screen.findByLabelText("Wording");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Approve this wording" }));
    await screen.findByText("The wording can't contain markup or links to scripts. Use plain text.");
  });

  it("shows a load failure as a failure", async () => {
    api.listConsentWordings.mockRejectedValue(new Error("down"));
    render(<ConsentWordingCard token="t" />);
    await screen.findByText(/couldn't load your consent wording/);
  });
});
