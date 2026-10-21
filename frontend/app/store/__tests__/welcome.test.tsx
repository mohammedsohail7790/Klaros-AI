import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INFO, renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreInfo: vi.fn(), listStoreProducts: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import Welcome, { metadata } from "@/app/store/welcome/page";

beforeEach(() => Object.values(api).forEach((f) => f.mockReset()));

describe("Clean Room welcome page", () => {
  it("tells the story in order: what we sell, why it keeps, who ships it, how you pay, where we deliver, how it works", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    renderInStore(await Welcome());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Pantry staples, delivered.");
    const sections = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(sections).toEqual(["Welcome", "What we sell", "Shelf-stable", "Shipped by our supplier", "Secure payment", "Where we deliver", "How it works", "Get in touch"]);
    expect(screen.getByRole("heading", { name: "No fridge. No freezer. No rush." })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Delivered across the continental US." })).toBeInTheDocument();
    expect(screen.getByText("We can't ship to Alaska, Hawaii or US territories.")).toBeInTheDocument();
  });

  it("explains the order in four steps", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    renderInStore(await Welcome());
    const steps = within(screen.getByRole("region", { name: "How it works" })).getAllByRole("listitem");
    expect(steps.map((li) => li.querySelector("p")?.textContent)).toEqual(["Choose", "Pay on Stripe", "We order it", "Follow it home"]);
  });

  it("draws its pictures as decoration only, hidden from screen readers", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    const { container } = renderInStore(await Welcome());
    const drawings = [...container.querySelectorAll("svg[focusable='false']")];
    expect(drawings.length).toBe(4);
    expect(drawings.every((d) => d.getAttribute("aria-hidden") === "true")).toBe(true);
  });

  it("offers the shop when it is open", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    renderInStore(await Welcome());
    expect(screen.getByRole("link", { name: "Shop now" })).toHaveAttribute("href", "/store#products");
    expect(screen.getByRole("link", { name: "Shop all products" })).toHaveAttribute("href", "/store#products");
    expect(screen.getByRole("heading", { name: "The shelves are stocked." })).toBeInTheDocument();
    expect(screen.queryByText("The shop opens soon")).not.toBeInTheDocument();
  });

  it("says the shop opens soon, with no shop link, when it is closed", async () => {
    api.getStoreInfo.mockResolvedValue(null);
    renderInStore(await Welcome());
    expect(screen.getByText("The shop opens soon")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The shelves are being stocked." })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Shop (now|all products)/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "info@yourcleanroom.com" })).toHaveAttribute("href", "mailto:info@yourcleanroom.com");
  });

  it("still renders when the shop cannot be reached", async () => {
    api.getStoreInfo.mockRejectedValue(new Error("down"));
    renderInStore(await Welcome());
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getByText("The shop opens soon")).toBeInTheDocument();
  });

  it("claims nothing it cannot back: no prices, no delivery times, no reviews", async () => {
    api.getStoreInfo.mockResolvedValue(INFO);
    const { container } = renderInStore(await Welcome());
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\$\d|\b\d+ ?(days?|hours?)\b|★|reviews?|testimonial|guarantee|free shipping/i);
  });

  it("names the business in the page title", () => {
    expect(JSON.stringify(metadata.title)).toContain("Clean Room");
  });
});
