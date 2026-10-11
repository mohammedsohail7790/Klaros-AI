import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearSession, getAccessToken, getRefreshToken, setSession } from "@/lib/session";
import { friendlyAuthError, passwordHint, safeNextPath } from "@/lib/authErrors";
import { ApiError } from "@/lib/api";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("session storage", () => {
  it("keeps the session in localStorage so it survives a closed tab, and leaves no tab-scoped copy behind", () => {
    sessionStorage.setItem("klaros_access_token", "old-tab-token");
    setSession({ access_token: "a1", refresh_token: "r1" });
    expect(localStorage.getItem("klaros_access_token")).toBe("a1");
    expect(sessionStorage.getItem("klaros_access_token")).toBeNull();
    expect(getAccessToken()).toBe("a1");
    expect(getRefreshToken()).toBe("r1");
  });

  it("still honours a session that was started in sessionStorage before this change", () => {
    sessionStorage.setItem("klaros_access_token", "legacy");
    expect(getAccessToken()).toBe("legacy");
  });

  it("clears both stores on sign-out", () => {
    setSession({ access_token: "a", refresh_token: "r" });
    sessionStorage.setItem("klaros_refresh_token", "x");
    clearSession();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it("falls back to this tab when localStorage is blocked, and never throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {
      throw new Error("blocked");
    });
    expect(() => setSession({ access_token: "a", refresh_token: "r" })).not.toThrow();
    spy.mockRestore();
  });
});

describe("auth error helpers", () => {
  it("never shows the validator's developer wording", () => {
    const raw = new ApiError(422, "value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.");
    expect(friendlyAuthError(raw)).toBe("Enter a valid email address, for example name@company.com.");
  });

  it("gives plain answers for bad credentials, throttling, outages and a dead network", () => {
    expect(friendlyAuthError(new ApiError(401, "Invalid email or password"))).toMatch(/don't match/);
    expect(friendlyAuthError(new ApiError(429, "x"))).toMatch(/Too many attempts/);
    expect(friendlyAuthError(new ApiError(503, "x"))).toMatch(/trouble right now/);
    expect(friendlyAuthError(new TypeError("Failed to fetch"))).toMatch(/couldn't reach Klaros/);
  });

  it("only follows same-site paths after sign-in", () => {
    expect(safeNextPath("/medical-tourism/tasks")).toBe("/medical-tourism/tasks");
    expect(safeNextPath("https://evil.example")).toBe("/business");
    expect(safeNextPath("//evil.example")).toBe("/business");
    expect(safeNextPath("/login?next=/x")).toBe("/business");
    expect(safeNextPath(null)).toBe("/business");
  });

  it("guides the new-password field without pretending to be the server", () => {
    expect(passwordHint("").ok).toBe(false);
    expect(passwordHint("abc").text).toMatch(/5 more characters/);
    expect(passwordHint("aaaaaaaa").ok).toBe(false);
    expect(passwordHint("a-much-better-one").ok).toBe(true);
  });
});
