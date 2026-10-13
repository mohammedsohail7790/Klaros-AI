import Link from "next/link";
import GradientBackdrop from "@/components/GradientBackdrop";

export default function NotFound() {
  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-6">
      <GradientBackdrop />
      <div className="max-w-md text-center">
        <p className="font-display text-6xl text-accent">404</p>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight text-foreground">We can't find that page</h1>
        <p className="mt-2 text-sm text-muted">The link may be old or mistyped. Here are some places to go instead.</p>
        <div className="mt-6 flex flex-wrap justify-center gap-3 text-sm">
          <Link href="/" className="klaros-card px-4 py-2 hover:bg-surface-muted">
            Home
          </Link>
          <Link href="/business" className="klaros-card px-4 py-2 hover:bg-surface-muted">
            Your workspace
          </Link>
          <Link href="/contact" className="klaros-card px-4 py-2 hover:bg-surface-muted">
            Contact us
          </Link>
        </div>
      </div>
    </main>
  );
}
