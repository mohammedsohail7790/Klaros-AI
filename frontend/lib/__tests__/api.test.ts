import { describe, expect, it, vi } from "vitest";
import { ApiError, withRetry } from "@/lib/api";

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
