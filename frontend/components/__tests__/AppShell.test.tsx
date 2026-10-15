import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let path = "/business/home";
const getModules = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => path }));
vi.mock("@/lib/api", () => ({
  logout: vi.fn(),
  getTenantModules: (...a: unknown[]) => getModules(...a),
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
      "Home", "CRM", "Sales", "Marketing", "Finance", "Operations", "AI team", "Website", "Business", "Settings",
    ]);
    expect(within(primary).getByRole("link", { name: "Sales" })).toHaveAttribute("href", "/quotes");
    expect(within(primary).getByRole("link", { name: "Finance" })).toHaveAttribute("href", "/finance/invoices");
  });

  it("shows the tabs of the current area, with exactly one active", () => {
    renderAt("/finance/ar");
    const tabs = screen.getByRole("navigation", { name: "Finance sections" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Invoices", "Receivables", "Cash", "Profitability", "Vendors"]);
    expect(within(tabs).getByRole("link", { name: "Receivables" })).toHaveAttribute("aria-current", "page");
    const active = within(screen.getByRole("navigation", { name: "Primary" })).getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(active.map((a) => a.textContent)).toEqual(["Finance"]);
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

  describe("modules a tenant has enabled", () => {
    const signedIn = (p: string) => {
      sessionStorage.setItem("klaros_access_token", "tok");
      return renderAt(p);
    };
    const reset = () => { sessionStorage.clear(); getModules.mockReset(); };
    const primary = () => within(screen.getByRole("navigation", { name: "Primary" }));

    it("shows the same areas to every business, and adds a module's tabs and area only when it is enabled", async () => {
      getModules.mockResolvedValue({ modules: ["medical_tourism"] });
      signedIn("/medical-tourism/pipeline");
      await waitFor(() => expect(primary().getByRole("link", { name: /^Clinics/ })).toBeInTheDocument());
      expect(primary().getAllByRole("link").map((a) => a.textContent)).toEqual(["Home", "CRM", "Sales", "Marketing", "Finance", "Operations3", "Clinics", "AI team", "Website", "Business", "Settings"]);
      const tabs = screen.getByRole("navigation", { name: "CRM sections" });
      expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Leads", "Customers", "Calendar", "Patients", "Consultations"]);
      expect(within(tabs).getByRole("link", { name: "Patients" })).toHaveAttribute("aria-current", "page");
      reset();
    });

    it("keeps a patient's case under Patients and puts tasks and referrals under Operations", async () => {
      getModules.mockResolvedValue({ modules: ["medical_tourism"] });
      const view = signedIn("/medical-tourism/patients/abc");
      await waitFor(() => expect(screen.getByRole("navigation", { name: "CRM sections" })).toBeInTheDocument());
      expect(within(screen.getByRole("navigation", { name: "CRM sections" })).getByRole("link", { name: "Patients" })).toHaveAttribute("aria-current", "page");
      view.unmount();
      signedIn("/medical-tourism/tasks");
      await waitFor(() => expect(screen.getByRole("navigation", { name: "Operations sections" })).toBeInTheDocument());
      const work = within(screen.getByRole("navigation", { name: "Operations sections" }));
      expect(work.getByRole("link", { name: /^Tasks/ })).toHaveAttribute("aria-current", "page");
      expect(work.getByRole("link", { name: "Referrals" })).toHaveAttribute("href", "/medical-tourism/provider-referrals");
      reset();
    });

    it("shows a business with no module the core areas only, with no Medical Tourism pages", async () => {
      getModules.mockResolvedValue({ modules: [] });
      signedIn("/leads");
      await waitFor(() => expect(getModules).toHaveBeenCalled());
      expect(primary().getAllByRole("link").map((a) => a.textContent)).toEqual(["Home", "CRM", "Sales", "Marketing", "Finance", "Operations", "AI team", "Website", "Business", "Settings"]);
      const tabs = within(screen.getByRole("navigation", { name: "CRM sections" }));
      expect(tabs.getAllByRole("link").map((a) => a.textContent)).toEqual(["Leads", "Customers", "Calendar"]);
      expect(screen.queryByRole("link", { name: "Patients" })).not.toBeInTheDocument();
      reset();
    });

    it("fails OPEN: if the modules cannot be read, the core navigation is still there", async () => {
      getModules.mockRejectedValue(new Error("network"));
      signedIn("/leads");
      await waitFor(() => expect(getModules).toHaveBeenCalled());
      expect(primary().getByRole("link", { name: "CRM" })).toBeInTheDocument();
      expect(primary().getByRole("link", { name: "Finance" })).toBeInTheDocument();
      reset();
    });

    it("searches the pages the tenant can see, including a module's", async () => {
      getModules.mockResolvedValue({ modules: ["medical_tourism"] });
      signedIn("/leads");
      await waitFor(() => expect(primary().getByRole("link", { name: /^Clinics/ })).toBeInTheDocument());
      fireEvent.change(screen.getByLabelText("Find a page"), { target: { value: "provider" } });
      expect(screen.getByRole("link", { name: "Providers" })).toBeInTheDocument();
      reset();
    });
  });

  it("has a menu button for narrow screens and shows who is signed in", () => {
    renderAt("/business/home");
    expect(screen.getByRole("button", { name: "Open menu" })).toBeInTheDocument();
    expect(screen.getByText("Ada")).toBeInTheDocument();
  });
});
