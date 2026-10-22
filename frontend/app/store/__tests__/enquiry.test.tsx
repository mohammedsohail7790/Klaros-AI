import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderInStore } from "./fixtures";

const api = vi.hoisted(() => ({ sendEnquiry: vi.fn() }));
vi.mock("@/lib/storeApi", async () => ({ ...(await vi.importActual<object>("@/lib/storeApi")), ...api }));
import EnquiryForm from "@/components/store/EnquiryForm";
import { StoreApiError } from "@/lib/storeApi";

beforeEach(() => api.sendEnquiry.mockReset());

async function fill(name = "Sam Shopper", email = "sam@example.com", message = "Do you ship to Ohio?") {
  if (name) await userEvent.type(screen.getByLabelText("Your name"), name);
  if (email) await userEvent.type(screen.getByLabelText("Email"), email);
  if (message) await userEvent.type(screen.getByLabelText("Message"), message);
}

describe("write-to-us form", () => {
  it("sends the enquiry once and thanks the visitor", async () => {
    api.sendEnquiry.mockResolvedValue({ received: true });
    renderInStore(<EnquiryForm />);
    await fill();
    await userEvent.type(screen.getByLabelText("Order number (if it is about an order)"), "CR-1001");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Thank you. We have your message.");
    expect(api.sendEnquiry).toHaveBeenCalledTimes(1);
    expect(api.sendEnquiry).toHaveBeenCalledWith(expect.objectContaining({ name: "Sam Shopper", email: "sam@example.com", message: "Do you ship to Ohio?", order_number: "CR-1001", website: "" }));
    expect(api.sendEnquiry.mock.calls[0][0].idempotency_key).toMatch(/^enq-/);
  });

  it("asks for what is missing and sends nothing", async () => {
    renderInStore(<EnquiryForm />);
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(screen.getByText("Please tell us your name.")).toBeInTheDocument();
    expect(screen.getByText("Please enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Please write a message.")).toBeInTheDocument();
    expect(api.sendEnquiry).not.toHaveBeenCalled();
  });

  it("refuses an address that is not an email", async () => {
    renderInStore(<EnquiryForm />);
    await fill("Sam", "not-an-email", "Hello");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(screen.getByText("Please enter a valid email address.")).toBeInTheDocument();
    expect(api.sendEnquiry).not.toHaveBeenCalled();
  });

  it("shows the problem and keeps the typed text when sending fails", async () => {
    api.sendEnquiry.mockRejectedValueOnce(new StoreApiError(429, "Too many requests. Please wait a minute and try again.")).mockResolvedValue({ received: true });
    renderInStore(<EnquiryForm />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests");
    expect(screen.getByLabelText("Message")).toHaveValue("Do you ship to Ohio?");
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  });

  it("hides the bot trap from people and from screen readers", () => {
    const { container } = renderInStore(<EnquiryForm />);
    const trap = container.querySelector("input[name='website']") as HTMLInputElement;
    expect(trap).toHaveAttribute("tabindex", "-1");
    expect(trap.closest("[aria-hidden='true']")).not.toBeNull();
  });
});
