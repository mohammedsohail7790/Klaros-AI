import { CheckCircle2, CircleDashed, Settings2 } from "lucide-react";

/**
 * An illustration of the product's Business Map. It is a picture, not live
 * data — it is labelled as an example and uses the same status vocabulary
 * (Ready / Planned) the real screens use.
 */
export function BuilderPreview() {
  const nodes = [
    { label: "Customers", sub: "Who you serve", tone: "plain" },
    { label: "Website", sub: "Front door", tone: "ready" },
    { label: "Klaros", sub: "Business brain", tone: "core" },
    { label: "CRM & scheduling", sub: "Operations", tone: "ready" },
    { label: "Supplier link", sub: "Partners", tone: "planned" },
  ] as const;
  return (
    <figure className="mx-auto mt-14 max-w-4xl" aria-label="Illustration of a Klaros Business Map">
      <div className="overflow-hidden rounded-2xl border border-border bg-surface shadow-popover">
        <div className="flex items-center gap-2 border-b border-border bg-surface-muted px-4 py-2.5 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">How your business operates</span>
          <span className="ml-auto rounded-full border border-border px-2 py-0.5">Example</span>
        </div>
        <div className="grid grid-cols-1 gap-3 p-5 sm:grid-cols-5 sm:items-center">
          {nodes.map((n, i) => (
            <div key={n.label} className="relative">
              <div
                className={`rounded-xl border p-3 text-left ${
                  n.tone === "core" ? "border-accent bg-accent-soft/70" : n.tone === "planned" ? "border-dashed border-border-strong" : "border-border bg-surface"
                }`}
              >
                <div className="text-sm font-medium text-foreground">{n.label}</div>
                <div className="text-xs text-muted">{n.sub}</div>
                {n.tone === "ready" && (
                  <span className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-success">
                    <CheckCircle2 className="h-3 w-3" aria-hidden="true" /> Ready
                  </span>
                )}
                {n.tone === "planned" && (
                  <span className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-muted">
                    <CircleDashed className="h-3 w-3" aria-hidden="true" /> Planned
                  </span>
                )}
                {n.tone === "core" && (
                  <span className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-accent-hover">
                    <Settings2 className="h-3 w-3" aria-hidden="true" /> Connects it all
                  </span>
                )}
              </div>
              {i < nodes.length - 1 && (
                <span aria-hidden="true" className="absolute -right-2 top-1/2 hidden h-px w-4 bg-border-strong sm:block" />
              )}
            </div>
          ))}
        </div>
      </div>
      <figcaption className="mt-3 text-center text-xs text-muted-foreground">
        Every business gets its own map, built from what you tell Klaros — pieces that don't exist yet are shown as planned, never as working.
      </figcaption>
    </figure>
  );
}
