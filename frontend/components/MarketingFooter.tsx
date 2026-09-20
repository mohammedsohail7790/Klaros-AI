import Link from "next/link";

export default function MarketingFooter() {
  return (
    <footer className="border-t border-border px-6 py-12">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-6 sm:flex-row">
        <div>
          <div className="font-display text-lg italic text-foreground">Klaros AI</div>
          <p className="mt-1 text-xs text-muted-foreground">
            © {new Date().getFullYear()} Klaros AI. The AI operating system for the one-person company.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Part of the{" "}
            <a href="https://hallaai.com" target="_blank" rel="noopener" className="underline hover:text-foreground">
              Halla AI
            </a>{" "}
            family — alongside AI Consultancy and the AI Receptionist.
          </p>
        </div>
        <nav className="flex items-center gap-6 text-sm text-muted">
          <Link href="/#features" className="transition-colors hover:text-foreground">
            Features
          </Link>
          <Link href="/pricing" className="transition-colors hover:text-foreground">
            Pricing
          </Link>
          <Link href="/#faq" className="transition-colors hover:text-foreground">
            FAQ
          </Link>
          <Link href="/login" className="transition-colors hover:text-foreground">
            Sign in
          </Link>
        </nav>
      </div>
    </footer>
  );
}
