import Link from "next/link";
import { Logo } from "@/components/Logo";

export default function MarketingFooter() {
  return (
    <footer className="border-t border-border px-6 py-12">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-6 sm:flex-row">
        <div>
          <Logo />
          <p className="mt-3 text-xs text-muted-foreground">
            © {new Date().getFullYear()} Klaros AI. Build and operate your business with AI.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Part of the{" "}
            <a href="https://hallaai.com" target="_blank" rel="noopener" className="underline hover:text-foreground">
              Halla AI
            </a>{" "}
            family — Halla AI is Klaros's separate AI workforce platform.
          </p>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-muted">
          <Link href="/#how-it-works" className="transition-colors hover:text-foreground">
            How it works
          </Link>
          <Link href="/pricing" className="transition-colors hover:text-foreground">
            Pricing
          </Link>
          <Link href="/#faq" className="transition-colors hover:text-foreground">
            FAQ
          </Link>
          <Link href="/contact" className="transition-colors hover:text-foreground">
            Contact
          </Link>
          <Link href="/privacy" className="transition-colors hover:text-foreground">
            Privacy
          </Link>
          <Link href="/terms" className="transition-colors hover:text-foreground">
            Terms
          </Link>
          <Link href="/login" className="transition-colors hover:text-foreground">
            Sign in
          </Link>
        </nav>
      </div>
    </footer>
  );
}
