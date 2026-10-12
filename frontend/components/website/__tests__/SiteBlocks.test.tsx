import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RenderedSection, RenderedWebsite } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  submitPublicWebsiteLead: vi.fn(),
  getPublicConsent: () => Promise.resolve({ required: false, open: true }),
}));

import { RenderSection } from "@/components/website/ComponentRegistry";
import { SiteView, themeStyle } from "@/components/website/SiteView";

const section = (component_type: string, props: Record<string, unknown>, data?: unknown) => ({ component_type, props, data }) as RenderedSection;
const show = (s: RenderedSection, alt = false) => render(<RenderSection section={s} tenantId="t1" alt={alt} />);

describe("website blocks (v2)", () => {
  it("HERO shows the route card, both buttons, and keeps the headline as the page's only h1", () => {
    show(
      section("HERO", {
        headline: "Acme Clinic",
        subheadline: "We arrange care abroad.",
        cta_text: "Contact us",
        cta_url: "mailto:hi@acme.example",
        secondary_cta_text: "Call us",
        secondary_cta_url: "tel:+966501234567",
        highlights_title: "How it works",
        highlights: ["Tell us", "We reply", "You decide"],
      }),
    );
    expect(screen.getByRole("heading", { level: 1, name: "Acme Clinic" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact us" })).toHaveAttribute("href", "mailto:hi@acme.example");
    expect(screen.getByRole("link", { name: "Call us" })).toHaveAttribute("href", "tel:+966501234567");
    const route = screen.getByRole("complementary", { name: "How it works" });
    expect(within(route).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Tell us", "We reply", "You decide"]);
  });

  it("HERO with nothing to route shows no side card, and never renders a button whose target is not a web, mail or phone link", () => {
    show(section("HERO", { headline: "Plain", cta_text: "Go", cta_url: "javascript:alert(1)", secondary_cta_text: "Also", secondary_cta_url: "data:text/html,x" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("STEPS are an ordered list in the order given, with descriptions where there are any", () => {
    const { container } = show(section("STEPS", { title: "How it works", items: [{ title: "First", description: "Do this" }, { title: "Second" }, { title: "Third" }] }));
    const list = screen.getByRole("list");
    expect(list.tagName).toBe("OL");
    expect(list).toHaveAttribute("data-count", "3");
    expect(within(list).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["First", "Second", "Third"]);
    expect(screen.getByText("Do this")).toBeInTheDocument();
    expect(container.querySelectorAll(".klaros-site-step")).toHaveLength(3);
  });

  it("STEPS with no items renders nothing rather than an empty heading", () => {
    const { container } = show(section("STEPS", { title: "How it works", items: [] }));
    expect(container).toBeEmptyDOMElement();
  });

  it("FAQ is a set of disclosure widgets that start closed and open on activation", () => {
    const { container } = show(section("FAQ", { title: "Questions", items: [{ question: "Is it free?", answer: "Ask us." }, { question: "", answer: "dropped" }] }));
    const details = container.querySelectorAll("details");
    expect(details).toHaveLength(1);
    expect(details[0]).not.toHaveAttribute("open");
    expect(screen.getByText("Is it free?")).toBeInTheDocument();
    details[0].setAttribute("open", "");
    expect(screen.getByText("Ask us.")).toBeInTheDocument();
    expect(screen.queryByText("dropped")).toBeNull();
  });

  it("TRUST_BAR lists the stated facts, and an empty bar renders nothing", () => {
    const first = show(section("TRUST_BAR", { items: ["Patients from Qatar", "Treatment in Turkey"] }));
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Patients from Qatar", "Treatment in Turkey"]);
    first.unmount();
    const empty = show(section("TRUST_BAR", { items: [] }));
    expect(empty.container).toBeEmptyDOMElement();
  });

  it("a FEATURE_GRID item with an icon key shows that icon; one without shows none", () => {
    const { container } = show(section("FEATURE_GRID", { title: "What we offer", items: [{ title: "A", icon_key: "shield" }, { title: "B", icon_key: "none" }] }), true);
    expect(container.querySelectorAll(".klaros-site-feature-icon")).toHaveLength(1);
    expect(container.querySelector(".klaros-site-band")).toHaveAttribute("data-tone", "alt");
  });

  it("CTA with an unsafe link shows the message but no button", () => {
    show(section("CTA", { heading: "Ready?", button_text: "Go", button_url: "javascript:alert(1)" }));
    expect(screen.getByText("Ready?")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("FOOTER drops a link whose target is not a web, mail or phone link", () => {
    show(section("FOOTER", { business_name: "Acme", links: [{ label: "Safe", url: "https://acme.example" }, { label: "Bad", url: "javascript:alert(1)" }] }));
    expect(screen.getByRole("link", { name: "Safe" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Bad" })).toBeNull();
  });

  it("an unknown block renders nothing", () => {
    const { container } = show(section("SCRIPT", { src: "x" }));
    expect(container).toBeEmptyDOMElement();
  });
});

const SITE = (theme: Record<string, unknown> = {}): RenderedWebsite => ({
  theme,
  seo_defaults: { title: "Acme Clinic", description: null, og_image_url: null },
  navigation: { items: [{ label: "Home", page_slug: "home" }, { label: "How it works", page_slug: "how" }, { label: "Contact", page_slug: "contact" }] },
  pages: [
    {
      slug: "home",
      title: "Home",
      seo: { title: null, description: null, og_image_url: null },
      sections: [
        section("HERO", { headline: "Acme Clinic" }),
        section("FEATURE_GRID", { title: "One", items: [{ title: "a" }] }),
        section("STEPS", { title: "Two", items: [{ title: "a" }, { title: "b" }] }),
        section("TEXT", { heading: "Three", body: "x" }),
        section("CTA", { heading: "Ready?", button_text: "Go", button_url: "https://acme.example" }),
        section("FOOTER", { business_name: "Acme Clinic" }),
      ],
    },
    { slug: "how", title: "How", seo: { title: null, description: null, og_image_url: null }, sections: [] },
  ],
});

describe("SiteView shell", () => {
  const view = (theme: Record<string, unknown> = {}, slug: string | null = "home") =>
    render(<SiteView site={SITE(theme)} tenantId="t1" pageSlug={slug} hrefForPage={(s) => `?page=${s}`} />);

  it("has a skip link, a wordmark with a monogram, and marks the current page in the navigation", () => {
    view({ logo_text: "Acme Clinic" });
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#site-main");
    expect(screen.getByRole("link", { name: /Acme Clinic/ })).toHaveAttribute("href", "?page=home");
    expect(document.querySelector(".klaros-site-logo-mark")).toHaveTextContent("A");
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Contact" })).not.toHaveAttribute("aria-current");
  });

  it("falls back to the site title for the wordmark when the theme sets none", () => {
    view({});
    expect(document.querySelector(".klaros-site-logo-text")).toHaveTextContent("Acme Clinic");
  });

  it("the mobile menu button toggles the navigation and says whether it is open", () => {
    view();
    const button = screen.getByRole("button", { name: /Menu/ });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(nav).toHaveAttribute("data-open", "false");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(nav).toHaveAttribute("data-open", "true");
    fireEvent.click(screen.getByRole("link", { name: "Contact" })); // choosing a page closes it again
    expect(nav).toHaveAttribute("data-open", "false");
  });

  it("alternates light and tinted bands among ordinary blocks, and leaves hero, call to action and footer to colour themselves", () => {
    const { container } = view();
    const tones = Array.from(container.querySelectorAll("main .klaros-site-band, main footer")).map((el) => el.getAttribute("data-tone"));
    expect(tones).toEqual(["hero", "base", "alt", "base", "primary", "footer"]);
  });

  it("an Arabic theme turns the whole page right to left", () => {
    const { container } = view({ text_direction: "RTL" });
    expect(container.querySelector(".klaros-site-root")).toHaveAttribute("dir", "rtl");
    expect(view().container.querySelector(".klaros-site-root")).toHaveAttribute("dir", "ltr");
  });

  it("passes the button style through so outline and ghost themes apply", () => {
    const { container } = view({ button_variant: "OUTLINE" });
    expect(container.querySelector(".klaros-site-root")).toHaveAttribute("data-button", "OUTLINE");
  });
});

describe("themeStyle", () => {
  it("maps tokens to custom properties, and a font outside the closed list can only fall back to the system font", () => {
    const style = themeStyle({
      primary_color: "#14304F",
      surface_color: "#F2F5F9",
      heading_font: "Playfair Display",
      body_font: "url(https://evil.example/x.css)",
      spacing_scale: "SPACIOUS",
      shadow_scale: "NONE",
      radius_scale: "LG",
    }) as Record<string, string>;
    expect(style["--klaros-site-primary"]).toBe("#14304F");
    expect(style["--klaros-site-surface"]).toBe("#F2F5F9");
    expect(style["--klaros-site-heading-font"]).toContain("--site-font-playfair");
    expect(style["--klaros-site-body-font"]).not.toContain("evil");
    expect(style["--klaros-site-section-y"]).toBe("6rem");
    expect(style["--klaros-site-shadow"]).toBe("none");
    expect(style["--klaros-site-radius"]).toBe("16px");
  });
});
