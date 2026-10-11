import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const send = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  sendContactMessage: (...a: unknown[]) => send(...a),
}));
vi.mock("@/components/MarketingHeader", () => ({ default: () => <header /> }));
vi.mock("@/components/MarketingFooter", () => ({ default: () => <footer /> }));
vi.mock("@/components/GradientBackdrop", () => ({ default: () => null }));
import ContactPage from "@/app/contact/page";
import PrivacyPage from "@/app/privacy/page";
import TermsPage from "@/app/terms/page";

beforeEach(() => send.mockReset());

describe("Contact", () => {
  it("sends the message only when it is complete, then confirms", async () => {
    send.mockResolvedValue({ message: "ok" });
    render(<ContactPage />);
    const button = screen.getByRole("button", { name: "Send message" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Pat" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "pat@example.com" } });
    fireEvent.change(screen.getByLabelText("How can we help?"), { target: { value: "short" } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("How can we help?"), { target: { value: "I would like to see a demo." } });
    fireEvent.click(button);
    await waitFor(() => expect(send).toHaveBeenCalledWith({ name: "Pat", email: "pat@example.com", message: "I would like to see a demo.", website: undefined }));
    expect(await screen.findByRole("status")).toHaveTextContent("on its way");
  });

  it("keeps the hidden trap field out of the accessibility tree and unfilled by people", () => {
    render(<ContactPage />);
    const trap = document.querySelector('input[tabindex="-1"]') as HTMLInputElement;
    expect(trap.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(trap.value).toBe("");
  });
});

describe("Legal pages", () => {
  it("the privacy policy states the consent rule for clinics and the processors used", () => {
    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Privacy Policy" })).toBeInTheDocument();
    expect(screen.getByText(/never sends a person's identifiable details to a clinic/i)).toBeInTheDocument();
    expect(screen.getByText(/OpenAI/)).toBeInTheDocument();
  });

  it("the terms say Klaros is not a healthcare provider and is not for emergencies", () => {
    render(<TermsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Terms of Service" })).toBeInTheDocument();
    expect(screen.getByText(/not a healthcare provider/i)).toBeInTheDocument();
  });
});
