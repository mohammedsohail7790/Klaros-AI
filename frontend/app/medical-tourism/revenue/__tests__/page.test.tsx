import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const revenue = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class ApiError extends Error {}, getMedicalTourismRevenue: (...a: unknown[]) => revenue(...a) }));
import Page from "@/app/medical-tourism/revenue/page";

const slot = (count = 0, amount = "0.00") => ({ count, amount });
const full = (over: object) => ({ PENDING: slot(), CONFIRMED: slot(), INVOICED: slot(), PAID: slot(), DISPUTED: slot(), CANCELLED: slot(), ...over });

beforeEach(() => { revenue.mockReset(); });

describe("Medical Tourism revenue", () => {
  it("separates potential from earned and paid, per currency, and never adds currencies together", async () => {
    revenue.mockResolvedValue({ by_currency: { EUR: full({ PENDING: slot(1, "400.00") }), USD: full({ PAID: slot(1, "500.00") }) }, definitions: "PENDING is potential value, not revenue." });
    render(<Page />);
    const eur = await screen.findByRole("region", { name: "Revenue in EUR" });
    expect(within(eur).getByText("Potential").closest("tr")).toHaveTextContent("400.00 EUR");
    expect(within(eur).getByText("Paid").closest("tr")).toHaveTextContent("0.00 EUR");
    const usd = screen.getByRole("region", { name: "Revenue in USD" });
    expect(within(usd).getByText("Paid").closest("tr")).toHaveTextContent("500.00 USD");
    expect(screen.getByText(/PENDING is potential value, not revenue/)).toBeInTheDocument();
  });

  it("says plainly when there are no commissions", async () => {
    revenue.mockResolvedValue({ by_currency: {}, definitions: "" });
    render(<Page />);
    expect(await screen.findByText("No commissions recorded yet.")).toBeInTheDocument();
  });
});
