import { Logo } from "@/components/Logo";
import { Reveal } from "@/components/marketing/motion";

/**
 * The shared frame for sign-in, sign-up and password recovery: one calm, centred column.
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
    <main className="relative flex min-h-screen flex-col items-center overflow-hidden bg-background px-6 pb-16 pt-10 sm:pt-16">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-0 h-[480px]"
        style={{ background: "radial-gradient(50% 60% at 50% 0%, rgb(var(--color-accent) / 0.09) 0%, transparent 70%)" }}
      />
      <Reveal y={12} className="relative flex flex-col items-center">
        <Logo />
      </Reveal>

      <Reveal delay={0.06} className="relative mt-10 w-full max-w-[400px] sm:mt-14">
        <h1 className="text-center font-display text-[2rem] font-semibold leading-tight tracking-[-0.025em] text-foreground sm:text-[2.5rem]">{title}</h1>
        {subtitle && <p className="mt-2 text-center text-[17px] leading-snug text-muted">{subtitle}</p>}
        <div className="mt-9">{children}</div>
        {footer && <div className="mt-8 text-center text-[15px] text-muted">{footer}</div>}
      </Reveal>

      <Reveal delay={0.14} className="relative mt-16 w-full max-w-[400px]">
        <div className="border-t border-border pt-8 text-center">
          <h2 className="text-[15px] font-semibold tracking-tight text-foreground">{headline}</h2>
          {points && (
            <ul className="mt-3 space-y-1.5 text-[14px] leading-snug text-muted">
              {points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          )}
          <p className="mt-8 text-xs text-muted-foreground">© {new Date().getFullYear()} Klaros AI</p>
        </div>
      </Reveal>
    </main>
  );
}
