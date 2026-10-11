import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Field, Input } from "@/components/ui/Input";

describe("Field", () => {
  it("associates its label with the control so it has an accessible name", () => {
    render(
      <Field label="Email">
        <Input type="email" />
      </Field>
    );
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
  });
  it("keeps an id the control already has", () => {
    render(
      <Field label="Name">
        <Input id="given" />
      </Field>
    );
    expect(screen.getByLabelText("Name")).toHaveAttribute("id", "given");
  });
  it("works with a textarea child and gives distinct ids to multiple fields", () => {
    render(
      <>
        <Field label="One"><textarea /></Field>
        <Field label="Two"><Input /></Field>
      </>
    );
    expect(screen.getByLabelText("One").id).not.toBe(screen.getByLabelText("Two").id);
  });
});
