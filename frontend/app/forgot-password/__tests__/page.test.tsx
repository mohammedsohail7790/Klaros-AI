import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const forgot = vi.fn();
const reset = vi.fn();
const push = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useSearchParams: () => new URLSearchParams(search) }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  forgotPassword: (...a: unknown[]) => forgot(...a),
  resetPassword: (...a: unknown[]) => reset(...a),
}));
import ForgotPage from "@/app/forgot-password/page";
import ResetPage from "@/app/reset-password/page";
import { ApiError } from "@/lib/api";

beforeEach(() => {
  forgot.mockReset();
  reset.mockReset();
  push.mockReset();
  search = "";
});

describe("Forgot password", () => {
  it("asks for the email and then says the same thing whether or not an account exists", async () => {
    forgot.mockResolvedValue({ message: "ok" });
    render(<ForgotPage />);
    const send = screen.getByRole("button", { name: "Send reset link" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(send);
    await waitFor(() => expect(forgot).toHaveBeenCalledWith("owner@example.com"));
    expect(await screen.findByRole("status")).toHaveTextContent("If an account exists");
  });

  it("shows a plain error when the request fails", async () => {
    forgot.mockRejectedValue(new ApiError(429, "slow down"));
    render(<ForgotPage />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });
});

describe("Reset password", () => {
  it("explains an incomplete link instead of showing a form that cannot work", () => {
    render(<ResetPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("incomplete");
    expect(screen.getByRole("link", { name: "ask for a new one" })).toHaveAttribute("href", "/forgot-password");
  });

  it("only allows a strong-enough matching password, then sends the token and goes to sign-in", async () => {
    search = "token=abcdefghijklmnopqrstuvwxyz";
    reset.mockResolvedValue(undefined);
    render(<ResetPage />);
    const submit = screen.getByRole("button", { name: "Set new password" });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "short" } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a-much-better-one" } });
    fireEvent.change(screen.getByLabelText("Repeat the new password"), { target: { value: "different-one-1" } });
    expect(screen.getByText("The two passwords don't match.")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Repeat the new password"), { target: { value: "a-much-better-one" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(reset).toHaveBeenCalledWith("abcdefghijklmnopqrstuvwxyz", "a-much-better-one"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/login?reset=1"));
  });

  it("offers a new link when the old one has expired", async () => {
    search = "token=abcdefghijklmnopqrstuvwxyz";
    reset.mockRejectedValue(new ApiError(400, "This reset link is invalid or has expired. Ask for a new one."));
    render(<ResetPage />);
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a-much-better-one" } });
    fireEvent.change(screen.getByLabelText("Repeat the new password"), { target: { value: "a-much-better-one" } });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
    expect(screen.getByRole("link", { name: "Ask for a new link" })).toHaveAttribute("href", "/forgot-password");
  });
});
