import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { product, renderInStore, saveCart, teaLine } from "./fixtures";

const api = vi.hoisted(() => ({ getStoreProduct: vi.fn(), createCheckout: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import CheckoutPage from "@/app/store/checkout/page";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  api.getStoreProduct.mockReset().mockResolvedValue(product());
  api.createCheckout.mockReset();
  saveCart([teaLine(2)]);
});

describe("checkout form errors", () => {
  it("marks each wrong field, explains it next to the field, and moves focus to the first one", async () => {
    renderInStore(<CheckoutPage />);
    const name = await screen.findByLabelText("Full name");
    await userEvent.click(screen.getByRole("button", { name: "Pay with card" }));
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(name.getAttribute("aria-describedby")!)).toHaveTextContent("Please enter your name.");
    expect(screen.getByLabelText("ZIP code")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Phone (optional)")).not.toHaveAttribute("aria-invalid");
    expect(name).toHaveFocus();
    expect(api.createCheckout).not.toHaveBeenCalled();
  });

  it("shows the order being paid for beside the form", async () => {
    renderInStore(<CheckoutPage />);
    const summary = (await screen.findByRole("heading", { name: "Your order" })).parentElement!;
    expect(summary).toHaveTextContent("Green Tea");
    expect(summary).toHaveTextContent("Quantity 2");
    expect(summary).toHaveTextContent("$25.00");
  });
});
