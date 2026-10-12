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
  it("always shows the nine primary destinations, in the order a business is built and run", () => {
    renderAt("/business/home");
    const primary = screen.getByRole("navigation", { name: "Primary" });
    const links = within(primary).getAllByRole("link").slice(0, 9).map((a) => a.textContent);
    expect(links).toEqual(["Home", "Build", "Business", "AI Workforce", "Website", "Integrations", "Data", "Automation", "Analytics"]);
  });

  it("keeps the day-to-day work one click away and the long tail tucked into All modules", () => {
    renderAt("/business/home");
    expect(screen.getByRole("link", { name: "Leads" })).toHaveAttribute("href", "/leads");
    expect(screen.queryByRole("link", { name: "Invoices" })).not.toBeInTheDocument(); // collapsed by default
    fireEvent.click(screen.getByRole("button", { name: /All modules/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finance/ }));
    expect(screen.getByRole("link", { name: "Invoices" })).toHaveAttribute("href", "/finance/invoices");
  });

  it("opens All modules by itself when the current page lives inside it", () => {
    renderAt("/finance/invoices");
    expect(screen.getByRole("link", { name: "Invoices" })).toHaveAttribute("aria-current", "page");
  });

  it("marks exactly one item active — the most specific", () => {
    renderAt("/business/discovery");
    const active = screen.getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(active.map((a) => a.textContent)).toEqual(["Build"]);
    renderAt("/business/map");
    expect(screen.getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page").map((a) => a.textContent)).toContain("Business");
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

  it("finds any page by name, including ones inside All modules", () => {
    renderAt("/business/home");
    fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "invoice" } });
    expect(screen.getByRole("link", { name: "Invoices" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Analytics" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "zzzz" } });
    expect(screen.getByText(/No pages match/)).toBeInTheDocument();
  });

  describe("Medical Tourism pilot organisation", () => {
    const signedIn = (p: string) => {
      sessionStorage.setItem("klaros_access_token", "tok");
      return renderAt(p);
    };
    const afterEach_ = () => { sessionStorage.clear(); getMode.mockReset(); };

    it("shows the owner's console: patients, clinic network, money, business and settings, without the general-purpose ERP pages", async () => {
      getMode.mockResolvedValue({ pilot_console: true, consent_gated: true });
      signedIn("/medical-tourism/overview");
      const primary = screen.getByRole("navigation", { name: "Primary" });
      await waitFor(() => expect(within(primary).getByRole("link", { name: "Patients" })).toHaveAttribute("href", "/medical-tourism/pipeline"));
      expect(within(primary).getAllByRole("link").map((a) => a.textContent)).toEqual([
        "Today", "Patients", "Tasks3", "Referrals", "Consultations",
        "Providers", "Procedures & offers", "Contracts",
        "Commissions", "Revenue",
        "Business setup", "Website", "Marketing", "AI receptionist",
        "Team", "Account & security", "Licences & insurance",
      ]);
      expect(within(primary).getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
      expect(within(primary).getByRole("link", { name: "Referrals" })).toHaveAttribute("href", "/medical-tourism/provider-referrals");
      expect(within(primary).getByRole("link", { name: "Business setup" })).toHaveAttribute("href", "/setup");
      expect(screen.queryByRole("button", { name: /All modules/ })).not.toBeInTheDocument();
      for (const hidden of ["Jobs", "Warranties", "Quotes", "Vendors", "Invoices", "Finance", "Retention"]) {
        expect(screen.queryByRole("link", { name: hidden })).not.toBeInTheDocument();
      }
      afterEach_();
    });

    it("searches only the pilot pages", async () => {
      getMode.mockResolvedValue({ pilot_console: true, consent_gated: true });
      signedIn("/medical-tourism/overview");
      await waitFor(() => expect(screen.getByRole("link", { name: "Patients" })).toBeInTheDocument());
      fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "invoice" } });
      expect(screen.getByText(/No pages match/)).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "patient" } });
      expect(screen.getByRole("link", { name: "Patients" })).toBeInTheDocument();
      afterEach_();
    });

    it("keeps the full navigation for an organisation that is not a pilot", async () => {
      getMode.mockResolvedValue({ pilot_console: false, consent_gated: false });
      signedIn("/business/home");
      await waitFor(() => expect(getMode).toHaveBeenCalled());
      expect(screen.getByRole("link", { name: "Leads" })).toHaveAttribute("href", "/leads");
      expect(screen.getByRole("button", { name: /All modules/ })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Patients" })).not.toBeInTheDocument();
      afterEach_();
    });

    it("fails OPEN: if the mode cannot be read, nothing is hidden", async () => {
      getMode.mockRejectedValue(new Error("network"));
      signedIn("/business/home");
      await waitFor(() => expect(getMode).toHaveBeenCalled());
      expect(screen.getByRole("link", { name: "Leads" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /All modules/ })).toBeInTheDocument();
      afterEach_();
    });
  });

  it("has a menu button for narrow screens and shows who is signed in", () => {
    renderAt("/business/home");
    expect(screen.getByRole("button", { name: "Open menu" })).toBeInTheDocument();
    expect(screen.getByText("Ada")).toBeInTheDocument();
  });
});
