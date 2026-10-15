import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
vi.mock("@/lib/api", () => ({ listLeadNotes: (...a: unknown[]) => list(...a) }));
import { LeadNotes } from "@/components/business/LeadNotes";

describe("LeadNotes", () => {
  beforeEach(() => list.mockReset());

  it("shows what the crew and the team wrote, newest first, and says who wrote each", async () => {
    list.mockResolvedValue({ notes: [
      { id: "1", author_type: "AI", author_id: null, body: "Qualified. Next step: send availability.", created_at: "2026-10-15T06:40:00Z" },
      { id: "2", author_type: "USER", author_id: "u", body: "Called, no answer.", created_at: "2026-10-15T06:30:00Z" },
    ] });
    render(<LeadNotes token="t" leadId="L1" />);
    expect(await screen.findByText("Qualified. Next step: send availability.")).toBeInTheDocument();
    expect(screen.getByText(/AI crew/)).toBeInTheDocument();
    expect(screen.getByText(/Team ·/)).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith("t", "L1");
  });

  it("stays hidden when there are no notes", async () => {
    list.mockResolvedValue({ notes: [] });
    const { container } = render(<LeadNotes token="t" leadId="L1" />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
