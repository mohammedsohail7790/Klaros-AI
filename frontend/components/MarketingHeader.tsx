"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, Menu, X } from "lucide-react";
import { Logo } from "@/components/Logo";
import { BuildCta } from "@/components/marketing/BuildCta";

const LINKS = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#product", label: "What's inside" },
  { href: "/pricing", label: "Pricing" },
  { href: "/#faq", label: "FAQ" },
];

export default function MarketingHeader() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`sticky top-0 z-40 w-full transition-[background-color,border-color,backdrop-filter] duration-500 ease-apple ${
        scrolled || menuOpen ? "border-b border-border/70 bg-background/75 backdrop-blur-2xl backdrop-saturate-150" : "border-b border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-12 max-w-6xl items-center justify-between px-5 sm:h-14">
        <Logo />
        <nav aria-label="Main" className="hidden items-center gap-7 text-[13px] text-muted sm:flex">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className="transition-colors duration-200 hover:text-foreground">
              {l.label}
            </Link>
          ))}
          <a href="https://hallaai.com" target="_blank" rel="noopener" className="inline-flex items-center gap-1 transition-colors duration-200 hover:text-foreground">
            Halla AI
            <ExternalLink className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
          </a>
        </nav>
        <div className="hidden items-center gap-4 sm:flex">
          <Link href="/login" className="text-[13px] text-muted transition-colors duration-200 hover:text-foreground">
            Sign in
          </Link>
          <BuildCta size="md">Build My Business</BuildCta>
        </div>
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
          className="flex h-9 w-9 items-center justify-center rounded-full text-foreground sm:hidden"
        >
          {menuOpen ? <X className="h-5 w-5" strokeWidth={1.75} /> : <Menu className="h-5 w-5" strokeWidth={1.75} />}
        </button>
      </div>
      <AnimatePresence>
        {menuOpen && (
          <motion.nav
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.28, 0.11, 0.32, 1] }}
            className="overflow-hidden sm:hidden"
            aria-label="Mobile"
          >
            <div className="flex flex-col gap-1 px-5 pb-6 pt-2 text-[22px] font-semibold tracking-tight text-foreground">
              {LINKS.map((l) => (
                <Link key={l.href} href={l.href} onClick={() => setMenuOpen(false)} className="border-b border-border/70 py-3">
                  {l.label}
                </Link>
              ))}
              <Link href="/login" onClick={() => setMenuOpen(false)} className="border-b border-border/70 py-3">
                Sign in
              </Link>
              <BuildCta size="md" className="mt-5 w-full">
                Build My Business
              </BuildCta>
            </div>
          </motion.nav>
        )}
      </AnimatePresence>
    </header>
  );
}
