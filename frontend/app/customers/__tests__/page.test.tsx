import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/customers",
}));

const searchCustomersMock = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  searchCustomers: (...args: unknown[]) => searchCustomersMock(...args),
  bulkImportCustomers: vi.fn(),
  createCustomer: vi.fn(),
  // AppShell -> NotificationBell also imports from "@/lib/api"; stub its
  // calls so the shared shell renders cleanly around the page under test.
  listNotifications: vi.fn().mockResolvedValue({ notifications: [] }),
  getUnreadNotificationCount: vi.fn().mockResolvedValue({ count: 0 }),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  dismissNotification: vi.fn(),
}));

vi.mock("@/lib/useAuth", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", tenant_id: "t1", email: "owner@example.com", full_name: "Test Owner", role: "OWNER" },
    loading: false,
    error: null,
  }),
}));

import CustomersPage from "@/app/customers/page";

describe("CustomersPage (representative CRUD page)", () => {
  beforeEach(() => {
    searchCustomersMock.mockReset();
  });

  it("shows the empty state when the tenant has no customers", async () => {
    searchCustomersMock.mockResolvedValue({ customers: [], total: 0 });
    render(<CustomersPage />);

    expect(await screen.findByText("No customers yet.")).toBeInTheDocument();
  });

  it("renders the customer list once loaded", async () => {
    searchCustomersMock.mockResolvedValue({
      customers: [
        {
          id: "c1",
          name: "Acme Plumbing",
          company_name: null,
          email: "billing@acme.test",
          phone: null,
          address: null,
          city: null,
          state: null,
          postal_code: null,
          status: "ACTIVE",
          created_at: new Date().toISOString(),
        },
      ],
      total: 1,
    });

    render(<CustomersPage />);

    expect(await screen.findByText("Acme Plumbing")).toBeInTheDocument();
    expect(screen.getByText("Customers (1)")).toBeInTheDocument();
  });

  it("shows a retryable error banner when the load fails", async () => {
    const { ApiError } = await import("@/lib/api");
    searchCustomersMock.mockRejectedValue(new ApiError(500, "Unable to load customers."));

    render(<CustomersPage />);

    await waitFor(() => expect(screen.getByText("Unable to load customers.")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
