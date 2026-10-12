import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
let search = "";
const loginMock = vi.fn();
const modeMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useSearchParams: () => new URLSearchParams(search) }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    code?: string;
    details?: Record<string, unknown>;
    constructor(status: number, message: string, code?: string, details?: Record<string, unknown>) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  },
  login: (...a: unknown[]) => loginMock(...a),
  getMedicalTourismMode: (...a: unknown[]) => modeMock(...a),
}));
import LoginPage from "@/app/login/page";
import { ApiError } from "@/lib/api";

beforeEach(() => {
  push.mockReset();
  loginMock.mockReset();
  modeMock.mockReset();
  modeMock.mockResolvedValue({ pilot_console: false });
  search = "";
  localStorage.clear();
  sessionStorage.clear();
});

function fill(email = "owner@example.com", password = "a-good-password") {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

describe("Sign in", () => {
  it("needs only an email and a password, and the workspace code is optional and tucked away", async () => {
    loginMock.mockResolvedValue({ access_token: "A", refresh_token: "R", token_type: "bearer" });
    render(<LoginPage />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByText("Have several workspaces?")).toBeInTheDocument();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(loginMock).toHaveBeenCalledWith(null, "owner@example.com", "a-good-password"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/business"));
    expect(localStorage.getItem("klaros_access_token")).toBe("A"); // persisted, not tab-scoped
  });

  it("goes back to the page the person was trying to open, but never to another site", async () => {
    loginMock.mockResolvedValue({ access_token: "A", refresh_token: "R", token_type: "bearer" });
    search = "next=%2Fmedical-tourism%2Ftasks";
    const { unmount } = render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/medical-tourism/tasks"));
    unmount();
    push.mockReset();
    search = "next=https%3A%2F%2Fevil.example";
    render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/business"));
  });

  it("shows a plain message for a wrong password", async () => {
    loginMock.mockRejectedValue(new ApiError(401, "Invalid email or password"));
    render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("don't match");
    expect(push).not.toHaveBeenCalled();
  });

  it("lets a person with several workspaces pick one, then signs in to that one", async () => {
    loginMock
      .mockRejectedValueOnce(new ApiError(409, "This email belongs to more than one workspace.", "multiple_workspaces", { workspaces: [{ name: "First Co", slug: "first-co" }, { name: "Second Co", slug: "second-co" }] }))
      .mockResolvedValueOnce({ access_token: "A", refresh_token: "R", token_type: "bearer" });
    render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    fireEvent.click(await screen.findByRole("button", { name: "Second Co" }));
    await waitFor(() => expect(loginMock).toHaveBeenLastCalledWith("second-co", "owner@example.com", "a-good-password"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/business"));
  });

  it("links to password recovery and confirms a completed reset", () => {
    search = "reset=1";
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.getByRole("status")).toHaveTextContent("Your password was changed");
  });

  it("can reveal the password being typed", () => {
    render(<LoginPage />);
    const password = screen.getByLabelText("Password") as HTMLInputElement;
    expect(password.type).toBe("password");
    fireEvent.click(screen.getByLabelText("Show password"));
    expect(password.type).toBe("text");
  });
});

describe("Sign in on a sleeping server", () => {
  it("says the server is waking up once the request has run for a while", async () => {
    vi.useFakeTimers();
    loginMock.mockReturnValue(new Promise(() => {}));
    render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.queryByText(/waking up/)).toBeNull();
    await vi.advanceTimersByTimeAsync(8000);
    expect(screen.getByRole("status")).toHaveTextContent("waking up");
    vi.useRealTimers();
  });
});

describe("Where sign-in lands", () => {
  it("opens Today for a Medical Tourism organisation and the builder for everyone else", async () => {
    loginMock.mockResolvedValue({ access_token: "A", refresh_token: "R", token_type: "bearer" });
    modeMock.mockResolvedValue({ pilot_console: true });
    render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/medical-tourism/overview"));
  });

  it("still signs in when the mode cannot be read", async () => {
    loginMock.mockResolvedValue({ access_token: "A", refresh_token: "R", token_type: "bearer" });
    modeMock.mockRejectedValue(new Error("down"));
    render(<LoginPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/business"));
  });
});
