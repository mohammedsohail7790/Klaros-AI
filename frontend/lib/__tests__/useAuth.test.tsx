import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const getCurrentUserMock = vi.fn();
vi.mock("@/lib/api", () => ({
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
    getCurrentUserMock.mockRejectedValue(new Error("401"));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toMatch(/session expired/i);
    expect(sessionStorage.getItem("klaros_access_token")).toBeNull();
    expect(pushMock).toHaveBeenCalledWith("/login");
  });
});
