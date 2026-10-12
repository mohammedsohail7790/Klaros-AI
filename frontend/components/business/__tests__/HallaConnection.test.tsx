import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  connectHalla: vi.fn(),
  disconnectHalla: vi.fn(),
  checkHallaHealth: vi.fn(),
  configureHalla: vi.fn(),
  listHallaAgents: vi.fn(),
  provisionHalla: vi.fn(),
};
// Rejections are produced outside the spies (a spied rejected promise is re-raised by the mock wrapper as an unhandled rejection).
let failNext: Error | null = null;
const maybeFail = <T,>(fn: (...a: unknown[]) => T) => (...a: unknown[]) => (failNext ? new Promise<never>((_, rej) => setTimeout(() => rej(failNext), 0)) : fn(...a));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } },
  connectHalla: (...a: unknown[]) => maybeFail(api.connectHalla)(...a),
  disconnectHalla: (...a: unknown[]) => maybeFail(api.disconnectHalla)(...a),
  checkHallaHealth: (...a: unknown[]) => maybeFail(api.checkHallaHealth)(...a),
  configureHalla: (...a: unknown[]) => maybeFail(api.configureHalla)(...a),
  listHallaAgents: (...a: unknown[]) => maybeFail(api.listHallaAgents)(...a),
  provisionHalla: (...a: unknown[]) => maybeFail(api.provisionHalla)(...a),
}));
import { HallaConnectionPanel } from "@/components/business/HallaConnection";
import type { WorkforceSetup } from "@/lib/api";

const setup = (status = "NOT_CONNECTED", halla: Partial<NonNullable<WorkforceSetup["halla"]>> = {}): WorkforceSetup =>
  ({
    status: { provider: "halla", status, adapter_implemented: true, mode: "live", message: "m", agent_id: null },
    channels: [], members: [], steps: [], context: {} as WorkforceSetup["context"], dev_simulator: false,
    halla: { halla_tenant_id: null, has_credential: false, has_signing_secret: false, last_verified_at: null, last_error: null, webhook_url: "https://klaros.test/api/v1/webhooks/halla/t1", ...halla },
  }) as WorkforceSetup;

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  failNext = null;
  api.listHallaAgents.mockResolvedValue({ agents: [] });
});

const fill = () => {
  fireEvent.change(screen.getByLabelText("Halla tenant ID"), { target: { value: "halla-tenant-1" } });
  fireEvent.change(screen.getByLabelText("Halla API key"), { target: { value: "super-secret-api-key" } });
  fireEvent.change(screen.getByLabelText("Webhook signing secret"), { target: { value: "super-secret-signing" } });
};

describe("HallaConnectionPanel", () => {
  it("renders nothing unless the real Halla adapter is enabled", () => {
    const s = setup();
    delete s.halla;
    const { container } = render(<HallaConnectionPanel token="t" setup={s} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("collects the credential in password fields and sends it once, then clears it whatever the outcome", async () => {
    api.connectHalla.mockResolvedValue({ status: "CONNECTED", mode: "live", message: "Connected to Halla.", adapter_implemented: true });
    const onChanged = vi.fn();
    render(<HallaConnectionPanel token="t" setup={setup()} onChanged={onChanged} />);
    expect(screen.getByLabelText("Halla API key")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Webhook signing secret")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Connect Halla" })).toBeDisabled(); // nothing to send yet
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Connect Halla" }));
    await waitFor(() => expect(api.connectHalla).toHaveBeenCalledWith("t", { halla_tenant_id: "halla-tenant-1", api_key: "super-secret-api-key", signing_secret: "super-secret-signing" }));
    expect(await screen.findByText(/Halla answered a real health check/)).toBeInTheDocument();
    expect((screen.getByLabelText("Halla API key") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Webhook signing secret") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("super-secret");
    expect(onChanged).toHaveBeenCalled();
  });

  it("never says connected when the backend says it is not — it shows the backend's reason, and still clears the secrets", async () => {
    api.connectHalla.mockResolvedValue({ status: "ERROR", mode: "live", message: "Halla rejected the credential", adapter_implemented: true });
    render(<HallaConnectionPanel token="t" setup={setup()} onChanged={vi.fn()} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Connect Halla" }));
    expect(await screen.findByText("Halla rejected the credential")).toBeInTheDocument();
    expect(screen.queryByText(/Connected/)).not.toBeInTheDocument();
    expect((screen.getByLabelText("Halla API key") as HTMLInputElement).value).toBe("");
  });

  it("explains a failed save and keeps the secrets out of the page", async () => {
    failNext = new Error("down");
    render(<HallaConnectionPanel token="t" setup={setup()} onChanged={vi.fn()} />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Connect Halla" }));
    expect(await screen.findByText(/couldn't save the connection/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("super-secret");
  });

  it("shows the webhook address to register, with the stored facts but never a credential", () => {
    render(<HallaConnectionPanel token="t" setup={setup("ERROR", { has_credential: true, has_signing_secret: true, halla_tenant_id: "halla-tenant-1", last_error: "Halla could not be reached" })} onChanged={vi.fn()} />);
    expect(screen.getByText("https://klaros.test/api/v1/webhooks/halla/t1")).toBeInTheDocument();
    expect(screen.getByText("halla-tenant-1")).toBeInTheDocument();
    expect(screen.getByText("Stored")).toBeInTheDocument();
    expect(screen.getByText("Halla could not be reached")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect Halla" })).toBeInTheDocument(); // an errored connection can be redone
  });

  it("when connected: no connect form, a real health check, agents from Halla, and send-context with real counts", async () => {
    api.listHallaAgents.mockResolvedValue({ agents: [{ id: "a1", name: "Receptionist", role: "inbound", status: null, available: true }, { id: "a2", name: "Follow-up", role: null, status: "paused", available: null }] });
    api.checkHallaHealth.mockResolvedValue({ status: "CONNECTED", mode: "live", message: "Connected to Halla.", adapter_implemented: true });
    api.configureHalla.mockResolvedValue({ sent: { servicesOffered: 3, serviceAreas: 2, qualificationQuestions: 5, escalationTriggers: 2, businessName: true } });
    render(<HallaConnectionPanel token="t" setup={setup("CONNECTED", { has_credential: true, has_signing_secret: true, halla_tenant_id: "ht", last_verified_at: new Date().toISOString() })} onChanged={vi.fn()} />);
    expect(screen.queryByLabelText("Halla API key")).not.toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Halla agents" });
    expect(within(list).getByText("Receptionist")).toBeInTheDocument();
    expect(list).toHaveTextContent("Available");
    expect(list).toHaveTextContent("paused");
    fireEvent.click(screen.getByRole("button", { name: /Check connection/ }));
    expect(await screen.findByText("Halla answered a real health check.")).toBeInTheDocument();
    expect(api.checkHallaHealth).toHaveBeenCalledWith("t");
    fireEvent.click(screen.getByRole("button", { name: /Send business context to Halla/ }));
    expect(await screen.findByText("Sent to Halla: 3 services, 2 markets, 5 qualification questions, 2 escalation rules.")).toBeInTheDocument();
  });

  it("can only send business context while connected", () => {
    render(<HallaConnectionPanel token="t" setup={setup("ERROR", { has_credential: true })} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Send business context to Halla/ })).toBeDisabled();
  });

  it("shows empty and failed agent lists honestly", async () => {
    const { unmount } = render(<HallaConnectionPanel token="t" setup={setup("CONNECTED", { has_credential: true })} onChanged={vi.fn()} />);
    expect(await screen.findByText(/Halla reports no agents yet/)).toBeInTheDocument();
    unmount();
    failNext = new Error("x");
    render(<HallaConnectionPanel token="t" setup={setup("CONNECTED", { has_credential: true })} onChanged={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load Halla's agents/);
  });

  it("asks before disconnecting, then removes the credential", async () => {
    api.disconnectHalla.mockResolvedValue({ status: "NOT_CONNECTED", mode: "live", message: "m", adapter_implemented: true });
    const onChanged = vi.fn();
    render(<HallaConnectionPanel token="t" setup={setup("CONNECTED", { has_credential: true })} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: /Disconnect/ }));
    expect(api.disconnectHalla).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(api.disconnectHalla).toHaveBeenCalledWith("t"));
    expect(await screen.findByText(/stored credential was removed/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });
});

describe("one-button receptionist setup", () => {
  const withProvisioning = (status = "NOT_CONNECTED", halla: Partial<NonNullable<WorkforceSetup["halla"]>> = {}) => setup(status, { provisioning_available: true, ...halla });

  it("offers to create the receptionist, with no credential asked for, and keeps pasting behind an \"I already have\" fold", () => {
    render(<HallaConnectionPanel token="t" setup={withProvisioning()} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Create my AI receptionist/ })).toBeEnabled();
    expect(screen.getByText(/You don.t need a Halla account or any key/)).toBeInTheDocument();
    expect(screen.getByText("I already have a Halla account")).toBeInTheDocument();
    expect(screen.getByText(/takes no calls/)).toBeInTheDocument(); // honest about what it is not doing yet
  });

  it("creates it with one request that carries no credential, then refreshes", async () => {
    api.provisionHalla.mockResolvedValue({ status: "CONNECTED", mode: "live", message: "ok", adapter_implemented: true, configured: true });
    const onChanged = vi.fn();
    render(<HallaConnectionPanel token="t" setup={withProvisioning()} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: /Create my AI receptionist/ }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.provisionHalla).toHaveBeenCalledWith("t");
    expect(screen.getByText(/created and connected/)).toBeInTheDocument();
    expect(screen.getByText(/not taking patient calls yet/)).toBeInTheDocument();
  });

  it("says plainly when the business details did not reach the receptionist", async () => {
    api.provisionHalla.mockResolvedValue({ status: "CONNECTED", mode: "live", message: "ok", adapter_implemented: true, configured: false });
    render(<HallaConnectionPanel token="t" setup={withProvisioning()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Create my AI receptionist/ }));
    await waitFor(() => expect(screen.getByText(/did not reach it yet/)).toBeInTheDocument());
  });

  it("shows a failure as a failure and never claims it is connected", async () => {
    failNext = new (class extends Error {})("Halla rejected the request");
    render(<HallaConnectionPanel token="t" setup={withProvisioning()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Create my AI receptionist/ }));
    await waitFor(() => expect(screen.getByText(/couldn.t create the receptionist/)).toBeInTheDocument());
    expect(screen.queryByText(/created and connected/)).toBeNull();
  });

  it("is not offered when this deployment cannot do it, or once a credential is stored", () => {
    const { unmount } = render(<HallaConnectionPanel token="t" setup={setup()} onChanged={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Create my AI receptionist/ })).toBeNull();
    expect(screen.getByLabelText("Halla tenant ID")).toBeInTheDocument(); // the existing form is unchanged
    unmount();
    render(<HallaConnectionPanel token="t" setup={withProvisioning("CONNECTED", { has_credential: true, has_signing_secret: true, halla_tenant_id: "h1" })} onChanged={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Create my AI receptionist/ })).toBeNull();
  });
});
