import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "@/components/ui/Toast";

function Trigger() {
  const toast = useToast();
  return (
    <button type="button" onClick={() => toast.success("Quote sent.")}>
      fire
    </button>
  );
}

describe("Toast", () => {
  it("renders a pushed toast message via the ToastProvider stack", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>
    );

    await user.click(screen.getByRole("button", { name: "fire" }));

    expect(await screen.findByText("Quote sent.")).toBeInTheDocument();
  });

  it("dismisses a toast when its close button is clicked", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>
    );

    await user.click(screen.getByRole("button", { name: "fire" }));
    expect(await screen.findByText("Quote sent.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByText("Quote sent.")).not.toBeInTheDocument());
  });

  it("throws when useToast is used outside a ToastProvider", () => {
    function Bare() {
      useToast();
      return null;
    }
    // Suppress React's expected error-boundary console noise for this
    // intentionally-invalid-usage assertion.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Bare />)).toThrow(/useToast must be used within a ToastProvider/);
    spy.mockRestore();
  });
});
