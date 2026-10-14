import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
vi.mock("@/lib/api", () => ({ ApiError: class extends Error {}, getAIProcessing: (...a: unknown[]) => get(...a), updateAIProcessing: (...a: unknown[]) => put(...a) }));
import { AIProcessingCard } from "@/components/AIProcessingCard";

describe("AIProcessingCard", () => {
  beforeEach(() => {
    get.mockReset().mockResolvedValue({ external_providers: [] });
    put.mockReset().mockResolvedValue({ external_providers: ["openai"] });
  });

  it("allows a provider only after the owner confirms a lawful basis", async () => {
    render(<AIProcessingCard token="t" />);
    expect(await screen.findByText(/No external AI provider is allowed/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("AI provider name"), { target: { value: "OpenAI" } });
    const allow = screen.getByRole("button", { name: "Allow provider" });
    expect(allow).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(allow);
    await waitFor(() => expect(put).toHaveBeenCalledWith("t", { external_providers: ["openai"], acknowledged: true }));
    expect(await screen.findByText(/Allowed for your business's data: openai/)).toBeInTheDocument();
  });

  it("lets the owner stop allowing providers", async () => {
    get.mockResolvedValue({ external_providers: ["openai"] });
    put.mockResolvedValue({ external_providers: [] });
    render(<AIProcessingCard token="t" />);
    fireEvent.click(await screen.findByRole("button", { name: "Stop allowing all providers" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("t", { external_providers: [], acknowledged: true }));
  });
});
