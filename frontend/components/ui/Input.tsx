import { InputHTMLAttributes, LabelHTMLAttributes, ReactElement, cloneElement, forwardRef, isValidElement, useId } from "react";
import { cn } from "@/lib/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn("klaros-input", className)} {...props} />
  )
);
Input.displayName = "Input";

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("klaros-label", className)} {...props} />;
}

/** A labelled form control. The label is programmatically associated with the control
 * (a generated id, unless the child already has one) so assistive tech announces it. */
export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const generated = useId();
  const child = isValidElement(children) ? (children as ReactElement<{ id?: string }>) : null;
  const id = child?.props.id ?? generated;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {child ? (child.props.id ? child : cloneElement(child, { id })) : children}
    </div>
  );
}
