import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const registerMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  register: (...a: unknown[]) => registerMock(...a),
}));
import RegisterPage from "@/app/register/page";
import { ApiError } from "@/lib/api";

beforeEach(() => {
  push.mockReset();
  registerMock.mockReset();
  localStorage.clear();
});

function fillAll(password = "a-good-password") {
  fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Atlas Care" } });
  fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Sam Owner" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "sam@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

describe("Create account", () => {
  it("does not enable Create until the password is acceptable", () => {
    render(<RegisterPage />);
    fillAll("short");
    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
    fillAll("a-good-password");
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
  });

  it("creates the account, keeps the session, and continues into the product", async () => {
    registerMock.mockResolvedValue({ organization_slug: "atlas-care", user: {}, tokens: { access_token: "A", refresh_token: "R", token_type: "bearer" } });
    render(<RegisterPage />);
    fillAll();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(registerMock).toHaveBeenCalledWith("Atlas Care", "Sam Owner", "sam@example.com", "a-good-password"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/business"));
    expect(localStorage.getItem("klaros_access_token")).toBe("A");
  });

  it("turns a developer-style validation error into a sentence a person can act on", async () => {
    registerMock.mockRejectedValue(new ApiError(422, "value is not a valid email address: The part after the @-sign is a special-use or reserved name"));
    render(<RegisterPage />);
    fillAll();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a valid email address");
    expect(push).not.toHaveBeenCalled();
  });

  it("links to the terms and privacy policy before the person agrees", () => {
    render(<RegisterPage />);
    expect(screen.getByRole("link", { name: "Terms of Service" })).toHaveAttribute("href", "/terms");
    expect(screen.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "/privacy");
  });
});
