import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const rename = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status; } },
  getBusinessName: (...a: unknown[]) => get(...a),
  renameBusiness: (...a: unknown[]) => rename(...a),
}));
import { BusinessNameCard } from "@/components/BusinessNameCard";
import { ApiError } from "@/lib/api";

beforeEach(() => { get.mockReset().mockResolvedValue({ name: "Aesthetic Tourism" }); rename.mockReset(); });

describe("BusinessNameCard", () => {
  it("is shown only to the owner", async () => {
    const { container } = render(<BusinessNameCard token="t" role="MANAGER" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
    expect(get).not.toHaveBeenCalled();
  });

  it("renames the business and says so; the button stays off until the name really changes", async () => {
    rename.mockResolvedValue({ name: "MedLuxe Travel", organization: true, medical_tourism_profile: true, blueprint: true, content_topics: true });
    render(<BusinessNameCard token="t" role="OWNER" />);
    const input = await screen.findByRole("textbox", { name: "Business name" });
    expect(screen.getByRole("button", { name: "Save name" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "MedLuxe Travel" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() => expect(rename).toHaveBeenCalledWith("t", "MedLuxe Travel"));
    expect(await screen.findByText("The business name was changed.")).toBeInTheDocument();
  });

  it("shows what the server refused", async () => {
    rename.mockRejectedValue(new ApiError(422, "Use a business name of 2 to 120 characters, with no angle brackets."));
    render(<BusinessNameCard token="t" role="OWNER" />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Business name" }), { target: { value: "Valid Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(await screen.findByText(/no angle brackets/)).toBeInTheDocument();
  });
});
