import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/mt/ClinicTerms", () => ({ ClinicTerms: ({ provider }: { provider: { name: string } }) => <div role="dialog">Terms for {provider.name}</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class extends Error {}, getClinicAgreements: (...a: unknown[]) => get(...a) }));
import ClinicAgreementsPage from "@/app/medical-tourism/agreements/page";

const none = { set: false, commission_basis: null, commission_percentage: null, flat_amount: null, currency: null, qualifying_outcome: null, agreement_reference: null };
const row = (name: string, terms: object, sent = 0) => ({ provider_id: name, name, country: "TR", verification_status: "VERIFIED", referrals_sent: sent, terms: { ...none, ...terms } });

describe("Clinic agreements", () => {
  beforeEach(() => get.mockReset());

  it("lists each clinic with what it pays, and says plainly when nothing is agreed", async () => {
    get.mockResolvedValue({ with_terms: 2, without_terms: 1, clinics: [
      row("Alpha", { set: true, commission_basis: "PERCENTAGE", commission_percentage: "15.00", currency: "USD", agreement_reference: "Folder 7" }, 3),
      row("Beta", { set: true, commission_basis: "FLAT", flat_amount: "300.00", currency: "EUR" }, 1),
      row("Gamma", {}),
    ] });
    render(<ClinicAgreementsPage />);
    expect(await screen.findByText("15% of the treatment price (USD)")).toBeInTheDocument();
    expect(screen.getByText("EUR 300 per patient")).toBeInTheDocument();
    expect(screen.getByText("No commission agreed yet")).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 clinics have commission terms/)).toBeInTheDocument();
    expect(screen.getByText(/creates no commission/)).toBeInTheDocument();
    expect(screen.getByText(/Agreement: Folder 7 · 3 referrals sent/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Set terms" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Terms for Gamma");
  });

  it("guides the owner when there are no clinics yet", async () => {
    get.mockResolvedValue({ with_terms: 0, without_terms: 0, clinics: [] });
    render(<ClinicAgreementsPage />);
    expect(await screen.findByText(/No clinics yet/)).toBeInTheDocument();
  });
});
