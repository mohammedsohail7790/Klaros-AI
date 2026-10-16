import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const preview = vi.fn();
const apply = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getClinicCleanup: (...a: unknown[]) => preview(...a),
  applyClinicCleanup: (...a: unknown[]) => apply(...a),
}));
import { ClinicCleanup } from "@/components/mt/ClinicCleanup";

const clinic = (id: string, name: string) => ({ id, name, country: "IN", city: null, verified: false, has_terms: false, has_email: false, live_referrals: 0 });
const withDupes = { groups: [{ keep: clinic("a", "Apollo Hospitals"), redundant: [clinic("b", "Apollo Hospital")] }], junk: [clinic("c", "TEST Clinic (delete me)")], suggested_ids: ["b", "c"], active_clinics: 3 };
const clean = { groups: [], junk: [], suggested_ids: [], active_clinics: 1 };
const onDone = vi.fn();
beforeEach(() => { preview.mockReset(); apply.mockReset().mockResolvedValue({ suspended: 2 }); onDone.mockReset(); });

describe("Clinic clean-up", () => {
  it("shows nothing when the list is already clean", async () => {
    preview.mockResolvedValue(clean);
    const { container } = render(<ClinicCleanup token="t" onDone={onDone} />);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("lists what is kept and suspended, and only suspends after the owner confirms", async () => {
    preview.mockResolvedValueOnce(withDupes).mockResolvedValue(clean);
    render(<ClinicCleanup token="t" onDone={onDone} />);
    expect(await screen.findByText(/2 clinic entries look like duplicates or junk/)).toBeInTheDocument();
    expect(screen.getByText("Apollo Hospitals")).toBeInTheDocument();
    expect(apply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Suspend 2 entries" }));
    await waitFor(() => expect(apply).toHaveBeenCalledWith("t", ["b", "c"]));
    expect(await screen.findByText(/2 duplicate or junk entries suspended/)).toBeInTheDocument();
    expect(onDone).toHaveBeenCalled();
  });
});
