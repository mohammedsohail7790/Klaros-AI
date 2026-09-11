import Link from "next/link";
import { ArrowRight, CheckCircle2, Sparkles, Bot, Layers3, BrainCircuit } from "lucide-react";
import { Button } from "@/components/ui/Button";
import HeroVisual from "@/components/HeroVisual";

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
];

const PROOF_POINTS = [
  "Real-time Owner Attention Queue — nothing falls through silently",
  "Governed AI execution — every action is policy-checked and audited",
  "Built for the one-person company, not an enterprise IT team",
];

export default function Home() {
  return (
    <main className="min-h-screen overflow-x-hidden bg-background">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <div className="font-display text-xl italic tracking-tight text-foreground">Klaros</div>
        <div className="flex items-center gap-3">
          <Link href="/login" className="klaros-btn-secondary">
            Sign in
          </Link>
          <Link href="/register" className="klaros-btn-primary">
            Create your company
          </Link>
        </div>
      </header>

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
          <Link href="/login">
            <Button variant="secondary" size="lg">
              Sign in
            </Button>
          </Link>
        </div>
        <div className="mt-10 flex flex-wrap items-center justify-center gap-x-8 gap-y-3">
          {PROOF_POINTS.map((point) => (
            <div key={point} className="flex items-center gap-2 text-sm text-muted">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" strokeWidth={2} />
              {point}
            </div>
          ))}
        </div>

        <HeroVisual />
      </section>

      <section className="border-t border-border bg-surface">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="grid gap-6 sm:grid-cols-3">
            {FEATURES.map((feature) => (
              <div
                key={feature.title}
                className="klaros-card p-6 transition-shadow hover:shadow-raised"
              >
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-accent-soft">
                  <feature.icon className="h-5 w-5 text-accent" strokeWidth={1.75} />
                </div>
                <h2 className="font-display text-xl text-foreground">{feature.title}</h2>
                <p className="mt-3 text-sm leading-relaxed text-muted">{feature.body}</p>
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

      <footer className="border-t border-border px-6 py-8 text-center text-xs text-muted-foreground">
        © {new Date().getFullYear()} Klaros AI
      </footer>
    </main>
  );
}
