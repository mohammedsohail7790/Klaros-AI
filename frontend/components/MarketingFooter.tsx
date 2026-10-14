import Link from "next/link";
import { Logo } from "@/components/Logo";

export default function MarketingFooter() {
  return (
    <footer className="bg-surface-muted px-6 py-14">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-6 sm:flex-row">
        <div>
          <Logo />
          <p className="mt-3 text-xs text-muted-foreground">
            © {new Date().getFullYear()} Klaros AI. Build and operate your business with AI.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Part of the Halla AI family:{" "}
            <a href="https://hallaai.com/consultancy" target="_blank" rel="noopener" className="underline hover:text-foreground">Consultancy</a>
            {" · "}
            <a href="https://hallaai.com/home" target="_blank" rel="noopener" className="underline hover:text-foreground">AI Receptionist</a>
            {" · "}
            <span>AI Intelligence (Klaros)</span>
          </p>
        </div>
        <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[13px] text-muted">
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
