"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * The marketing site's primary call to action. It always lands in the real
 * product: signed-in visitors go straight to "What are you building?"
 * (/business); everyone else goes to sign-up, and sign-up/sign-in both
 * continue to /business. The decision is made on the client because the
 * session token lives in sessionStorage.
 */
export function BuildCta({
  children = "Build My Business",
  className,
  size = "lg",
  variant = "primary",
}: {
  children?: React.ReactNode;
  className?: string;
  size?: "md" | "lg";
  variant?: "primary" | "secondary";
}) {
  const [href, setHref] = useState("/register");
  useEffect(() => {
    try {
      if (sessionStorage.getItem("klaros_access_token")) setHref("/business");
    } catch {
      // sessionStorage unavailable — sign-up is the safe default.
    }
  }, []);
  return (
    <Link
      href={href}
      className={cn(
        variant === "primary" ? "klaros-btn-primary" : "klaros-btn-secondary",
        "inline-flex items-center justify-center gap-2",
        size === "lg" && "px-6 py-3 text-base",
        className
      )}
    >
      {children}
      <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
    </Link>
  );
}
