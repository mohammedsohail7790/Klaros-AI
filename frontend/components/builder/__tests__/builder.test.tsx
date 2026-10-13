import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { BuilderRun } from "@/lib/api";
import ChatPanel from "@/components/builder/ChatPanel";
import PreviewPane from "@/components/builder/PreviewPane";
import { routeDir, timeAgo } from "@/components/builder/labels";

const run = (over: Partial<BuilderRun> = {}): BuilderRun => ({
  id: "r1", project_id: "p1", kind: "create", state: "generating", source: null, progress: [{ at: "2026-10-13T10:00:00Z", state: "generating", message: "Writing the project files for 6 routes…" }],
  error: null, validation: null, usage: {}, repair_attempts: 0, cancel_requested: false, result_version_id: null, started_at: null, finished_at: null, ...over,
});

describe("PreviewPane", () => {
  const routes = [
    { path: "/", title: "Home", nav: true, detail: false },
    { path: "/pricing", title: "Pricing", nav: true, detail: false },
    { path: "/properties/[slug]", title: "Property", nav: false, detail: true },
  ];

  it("shows the project in a sandbox that can never touch Klaros' own session", () => {
    render(<PreviewPane previewUrl="https://api.example.com/api/v1/builder-preview/tok/" routes={routes} loading={false} onRefresh={() => {}} building={false} error={null} />);
    const frame = screen.getByTitle("Project preview") as HTMLIFrameElement;
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox).toContain("allow-scripts");
    expect(sandbox).not.toContain("allow-same-origin");
    expect(sandbox).not.toContain("allow-top-navigation");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.getAttribute("src")).toMatch(/^https:\/\/api\.example\.com\/api\/v1\/builder-preview\/tok\/\?t=/);
  });

  it("lists real pages only (detail templates are not pages) and loads the chosen one", () => {
    render(<PreviewPane previewUrl="https://x.test/p/" routes={routes} loading={false} onRefresh={() => {}} building={false} error={null} />);
    expect(screen.queryByRole("option", { name: /Property/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Page"), { target: { value: "/pricing" } });
    expect((screen.getByTitle("Project preview") as HTMLIFrameElement).getAttribute("src")).toContain("https://x.test/p/pricing/");
  });

  it("is honest while building and when there is nothing yet", () => {
    const { rerender } = render(<PreviewPane previewUrl={null} routes={[]} loading={false} onRefresh={() => {}} building={true} error={null} />);
    expect(screen.getByText(/first version is being built/i)).toBeInTheDocument();
    rerender(<PreviewPane previewUrl="https://x.test/p/" routes={routes} loading={false} onRefresh={() => {}} building={true} error={null} />);
    expect(screen.getByText(/seeing the last working version/i)).toBeInTheDocument();
    rerender(<PreviewPane previewUrl={null} routes={[]} loading={false} onRefresh={() => {}} building={false} error="Preview unavailable" />);
    expect(screen.getByText("Preview unavailable")).toBeInTheDocument();
  });

  it("refreshes on request", () => {
    const onRefresh = vi.fn();
    render(<PreviewPane previewUrl="https://x.test/p/" routes={routes} loading={false} onRefresh={onRefresh} building={false} error={null} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    expect(onRefresh).toHaveBeenCalled();
  });
});

describe("ChatPanel", () => {
  it("shows real progress lines and blocks new requests while a run is active, and lets you cancel", () => {
    const onCancel = vi.fn();
    render(<ChatPanel messages={[]} run={run()} onSend={async () => {}} onCancel={onCancel} sending={false} />);
    expect(screen.getByText("Writing the project files for 6 routes…")).toBeInTheDocument();
    expect(screen.getByLabelText("Ask for a change")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("sends a request when idle", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<ChatPanel messages={[]} run={run({ state: "ready", progress: [] })} onSend={onSend} onCancel={() => {}} sending={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a pricing page" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await vi.waitFor(() => expect(onSend).toHaveBeenCalledWith("Add a pricing page"));
  });

  it("reports a failure without pretending it worked", () => {
    render(<ChatPanel messages={[]} run={run({ state: "failed", error: "index.html: inline scripts are not allowed" })} onSend={async () => {}} onCancel={() => {}} sending={false} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/inline scripts are not allowed/)).toBeInTheDocument();
  });

  it("says plainly when no AI model was used", () => {
    render(<ChatPanel messages={[]} run={run({ state: "ready", source: "deterministic" })} onSend={async () => {}} onCancel={() => {}} sending={false} />);
    expect(screen.getByText(/no AI model was used/i)).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("maps routes to preview folders", () => {
    expect(routeDir("/")).toBe("");
    expect(routeDir("/pricing")).toBe("pricing/");
    expect(routeDir("/blog/archive")).toBe("blog/archive/");
  });
  it("treats zone-less timestamps as UTC", () => {
    const t = new Date(Date.now() - 5 * 60_000).toISOString().replace("Z", "");
    expect(timeAgo(t)).toBe("5 min ago");
    expect(timeAgo(null)).toBe("");
  });
});
