import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const agreements = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class ApiError extends Error {}, getClinicAgreements: (...a: unknown[]) => agreements(...a) }));
import Page from "@/app/medical-tourism/clinic-pipeline/page";

const terms = { commission_basis: null };
const clinic = (name: string, stage: string, country: string, over: object = {}) => ({ provider_id: name, name, country, verification_status: "UNDER_REVIEW", referrals_sent: 0, terms, stage, next_step: `Next for ${name}.`, ...over });

beforeEach(() => {
  agreements.mockReset();
  agreements.mockResolvedValue({
    clinics: [clinic("Acibadem Atakent", "needs_contact", "Türkiye"), clinic("Acibadem Maslak", "ready_to_invite", "Türkiye"), clinic("Artemis Hospital", "ready_to_invite", "India"), clinic("Bumrungrad", "awaiting_reply", "Thailand")],
    with_terms: 0,
    without_terms: 4,
  });
});

describe("Hospital pipeline", () => {
  it("shows each hospital in the column for its stage, with the backend's next step", async () => {
    render(<Page />);
    const ready = within(await screen.findByRole("listitem", { name: "Ready to contact" }));
    expect(ready.getByText("Acibadem Maslak")).toBeInTheDocument();
    expect(ready.getByText("Artemis Hospital")).toBeInTheDocument();
    expect(ready.getByText("Next for Artemis Hospital.")).toBeInTheDocument();
    expect(within(screen.getByRole("listitem", { name: "Waiting for a reply" })).getByText("Bumrungrad")).toBeInTheDocument();
  });

  it("says plainly that nothing can be referred yet and what is blocking it", async () => {
    render(<Page />);
    expect(await screen.findByText("Nothing can be referred yet: no hospital is ready to receive a patient.")).toBeInTheDocument();
    expect(screen.getByText("2 have a contact and are ready to be contacted, 1 is waiting for a reply, and 1 still needs a contact.")).toBeInTheDocument();
  });

  it("filters by country and tags a group that shares a name", async () => {
    render(<Page />);
    await screen.findByText("Artemis Hospital");
    expect(screen.getAllByText("Acibadem group").length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "India" }));
    expect(screen.queryByText("Acibadem Maslak")).not.toBeInTheDocument();
    expect(screen.getByText("Artemis Hospital")).toBeInTheDocument();
  });

  it("shows an error when the data cannot be loaded", async () => {
    agreements.mockRejectedValue(new Error("boom"));
    render(<Page />);
    expect(await screen.findByText("Unable to load the hospital pipeline.")).toBeInTheDocument();
  });
});
