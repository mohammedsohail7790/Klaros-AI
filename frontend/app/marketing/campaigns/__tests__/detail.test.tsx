import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getOutreach = vi.fn();
const getCampaign = vi.fn();
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "c1" }) }));
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: null, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/ui/Toast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("@/components/marketing/ClinicOutreachPanel", () => ({ ClinicOutreachPanel: () => <div>outreach panel</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getOutreachCampaign: (...a: unknown[]) => getOutreach(...a),
  getCampaign: (...a: unknown[]) => getCampaign(...a),
  getCampaignBudgetStatus: vi.fn().mockResolvedValue({ budget: null }),
  recordCampaignSpend: vi.fn(),
  setCampaignStatus: vi.fn(),
}));
import { ApiError } from "@/lib/api";
import CampaignDetailPage from "@/app/marketing/campaigns/[id]/page";

const ordinary = {
  id: "c1", name: "Ordinary campaign", channel: "GOOGLE_ADS", status: "DRAFT",
  performance: { spend: "0", leads: 0, qualified_leads: 0, booked: 0, jobs_created: 0, jobs_closed: 0, revenue: "0", collected_revenue: "0", cac: null, roas: null },
};

describe("CampaignDetailPage outreach guard", () => {
  beforeEach(() => {
    getOutreach.mockReset();
    getCampaign.mockReset().mockResolvedValue(ordinary);
  });

  it("falls back to the ordinary page, with its controls, on a 404", async () => {
    getOutreach.mockRejectedValue(new ApiError(404, "Not found"));
    render(<CampaignDetailPage />);
    expect(await screen.findByRole("button", { name: "Activate" })).toBeInTheDocument();
    expect(screen.getByText("Ordinary campaign")).toBeInTheDocument();
  });

  it("shows an error with Retry and no ordinary controls on a 500", async () => {
    getOutreach.mockRejectedValueOnce(new ApiError(500, "boom"));
    render(<CampaignDetailPage />);
    expect(await screen.findByText(/Couldn't load outreach details/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    getOutreach.mockResolvedValue({ campaign: { id: "c1", name: "Out", status: "DRAFT" } });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("outreach panel")).toBeInTheDocument();
  });

  it("explains a 403 and shows no ordinary controls", async () => {
    getOutreach.mockRejectedValue(new ApiError(403, "Forbidden"));
    render(<CampaignDetailPage />);
    expect(await screen.findByText(/don't have permission to view this campaign's outreach details/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("renders the panel when the campaign is an outreach campaign", async () => {
    getOutreach.mockResolvedValue({ campaign: { id: "c1", name: "Turkey clinics", status: "DRAFT" } });
    render(<CampaignDetailPage />);
    expect(await screen.findByText("outreach panel")).toBeInTheDocument();
    expect(screen.getByText("Turkey clinics")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("shows no activate or pause control while the outreach check is pending", async () => {
    getOutreach.mockReturnValue(new Promise(() => {}));
    render(<CampaignDetailPage />);
    expect(await screen.findByText("Ordinary campaign")).toBeInTheDocument();
    await waitFor(() => expect(getOutreach).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  });
});
