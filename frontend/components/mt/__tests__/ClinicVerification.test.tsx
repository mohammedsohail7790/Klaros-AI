import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
const add = vi.fn();
const verify = vi.fn();
const setVer = vi.fn();
vi.mock("@/components/ui/Modal", () => ({ Modal: ({ title, children }: { title: string; children: React.ReactNode }) => <div role="dialog" aria-label={title}>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  listProviderCredentials: (...a: unknown[]) => list(...a),
  addProviderCredential: (...a: unknown[]) => add(...a),
  verifyProviderCredential: (...a: unknown[]) => verify(...a),
  setProviderVerification: (...a: unknown[]) => setVer(...a),
}));
import { ClinicVerification } from "@/components/mt/ClinicVerification";

const provider = { id: "P1", name: "Anatolia Dental Clinic", verification_status: "UNVERIFIED", status: "ACTIVE" } as never;
const cred = (over: object = {}) => ({ id: "C1", provider_id: "P1", credential_type: "Operating licence", issuing_authority: "Ministry of Health", credential_number: "OL-1", expiry_date: "2999-01-01", status: "PENDING", verified_at: null, ...over });
const onSaved = vi.fn();

beforeEach(() => { [list, add, verify, setVer, onSaved].forEach((m) => m.mockReset()); list.mockResolvedValue({ credentials: [] }); });
const open = () => render(<ClinicVerification token="t" provider={provider} onClose={vi.fn()} onSaved={onSaved} />);

describe("Clinic verification and credentials", () => {
  it("lets an owner add a credential with its expiry date", async () => {
    add.mockResolvedValue({ credential: cred() });
    open();
    await screen.findByText(/None yet/);
    fireEvent.change(screen.getByLabelText("Issuing authority"), { target: { value: "Ministry of Health" } });
    fireEvent.change(screen.getByLabelText("Credential number"), { target: { value: "OL-1" } });
    fireEvent.change(screen.getByLabelText("Expiry date"), { target: { value: "2999-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Add credential" }));
    await waitFor(() => expect(add).toHaveBeenCalledWith("t", "P1", { credential_type: "Operating licence", issuing_authority: "Ministry of Health", credential_number: "OL-1", expiry_date: "2999-01-01" }));
  });

  it("an added credential is 'not checked yet' until a person records the check", async () => {
    list.mockResolvedValueOnce({ credentials: [cred()] }).mockResolvedValue({ credentials: [cred({ status: "VERIFIED", verified_at: "2026-10-12" })] });
    verify.mockResolvedValue({});
    open();
    expect(await screen.findByText("Not checked yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark clinic as verified" })).not.toBeInTheDocument(); // nothing checked: no shortcut
    fireEvent.click(screen.getByRole("button", { name: "I checked Operating licence" }));
    await waitFor(() => expect(verify).toHaveBeenCalledWith("t", "P1", "C1"));
    expect(await screen.findByText("Checked")).toBeInTheDocument();
  });

  it("offers 'Mark clinic as verified' only once a checked, unexpired credential exists, and saves it", async () => {
    list.mockResolvedValue({ credentials: [cred({ status: "VERIFIED" })] });
    setVer.mockResolvedValue({});
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Mark clinic as verified" }));
    await waitFor(() => expect(setVer).toHaveBeenCalledWith("t", "P1", "VERIFIED", undefined));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("an expired credential never counts, even if it was checked", async () => {
    list.mockResolvedValue({ credentials: [cred({ status: "VERIFIED", expiry_date: "2000-01-01" })] });
    open();
    expect(await screen.findByText("Expired")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark clinic as verified" })).not.toBeInTheDocument();
  });

  it("explains what is missing when Verified is chosen without a checked credential, and shows the backend's refusal", async () => {
    const { ApiError } = await import("@/lib/api");
    setVer.mockRejectedValue(new ApiError(409, "A provider is verified only with at least one verified, unexpired credential on record."));
    open();
    await screen.findByText(/None yet/);
    fireEvent.change(screen.getByLabelText("Verification status"), { target: { value: "VERIFIED" } });
    expect(screen.getByText(/Add a credential and record that you checked it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("verified only with at least one verified");
  });
});
