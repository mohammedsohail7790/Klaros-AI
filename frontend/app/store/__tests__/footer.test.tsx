import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StoreFooter } from "@/components/store/Footer";
import { INFO } from "./fixtures";

describe("store footer", () => {
  it("names the company behind the brand, gives only an e-mail address to contact, and no phone number", () => {
    const { container } = render(<StoreFooter info={INFO} categories={["Tea"]} />);
    expect(screen.getByText(/Clean Room LLC\. Clean Room is our brand\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "info@yourcleanroom.com" })).toHaveAttribute("href", "mailto:info@yourcleanroom.com");
    expect(container.textContent).not.toMatch(/\+?\d[\d\s().-]{8,}\d|tel:|phone/i);
  });
});
