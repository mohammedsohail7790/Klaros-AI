import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, revokeAgentToolPermission, withRetry } from "@/lib/api";

describe("withRetry", () => {
  it("returns the result on first success without retrying", async () => {
    const fn = vi.fn().mockResolvedValue("ok");
    const result = await withRetry(fn);
    expect(result).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("retries a 5xx ApiError up to the retry limit, then throws", async () => {
    const fn = vi.fn().mockRejectedValue(new ApiError(503, "Service unavailable"));
    await expect(withRetry(fn, { retries: 2, baseDelayMs: 1 })).rejects.toThrow("Service unavailable");
    // Initial attempt + 2 retries = 3 calls total.
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("retries a 429 ApiError (rate limited)", async () => {
    const fn = vi.fn().mockRejectedValue(new ApiError(429, "Too many requests"));
    await expect(withRetry(fn, { retries: 1, baseDelayMs: 1 })).rejects.toThrow();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("does NOT retry a real 4xx application error (e.g. 404)", async () => {
    const fn = vi.fn().mockRejectedValue(new ApiError(404, "Not found"));
    await expect(withRetry(fn, { retries: 3, baseDelayMs: 1 })).rejects.toThrow("Not found");
    // No retries — the first attempt's error is the real, final answer.
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("does NOT retry a 401 (unauthenticated)", async () => {
    const fn = vi.fn().mockRejectedValue(new ApiError(401, "Not authenticated"));
    await expect(withRetry(fn, { retries: 3, baseDelayMs: 1 })).rejects.toThrow();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("retries a non-ApiError (network/CORS-reported) failure", async () => {
    const fn = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue("recovered");
    const result = await withRetry(fn, { retries: 1, baseDelayMs: 1 });
    expect(result).toBe("recovered");
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe("ApiError", () => {
  it("carries the HTTP status alongside the message", () => {
    const err = new ApiError(422, "Validation failed");
    expect(err.status).toBe(422);
    expect(err.message).toBe("Validation failed");
    expect(err).toBeInstanceOf(Error);
  });
});

describe("request() 204 No Content handling", () => {
  // Regression (Bug B, Agent tool-permissions UI sync, Round 3): the
  // shared `request()` helper used to call `res.json()` unconditionally
  // on every 2xx response. A 204 No Content response (e.g. DELETE
  // /agents/{id}/tool-permissions/{tool}, revokeAgentToolPermission,
  // declared `request<void>`) has no body, so that `res.json()` call
  // threw "Unexpected end of JSON input" even though the HTTP request
  // itself succeeded. The caller's `await revokeAgentToolPermission(...)`
  // then rejected, so frontend/app/agents/[id]/page.tsx's
  // ToolPermissionsSection.toggle() landed in its `catch` block instead
  // of its success path: `onChanged(...)` never ran (stale checkbox,
  // still showing the tool as granted after a real, successful revoke)
  // and a generic "Unable to update this tool's permission." error banner
  // appeared despite nothing actually failing (spurious error) — both
  // reproduced live against a real backend before this fix.
  const originalFetch = global.fetch;
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("resolves instead of throwing on a real 204 No Content response (empty body)", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      text: () => Promise.resolve(""),
      json: () => Promise.reject(new Error("should never be called for an empty body")),
    }) as unknown as typeof fetch;

    await expect(revokeAgentToolPermission("test-token", "a1", "some_tool")).resolves.toBeUndefined();
  });

  it("still parses a normal JSON body on a 200 response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify({ ok: true })),
    }) as unknown as typeof fetch;

    await expect(revokeAgentToolPermission("test-token", "a1", "some_tool")).resolves.toEqual({ ok: true });
  });

  it("still surfaces a genuine backend error as an ApiError (the 204 fix only touches the success path)", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "Agent not found" }),
    }) as unknown as typeof fetch;

    await expect(revokeAgentToolPermission("test-token", "a1", "some_tool")).rejects.toMatchObject({
      status: 404,
      message: "Agent not found",
    });
  });
});
