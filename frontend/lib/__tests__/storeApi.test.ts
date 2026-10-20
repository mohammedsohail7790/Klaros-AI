import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StoreApiError, createCheckout, getStoreInfo, getStoreOrder, getStoreProduct, listStoreProducts, payStoreOrder } from "@/lib/storeApi";

const fetchMock = vi.fn();
const json = (status: number, body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the public store client", () => {
  it("treats a 404 from the store, the product and the info call as 'closed' or 'not found', not an error", async () => {
    fetchMock.mockImplementation(() => json(404, { detail: "Not found" }));
    expect(await getStoreInfo()).toBeNull();
    expect(await getStoreProduct("nope")).toBeNull();
  });

  it("lets the server cache catalog reads for a minute, and never caches orders or checkout", async () => {
    fetchMock.mockImplementation(() => json(200, { products: [], product: {}, order: {} }));
    await listStoreProducts({ q: "tea", category: "Tea", limit: 24, offset: 24 });
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/public/store/products?q=tea&category=Tea&limit=24&offset=24");
    expect(fetchMock.mock.calls[0][1].next).toEqual({ revalidate: 60 });
    await getStoreProduct("green-tea", { live: true });
    expect(fetchMock.mock.calls[1][1].cache).toBe("no-store");
    await getStoreOrder("tok en");
    expect(fetchMock.mock.calls[2][0]).toContain("/orders/tok%20en");
    expect(fetchMock.mock.calls[2][1].cache).toBe("no-store");
  });

  it("sends only what the shopper chose to checkout, as JSON", async () => {
    fetchMock.mockImplementation(() => json(200, { order_number: "DS-1", status_token: "t", status: "PENDING_PAYMENT", total: "35.00", checkout_url: "https://stripe.test/x" }));
    const body = { lines: [{ sku: "TEA-1", quantity: 2 }], contact: { name: "Pat", email: "pat@example.com" }, ship_to: { name: "Pat", line1: "1 Main", city: "Austin", state: "TX", postal_code: "78701" }, idempotency_key: "k-12345678" };
    const r = await createCheckout(body);
    expect(r.checkout_url).toBe("https://stripe.test/x");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/public/store/checkout");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(body);
  });

  it("turns the server's refusal into a message and a list of reasons", async () => {
    fetchMock.mockImplementation(() => json(422, { detail: { message: "The request was refused.", problems: ["ship_to.state must be one of the 48 contiguous states", "NOPE-1: unknown item"] } }));
    const err = await createCheckout({} as never).catch((e) => e);
    expect(err).toBeInstanceOf(StoreApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe("The request was refused.");
    expect(err.problems).toHaveLength(2);
  });

  it("explains rate limiting and an unreachable store in plain words", async () => {
    fetchMock.mockImplementationOnce(() => json(429, { detail: "rate limit exceeded" }));
    expect((await payStoreOrder("t").catch((e) => e)).message).toMatch(/Too many requests/);
    fetchMock.mockImplementationOnce(() => Promise.reject(new TypeError("network")));
    expect((await payStoreOrder("t").catch((e) => e)).message).toMatch(/could not be reached/);
  });

  it("rethrows other failures when loading the info", async () => {
    fetchMock.mockImplementation(() => json(500, { detail: "boom" }));
    await expect(getStoreInfo()).rejects.toBeInstanceOf(StoreApiError);
  });
});
