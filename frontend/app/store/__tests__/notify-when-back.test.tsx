import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { product, renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ askToBeTold: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import { AddToCart } from "@/components/store/AddToCart";
import NotifyWhenBack from "@/components/store/NotifyWhenBack";
import { StoreApiError } from "@/lib/storeApi";

beforeEach(() => api.askToBeTold.mockReset());

const soldOut = () => product({ items: [{ sku: "TEA-1", price: "10.00", currency: "USD", in_stock: false }] });

describe("tell me when it is back", () => {
  it("is offered for a sold-out item and not for one in stock", () => {
    const out = renderInStore(<AddToCart product={soldOut()} />);
    expect(screen.getByText("Out of stock right now.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Tell me when it is back" })).toBeInTheDocument();
    out.unmount();
    renderInStore(<AddToCart product={product()} />);
    expect(screen.queryByRole("form", { name: "Tell me when it is back" })).not.toBeInTheDocument();
  });

  it("sends the item and the address once, then says what happens", async () => {
    api.askToBeTold.mockResolvedValue({ received: true });
    renderInStore(<NotifyWhenBack sku="TEA-1" />);
    await userEvent.type(screen.getByRole("textbox", { name: "Tell me when it is back" }), "sam@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Tell me" }));
    expect(await screen.findByRole("status")).toHaveTextContent("one e-mail when it is back");
    expect(api.askToBeTold).toHaveBeenCalledTimes(1);
    expect(api.askToBeTold).toHaveBeenCalledWith("TEA-1", "sam@example.com", "");
  });

  it("refuses an address that is not one, and sends nothing", async () => {
    renderInStore(<NotifyWhenBack sku="TEA-1" />);
    await userEvent.type(screen.getByRole("textbox", { name: "Tell me when it is back" }), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Tell me" }));
    expect(screen.getByText("Please enter a valid email address.")).toBeInTheDocument();
    expect(api.askToBeTold).not.toHaveBeenCalled();
  });

  it("says the item may be in stock when the store answers that it is", async () => {
    api.askToBeTold.mockRejectedValueOnce(new StoreApiError(409, "This item is in stock now.")).mockResolvedValue({ received: true });
    renderInStore(<NotifyWhenBack sku="TEA-1" />);
    await userEvent.type(screen.getByRole("textbox", { name: "Tell me when it is back" }), "sam@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Tell me" }));
    expect(await screen.findByText(/may be in stock now/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Tell me when it is back" })).toHaveValue("sam@example.com");
  });

  it("hides the bot trap from people and screen readers", () => {
    const { container } = renderInStore(<NotifyWhenBack sku="TEA-1" />);
    const trap = container.querySelector("input[name='website']") as HTMLInputElement;
    expect(trap).toHaveAttribute("tabindex", "-1");
    expect(trap.closest("[aria-hidden='true']")).not.toBeNull();
  });
});
