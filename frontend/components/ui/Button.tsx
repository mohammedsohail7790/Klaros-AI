import { ButtonHTMLAttributes, forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-200 ease-out disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none disabled:hover:translate-y-0",
  {
    variants: {
      variant: {
        primary:
          "bg-accent text-accent-foreground shadow-card hover:-translate-y-px hover:bg-accent-hover hover:shadow-glow active:translate-y-0 active:shadow-card",
        secondary:
          "border border-border bg-surface text-foreground shadow-card hover:-translate-y-px hover:border-border-strong hover:bg-surface-muted hover:shadow-raised active:translate-y-0 active:shadow-card",
        ghost: "text-muted hover:bg-surface-muted hover:text-foreground",
        danger:
          "bg-danger text-white shadow-card hover:-translate-y-px hover:opacity-90 hover:shadow-[0_10px_28px_-6px_rgb(173_59_44_/_0.38)] active:translate-y-0 active:shadow-card",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9 px-4",
        lg: "h-11 px-6 text-base",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  )
);
Button.displayName = "Button";

export { Button, buttonVariants };
