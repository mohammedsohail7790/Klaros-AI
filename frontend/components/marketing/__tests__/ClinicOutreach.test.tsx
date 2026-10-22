import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const eligible = vi.fn();
const create = vi.fn();
const getCampaign = vi.fn();
const findContacts = vi.fn();
const setContact = vi.fn();
const start = vi.fn();
const pause = vi.fn();
const stop = vi.fn();
const getBlocks = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  listEligibleOutreachClinics: (...a: unknown[]) => eligible(...a),
  createOutreachCampaign: (...a: unknown[]) => create(...a),
  getOutreachCampaign: (...a: unknown[]) => getCampaign(...a),
  findOutreachContacts: (...a: unknown[]) => findContacts(...a),
  setOutreachContact: (...a: unknown[]) => setContact(...a),
  startOutreachCampaign: (...a: unknown[]) => start(...a),
  pauseOutreachCampaign: (...a: unknown[]) => pause(...a),
  stopOutreachCampaign: (...a: unknown[]) => stop(...a),
  getOutreachBlocks: (...a: unknown[]) => getBlocks(...a),
  getOutreachShadowProgress: vi.fn(),
  listOutreachMessages: vi.fn(),
}));
import { ApiError } from "@/lib/api";
import { ClinicOutreachWizard } from "@/components/marketing/ClinicOutreachWizard";
import { ClinicOutreachPanel } from "@/components/marketing/ClinicOutreachPanel";

const clinic = (id: string, name: string) => ({ id, name, country: "TR", city: "Istanbul", has_email: true });
const funnelOf = (over: Record<string, number> = {}) => ({ QUEUED: 0, NO_EMAIL: 0, READY: 0, INTRO_SENT: 0, ...over });
const row = (id: string, name: string, over: Record<string, unknown> = {}) => ({ provider_id: id, name, country: "TR", city: "Istanbul", stage: "QUEUED", contact_email: null, contact_source: null, contact_checked_at: null, ...over });
const detail = (over: Record<string, unknown> = {}) => ({
  campaign: { id: "c1", name: "Turkey clinics", status: "DRAFT" },
  config: null,
  mode: "SHADOW",
  funnel: funnelOf({ QUEUED: 2, READY: 1 }),
  clinics: [row("p1", "Alpha Clinic"), row("p2", "Beta Clinic", { stage: "READY", contact_email: "hello@beta.com", contact_source: "website" })],
  ...over,
});

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
const next = () => fireEvent.click(screen.getByRole("button", { name: "Next" }));

function fillBasics() {
  type("Campaign name", "Turkey clinics");
  type(/Countries/, "tr");
}
function fillTermSheet(min = "10", max = "15") {
  type("Minimum", min);
  type("Maximum", max);
  type("Qualifying outcome", "Patient completes treatment");
}
function fillSender() {
  type("Sender name", "Sam");
  type("Signature", "Sam at Medluxe");
  type("Company name", "Medluxe Travel");
  type("Postal address", "1 High Street, London");
}

describe("ClinicOutreachWizard", () => {
  beforeEach(() => {
    eligible.mockReset().mockResolvedValue({ clinics: [clinic("p1", "Alpha Clinic"), clinic("p2", "Beta Clinic")] });
    create.mockReset().mockResolvedValue({ campaign_id: "c1" });
  });

  it("test_the_wizard_blocks_next_until_required_fields_are_valid", async () => {
    render(<ClinicOutreachWizard token="t" onCreated={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fillBasics();
    next();
    expect(await screen.findByText("Alpha Clinic")).toBeInTheDocument();
    next();
    fillTermSheet("20", "10");
    expect(screen.getByRole("alert")).toHaveTextContent("The minimum must not be above the maximum.");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fillTermSheet("10", "20");
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("test_the_wizard_submits_the_chosen_clinics_and_calls_onCreated", async () => {
    const onCreated = vi.fn();
    render(<ClinicOutreachWizard token="t" onCreated={onCreated} />);
    fillBasics();
    next();
    expect(await screen.findByText("Beta Clinic")).toBeInTheDocument();
    expect(eligible).toHaveBeenCalledWith("t", ["TR"]);
    fireEvent.click(screen.getByLabelText(/Beta Clinic/)); // untick one
    next();
    fillTermSheet();
    next();
    fillSender();
    next();
    next(); // limits keep their defaults
    expect(screen.getByText(/Nothing is sent in this release\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("c1"));
    const [token, body] = create.mock.calls[0];
    expect(token).toBe("t");
    expect(body.name).toBe("Turkey clinics");
    expect(body.provider_ids).toEqual(["p1"]);
    expect(body.config.countries).toEqual(["TR"]);
    expect(body.config.term_sheet).toMatchObject({ basis: "PERCENTAGE", min_value: "10", max_value: "15", payment_days: 30 });
    expect(body.config.limits).toMatchObject({ daily_cap: 20, ramp_start: 5, max_emails_per_clinic: 3, send_window_start: 9, send_window_end: 17, weekdays_only: true });
  });
});

describe("ClinicOutreachPanel", () => {
  beforeEach(() => {
    getCampaign.mockReset().mockResolvedValue(detail());
    getBlocks.mockReset().mockResolvedValue({ status: "APPROVED", version: 1, approved_by: "u", approved_at: "2026-10-22T00:00:00Z", blocks: {} });
    findContacts.mockReset().mockResolvedValue({ status: "ok", checked: 1, found: 1, no_email: 0, search_failed: 0, search_configured: true });
    setContact.mockReset().mockResolvedValue({ status: "ok" });
    start.mockReset().mockResolvedValue({ status: "ok" });
    pause.mockReset().mockResolvedValue({ status: "ok" });
    stop.mockReset().mockResolvedValue({ status: "ok" });
  });

  it("test_the_panel_shows_funnel_counts_and_the_nothing_is_sent_notice", async () => {
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    expect((await screen.findAllByText("Alpha Clinic")).length).toBeGreaterThan(0);
    expect(screen.getByText("Nothing is sent in this release.")).toBeInTheDocument();
    expect(screen.getByText("Shadow")).toBeInTheDocument();
    expect(screen.getByTestId("funnel-QUEUED")).toHaveTextContent("2");
    expect(screen.getByTestId("funnel-READY")).toHaveTextContent("1");
    expect(screen.getByText("hello@beta.com")).toBeInTheDocument();
  });

  it("test_start_is_shadow_only_and_go_live_is_disabled", async () => {
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    await screen.findAllByText("Alpha Clinic");
    const goLive = screen.getByRole("button", { name: /Go live/ });
    expect(goLive).toBeDisabled();
    expect(goLive).toHaveTextContent("Available after the shadow week (a later release)");
    expect(screen.getByRole("button", { name: "Start in shadow mode" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start in shadow mode" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("t", "c1"));
    expect(getCampaign).toHaveBeenCalledTimes(2);
  });

  it("test_find_contacts_updates_the_list", async () => {
    getCampaign
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(detail({ clinics: [row("p1", "Alpha Clinic", { stage: "READY", contact_email: "info@alpha.com", contact_source: "website" })] }));
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    await screen.findAllByText("Alpha Clinic");
    expect(screen.queryByText("info@alpha.com")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Find contacts" }));
    expect(await screen.findByText("info@alpha.com")).toBeInTheDocument();
    expect(findContacts).toHaveBeenCalledWith("t", "c1");
  });

  it("test_a_no_email_clinic_accepts_a_manual_email_and_shows_the_api_error_for_free_mail", async () => {
    getCampaign.mockResolvedValue(detail({ funnel: funnelOf({ NO_EMAIL: 1 }), clinics: [row("p1", "Alpha Clinic", { stage: "NO_EMAIL" })] }));
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    await screen.findByText("Needs you");
    setContact.mockRejectedValueOnce(new ApiError(409, "That is a free-mail address; use the clinic's own domain."));
    type("Email for Alpha Clinic", "alpha@gmail.com");
    fireEvent.click(screen.getByRole("button", { name: "Save email" }));
    expect(await screen.findByText("That is a free-mail address; use the clinic's own domain.")).toBeInTheDocument();
    expect(setContact).toHaveBeenCalledWith("t", "c1", "p1", "alpha@gmail.com");
    type("Email for Alpha Clinic", "info@alpha-clinic.com");
    fireEvent.click(screen.getByRole("button", { name: "Save email" }));
    await waitFor(() => expect(setContact).toHaveBeenLastCalledWith("t", "c1", "p1", "info@alpha-clinic.com"));
    await waitFor(() => expect(screen.queryByText(/free-mail/)).not.toBeInTheDocument());
  });

  it("test_a_queued_clinic_without_an_address_gets_the_manual_field_and_a_ready_one_can_be_edited", async () => {
    getCampaign.mockResolvedValue(detail());
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    await screen.findByText("Needs you");
    expect(screen.getByLabelText("Email for Alpha Clinic")).toBeInTheDocument();
    expect(screen.queryByLabelText("Email for Beta Clinic")).not.toBeInTheDocument();
    type("Change email for Beta Clinic", "new@beta.com");
    fireEvent.click(screen.getByRole("button", { name: "Update email" }));
    await waitFor(() => expect(setContact).toHaveBeenCalledWith("t", "c1", "p2", "new@beta.com"));
  });

  it("test_button_states_follow_the_campaign_status", async () => {
    getCampaign.mockResolvedValue(detail({ campaign: { id: "c1", name: "T", status: "PAUSED" } }));
    const { unmount } = render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    expect(await screen.findByRole("button", { name: "Resume (shadow)" })).toBeEnabled();
    unmount();
    getCampaign.mockResolvedValue(detail({ campaign: { id: "c1", name: "T", status: "ARCHIVED" } }));
    const second = render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    expect(await screen.findByRole("button", { name: "Start in shadow mode" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Find contacts" })).toBeDisabled();
    second.unmount();
    getCampaign.mockResolvedValue(detail({ campaign: { id: "c1", name: "T", status: "COMPLETED" } }));
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    expect(await screen.findByRole("button", { name: "Find contacts" })).toBeDisabled();
  });

  it("test_the_find_notice_mentions_no_search_and_failed_searches", async () => {
    findContacts.mockResolvedValue({ status: "not_configured", checked: 0, found: 0, no_email: 0, search_failed: 0, search_configured: false });
    render(<ClinicOutreachPanel token="t" campaignId="c1" />);
    await screen.findByText("Needs you");
    fireEvent.click(screen.getByRole("button", { name: "Find contacts" }));
    expect(await screen.findByText(/No web search is set up, so only addresses already on file were checked; add the rest by hand\./)).toBeInTheDocument();
    findContacts.mockResolvedValue({ status: "ok", checked: 3, found: 1, no_email: 0, search_failed: 2, search_configured: true });
    fireEvent.click(screen.getByRole("button", { name: "Find contacts" }));
    expect(await screen.findByText(/2 searches failed/)).toBeInTheDocument();
  });
});
