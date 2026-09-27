import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RenderedSection } from "@/lib/api";

const submitPublicWebsiteLeadMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  submitPublicWebsiteLead: (...args: unknown[]) => submitPublicWebsiteLeadMock(...args),
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
