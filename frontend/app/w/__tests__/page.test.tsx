import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ tenantId: "11111111-1111-1111-1111-111111111111" }),
  useSearchParams: () => new URLSearchParams(),
}));

const getPublicWebsiteMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getPublicWebsite: (...args: unknown[]) => getPublicWebsiteMock(...args),
  submitPublicWebsiteLead: vi.fn(),
}));

import { ApiError } from "@/lib/api";
import PublicWebsitePage from "@/app/w/[tenantId]/page";

describe("PublicWebsitePage — the public, unauthenticated website runtime", () => {
  beforeEach(() => {
    getPublicWebsiteMock.mockReset();
  });

  it("renders the tenant's own published site with no login/AppShell chrome", async () => {
    getPublicWebsiteMock.mockResolvedValue({
      theme: { primary_color: "#112233" },
      navigation: { items: [] },
      seo_defaults: { title: "Synthetic Global Health", description: null, og_image_url: null },
      pages: [
        {
          slug: "home",
          title: "Home",
          seo: { title: null, description: null, og_image_url: null },
          sections: [{ component_type: "HERO", props: { headline: "Welcome to Synthetic Global Health" } }],
        },
      ],
    });

    render(<PublicWebsitePage />);
    expect(await screen.findByText("Welcome to Synthetic Global Health")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /klaros/i })).toBeNull();
  });

  it("shows a not-found message rather than crashing on a 404 (unpublished/unknown site)", async () => {
    getPublicWebsiteMock.mockRejectedValue(new ApiError(404, "not found"));

    render(<PublicWebsitePage />);
    await waitFor(() => expect(screen.getByText(/could not be found/i)).toBeInTheDocument());
  });
});
