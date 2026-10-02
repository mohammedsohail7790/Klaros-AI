import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BusinessMap } from "@/components/business/BusinessMap";
import { StageTracker } from "@/components/business/StageTracker";
import { StatusPill } from "@/components/business/StatusPill";
import type { BusinessMap as MapData } from "@/lib/api";

const MAP: MapData = {
  lanes: ["Customers", "Front door", "Klaros", "Operations", "Money & growth", "Systems & partners"],
  nodes: [
    { id: "actor:customers", kind: "actor", label: "Your customers", sublabel: null, lane: 0, state: "READY", why: "Who you serve", route: null, planned: false },
    { id: "core:klaros", kind: "core", label: "Klaros", sublabel: "Business brain", lane: 2, state: "READY", why: "Holds the record", route: null, planned: false },
    { id: "cap:storefront", kind: "capability", label: "Online storefront", sublabel: null, lane: 1, state: "PLANNED", why: "A shop", route: null, planned: true },
    { id: "workforce:ai", kind: "workforce", label: "AI workforce", sublabel: "Halla · external platform", lane: 1, state: "NOT_CONNECTED", why: "None connected", route: "/workforce", planned: true },
  ],
  edges: [
    { source: "actor:customers", target: "cap:storefront", kind: "reaches you via" },
    { source: "cap:storefront", target: "core:klaros", kind: "sends data" },
  ],
};

describe("StatusPill (full vocabulary)", () => {
  it("renders distinct, plain-language labels for the new states", () => {
    const { rerender } = render(<StatusPill state="AVAILABLE" />);
    expect(screen.getByText("Available — not connected")).toBeInTheDocument();
    rerender(<StatusPill state="INTEGRATION_REQUIRED" />);
    expect(screen.getByText("Integration required")).toBeInTheDocument();
    rerender(<StatusPill state="NOT_READY" />);
    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
  });
});

describe("StatusPill", () => {
  it("only says Connected for CONNECTED", () => {
    const { rerender } = render(<StatusPill state="CONNECTED" />);
    expect(screen.getByText("Connected")).toBeInTheDocument();
    for (const s of ["READY", "CONFIGURATION_REQUIRED", "NOT_CONNECTED", "PLANNED"] as const) {
      rerender(<StatusPill state={s} />);
      expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    }
  });
  it("renders nothing without a state", () => {
    const { container } = render(<StatusPill state={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("BusinessMap", () => {
  it("renders every lane and node from the data", () => {
    render(<BusinessMap map={MAP} />);
    for (const l of MAP.lanes) expect(screen.getByRole("region", { name: l })).toBeInTheDocument();
    expect(screen.getAllByText("Online storefront").length).toBeGreaterThan(0);
    const front = screen.getByRole("region", { name: "Front door" });
    expect(front).toHaveTextContent("Planned");
    expect(front).toHaveTextContent("Not connected");
    // The legend explains every status, including the "available" vs "integration required" distinction.
    const legend = screen.getByRole("group", { name: "Legend" });
    expect(legend).toHaveTextContent("Available — not connected");
    expect(legend).toHaveTextContent("Integration required");
  });
  it("shows what a selected node receives from and sends to", () => {
    render(<BusinessMap map={MAP} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Online storefront/ })[0]);
    expect(screen.getByText("Receives from")).toBeInTheDocument();
    expect(screen.getAllByText(/Your customers/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/sends data/).length).toBeGreaterThan(0);
  });
  it("links to the real screen only for nodes that have one", () => {
    render(<BusinessMap map={MAP} />);
    fireEvent.click(screen.getByRole("button", { name: /AI workforce/ }));
    expect(screen.getByRole("link", { name: "Open AI workforce" })).toHaveAttribute("href", "/workforce");
    fireEvent.click(screen.getAllByRole("button", { name: /Online storefront/ })[0]);
    expect(screen.queryByRole("link", { name: /^Open/ })).not.toBeInTheDocument();
  });
});

describe("StageTracker", () => {
  const stages = [
    { key: "idea", label: "Idea", state: "done" as const, route: "/business" },
    { key: "map", label: "Business Map", state: "current" as const, route: "/business/map" },
    { key: "website", label: "Website", state: "todo" as const, route: "/website" },
  ];
  it("links only completed and current stages — never a stage not reached", () => {
    render(<StageTracker stages={stages} />);
    expect(screen.getByRole("link", { name: /Idea/ })).toHaveAttribute("href", "/business");
    expect(screen.getByRole("link", { name: /Business Map/ })).toHaveAttribute("href", "/business/map");
    expect(screen.queryByRole("link", { name: /Website/ })).not.toBeInTheDocument();
  });
});
