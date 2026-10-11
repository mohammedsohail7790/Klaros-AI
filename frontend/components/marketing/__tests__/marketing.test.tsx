import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock, replace: vi.fn() }), usePathname: () => "/" }));

import { BuildCta } from "@/components/marketing/BuildCta";
import { HeroIdeaForm } from "@/components/marketing/HeroIdeaForm";
import Home from "@/app/page";

describe("marketing → application flow", () => {
  beforeEach(() => {
    sessionStorage.clear();
    pushMock.mockReset();
  });

  it("Build My Business goes to sign-up for visitors and straight into the product when signed in", async () => {
    const { unmount } = render(<BuildCta />);
    expect(screen.getByRole("link", { name: /Build My Business/ })).toHaveAttribute("href", "/register");
    unmount();
    sessionStorage.setItem("klaros_access_token", "t");
    render(<BuildCta />);
    await waitFor(() => expect(screen.getByRole("link", { name: /Build My Business/ })).toHaveAttribute("href", "/business"));
  });

  it("carries the typed idea into the app through sign-up", () => {
    render(<HeroIdeaForm />);
    fireEvent.change(screen.getByLabelText("What are you building?"), { target: { value: "A dropshipping store" } });
    fireEvent.click(screen.getByRole("button", { name: /Build My Business/ }));
    expect(sessionStorage.getItem("klaros_pending_idea")).toBe("A dropshipping store");
    expect(pushMock).toHaveBeenCalledWith("/register");
  });

  it("goes straight to the product when already signed in", () => {
    sessionStorage.setItem("klaros_access_token", "t");
    render(<HeroIdeaForm />);
    fireEvent.click(screen.getByRole("button", { name: /Build My Business/ }));
    expect(pushMock).toHaveBeenCalledWith("/business");
  });

  it("the landing page states the product, has both CTAs, and every in-page link has a target", () => {
    const { container } = render(<Home />);
    expect(screen.getAllByText(/your business with AI\./).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Build My Business/).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "See How It Works" })).toHaveAttribute("href", "#how-it-works");
    for (const a of container.querySelectorAll('a[href^="#"]')) {
      const id = a.getAttribute("href")!.slice(1);
      expect(container.querySelector(`[id="${id}"]`), `anchor #${id}`).not.toBeNull();
    }
    // No fabricated statistics.
    expect(container.textContent).not.toMatch(/\b\d+%|24\/7|\d+\+ customers/);
  });

  it("is honest that the Halla connection and parts of ecommerce are not live", () => {
    render(<Home />);
    expect(screen.getAllByText(/isn't live yet|not built|still being built|are still planned/i).length).toBeGreaterThan(0);
  });
});
