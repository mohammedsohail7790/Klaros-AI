import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
const start = vi.fn();
const connect = vi.fn();
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("connector=meta_ads&result=error&detail=access_denied") }));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  listConnectors: (...a: unknown[]) => list(...a),
  startConnector: (...a: unknown[]) => start(...a),
  connectIntegration: (...a: unknown[]) => connect(...a),
  completeConnector: vi.fn(),
  disconnectIntegration: vi.fn(),
  verifyIntegrationConnection: vi.fn(),
}));
import { ConnectorsCard } from "@/components/business/ConnectorsCard";

const base = {
  category: "marketing", description: "d", built_in: false, platform_ready: true, missing_platform_settings: [], redirect_uri: null, start_path: null,
  owner_steps: ["Step one"], fields: [], status: "NOT_CONNECTED", last_error: null, external_account_id: null,
};

describe("ConnectorsCard", () => {
  beforeEach(() => {
    list.mockReset();
    start.mockReset();
    connect.mockReset();
  });

  it("names the missing server settings and disables Connect until they exist", async () => {
    list.mockResolvedValue([{ ...base, key: "google_ads", name: "Google Ads", mode: "oauth", platform_ready: false, missing_platform_settings: ["GOOGLE_ADS_DEVELOPER_TOKEN"] }]);
    render(<ConnectorsCard token="t" />);
    expect(await screen.findByText(/GOOGLE_ADS_DEVELOPER_TOKEN/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  });

  it("saves pasted credentials for a credentials connector", async () => {
    list.mockResolvedValue([{ ...base, key: "whatsapp", name: "WhatsApp", mode: "credentials", fields: [{ name: "access_token", label: "Access token", secret: true, help: "" }] }]);
    connect.mockResolvedValue({});
    render(<ConnectorsCard token="t" />);
    fireEvent.change(await screen.findByPlaceholderText("Access token"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(connect).toHaveBeenCalledWith("t", "whatsapp", { access_token: "abc" }));
  });

  it("shows a declined consent from the provider's redirect", async () => {
    list.mockResolvedValue([]);
    render(<ConnectorsCard token="t" />);
    expect(await screen.findByText(/did not complete: access_denied/)).toBeInTheDocument();
  });

  it("shows the built-in e-signature as covered with no button", async () => {
    list.mockResolvedValue([{ ...base, key: "esign", name: "E-signature", mode: "credentials", built_in: true, status: "CONNECTED" }]);
    render(<ConnectorsCard token="t" />);
    expect(await screen.findByText("Built in")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });
});
