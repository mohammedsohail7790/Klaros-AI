import Link from "next/link";

export default function MarketingHeader() {
  return (
    <header className="klaros-glass sticky top-0 z-40 mx-auto flex max-w-6xl items-center justify-between rounded-b-2xl px-6 py-4">
      <Link href="/" className="font-display text-xl italic tracking-tight text-foreground">
        Klaros
      </Link>
      <nav className="hidden items-center gap-8 text-sm font-medium text-muted sm:flex">
        <Link href="/#features" className="transition-colors hover:text-foreground">
          Features
        </Link>
        <Link href="/pricing" className="transition-colors hover:text-foreground">
          Pricing
        </Link>
        <Link href="/#faq" className="transition-colors hover:text-foreground">
          FAQ
        </Link>
      </nav>
      <div className="flex items-center gap-3">
        <Link href="/login" className="klaros-btn-secondary">
          Sign in
        </Link>
        <Link href="/register" className="klaros-btn-primary">
          Create your company
        </Link>
      </div>
    </header>
  );
}
