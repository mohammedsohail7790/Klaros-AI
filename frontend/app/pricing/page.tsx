import Link from "next/link";
import { Check, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import GradientBackdrop from "@/components/GradientBackdrop";
import MarketingHeader from "@/components/MarketingHeader";
import MarketingFooter from "@/components/MarketingFooter";
import { cn } from "@/lib/cn";

const TIERS = [
  {
    name: "Solo",
    price: "$49",
    period: "/mo",
    description: "For a single operator just getting off the ground.",
    features: [
      "Leads, quotes, jobs, invoices",
      "AI Next Action — up to 50 recommendations/mo",
      "Owner Attention Queue",
      "Knowledge layer (brand voice, pricing rules)",
      "1 user",
    ],
    cta: "Start with Solo",
    highlighted: false,
  },
  {
    name: "Growth",
    price: "$129",
    period: "/mo",
    description: "The full operating system, for a business that's actually running.",
    features: [
      "Everything in Solo",
      "Unlimited AI Next Action recommendations",
      "Full Automation Engine",
      "Stripe, Google Calendar, QuickBooks sync",
      "Retention, referrals, marketing tools",
      "Priority support",
    ],
    cta: "Start with Growth",
    highlighted: true,
  },
  {
    name: "Scale",
    price: "Contact us",
    period: "",
    description: "For teams growing past one person, or with custom needs.",
    features: [
      "Everything in Growth",
      "Multiple users & roles",
      "Custom automation policies",
      "Dedicated onboarding",
      "Direct line to support",
    ],
    cta: "Get started",
    highlighted: false,
  },
];

export default function PricingPage() {
  return (
    <main className="min-h-screen overflow-x-hidden">
      <GradientBackdrop />
      <MarketingHeader />

      <section className="mx-auto max-w-3xl px-6 pb-8 pt-16 text-center sm:pt-24">
        <h1 className="font-display text-4xl text-foreground sm:text-5xl">
          Simple pricing, <span className="italic text-accent">built to earn its keep.</span>
        </h1>
        <p className="mx-auto mt-5 max-w-xl text-balance text-lg leading-relaxed text-muted">
          No per-seat games — Klaros is priced for a business run by one person, not a
          sales team you have to negotiate with.
        </p>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-24 pt-8">
        <div className="grid gap-6 lg:grid-cols-3">
          {TIERS.map((tier) => (
            <div
              key={tier.name}
              className={cn(
                "relative flex flex-col rounded-2xl p-8",
                tier.highlighted
                  ? "klaros-glass border-accent/30 shadow-popover lg:-translate-y-3"
                  : "klaros-card"
              )}
            >
              {tier.highlighted && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-foreground">
                  Most popular
                </div>
              )}
              <h2 className="font-display text-2xl text-foreground">{tier.name}</h2>
              <p className="mt-1.5 text-sm text-muted">{tier.description}</p>
              <div className="mt-6 flex items-baseline gap-1">
                <span className="font-display text-4xl text-foreground">{tier.price}</span>
                {tier.period && <span className="text-sm text-muted-foreground">{tier.period}</span>}
              </div>
              <ul className="mt-6 flex-1 space-y-3">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-start gap-2 text-sm text-muted">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent" strokeWidth={2} />
                    {f}
                  </li>
                ))}
              </ul>
              <Link href="/register" className="mt-8">
                <Button
                  size="lg"
                  variant={tier.highlighted ? "primary" : "secondary"}
                  className="w-full gap-2"
                >
                  {tier.cta}
                  <ArrowRight className="h-4 w-4" strokeWidth={2} />
                </Button>
              </Link>
            </div>
          ))}
        </div>
        <p className="mx-auto mt-4 max-w-xl text-center text-xs text-muted-foreground">
          Every plan starts with a real 14-day free trial — full access, no card required.
        </p>
        <p className="mx-auto mt-2 max-w-xl text-center text-xs text-muted-foreground">
          Every plan includes governed AI execution, a full audit trail, and honest
          integration status — nothing fabricated, nothing hidden.
        </p>
      </section>

      <MarketingFooter />
    </main>
  );
}
