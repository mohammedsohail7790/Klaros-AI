"use client";

import { useState } from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

export default function MarketingHeader() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="klaros-glass sticky top-0 z-40 mx-auto max-w-6xl rounded-b-2xl px-6 py-4">
      <div className="flex items-center justify-between">
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
        <div className="hidden items-center gap-3 sm:flex">
          <Link href="/login" className="klaros-btn-secondary">
            Sign in
          </Link>
          <Link href="/register" className="klaros-btn-primary">
            Create your company
          </Link>
        </div>
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-foreground sm:hidden"
        >
          {menuOpen ? <X className="h-5 w-5" strokeWidth={2} /> : <Menu className="h-5 w-5" strokeWidth={2} />}
        </button>
      </div>
      {menuOpen && (
        <nav className="mt-4 flex flex-col gap-4 border-t border-border pt-4 text-sm font-medium text-muted sm:hidden">
          <Link href="/#features" onClick={() => setMenuOpen(false)} className="transition-colors hover:text-foreground">
            Features
          </Link>
          <Link href="/pricing" onClick={() => setMenuOpen(false)} className="transition-colors hover:text-foreground">
            Pricing
          </Link>
          <Link href="/#faq" onClick={() => setMenuOpen(false)} className="transition-colors hover:text-foreground">
            FAQ
          </Link>
          <Link href="/login" onClick={() => setMenuOpen(false)} className="klaros-btn-secondary text-center">
            Sign in
          </Link>
          <Link href="/register" onClick={() => setMenuOpen(false)} className="klaros-btn-primary text-center">
            Create your company
          </Link>
        </nav>
      )}
    </header>
  );
}
