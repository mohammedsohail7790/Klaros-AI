import Link from "next/link";
import GradientBackdrop from "@/components/GradientBackdrop";

/**
 * The shared frame for sign-in, sign-up and password recovery: a brand panel on large screens and a focused card everywhere. Keeping it in one place means
 * the four auth pages cannot drift apart.
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
    <main className="relative flex min-h-screen overflow-hidden">
      <GradientBackdrop />

      <div className="relative hidden w-[42%] flex-col justify-between overflow-hidden border-r border-border/60 px-12 py-12 lg:flex">
        <div
          className="absolute inset-0 -z-10"
          style={{
            background:
              "linear-gradient(160deg, rgb(var(--color-accent-2) / 0.92) 0%, rgb(var(--color-accent-2)) 55%, rgb(var(--color-accent-2) / 0.85) 100%)",
          }}
        />
        <div
          className="absolute -bottom-24 -left-24 h-96 w-96 rounded-full opacity-30 blur-3xl"
          style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
        />
        <Link href="/" className="font-display block text-xl italic text-white">
          Klaros AI
        </Link>
        <div className="max-w-sm">
          <p className="font-display text-3xl leading-snug text-white">{headline}</p>
          {points && (
            <ul className="mt-8 space-y-4">
              {points.map((point) => (
                <li key={point} className="flex items-start gap-3 text-sm text-white/80">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[rgb(var(--color-accent))]" />
                  {point}
                </li>
              ))}
            </ul>
          )}
        </div>
        <p className="text-xs text-white/50">© {new Date().getFullYear()} Klaros AI</p>
      </div>

      <div className="flex w-full flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground lg:hidden">
            Klaros AI
          </Link>
          <div className="klaros-glass rounded-2xl p-7 shadow-xl shadow-black/5">
            <h1 className="font-display text-2xl text-foreground">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
            <div className="mt-6">{children}</div>
          </div>
          {footer && <div className="mt-6 text-center text-sm text-muted">{footer}</div>}
        </div>
      </div>
    </main>
  );
}
