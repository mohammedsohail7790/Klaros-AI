import Link from "next/link";
import { Check, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Reveal, Stagger, StaggerItem } from "@/components/marketing/motion";
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
    cta: "Talk to us",
    highlighted: false,
    href: "/contact",
  },
];

export default function PricingPage() {
  return (
    <main className="min-h-screen overflow-x-hidden">
      <MarketingHeader />

      <section className="mx-auto max-w-4xl px-6 pb-10 pt-20 text-center sm:pt-32">
        <Reveal>
        <h1 className="text-balance font-display text-5xl font-semibold leading-[1.04] tracking-[-0.03em] text-foreground sm:text-7xl">
          Simple pricing, <span className="text-accent">built to earn its keep.</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-balance text-xl leading-snug text-muted">
          No per-seat games — Klaros is priced for a business run by one person, not a
          sales team you have to negotiate with.
        </p>
        </Reveal>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-24 pt-8">
        <Stagger className="grid gap-5 lg:grid-cols-3">
          {TIERS.map((tier) => (
            <StaggerItem key={tier.name} className="h-full">
            <div
              className={cn(
                "relative flex h-full flex-col rounded-3xl p-9 transition-transform duration-500 ease-apple hover:-translate-y-1",
                tier.highlighted ? "klaros-dark" : "bg-surface-muted"
              )}
            >
              {tier.highlighted && (
                <div className="absolute right-6 top-6 rounded-full bg-accent px-3 py-1 text-xs font-medium text-accent-foreground">
                  Most popular
                </div>
              )}
              <h2 className="font-display text-3xl font-semibold tracking-tight text-foreground">{tier.name}</h2>
              <p className="mt-2 text-[15px] leading-snug text-muted">{tier.description}</p>
              <div className="mt-6 flex items-baseline gap-1">
                <span className="font-display text-5xl font-semibold tracking-[-0.03em] text-foreground">{tier.price}</span>
                {tier.period && <span className="text-sm text-muted-foreground">{tier.period}</span>}
              </div>
              <ul className="mt-6 flex-1 space-y-3">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-start gap-3 text-[15px] leading-snug text-muted">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent" strokeWidth={2} />
                    {f}
                  </li>
                ))}
              </ul>
              <Link href={"href" in tier && tier.href ? tier.href : "/register"} className="mt-8">
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
            </StaggerItem>
          ))}
        </Stagger>
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
