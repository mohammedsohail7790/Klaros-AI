import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let path = "/business/home";
const getMode = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => path }));
vi.mock("@/lib/api", () => ({
  logout: vi.fn(),
  getMedicalTourismMode: (...a: unknown[]) => getMode(...a),
  listHandoffTasks: vi.fn().mockResolvedValue({ tasks: [], total: 3 }),
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(), markAllNotificationsRead: vi.fn(), dismissNotification: vi.fn(),
}));
import AppShell from "@/components/AppShell";

const user = { id: "u", tenant_id: "t", email: "a@b.c", full_name: "Ada", role: "OWNER" };
const renderAt = (p: string, props: Record<string, unknown> = {}) => {
  path = p;
  localStorage.clear();
  return render(<AppShell user={user as never} {...props}><p>page</p></AppShell>);
};

describe("AppShell navigation", () => {
  it("shows the areas of the business, in the order it is built and then run", () => {
    renderAt("/business/home");
    const primary = screen.getByRole("navigation", { name: "Primary" });
    expect(within(primary).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Home", "People", "Work", "Money", "Growth", "AI team", "Website", "Business", "Medical Tourism", "Settings",
    ]);
    expect(within(primary).getByRole("link", { name: "Money" })).toHaveAttribute("href", "/quotes");
  });

  it("shows the tabs of the current area, with exactly one active", () => {
    renderAt("/finance/ar");
    const tabs = screen.getByRole("navigation", { name: "Money sections" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Quotes", "Contracts", "Invoices", "Receivables", "Cash", "Profitability", "Vendors"]);
    expect(within(tabs).getByRole("link", { name: "Receivables" })).toHaveAttribute("aria-current", "page");
    const active = within(screen.getByRole("navigation", { name: "Primary" })).getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(active.map((a) => a.textContent)).toEqual(["Money"]);
  });

  it("files a page under the area of its most specific tab", () => {
    let view = renderAt("/retention/reviews");
    expect(screen.getByRole("link", { name: "Customer care" })).toHaveAttribute("aria-current", "page");
    view.unmount();
    view = renderAt("/business/discovery");
    expect(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Business" })).toHaveAttribute("aria-current", "page");
    view.unmount();
    renderAt("/business/home");
    expect(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
  });

  it("shows a breadcrumb for the current page and accepts a custom trail and page actions", () => {
    const { unmount } = renderAt("/leads");
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText("Leads")).toHaveAttribute("aria-current", "page");
    unmount();
    renderAt("/leads/abc", { crumbs: [{ label: "Leads", href: "/leads" }, { label: "Omar Al Farsi" }], actions: <button>Do it</button> });
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Leads" })).toHaveAttribute("href", "/leads");
    expect(within(crumbs).getByText("Omar Al Farsi")).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Do it" })).toBeInTheDocument();
  });

  it("finds any page by name, including ones that live in another area's tabs", () => {
    renderAt("/business/home");
    fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "invoice" } });
    expect(screen.getByRole("link", { name: "Invoices" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Leads" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "zzzz" } });
    expect(screen.getByText(/No pages match/)).toBeInTheDocument();
  });

  describe("Medical Tourism pilot organisation", () => {
    const signedIn = (p: string) => {
      sessionStorage.setItem("klaros_access_token", "tok");
      return renderAt(p);
    };
    const afterEach_ = () => { sessionStorage.clear(); getMode.mockReset(); };

    it("shows the owner's console as five areas, without the general-purpose ERP pages", async () => {
      getMode.mockResolvedValue({ pilot_console: true, consent_gated: true });
      signedIn("/medical-tourism/overview");
      const primary = screen.getByRole("navigation", { name: "Primary" });
      await waitFor(() => expect(within(primary).getByRole("link", { name: /^Patients/ })).toHaveAttribute("href", "/medical-tourism/overview"));
      expect(within(primary).getAllByRole("link").map((a) => a.textContent)).toEqual(["Patients3", "Clinics", "Money", "Business", "Settings"]);
      expect(within(primary).getByRole("link", { name: /^Patients/ })).toHaveAttribute("aria-current", "page");
      const tabs = screen.getByRole("navigation", { name: "Patients sections" });
      expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Today", "Patients", "Tasks3", "Referrals", "Consultations"]);
      expect(within(tabs).getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
      expect(within(tabs).getByRole("link", { name: "Referrals" })).toHaveAttribute("href", "/medical-tourism/provider-referrals");
      for (const hidden of ["Jobs", "Warranties", "Quotes", "Vendors", "Invoices", "Finance", "Retention", "People", "Growth"]) {
        expect(screen.queryByRole("link", { name: hidden })).not.toBeInTheDocument();
      }
      afterEach_();
    });

    it("keeps a patient's case under Patients and lists the clinic area's tabs", async () => {
      getMode.mockResolvedValue({ pilot_console: true, consent_gated: true });
      const view = signedIn("/medical-tourism/patients/abc");
      await waitFor(() => expect(screen.getByRole("navigation", { name: "Patients sections" })).toBeInTheDocument());
      expect(within(screen.getByRole("navigation", { name: "Patients sections" })).getByRole("link", { name: "Patients" })).toHaveAttribute("aria-current", "page");
      view.unmount();
      signedIn("/contracts");
      await waitFor(() => expect(screen.getByRole("navigation", { name: "Clinics sections" })).toBeInTheDocument());
      expect(within(screen.getByRole("navigation", { name: "Clinics sections" })).getAllByRole("link").map((a) => a.textContent)).toEqual(["Providers", "Procedures & offers", "Contracts"]);
      afterEach_();
    });

    it("searches only the pilot pages", async () => {
      getMode.mockResolvedValue({ pilot_console: true, consent_gated: true });
      signedIn("/medical-tourism/overview");
      await waitFor(() => expect(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: /^Clinics/ })).toBeInTheDocument());
      fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "invoice" } });
      expect(screen.getByText(/No pages match/)).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "provider" } });
      expect(screen.getByRole("link", { name: "Providers" })).toBeInTheDocument();
      afterEach_();
    });

    it("keeps the full navigation for an organisation that is not a pilot", async () => {
      getMode.mockResolvedValue({ pilot_console: false, consent_gated: false });
      signedIn("/business/home");
      await waitFor(() => expect(getMode).toHaveBeenCalled());
      expect(screen.getByRole("link", { name: "People" })).toHaveAttribute("href", "/leads");
      expect(screen.queryByRole("link", { name: "Patients" })).not.toBeInTheDocument();
      afterEach_();
    });

    it("fails OPEN: if the mode cannot be read, nothing is hidden", async () => {
      getMode.mockRejectedValue(new Error("network"));
      signedIn("/business/home");
      await waitFor(() => expect(getMode).toHaveBeenCalled());
      expect(screen.getByRole("link", { name: "People" })).toBeInTheDocument();
      afterEach_();
    });
  });

  it("has a menu button for narrow screens and shows who is signed in", () => {
    renderAt("/business/home");
    expect(screen.getByRole("button", { name: "Open menu" })).toBeInTheDocument();
    expect(screen.getByText("Ada")).toBeInTheDocument();
  });
});
