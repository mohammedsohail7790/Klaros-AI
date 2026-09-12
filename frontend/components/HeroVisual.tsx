import { CheckCircle2, TrendingUp, Sparkles } from "lucide-react";

export default function HeroVisual() {
  return (
    <div className="klaros-perspective relative mx-auto mt-16 max-w-4xl">
      {/* Layered blurred gradient orbs — the "designed depth" backdrop */}
      <div
        aria-hidden
        className="absolute -top-24 left-1/4 h-72 w-72 -translate-x-1/2 rounded-full opacity-[0.22] blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
      <div
        aria-hidden
        className="absolute -top-10 right-1/4 h-64 w-64 translate-x-1/2 rounded-full opacity-[0.18] blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent-2)) 0%, transparent 70%)" }}
      />

      {/* The tilted "product window" — rotation on the outer element, the
          float animation on the inner one, since a CSS animation's own
          `transform` keyframes fully replace an inline transform rather
          than composing with it. */}
      <div
        className="relative mx-auto"
        style={{ transform: "rotateX(8deg) rotateY(-10deg) rotateZ(1deg)", transformStyle: "preserve-3d" }}
      >
        <div className="klaros-float overflow-hidden rounded-2xl border border-border bg-surface shadow-popover">
          <div className="flex items-center gap-1.5 border-b border-border bg-surface-muted px-4 py-2.5">
            <span className="h-2.5 w-2.5 rounded-full bg-red-300" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
            <span className="ml-3 rounded-md bg-surface px-2.5 py-0.5 text-[11px] text-muted-foreground">
              meetklaros.com/dashboard
            </span>
          </div>
          <div className="flex">
            <div className="hidden w-32 shrink-0 space-y-2 border-r border-border bg-surface-muted p-3 sm:block">
              <div className="h-2 w-16 rounded bg-accent-soft" />
              <div className="mt-3 space-y-1.5">
                {[70, 50, 60, 40].map((w, i) => (
                  <div
                    key={i}
                    className={`h-1.5 rounded-full ${i === 0 ? "bg-accent" : "bg-border-strong"}`}
                    style={{ width: `${w}%` }}
                  />
                ))}
              </div>
            </div>
            <div className="flex-1 space-y-3 p-4">
              <div className="grid grid-cols-3 gap-2.5">
                {[
                  { label: "Awaiting approval", value: "2", icon: Sparkles },
                  { label: "Automatic today", value: "14", icon: CheckCircle2 },
                  { label: "Pipeline", value: "+18%", icon: TrendingUp },
                ].map((stat) => (
                  <div key={stat.label} className="klaros-card p-3">
                    <stat.icon className="h-3.5 w-3.5 text-accent" strokeWidth={2} />
                    <div className="mt-1.5 text-sm font-semibold text-foreground">{stat.value}</div>
                    <div className="text-[10px] text-muted-foreground">{stat.label}</div>
                  </div>
                ))}
              </div>
              <div className="klaros-card space-y-2 p-3">
                <div className="h-2 w-24 rounded bg-border-strong" />
                <div className="flex items-end gap-1.5 pt-1">
                  {[40, 65, 50, 80, 60, 95, 70].map((h, i) => (
                    <div
                      key={i}
                      className="w-full rounded-t-sm bg-accent-soft"
                      style={{ height: `${h * 0.4}px` }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
        {/* Floating glass badges — real frosted-glass panels (backdrop-blur
            over the gradient orbs behind), not a flat card, since these sit
            on open atmosphere rather than over dense data. */}
        <div className="klaros-glass absolute -right-6 -top-6 hidden items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-medium text-foreground sm:flex">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          AI provider connected
        </div>
        <div className="klaros-glass absolute -bottom-5 -left-6 hidden items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-medium text-foreground sm:flex">
          <TrendingUp className="h-3.5 w-3.5 text-accent" strokeWidth={2} />
          +18% pipeline this week
        </div>
      </div>
    </div>
  );
}
