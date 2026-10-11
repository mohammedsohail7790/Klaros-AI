import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const change = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u", full_name: "Sam Owner", email: "sam@example.com", role: "OWNER" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  changePassword: (...a: unknown[]) => change(...a),
}));
import Page from "@/app/settings/account/page";
import { ApiError } from "@/lib/api";

beforeEach(() => {
  change.mockReset();
  localStorage.clear();
});

function fill(current = "old-password-1", next = "a-much-better-one", confirm = "a-much-better-one") {
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: next } });
  fireEvent.change(screen.getByLabelText("Repeat the new password"), { target: { value: confirm } });
}

describe("Account & security", () => {
  it("shows who is signed in", () => {
    render(<Page />);
    expect(screen.getByText("Sam Owner")).toBeInTheDocument();
    expect(screen.getByText("sam@example.com")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
  });

  it("changes the password and keeps this session alive with the fresh tokens", async () => {
    change.mockResolvedValue({ access_token: "NEW-A", refresh_token: "NEW-R", token_type: "bearer" });
    render(<Page />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(change).toHaveBeenCalledWith("t", "old-password-1", "a-much-better-one"));
    expect(await screen.findByText("Your password was changed.")).toBeInTheDocument();
    expect(localStorage.getItem("klaros_access_token")).toBe("NEW-A");
    expect((screen.getByLabelText("Current password") as HTMLInputElement).value).toBe("");
  });

  it("refuses a mismatched or weak new password before calling the server", () => {
    render(<Page />);
    fill("old-password-1", "a-much-better-one", "different-one-2");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    fill("old-password-1", "short", "short");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    expect(change).not.toHaveBeenCalled();
  });

  it("tells the person when the current password is wrong", async () => {
    change.mockRejectedValue(new ApiError(400, "Your current password is not correct."));
    render(<Page />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("current password is not correct");
  });
});
