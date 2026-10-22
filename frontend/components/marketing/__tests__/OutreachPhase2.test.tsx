import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getBlocks = vi.fn();
const saveBlocks = vi.fn();
const approveBlocks = vi.fn();
const listMessages = vi.fn();
const getMessage = vi.fn();
const review = vi.fn();
const previewSeq = vi.fn();
const progress = vi.fn();
const getCampaign = vi.fn();
const start = vi.fn();
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {
    status: number;
    details?: Record<string, unknown>;
    constructor(status: number, message: string, _code?: string, details?: Record<string, unknown>) {
      super(message);
      this.status = status;
      this.details = details;
    }
  },
  getOutreachBlocks: (...a: unknown[]) => getBlocks(...a),
  saveOutreachBlocks: (...a: unknown[]) => saveBlocks(...a),
  approveOutreachBlocks: (...a: unknown[]) => approveBlocks(...a),
  listOutreachMessages: (...a: unknown[]) => listMessages(...a),
  getOutreachMessage: (...a: unknown[]) => getMessage(...a),
  reviewOutreachMessage: (...a: unknown[]) => review(...a),
  previewOutreachSequence: (...a: unknown[]) => previewSeq(...a),
  getOutreachShadowProgress: (...a: unknown[]) => progress(...a),
  getOutreachCampaign: (...a: unknown[]) => getCampaign(...a),
  startOutreachCampaign: (...a: unknown[]) => start(...a),
  findOutreachContacts: vi.fn(),
  setOutreachContact: vi.fn(),
  pauseOutreachCampaign: vi.fn(),
  stopOutreachCampaign: vi.fn(),
}));
import { ApiError } from "@/lib/api";
import { OutreachWording } from "@/components/marketing/OutreachWording";
import { ShadowReview } from "@/components/marketing/ShadowReview";
import { ClinicOutreachPanel } from "@/components/marketing/ClinicOutreachPanel";

const blocks = (status = "DRAFT") => ({
  status,
  version: 1,
  approved_by: status === "APPROVED" ? "u1" : null,
  approved_at: status === "APPROVED" ? "2026-10-22T09:00:00Z" : null,
  blocks: { subject_intro: "Hello from {business_name}", intro: "Dear {clinic_name} team, {opt_out_line}", opt_out_line: "Reply stop to opt out." },
});
const msg = (id: string, over: Record<string, unknown> = {}) => ({
  id, provider_id: "p" + id, clinic: "Alpha Clinic", kind: "intro", status: "QUEUED_SHADOW", subject: "Hello", scheduled_for: "2026-11-02T07:00:00Z",
  review_mark: null, review_reason: null, blocked_reason: null, ...over,
});
const prog = (over: Record<string, unknown> = {}) => ({
  days_in_shadow: 2, shadow_messages: 2, reviewed: 0, flagged: 0, flagged_share: 0, blocked: 0, engine_last_run_at: null, blocks_status: "APPROVED",
  needs_you: [], next_sends: [], try_a_reply_tests: 0, try_a_reply_available: false, ...over,
});

describe("OutreachWording", () => {
  beforeEach(() => {
    getBlocks.mockReset().mockResolvedValue(blocks());
    saveBlocks.mockReset();
    approveBlocks.mockReset().mockResolvedValue(blocks("APPROVED"));
  });

  it("shows drafts and the placeholder help", async () => {
    render(<OutreachWording token="t" />);
    expect(await screen.findByText(/Drafts written by the assistant. Nothing is used until you approve./)).toBeTruthy();
    expect(screen.getByText(/\{clinic_name\}, \{business_name\}/)).toBeTruthy();
    expect(screen.getByText("Draft")).toBeTruthy();
    expect(screen.getByText(/Preview: Dear Example Clinic team/)).toBeTruthy();
  });

  it("shows per-block errors from the API when a save is refused", async () => {
    saveBlocks.mockRejectedValue(new ApiError(422, "bad", undefined, { intro: ["contains a digit"] }));
    render(<OutreachWording token="t" />);
    fireEvent.change(await screen.findByLabelText("Introduction: message"), { target: { value: "We pay 10 percent" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("contains a digit")).toBeTruthy();
  });

  it("needs confirmation to approve and then shows the approved banner", async () => {
    render(<OutreachWording token="t" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve wording" }));
    expect(approveBlocks).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, approve" }));
    await waitFor(() => expect(approveBlocks).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/^Approved on/)).toBeTruthy();
  });
});

describe("ShadowReview", () => {
  beforeEach(() => {
    progress.mockReset().mockResolvedValue(prog({ needs_you: [{ provider_id: "p9", clinic: "Gamma Clinic", reason: "clinic country has no known time zone" }] }));
    listMessages.mockReset().mockResolvedValue({ items: [msg("1"), msg("2", { clinic: "Beta Clinic" })], total: 2 });
    getMessage.mockReset().mockResolvedValue({ ...msg("1"), body: "Dear Alpha Clinic team" });
    review.mockReset().mockResolvedValue({ status: "ok" });
    previewSeq.mockReset().mockResolvedValue({ messages: [
      { kind: "intro", subject: "S1", body: "B1", problems: [] },
      { kind: "followup_1", subject: "S2", body: "B2", problems: [] },
      { kind: "followup_2", subject: "S3", body: "B3", problems: [] },
    ] });
  });

  it("lists messages, filters them and shows what needs you", async () => {
    render(<ShadowReview token="t" campaignId="c1" />);
    expect(await screen.findByText(/Alpha Clinic: Introduction/)).toBeTruthy();
    expect(screen.getByText(/Gamma Clinic: clinic country has no known time zone/)).toBeTruthy();
    expect(screen.getByText("Nothing is sent in this release.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Flagged" }));
    await waitFor(() => expect(listMessages).toHaveBeenLastCalledWith("t", "c1", { review: "FLAGGED", limit: 100 }));
    fireEvent.click(screen.getByRole("button", { name: "Blocked" }));
    await waitFor(() => expect(listMessages).toHaveBeenLastCalledWith("t", "c1", { status: "BLOCKED", limit: 100 }));
  });

  it("marks a message good and flags another with a reason", async () => {
    render(<ShadowReview token="t" campaignId="c1" />);
    fireEvent.click(await screen.findByText(/Alpha Clinic: Introduction/));
    expect(await screen.findByText("Dear Alpha Clinic team")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Good" }));
    await waitFor(() => expect(review).toHaveBeenCalledWith("t", "c1", "1", "GOOD", undefined));
    fireEvent.click(screen.getByRole("button", { name: "Flag" }));
    fireEvent.change(screen.getByLabelText("Why are you flagging it?"), { target: { value: "too pushy" } });
    fireEvent.click(screen.getByRole("button", { name: "Save flag" }));
    await waitFor(() => expect(review).toHaveBeenCalledWith("t", "c1", "1", "FLAGGED", "too pushy"));
  });

  it("blocks a flag without a reason in the form", async () => {
    render(<ShadowReview token="t" campaignId="c1" />);
    fireEvent.click(await screen.findByText(/Alpha Clinic: Introduction/));
    fireEvent.click(await screen.findByRole("button", { name: "Flag" }));
    fireEvent.click(screen.getByRole("button", { name: "Save flag" }));
    expect(await screen.findByText(/Say what is wrong/)).toBeTruthy();
    expect(review).not.toHaveBeenCalled();
  });

  it("previews the full sequence of three messages", async () => {
    render(<ShadowReview token="t" campaignId="c1" />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Preview full sequence" }))[0]);
    expect(await screen.findByText("Introduction: S1")).toBeTruthy();
    expect(screen.getByText("First follow-up: S2")).toBeTruthy();
    expect(screen.getByText("Last follow-up: S3")).toBeTruthy();
    expect(previewSeq).toHaveBeenCalledWith("t", "c1", "p1");
  });
});

describe("ClinicOutreachPanel wording gate", () => {
  beforeEach(() => {
    getCampaign.mockReset().mockResolvedValue({ campaign: { id: "c1", name: "Turkey", status: "DRAFT" }, config: null, mode: "SHADOW", funnel: { QUEUED: 0 }, clinics: [] });
    progress.mockReset().mockResolvedValue(prog());
    listMessages.mockReset().mockResolvedValue({ items: [], total: 0 });
  });

  it("shows the approve-wording notice and disables Start until approved", async () => {
    getBlocks.mockReset().mockResolvedValue(blocks("DRAFT"));
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    expect(await screen.findByText(/Approve the wording to begin/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Start in shadow mode" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("link", { name: "Open the wording page" }).getAttribute("href")).toBe("/marketing/outreach-wording");
  });

  it("enables Start once approved and offers the Shadow review tab", async () => {
    getBlocks.mockReset().mockResolvedValue(blocks("APPROVED"));
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    await waitFor(() => expect((screen.getByRole("button", { name: "Start in shadow mode" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText(/Approve the wording to begin/)).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Shadow review" }));
    expect(await screen.findByText("Days in shadow")).toBeTruthy();
  });
});
