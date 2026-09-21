import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  Sparkles,
  Bot,
  Layers3,
  BrainCircuit,
  ShieldCheck,
  Workflow,
  Plug,
  UserCheck,
  Wand2,
  ClipboardCheck,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import HeroVisual from "@/components/HeroVisual";
import GradientBackdrop from "@/components/GradientBackdrop";
import MarketingHeader from "@/components/MarketingHeader";
import MarketingFooter from "@/components/MarketingFooter";

const FEATURES = [
  {
    icon: Bot,
    title: "An AI that acts, not just chats",
    body: "Klaros reads every lead, quote, invoice, and job — then proposes the next action, waits for your approval on anything that matters, and executes safely within limits you set.",
  },
  {
    icon: Layers3,
    title: "One operating system, not twelve tabs",
    body: "CRM, scheduling, quoting, invoicing, and retention live in one place, wired together by a real event system — not a pile of disconnected tools pretending to integrate.",
  },
  {
    icon: BrainCircuit,
    title: "It learns your business, on the record",
    body: "Every recommendation, approval, and outcome is logged. Klaros gets better at recommending what you'd actually do — and you can always see why.",
  },
  {
    icon: ShieldCheck,
    title: "Nothing runs outside your rules",
    body: "Every AI action is checked against a fixed policy before it executes and written to an audit trail afterward — the AI can propose, but it can never quietly approve its own request.",
  },
  {
    icon: Workflow,
    title: "Automations that actually hold up",
    body: "Event → condition → action, running on the same governed pipeline as everything else. Edit one mid-flight and an execution already in progress keeps running against the version it started with.",
  },
  {
    icon: Plug,
    title: "Real integrations, honestly reported",
    body: "Stripe, Google Calendar, and QuickBooks connect for real — and every connection status is checked live against the provider, never faked just to look good.",
  },
];

const STEPS = [
  {
    icon: UserCheck,
    title: "Set up your business",
    body: "Add your pricing rules, service catalog, and brand voice once — Klaros reads these before it ever proposes anything on your behalf.",
  },
  {
    icon: Wand2,
    title: "Klaros proposes the next action",
    body: "A new lead, an overdue invoice, a quote gone quiet — Klaros reads what changed and recommends what to do about it, in plain language.",
  },
  {
    icon: ClipboardCheck,
    title: "You approve, or it runs on its own",
    body: "Set what's automatic and what needs your sign-off. Every action — either way — is policy-checked and written to an audit trail you can always inspect.",
  },
];

const FAQS = [
  {
    q: "Can the AI take actions without me knowing?",
    a: "No. Every action — automatic or approved — is logged to an audit trail you can see in full, including exactly what was proposed, what was approved, and what ran. Nothing happens silently.",
  },
  {
    q: "What if I don't trust an AI recommendation?",
    a: "Reject it. Klaros treats every AI output as a proposal, never a decision — you can require approval on any action type, and rejecting one doesn't just discard it, it teaches Klaros what you'd actually do instead.",
  },
  {
    q: "Do I need to be technical to set this up?",
    a: "No. Setup is entering your business details in plain forms — pricing rules, service catalog, brand voice. If you can fill out a form, you can configure Klaros.",
  },
  {
    q: "Which tools does Klaros actually integrate with today?",
    a: "Stripe, Google Calendar, and QuickBooks connect for real today. Every integration's connection status on your dashboard is checked live against the real provider — never simulated.",
  },
  {
    q: "What happens to my data if I stop using Klaros?",
    a: "Your data is yours. Every record Klaros holds — leads, jobs, invoices, the audit trail — is exportable, and there's no lock-in beyond the switching cost of any software you'd change.",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen overflow-x-hidden">
      <GradientBackdrop />
      <MarketingHeader />

      <section className="mx-auto max-w-4xl px-6 pb-20 pt-16 text-center sm:pt-24">
        <div className="mx-auto mb-6 inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted">
          <Sparkles className="h-3.5 w-3.5 text-accent" strokeWidth={2} />
          The AI operating system for the one-person company
        </div>
        <h1 className="font-display text-4xl font-medium leading-[1.1] tracking-tight text-foreground sm:text-6xl">
          Run your entire business.
          <br />
          <span className="italic text-accent">Not just track it.</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-balance text-lg leading-relaxed text-muted">
          Klaros is the operating system built for solo operators and small service
          businesses — leads to cash, quotes to reviews, one governed AI layer that
          proposes, waits for you, and executes.
        </p>
        <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link href="/register">
            <Button size="lg" className="gap-2">
              Create your company
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </Button>
          </Link>
          <Link href="/pricing">
            <Button variant="secondary" size="lg">
              See pricing
            </Button>
          </Link>
        </div>
        <div className="mt-10 flex flex-wrap items-center justify-center gap-x-8 gap-y-3">
          {[
            "Real-time Owner Attention Queue — nothing falls through silently",
            "Governed AI execution — every action is policy-checked and audited",
            "Built for the one-person company, not an enterprise IT team",
          ].map((point) => (
            <div key={point} className="flex items-center gap-2 text-sm text-muted">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" strokeWidth={2} />
              {point}
            </div>
          ))}
        </div>

        <HeroVisual />
      </section>

      {/* Product facts, not vanity metrics — real capabilities of the
          platform today, not fabricated customer/usage numbers. */}
      <section className="border-y border-border bg-surface/60 px-6 py-10">
        <div className="mx-auto grid max-w-4xl grid-cols-2 gap-8 text-center sm:grid-cols-4">
          {[
            { value: "100%", label: "AI actions policy-checked & audited" },
            { value: "5", label: "modules in one operating system" },
            { value: "3", label: "real integrations, live-verified" },
            { value: "24/7", label: "Owner Attention Queue coverage" },
          ].map((stat) => (
            <div key={stat.label}>
              <div className="font-display text-3xl text-accent">{stat.value}</div>
              <div className="mt-1 text-xs leading-snug text-muted">{stat.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="border-t border-border bg-surface px-6 py-20">
        <div className="mx-auto max-w-5xl">
          <div className="mx-auto max-w-xl text-center">
            <h2 className="font-display text-3xl text-foreground">How it actually works</h2>
            <p className="mt-3 text-sm text-muted">Three real steps, no black box.</p>
          </div>
          <div className="mt-14 grid gap-10 sm:grid-cols-3">
            {STEPS.map((step, i) => (
              <div key={step.title} className="relative text-center">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
                  <step.icon className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <div className="mt-4 font-display text-lg text-foreground">
                  {i + 1}. {step.title}
                </div>
                <p className="mt-2 text-sm leading-relaxed text-muted">{step.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="px-6 py-20">
        <div className="mx-auto max-w-6xl">
          <div className="mx-auto max-w-xl text-center">
            <h2 className="font-display text-3xl text-foreground">Everything one person needs to run a real business</h2>
          </div>
          <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="klaros-card p-6 transition-shadow hover:shadow-raised">
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-accent-soft">
                  <feature.icon className="h-5 w-5 text-accent" strokeWidth={1.75} />
                </div>
                <h3 className="font-display text-xl text-foreground">{feature.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-muted">{feature.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="border-t border-border bg-surface px-6 py-20">
        <div className="mx-auto max-w-3xl">
          <h2 className="text-center font-display text-3xl text-foreground">Questions worth answering honestly</h2>
          <div className="mt-12 space-y-6">
            {FAQS.map((item) => (
              <div key={item.q} className="klaros-card p-6">
                <h3 className="font-medium text-foreground">{item.q}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted">{item.a}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-4xl px-6 py-20 text-center">
        <h2 className="font-display text-3xl italic text-foreground">
          Built for one person to run what used to take a team.
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-muted">
          Set up your company in a couple of minutes and see your first Morning Brief
          before your coffee's cold.
        </p>
        <Link href="/register" className="mt-8 inline-block">
          <Button size="lg" className="gap-2">
            Get started
            <ArrowRight className="h-4 w-4" strokeWidth={2} />
          </Button>
        </Link>
      </section>

      <MarketingFooter />
    </main>
  );
}
