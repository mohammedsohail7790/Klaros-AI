import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/keepwarm/route";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("keep-warm route", () => {
  it("asks the backend's health endpoint and reports how it answered", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
    const body = await (await GET()).json();
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/health$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
    expect(body).toMatchObject({ ok: true, status: 200 });
    expect(typeof body.ms).toBe("number");
  });

  it("answers normally even when the backend does not, so the scheduled job never fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("timeout")));
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: false, status: null });
  });
});
