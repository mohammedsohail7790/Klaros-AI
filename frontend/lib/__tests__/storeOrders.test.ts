import { beforeEach, describe, expect, it } from "vitest";
import { checkoutKey, friendlyProblem, lastOrder, rememberOrder } from "@/lib/storeOrders";

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });

describe("checkout key", () => {
  it("is kept while the basket is unchanged and renewed when it changes", () => {
    const a = checkoutKey("TEA-1:2");
    expect(checkoutKey("TEA-1:2")).toBe(a);
    const b = checkoutKey("TEA-1:3");
    expect(b).not.toBe(a);
    expect(checkoutKey("TEA-1:3")).toBe(b);
    expect(a.length).toBeGreaterThanOrEqual(8);
    expect(a.length).toBeLessThanOrEqual(100);
  });
});

describe("last order", () => {
  it("remembers only the order number and its private token", () => {
    expect(lastOrder()).toBeNull();
    rememberOrder("tok123", "DS-ABCD2345");
    expect(lastOrder()).toEqual({ token: "tok123", orderNumber: "DS-ABCD2345" });
    expect(Object.keys(JSON.parse(localStorage.getItem("klaros_store_last_order")!))).toEqual(["token", "orderNumber"]);
  });

  it("ignores a damaged saved value", () => {
    localStorage.setItem("klaros_store_last_order", "{bad");
    expect(lastOrder()).toBeNull();
  });
});

describe("the server's reasons in words", () => {
  it("names the fields the way a shopper would", () => {
    expect(friendlyProblem("ship_to.postal_code must be a 5-digit ZIP code")).toBe("ZIP code must be a 5-digit ZIP code");
    expect(friendlyProblem("ship_to.state must be one of the 48 contiguous states")).toBe("Delivery address: state must be one of the 48 contiguous states");
    expect(friendlyProblem("contact.email is not a valid address")).toBe("Contact: email is not a valid address");
  });
});
