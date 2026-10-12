import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WebsiteSection } from "@/lib/api";
import { COMPONENT_TYPES, SECTION_LABELS, SectionPropsForm, defaultPropsFor } from "@/components/website/SectionEditor";

const section = (component_type: WebsiteSection["component_type"], props: Record<string, unknown>): WebsiteSection => ({ component_type, props, data_source: null });

describe("SectionEditor — v2 blocks", () => {
  it("every block type has a plain-language label and sensible starting content", () => {
    for (const type of COMPONENT_TYPES) {
      expect(SECTION_LABELS[type]).toBeTruthy();
      expect(defaultPropsFor(type)).toBeTypeOf("object");
    }
    expect(COMPONENT_TYPES).toEqual(expect.arrayContaining(["STEPS", "FAQ", "TRUST_BAR"]));
  });

  it("STEPS: edits a step title, adds a step, removes a step", () => {
    const onChange = vi.fn();
    render(<SectionPropsForm section={section("STEPS", { title: "How it works", items: [{ title: "One" }, { title: "Two" }] })} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("One"), { target: { value: "First" } });
    expect(onChange.mock.lastCall![0].props.items).toEqual([{ title: "First" }, { title: "Two" }]);
    fireEvent.click(screen.getByRole("button", { name: "Add step" }));
    expect(onChange.mock.lastCall![0].props.items).toHaveLength(3);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove step" })[0]);
    expect(onChange.mock.lastCall![0].props.items).toEqual([{ title: "Two" }]);
  });

  it("STEPS: no more than eight steps can be added", () => {
    const items = Array.from({ length: 8 }, (_, i) => ({ title: `S${i}` }));
    render(<SectionPropsForm section={section("STEPS", { items })} onChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Add step" })).toBeNull();
  });

  it("FAQ: edits a question and an answer, and removes one", () => {
    const onChange = vi.fn();
    render(<SectionPropsForm section={section("FAQ", { items: [{ question: "Q1", answer: "A1" }] })} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("Q1"), { target: { value: "Is it free?" } });
    expect(onChange.mock.lastCall![0].props.items).toEqual([{ question: "Is it free?", answer: "A1" }]);
    fireEvent.change(screen.getByDisplayValue("A1"), { target: { value: "Ask us." } });
    expect(onChange.mock.lastCall![0].props.items[0].answer).toBe("Ask us.");
    fireEvent.click(screen.getByRole("button", { name: "Remove question" }));
    expect(onChange.mock.lastCall![0].props.items).toEqual([]);
  });

  it("TRUST_BAR: one fact per line, blank lines dropped, at most six", () => {
    const onChange = vi.fn();
    render(<SectionPropsForm section={section("TRUST_BAR", { items: ["A"] })} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("A"), { target: { value: "One\n\n Two \nThree\nFour\nFive\nSix\nSeven" } });
    expect(onChange.mock.lastCall![0].props.items).toEqual(["One", "Two", "Three", "Four", "Five", "Six"]);
  });

  it("HERO: the second button and the side card lines are editable", () => {
    const onChange = vi.fn();
    render(<SectionPropsForm section={section("HERO", { headline: "H", highlights: ["a", "b"] })} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Second button text (optional)"), { target: { value: "Call us" } });
    expect(onChange.mock.lastCall![0].props.secondary_cta_text).toBe("Call us");
    fireEvent.change(screen.getByDisplayValue(/a\s*b/), { target: { value: "x\ny\n\nz" } });
    expect(onChange.mock.lastCall![0].props.highlights).toEqual(["x", "y", "z"]);
  });
});
