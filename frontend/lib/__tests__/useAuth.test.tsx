import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const getCurrentUserMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getCurrentUser: (...args: unknown[]) => getCurrentUserMock(...args),
}));

import { useAuth } from "@/lib/useAuth";

describe("useAuth", () => {
  beforeEach(() => {
    sessionStorage.clear();
    pushMock.mockClear();
    getCurrentUserMock.mockReset();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  it("redirects to /login when no access token is stored (protected-route behavior)", async () => {
    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/login"));
    expect(result.current.user).toBeNull();
  });

  it("loads the current user when a token is present", async () => {
    sessionStorage.setItem("klaros_access_token", "test-token");
    getCurrentUserMock.mockResolvedValue({
      id: "u1",
      tenant_id: "t1",
      email: "owner@example.com",
      full_name: "Test Owner",
      role: "OWNER",
    });

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.user?.email).toBe("owner@example.com");
    expect(result.current.token).toBe("test-token");
    expect(pushMock).not.toHaveBeenCalledWith("/login");
  });

  it("clears the token and redirects to /login when the session has expired", async () => {
    sessionStorage.setItem("klaros_access_token", "stale-token");
    const { ApiError } = await import("@/lib/api");
    getCurrentUserMock.mockImplementation(async () => {
      throw new ApiError(401, "Unauthorized");
    });

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toMatch(/session expired/i);
    expect(sessionStorage.getItem("klaros_access_token")).toBeNull();
    expect(pushMock).toHaveBeenCalledWith("/login");
  });

  it("does NOT log the user out on a transient network or server failure", async () => {
    sessionStorage.setItem("klaros_access_token", "valid-token");
    getCurrentUserMock.mockImplementation(async () => {
      throw new TypeError("Failed to fetch");
    });
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toMatch(/couldn't reach klaros/i);
    expect(sessionStorage.getItem("klaros_access_token")).toBe("valid-token");
    expect(pushMock).not.toHaveBeenCalledWith("/login");
  });
});
