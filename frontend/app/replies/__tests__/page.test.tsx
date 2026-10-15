import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("@/lib/useAuth", () => ({ useAuth: () => ({ token: "t", user: { id: "u" }, loading: false }) }));
vi.mock("@/components/AppShell", () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/lib/api", () => ({ ApiError: class extends Error {}, getInboxReplies: (...a: unknown[]) => get(...a) }));
import RepliesPage from "@/app/replies/page";

const reply = (over: object = {}) => ({ id: "1", entity_type: "lead", entity_id: "L1", about: "Pat Ient", from_address: "pat@example.com", from_name: "Pat", subject: "Re: your enquiry", body_text: "I can travel in March.", body_stored: true, sender_matches: true, attachment_count: 0, received_at: "2026-10-16T10:00:00Z", ...over });

describe("Replies", () => {
  beforeEach(() => get.mockReset());

  it("lists a reply with who it is about, who sent it and what they wrote", async () => {
    get.mockResolvedValue({ replies: [reply()] });
    render(<RepliesPage />);
    expect(await screen.findByText("I can travel in March.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pat Ient" })).toHaveAttribute("href", "/leads/L1");
    expect(screen.getByText(/From Pat <pat@example.com>/)).toBeInTheDocument();
  });

  it("warns when it came from another address, says attachments were not kept, and explains a missing text", async () => {
    get.mockResolvedValue({ replies: [
      reply({ id: "2", sender_matches: false, attachment_count: 2 }),
      reply({ id: "3", body_stored: false, body_text: null, about: "No Consent" }),
    ] });
    render(<RepliesPage />);
    expect(await screen.findByText(/different address than the one we wrote to/)).toBeInTheDocument();
    expect(screen.getByText(/2 attachments not kept/)).toBeInTheDocument();
    expect(screen.getByText(/consent does not yet cover keeping medical information/)).toBeInTheDocument();
  });

  it("shows a clear empty state", async () => {
    get.mockResolvedValue({ replies: [] });
    render(<RepliesPage />);
    expect(await screen.findByText(/No replies yet/)).toBeInTheDocument();
  });
});
