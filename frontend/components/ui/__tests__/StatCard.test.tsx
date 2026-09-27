import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Users } from "lucide-react";
import { StatCard } from "@/components/ui/StatCard";

describe("StatCard", () => {
  it("renders the label and value", () => {
    render(<StatCard label="Open jobs" value={12} icon={Users} />);
    expect(screen.getByText("Open jobs")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders an upward trend badge for a positive trend", () => {
    render(<StatCard label="Revenue" value="$4,200" trend={12} />);
    expect(screen.getByText("12%")).toBeInTheDocument();
  });

  it("renders a downward trend badge for a negative trend", () => {
    render(<StatCard label="Churn" value="3" trend={-5} />);
    expect(screen.getByText("5%")).toBeInTheDocument();
  });

  it("omits the trend badge when trend is 0 or absent", () => {
    render(<StatCard label="Neutral" value="0" trend={0} />);
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("renders an explanatory note when provided", () => {
    render(<StatCard label="Cash" value="N/A" note="Insufficient data — connect a bank feed." />);
    expect(screen.getByText(/insufficient data/i)).toBeInTheDocument();
  });
});
