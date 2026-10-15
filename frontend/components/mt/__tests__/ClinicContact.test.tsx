import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const save = vi.fn();
vi.mock("@/components/ui/Modal", () => ({ Modal: ({ title, children }: { title: string; children: React.ReactNode }) => <div role="dialog" aria-label={title}>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  updateClinicContact: (...a: unknown[]) => save(...a),
}));
import { ClinicContact } from "@/components/mt/ClinicContact";
import { ApiError } from "@/lib/api";

const provider = { id: "P1", name: "Artemis Hospital", contact_email: null, contact_phone: "+91 1", city: "Gurugram" } as never;
const onSaved = vi.fn();
beforeEach(() => { save.mockReset().mockResolvedValue({}); onSaved.mockReset(); });

describe("Clinic contact", () => {
  it("saves the trimmed email, phone and city", async () => {
    render(<ClinicContact token="t" provider={provider} onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText("Clinic email"), { target: { value: "  internationaldesk@artemishospitals.com " } });
    fireEvent.click(screen.getByRole("button", { name: "Save contact" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("t", "P1", { contact_email: "internationaldesk@artemishospitals.com", contact_phone: "+91 1", city: "Gurugram" }));
    expect(onSaved).toHaveBeenCalled();
  });

  it("shows what the server refused and stays open", async () => {
    save.mockRejectedValue(new ApiError(422, "Enter a single, valid email address."));
    render(<ClinicContact token="t" provider={provider} onClose={vi.fn()} onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText("Clinic email"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Save contact" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("valid email");
    expect(onSaved).not.toHaveBeenCalled();
  });
});
