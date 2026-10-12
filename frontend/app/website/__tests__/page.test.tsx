import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/website",
}));

const getMyWebsiteMock = vi.fn();
const listWebsiteVersionsMock = vi.fn();
const previewWebsiteVersionMock = vi.fn();
const replaceWebsitePageSectionsMock = vi.fn();
const updateWebsiteThemeMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getMyWebsite: (...args: unknown[]) => getMyWebsiteMock(...args),
  listWebsiteVersions: (...args: unknown[]) => listWebsiteVersionsMock(...args),
  previewWebsiteVersion: (...args: unknown[]) => previewWebsiteVersionMock(...args),
  replaceWebsitePageSections: (...args: unknown[]) => replaceWebsitePageSectionsMock(...args),
  generateWebsite: vi.fn(),
  addWebsitePage: vi.fn(),
  updateWebsiteTheme: (...args: unknown[]) => updateWebsiteThemeMock(...args),
  newWebsiteDraft: vi.fn(),
  publishWebsiteVersion: vi.fn(),
  unpublishWebsite: vi.fn(),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", tenant_id: "t1", email: "owner@example.com", full_name: "Test Owner", role: "OWNER" },
    loading: false,
    error: null,
  }),
}));

import WebsitePage from "@/app/website/page";

function website() {
  return { id: "w1", name: "Test Site", slug: "site", blueprint_id: "b1", current_published_version_id: null };
}

function draftVersion() {
  return {
    id: "v1",
    website_id: "w1",
    version: 1,
    status: "DRAFT" as const,
    theme: {},
    navigation: { items: [] },
    seo_defaults: {},
    generation_provenance: {},
    published_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("Website Builder page", () => {
  beforeEach(() => {
    getMyWebsiteMock.mockReset();
    listWebsiteVersionsMock.mockReset();
    previewWebsiteVersionMock.mockReset();
    replaceWebsitePageSectionsMock.mockReset();
    updateWebsiteThemeMock.mockReset();
  });

  it("rehydrates a saved PROVIDER_DIRECTORY section's data source provider key from the preview response", async () => {
    // Regression test for Bug A: the preview endpoint used to never echo
    // `data_source` back, so this field always started blank after a
    // save/reload even though the backend had the real value saved
    // (backend/app/api/v1/websites.py's preview_version now overlays it).
    getMyWebsiteMock.mockResolvedValue(website());
    listWebsiteVersionsMock.mockResolvedValue([draftVersion()]);
    previewWebsiteVersionMock.mockResolvedValue({
      theme: {},
      navigation: { items: [] },
      seo_defaults: { title: null, description: null, og_image_url: null },
      pages: [
        {
          slug: "home",
          title: "Home",
          seo: { title: null, description: null, og_image_url: null },
          sections: [
            {
              component_type: "PROVIDER_DIRECTORY",
              props: { title: "Our providers" },
              data: [],
              data_source: { provider_key: "medical_tourism.provider_directory", params: {} },
            },
          ],
        },
      ],
    });

    render(<WebsitePage />);

    const providerKeyInput = await screen.findByPlaceholderText<HTMLInputElement>("<namespace>.<key>");
    expect(providerKeyInput.value).toBe("medical_tourism.provider_directory");
  });

  it("starts the data source field blank for a section with no saved provider key (no guessing)", async () => {
    getMyWebsiteMock.mockResolvedValue(website());
    listWebsiteVersionsMock.mockResolvedValue([draftVersion()]);
    previewWebsiteVersionMock.mockResolvedValue({
      theme: {},
      navigation: { items: [] },
      seo_defaults: { title: null, description: null, og_image_url: null },
      pages: [
        {
          slug: "home",
          title: "Home",
          seo: { title: null, description: null, og_image_url: null },
          sections: [
            {
              component_type: "PROCEDURE_LIST",
              props: { title: "Our procedures" },
              data: [],
              data_source: null,
            },
          ],
        },
      ],
    });

    render(<WebsitePage />);

    const providerKeyInput = await screen.findByPlaceholderText<HTMLInputElement>("<namespace>.<key>");
    expect(providerKeyInput.value).toBe("");
  });

  it("saves the edited provider key through replaceWebsitePageSections with the generic data_source shape", async () => {
    getMyWebsiteMock.mockResolvedValue(website());
    listWebsiteVersionsMock.mockResolvedValue([draftVersion()]);
    previewWebsiteVersionMock.mockResolvedValue({
      theme: {},
      navigation: { items: [] },
      seo_defaults: { title: null, description: null, og_image_url: null },
      pages: [
        {
          slug: "home",
          title: "Home",
          seo: { title: null, description: null, og_image_url: null },
          sections: [
            {
              component_type: "PROVIDER_DIRECTORY",
              props: { title: "Our providers" },
              data: [],
              data_source: { provider_key: "medical_tourism.provider_directory", params: {} },
            },
          ],
        },
      ],
    });
    replaceWebsitePageSectionsMock.mockResolvedValue({ id: "p1", slug: "home", section_count: 1 });

    render(<WebsitePage />);
    await screen.findByPlaceholderText<HTMLInputElement>("<namespace>.<key>");

    const saveButton = await screen.findByText("Save sections");
    saveButton.click();

    await waitFor(() =>
      expect(replaceWebsitePageSectionsMock).toHaveBeenCalledWith(
        "test-token",
        "w1",
        "v1",
        "home",
        expect.arrayContaining([
          expect.objectContaining({
            component_type: "PROVIDER_DIRECTORY",
            data_source: { provider_key: "medical_tourism.provider_directory", params: {} },
          }),
        ])
      )
    );
  });
});

describe("Website Builder theme and brand", () => {
  const draftWith = (theme: Record<string, unknown>) => {
    getMyWebsiteMock.mockResolvedValue(website());
    listWebsiteVersionsMock.mockResolvedValue([draftVersion()]);
    previewWebsiteVersionMock.mockResolvedValue({
      theme,
      navigation: { items: [] },
      seo_defaults: { title: null, description: null, og_image_url: null },
      pages: [{ slug: "home", title: "Home", seo: { title: null, description: null, og_image_url: null }, sections: [{ component_type: "TEXT", props: { body: "Hello" }, data: null }] }],
    });
  };

  beforeEach(() => {
    getMyWebsiteMock.mockReset();
    listWebsiteVersionsMock.mockReset();
    previewWebsiteVersionMock.mockReset();
    updateWebsiteThemeMock.mockReset().mockResolvedValue({});
  });

  it("picking a look sets colours and fonts together, and saving sends the whole theme including the wordmark and direction", async () => {
    draftWith({ primary_color: "#1A1A2E", logo_text: "Old name", text_direction: "LTR" });
    render(<WebsitePage />);
    fireEvent.click(await screen.findByText("Theme and brand"));
    fireEvent.click(screen.getByRole("button", { name: /Lagoon/ }));
    fireEvent.change(screen.getByPlaceholderText("Your business name"), { target: { value: "Acme Clinic" } });
    fireEvent.change(screen.getByDisplayValue("Left to right (English)"), { target: { value: "RTL" } });
    fireEvent.click(screen.getByRole("button", { name: "Save theme" }));
    await waitFor(() => expect(updateWebsiteThemeMock).toHaveBeenCalledTimes(1));
    const sent = updateWebsiteThemeMock.mock.calls[0][3];
    expect(sent).toMatchObject({ primary_color: "#0F5C5A", secondary_color: "#E27D60", surface_color: "#EFF6F5", heading_font: "DM Sans", logo_text: "Acme Clinic", text_direction: "RTL" });
  });

  it("warns, without blocking, when the chosen colours would be hard to read", async () => {
    draftWith({ primary_color: "#F0F0F0", text_color: "#EEEEEE", background_color: "#FFFFFF" });
    render(<WebsitePage />);
    fireEvent.click(await screen.findByText("Theme and brand"));
    expect(screen.getByText(/text colour is hard to read/)).toBeInTheDocument();
    expect(screen.getByText(/White button text is hard to read/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save theme" })).toBeEnabled();
  });

  it("says nothing when the colours are fine", async () => {
    draftWith({ primary_color: "#14304F", text_color: "#14243A", background_color: "#FFFFFF" });
    render(<WebsitePage />);
    fireEvent.click(await screen.findByText("Theme and brand"));
    expect(screen.queryByText(/hard to read/)).toBeNull();
  });
});
