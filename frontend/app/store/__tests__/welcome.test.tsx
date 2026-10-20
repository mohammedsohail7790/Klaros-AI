import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreInfo: vi.fn(), listStoreProducts: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import Welcome, { metadata } from "@/app/store/welcome/page";

beforeEach(() => Object.values(api).forEach((f) => f.mockReset()));

describe("Clean Room welcome page", () => {
  it("introduces the business and explains how it works", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    renderInStore(await Welcome());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Pantry staples, delivered.");
    expect(screen.getByRole("list", { name: "Why shop here" })).toHaveTextContent("Delivered across the continental US");
    const steps = within(screen.getByRole("region", { name: "How it works" }));
    expect(steps.getAllByRole("listitem").map((li) => li.textContent?.replace(/^\d/, "").slice(0, 6))).toEqual(["Choose", "Pay se", "We shi"]);
  });

  it("offers the shop when it is open", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    renderInStore(await Welcome());
    expect(screen.getByRole("link", { name: "Shop now" })).toHaveAttribute("href", "/store#products");
    expect(screen.queryByText("The shop opens soon")).not.toBeInTheDocument();
  });

  it("says the shop opens soon, with no shop link, when it is closed", async () => {
    api.getStoreInfo.mockResolvedValue(null);
    renderInStore(await Welcome());
    expect(screen.getByText("The shop opens soon")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Shop (now|all products)/ })).not.toBeInTheDocument();
  });

  it("still renders when the shop cannot be reached", async () => {
    api.getStoreInfo.mockRejectedValue(new Error("down"));
    renderInStore(await Welcome());
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getByText("The shop opens soon")).toBeInTheDocument();
  });

  it("names the business in the page title", () => {
    expect(JSON.stringify(metadata.title)).toContain("Clean Room");
  });
});
