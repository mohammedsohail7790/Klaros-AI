import { describe, expect, it } from "vitest";
import { locate, visibleAreas } from "@/components/navAreas";

describe("the Store navigation area", () => {
  it("appears only when the dropshipping module is enabled", () => {
    expect(visibleAreas([]).map((a) => a.key)).not.toContain("store");
    expect(visibleAreas(["medical_tourism"]).map((a) => a.key)).not.toContain("store");
    const store = visibleAreas(["dropshipping"]).find((a) => a.key === "store")!;
    expect(store.tabs.map((t) => t.label)).toEqual(["Today", "Orders", "Fulfilment", "Catalog", "Store settings"]);
  });

  it("changes nothing for a business without the module", () => {
    const before = visibleAreas([]).map((a) => a.key);
    expect(before).toEqual(["home", "crm", "sales", "marketing", "finance", "operations", "ai", "website", "business", "settings"]);
  });

  it("puts every store page, including one order, under the right tab", () => {
    const areas = visibleAreas(["dropshipping"]);
    expect(locate("/dropshipping", areas)?.tab.label).toBe("Today");
    expect(locate("/dropshipping/orders/abc", areas)?.tab.label).toBe("Orders");
    expect(locate("/dropshipping/fulfilment", areas)?.tab.label).toBe("Fulfilment");
    expect(locate("/dropshipping/store", areas)?.tab.label).toBe("Store settings");
  });

  it("works alongside medical tourism", () => {
    const keys = visibleAreas(["medical_tourism", "dropshipping"]).map((a) => a.key);
    expect(keys).toContain("store");
    expect(keys).toContain("clinics");
  });
});
