"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight } from "lucide-react";

/** Key shared with /business, which pre-fills "What are you building?" from it. */
export const PENDING_IDEA_KEY = "klaros_pending_idea";

/**
 * The landing page's real first step: the visitor types their idea here,
 * it is carried (in this browser's sessionStorage only) through sign-up or
 * sign-in, and lands pre-filled in the product's own "What are you
 * building?" screen. Nothing is sent to the server from the marketing site.
 */
export function HeroIdeaForm() {
  const router = useRouter();
  const [idea, setIdea] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    let signedIn = false;
    try {
      if (idea.trim()) sessionStorage.setItem(PENDING_IDEA_KEY, idea.trim());
      signedIn = !!sessionStorage.getItem("klaros_access_token");
    } catch {
      // Storage blocked: the visitor simply re-types it in the app.
    }
    router.push(signedIn ? "/business" : "/register");
  }

  return (
    <form onSubmit={submit} className="klaros-card mx-auto mt-10 max-w-2xl p-2 text-left shadow-raised focus-within:ring-2 focus-within:ring-accent" aria-label="Start building">
      <label htmlFor="hero-idea" className="sr-only">
        What are you building?
      </label>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          id="hero-idea"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          maxLength={500}
          placeholder="What are you building?"
          className="min-w-0 flex-1 rounded-lg bg-transparent px-4 py-3 text-base text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <button type="submit" className="klaros-btn-primary inline-flex items-center justify-center gap-2 px-5 py-3">
          Build My Business
          <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
    </form>
  );
}
