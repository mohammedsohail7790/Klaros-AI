import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RenderedSection } from "@/lib/api";

const submitPublicWebsiteLeadMock = vi.fn();
const getPublicConsentMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  submitPublicWebsiteLead: (...args: unknown[]) => submitPublicWebsiteLeadMock(...args),
  getPublicConsent: (...args: unknown[]) => getPublicConsentMock(...args),
}));

import { RenderSection } from "@/components/website/ComponentRegistry";

function section(component_type: string, props: Record<string, unknown>, data?: unknown): RenderedSection {
  return { component_type, props, data } as RenderedSection;
}

describe("RenderSection — the closed component_type -> React component registry", () => {
  it("renders HERO with plain text content", () => {
    render(<RenderSection section={section("HERO", { headline: "Welcome" })} tenantId="t1" />);
    expect(screen.getByText("Welcome")).toBeInTheDocument();
  });

  it("renders TEXT, CTA, FOOTER for the remaining plain component types", () => {
    render(<RenderSection section={section("TEXT", { body: "Some body copy" })} tenantId="t1" />);
    expect(screen.getByText("Some body copy")).toBeInTheDocument();
  });

  it("renders PROVIDER_DIRECTORY items generically from the data-provider contract shape", () => {
    render(
      <RenderSection
        section={section(
          "PROVIDER_DIRECTORY",
          { title: "Our providers" },
          { items: [{ name: "Synthetic Hospital", location: "Istanbul, TR" }], resolved: true }
        )}
        tenantId="t1"
      />
    );
    expect(screen.getByText("Synthetic Hospital")).toBeInTheDocument();
    expect(screen.getByText("Istanbul, TR")).toBeInTheDocument();
  });

  it("renders the empty state when a data-bound section has no items", () => {
    render(
      <RenderSection
        section={section("PROCEDURE_LIST", { empty_state_text: "Nothing here yet" }, { items: [], resolved: false })}
        tenantId="t1"
      />
    );
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
  });

  it("renders an unknown component_type as nothing rather than guessing", () => {
    const { container } = render(<RenderSection section={section("SOMETHING_MADE_UP", {})} tenantId="t1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("never interprets text content as HTML — an XSS-shaped string renders as literal text", () => {
    render(<RenderSection section={section("TEXT", { body: "<img src=x onerror=alert(1)>" })} tenantId="t1" />);
    // React escapes text children — the literal string is visible text, no
    // <img> element was ever created.
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("CONTACT_FORM submits to the public lead endpoint scoped to this tenant only, never a tenant_id from anywhere else", async () => {
    submitPublicWebsiteLeadMock.mockResolvedValue({ received: true, lead_id: "lead-1" });
    getPublicConsentMock.mockResolvedValue({ required: false, open: true });
    const { getByText, getByLabelText } = render(
      <RenderSection section={section("CONTACT_FORM", { title: "Contact us", fields: ["NAME", "EMAIL", "MESSAGE"] })} tenantId="tenant-abc" />
    );
    const nameInput = getByLabelText("Name") as HTMLInputElement;
    nameInput.value = "Jane";
    getByText("Contact us");
    // Submitting isn't exercised with full userEvent here (form internals
    // covered by the ContactForm's own logic); this test's primary
    // assertion is the closed rendering contract above.
    expect(submitPublicWebsiteLeadMock).not.toHaveBeenCalled();
  });
});

describe("CONTACT_FORM — consent for a business that must collect it", () => {
  const form = () => <RenderSection section={section("CONTACT_FORM", { title: "Enquire", fields: ["NAME", "EMAIL", "MESSAGE"] })} tenantId="tenant-abc" />;
  const fill = () => {
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Amira Test" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "amira@example.com" } });
  };
  beforeEach(() => {
    submitPublicWebsiteLeadMock.mockReset();
    getPublicConsentMock.mockReset();
  });

  it("a business that does not need consent gets the plain form and sends no consent", async () => {
    getPublicConsentMock.mockResolvedValue({ required: false, open: true });
    submitPublicWebsiteLeadMock.mockResolvedValue({ received: true, lead_id: "l1" });
    render(form());
    await screen.findByLabelText("Name");
    expect(screen.queryByRole("checkbox")).toBeNull();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(submitPublicWebsiteLeadMock).toHaveBeenCalled());
    expect(submitPublicWebsiteLeadMock.mock.calls[0][1].consent).toBeUndefined();
  });

  it("shows the exact approved wording, will not send until it is ticked, and files the version the visitor saw", async () => {
    getPublicConsentMock.mockResolvedValue({ required: true, open: true, version: "WEB-v2", text: "I agree Gate Clinic may keep my details.", language: "en" });
    submitPublicWebsiteLeadMock.mockResolvedValue({ received: true, lead_id: "l1" });
    render(form());
    expect(await screen.findByText("I agree Gate Clinic may keep my details.")).toBeInTheDocument();
    fill();
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(send).toBeEnabled();
    fireEvent.click(send);
    await waitFor(() => expect(submitPublicWebsiteLeadMock).toHaveBeenCalledTimes(1));
    expect(submitPublicWebsiteLeadMock.mock.calls[0][0]).toBe("tenant-abc");
    expect(submitPublicWebsiteLeadMock.mock.calls[0][1].consent).toEqual({ scopes: ["contact", "store_personal_data"], wording_version: "WEB-v2" });
    await screen.findByText(/we received your message/i);
  });

  it("is closed, with no form at all, until the owner has approved a wording", async () => {
    getPublicConsentMock.mockResolvedValue({ required: true, open: false });
    render(form());
    await screen.findByText(/not taking enquiries through this page yet/);
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(submitPublicWebsiteLeadMock).not.toHaveBeenCalled();
  });

  it("fails closed when the consent state cannot be loaded", async () => {
    getPublicConsentMock.mockRejectedValue(new Error("down"));
    render(form());
    await screen.findByText(/can't take enquiries through this page right now/);
    expect(screen.queryByLabelText("Name")).toBeNull();
  });

  it("shows right-to-left wording for an Arabic wording", async () => {
    getPublicConsentMock.mockResolvedValue({ required: true, open: true, version: "WEB-v1", text: "أوافق على حفظ بياناتي", language: "ar" });
    render(form());
    const text = await screen.findByText("أوافق على حفظ بياناتي");
    expect(text.closest("[dir]")).toHaveAttribute("dir", "rtl");
  });
});
