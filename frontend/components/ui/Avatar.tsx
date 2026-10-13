import { cn } from "@/lib/cn";

const TONES = [
  "bg-indigo-100 text-indigo-700",
  "bg-violet-100 text-accent-hover",
  "bg-sky-100 text-sky-700",
  "bg-emerald-100 text-emerald-700",
  "bg-amber-100 text-amber-700",
  "bg-rose-100 text-rose-700",
  "bg-teal-100 text-teal-700",
];

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = Array.from(parts[0])[0] ?? "?";
  const last = parts.length > 1 ? Array.from(parts[parts.length - 1])[0] : "";
  return (first + last).toUpperCase();
}

/** A person's initials on a stable tint (the same name always gets the same colour). */
export function Avatar({ name, size = "md", className }: { name: string; size?: "sm" | "md" | "lg"; className?: string }) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold",
        size === "sm" ? "h-6 w-6 text-xs" : size === "lg" ? "h-11 w-11 text-sm" : "h-8 w-8 text-xs",
        TONES[hash % TONES.length],
        className
      )}
    >
      {initials(name)}
    </span>
  );
}
