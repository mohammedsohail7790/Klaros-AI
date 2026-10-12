import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let search = "version=v-draft";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search),
}));

const getMyWebsiteMock = vi.fn();
const listWebsiteVersionsMock = vi.fn();
const previewWebsiteVersionMock = vi.fn();
const submitPublicWebsiteLeadMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getMyWebsite: (...a: unknown[]) => getMyWebsiteMock(...a),
  listWebsiteVersions: (...a: unknown[]) => listWebsiteVersionsMock(...a),
  previewWebsiteVersion: (...a: unknown[]) => previewWebsiteVersionMock(...a),
  submitPublicWebsiteLead: (...a: unknown[]) => submitPublicWebsiteLeadMock(...a),
}));

vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({ token: "test-token", user: { id: "u1", tenant_id: "t1", email: "o@example.com", full_name: "Owner", role: "OWNER" }, loading: false, error: null }),
}));

import WebsitePreviewPage from "@/app/website/preview/page";

const SITE = {
  theme: {},
  seo_defaults: {},
  navigation: { items: [{ label: "Contact", page_slug: "contact" }] },
  pages: [
    { slug: "home", title: "Home", sections: [{ component_type: "HERO", props: { headline: "Draft headline" }, data: null }] },
    { slug: "contact", title: "Contact", sections: [{ component_type: "CONTACT_FORM", props: { title: "Get in touch" }, data: null }] },
  ],
};

describe("Website draft preview", () => {
  beforeEach(() => {
    search = "version=v-draft";
    getMyWebsiteMock.mockReset().mockResolvedValue({ id: "w1", name: "Site", slug: "site", blueprint_id: null, current_published_version_id: null });
    listWebsiteVersionsMock.mockReset().mockResolvedValue([{ id: "v-draft", website_id: "w1", version: 1, status: "DRAFT" }]);
    previewWebsiteVersionMock.mockReset().mockResolvedValue(SITE);
    submitPublicWebsiteLeadMock.mockReset();
  });

  it("renders an unpublished draft through the signed-in preview endpoint and says it is not public", async () => {
    render(<WebsitePreviewPage />);
    expect(await screen.findByText("Draft headline")).toBeInTheDocument();
    expect(previewWebsiteVersionMock).toHaveBeenCalledWith("test-token", "w1", "v-draft");
    expect(screen.getByRole("status")).toHaveTextContent(/v1 — draft, not public/);
    expect(screen.getByRole("link", { name: "Contact" })).toHaveAttribute("href", "?version=v-draft&page=contact");
  });

  it("never creates a lead from the preview's enquiry form", async () => {
    search = "version=v-draft&page=contact";
    const { container } = render(<WebsitePreviewPage />);
    await screen.findByText("Get in touch");
    fireEvent.submit(container.querySelector("form")!);
    await waitFor(() => expect(screen.getByText(/This is a preview/)).toBeInTheDocument());
    expect(submitPublicWebsiteLeadMock).not.toHaveBeenCalled();
  });
});
