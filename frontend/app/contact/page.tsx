"use client";

import { useState } from "react";
import { sendContactMessage } from "@/lib/api";
import { friendlyAuthError } from "@/lib/authErrors";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import GradientBackdrop from "@/components/GradientBackdrop";
import MarketingHeader from "@/components/MarketingHeader";
import MarketingFooter from "@/components/MarketingFooter";

export default function ContactPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [trap, setTrap] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const ready = name.trim() !== "" && email.trim() !== "" && message.trim().length >= 10;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await sendContactMessage({ name: name.trim(), email: email.trim(), message: message.trim(), website: trap || undefined });
      setSent(true);
    } catch (err) {
      setError(friendlyAuthError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen overflow-x-hidden">
      <GradientBackdrop />
      <MarketingHeader />
      <section className="mx-auto max-w-xl px-6 pb-24 pt-16 sm:pt-24">
        <h1 className="font-display text-4xl text-foreground">Talk to us</h1>
        <p className="mt-3 text-base leading-relaxed text-muted">
          Questions about Klaros, a plan that fits your team, or something not working? Send a message and a person will reply by email.
        </p>
        {sent ? (
          <div role="status" className="klaros-card mt-8 p-6 text-sm text-muted">
            <p className="text-foreground">Thanks, your message is on its way.</p>
            <p className="mt-1">We'll reply to {email.trim()}.</p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="klaros-card mt-8 space-y-4 p-6">
            <Field label="Your name">
              <Input required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Email">
              <Input required type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="How can we help?">
              <textarea
                required
                minLength={10}
                maxLength={4000}
                rows={6}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                className="klaros-input min-h-[140px]"
              />
            </Field>
            {/* Honeypot: hidden from people and from assistive tech; a bot that fills it is ignored server-side. */}
            <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
              <label>
                Website
                <input tabIndex={-1} autoComplete="off" value={trap} onChange={(e) => setTrap(e.target.value)} />
              </label>
            </div>
            {error && <p role="alert" className="text-sm text-danger">{error}</p>}
            <Button type="submit" disabled={loading || !ready} className="w-full">
              {loading ? "Sending..." : "Send message"}
            </Button>
            <p className="text-xs text-muted-foreground">Please don't include passwords or patient details in this form.</p>
          </form>
        )}
      </section>
      <MarketingFooter />
    </main>
  );
}
