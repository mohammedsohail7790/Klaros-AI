import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const status = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  getBillingStatus: (...a: unknown[]) => status(...a),
  createBillingCheckout: vi.fn(),
  createBillingPortalSession: vi.fn(),
}));
import Page from "@/app/settings/billing/page";

const base = { plan: "growth", billing_status: "trialing", trial_ends_at: new Date(Date.now() - 3 * 86_400_000).toISOString(), current_period_end: null, ai_usage_this_month: 12, ai_usage_limit: null };

beforeEach(() => status.mockReset());

describe("Billing settings", () => {
  it("says nothing is owed, and offers no plans, when this Klaros is not billed", async () => {
    status.mockResolvedValue({ ...base, enforced: false });
    render(<Page />);
    expect(await screen.findByText("Included, nothing to pay")).toBeInTheDocument();
    expect(screen.queryByText(/free trial has ended/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Subscribe to/)).not.toBeInTheDocument();
  });

  it("still shows the ended trial and the plans when billing is enforced (or the backend is older)", async () => {
    status.mockResolvedValue(base);
    render(<Page />);
    expect(await screen.findByText(/free trial has ended/i)).toBeInTheDocument();
    expect(screen.getByText("Subscribe to Growth")).toBeInTheDocument();
  });
});
