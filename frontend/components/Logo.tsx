import Link from "next/link";
import { cn } from "@/lib/cn";

/** The Klaros mark: a rounded indigo tile with a bold "K" cut by an upward stroke. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-gradient-to-br from-accent to-[rgb(124_58_237)] text-white shadow-sm",
        className
      )}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M4 2.5v11M4 8.5l6.5-6M6.8 7l4.7 6.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

export function Logo({
  href = "/",
  tone = "default",
  className,
}: {
  href?: string;
  tone?: "default" | "light";
  className?: string;
}) {
  return (
    <Link href={href} className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className={cn("text-[17px] font-semibold tracking-tight", tone === "light" ? "text-white" : "text-foreground")}>Klaros</span>
    </Link>
  );
}
