import { CheckCircle2, Sparkles, Store, Headset, Globe2 } from "lucide-react";
import { Logo } from "@/components/Logo";

/**
 * The shared frame for sign-in, sign-up and password recovery: a brand panel on large screens and a focused form everywhere.
 * Keeping it in one place means the auth pages cannot drift apart.
 */
export default function AuthLayout({
  headline,
  points,
  title,
  subtitle,
  children,
  footer,
}: {
  headline: string;
  points?: string[];
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="flex min-h-screen bg-background">
      <aside className="relative hidden w-[46%] max-w-[640px] flex-col justify-between overflow-hidden bg-accent-2 px-12 py-10 text-white lg:flex">
        <div
          aria-hidden="true"
          className="absolute inset-0"
          style={{
            background:
              "radial-gradient(70% 55% at 0% 0%, rgb(var(--color-accent) / 0.45) 0%, transparent 60%), radial-gradient(60% 50% at 100% 100%, rgb(124 58 237 / 0.35) 0%, transparent 60%)",
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-[0.18]"
          style={{
            backgroundImage: "linear-gradient(rgb(255 255 255 / 0.12) 1px, transparent 1px), linear-gradient(90deg, rgb(255 255 255 / 0.12) 1px, transparent 1px)",
            backgroundSize: "44px 44px",
            maskImage: "radial-gradient(80% 70% at 30% 40%, black 0%, transparent 80%)",
            WebkitMaskImage: "radial-gradient(80% 70% at 30% 40%, black 0%, transparent 80%)",
          }}
        />

        <div className="relative">
          <Logo tone="light" />
        </div>

        <div className="relative max-w-md">
          <p className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.06] px-3 py-1 text-xs font-medium text-white/80">
            <Sparkles className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" /> The AI business builder
          </p>
          <h2 className="mt-5 text-[2.1rem] font-semibold leading-[1.15] tracking-tight">{headline}</h2>

          {points && (
            <ul className="mt-8 space-y-3.5">
              {points.map((point) => (
                <li key={point} className="flex items-start gap-3 text-sm leading-relaxed text-white/75">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent-soft" strokeWidth={2} aria-hidden="true" />
                  {point}
                </li>
              ))}
            </ul>
          )}

          <div aria-hidden="true" className="mt-10 rounded-2xl border border-white/10 bg-white/[0.05] p-4 backdrop-blur">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-white/45">From idea to operating business</p>
            <div className="mt-3 grid grid-cols-3 gap-2.5 text-xs">
              {[
                { icon: Sparkles, label: "Describe it" },
                { icon: Globe2, label: "Launch a site" },
                { icon: Headset, label: "AI team answers" },
              ].map(({ icon: Icon, label }) => (
                <div key={label} className="flex flex-col items-start gap-2 rounded-xl border border-white/10 bg-white/[0.04] p-3">
                  <Icon className="h-4 w-4 text-white/80" strokeWidth={2} />
                  <span className="text-white/75">{label}</span>
                </div>
              ))}
            </div>
            <div className="mt-2.5 flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] p-3 text-xs text-white/70">
              <Store className="h-4 w-4 text-white/80" strokeWidth={2} />
              Your leads, customers and money — in one workspace.
            </div>
          </div>
        </div>

        <p className="relative text-xs text-white/40">© {new Date().getFullYear()} Klaros AI</p>
      </aside>

      <div className="relative flex flex-1 items-center justify-center overflow-hidden px-6 py-12">
        <div aria-hidden="true" className="absolute inset-0 -z-0 lg:hidden" style={{ background: "radial-gradient(60% 40% at 50% 0%, rgb(var(--color-accent) / 0.1) 0%, transparent 70%)" }} />
        <div className="relative w-full max-w-[400px]">
          <div className="mb-8 lg:hidden">
            <Logo />
          </div>
          <h1 className="text-[1.65rem] font-semibold tracking-tight text-foreground">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-muted">{subtitle}</p>}
          <div className="mt-7">{children}</div>
          {footer && <div className="mt-8 border-t border-border pt-6 text-center text-sm text-muted">{footer}</div>}
        </div>
      </div>
    </main>
  );
}
