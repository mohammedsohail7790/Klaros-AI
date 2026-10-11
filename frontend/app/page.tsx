import Link from "next/link";
import {
  BarChart3,
  Bot,
  Boxes,
  ClipboardList,
  Compass,
  FileText,
  Globe,
  ListChecks,
  Network,
  Rocket,
  Settings2,
  Sparkles,
  Workflow,
} from "lucide-react";
import GradientBackdrop from "@/components/GradientBackdrop";
import MarketingHeader from "@/components/MarketingHeader";
import MarketingFooter from "@/components/MarketingFooter";
import { BuildCta } from "@/components/marketing/BuildCta";
import { HeroIdeaForm } from "@/components/marketing/HeroIdeaForm";
import { BuilderPreview } from "@/components/marketing/BuilderPreview";

const JOURNEY = [
  { icon: Sparkles, title: "Idea", body: "Say what you want to build, in your own words." },
  { icon: Compass, title: "Discovery", body: "Klaros asks the few questions that matter — and no more." },
  { icon: FileText, title: "Blueprint", body: "Your business, structured: customers, offer, revenue, operations." },
  { icon: ClipboardList, title: "Requirements", body: "What the business needs to run, each with the reason why." },
  { icon: ListChecks, title: "Recommendations", body: "The tools and integrations that fit — honest about what's available today." },
  { icon: Network, title: "Business architecture", body: "A map of how your business operates, end to end — what exists, and what is still planned." },
  { icon: Globe, title: "Website", body: "Generated from your Blueprint. You review it before it goes live." },
  { icon: Bot, title: "AI workforce", body: "A conversation layer for calls and messages, connected to your business." },
  { icon: Rocket, title: "Launch & operations", body: "Go live when the launch checklist says you are ready, then run the business from one place." },
];

const CAPABILITIES = [
  {
    icon: Compass,
    title: "AI Business Discovery",
    body: "A short, adaptive conversation that turns a loose idea into structured facts. It never loops forever — it stops when it has what it needs, and you can review every answer.",
  },
  {
    icon: FileText,
    title: "Business Blueprint",
    body: "Everything Klaros understands about your business, in one place you can read, correct and confirm. Every statement is marked as something you said, something Klaros inferred, or something still unknown.",
  },
  {
    icon: ListChecks,
    title: "Recommended tools & integrations",
    body: "Each suggestion says what it is, why it's recommended, what it depends on and whether it's available now. If an integration doesn't exist yet it says so — it's labelled Planned, not faked.",
  },
  {
    icon: Network,
    title: "AI-generated business architecture",
    body: "The Business Map connects your customers, capabilities, systems and partners, and updates as your business changes.",
  },
  {
    icon: Globe,
    title: "Website builder",
    body: "Pages are generated from what your business actually needs — a provider directory only if you have providers. Preview it, publish it, and capture real leads into your CRM.",
  },
  {
    icon: Bot,
    title: "AI workforce, with Halla",
    body: "Halla AI is our separate voice and conversation platform for inbound and outbound calls, qualification and booking. Klaros is built to sit behind it; the connection itself is still being built.",
  },
  {
    icon: Workflow,
    title: "Automation",
    body: "Governed automations and approvals: nothing runs outside rules you set, and every action is logged.",
  },
  {
    icon: BarChart3,
    title: "Analytics & operations",
    body: "Leads, customers, jobs, invoices and cash in one operating view — so you can see what's happening in your business.",
  },
];

const FAQS = [
  {
    q: "What does Klaros actually do for me?",
    a: "It starts from your idea and works out what the business needs: it interviews you, writes a Business Blueprint, derives requirements, recommends tools, draws a map of how the business operates and helps you build the website. Then it becomes the place you run the business from.",
  },
  {
    q: "Does it build everything automatically?",
    a: "No. Klaros proposes and you decide: you confirm the Blueprint, accept or reject recommendations, and review the website before anything is published. Nothing is connected or published on your behalf without you.",
  },
  {
    q: "Which integrations work today?",
    a: "Stripe, Google Calendar and QuickBooks connect for real, and their status is checked live. Anything else Klaros recommends is clearly marked Planned or as needing an adapter — it won't pretend to be connected.",
  },
  {
    q: "Is the AI workforce included?",
    a: "Halla AI is a separate product with its own voice and calling platform. Klaros defines how the two connect and shows the workforce on your Business Map, but the connection isn't live yet, so Klaros shows it as not connected.",
  },
  {
    q: "What kinds of business can I build?",
    a: "Any. The engine doesn't assume a business type. Medical tourism is the most developed example today; a dropshipping store works through the same steps, with different requirements and a different map — though parts of ecommerce, like supplier connections and a storefront, are still planned.",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen overflow-x-hidden">
      <GradientBackdrop />
      <MarketingHeader />

      <section className="mx-auto max-w-4xl px-6 pb-16 pt-14 text-center sm:pt-20">
        <div className="mx-auto mb-6 inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted">
          <Sparkles className="h-3.5 w-3.5 text-accent" strokeWidth={2} aria-hidden="true" />
          The AI business builder
        </div>
        <h1 className="font-display text-4xl font-medium leading-[1.1] tracking-tight text-foreground sm:text-6xl">
          Build and operate
          <br />
          <span className="italic text-accent">your business with AI.</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-balance text-lg leading-relaxed text-muted">
          Describe what you want to build. Klaros figures out what your business needs — then helps you build it and operate it.
        </p>

        <HeroIdeaForm />

        <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link href="#how-it-works" className="klaros-btn-secondary px-6 py-3 text-base">
            See How It Works
          </Link>
        </div>

        <ol aria-label="How Klaros works" className="mx-auto mt-8 flex max-w-3xl flex-wrap items-center justify-center gap-x-2 gap-y-1 text-xs font-medium text-muted">
          {["Idea", "Discovery", "Blueprint", "Architecture", "Website", "AI workforce", "Operations"].map((step, i, a) => (
            <li key={step} className="flex items-center gap-2">
              {step}
              {i < a.length - 1 && <span aria-hidden="true" className="text-accent">→</span>}
            </li>
          ))}
        </ol>

        <BuilderPreview />
      </section>

      <section id="how-it-works" className="scroll-mt-24 border-y border-border bg-surface px-6 py-20">
        <div className="mx-auto max-w-6xl">
          <div className="mx-auto max-w-xl text-center">
            <h2 className="font-display text-3xl text-foreground">From business idea to operating business</h2>
            <p className="mt-3 text-sm text-muted">One continuous journey — not a pile of unrelated admin pages.</p>
          </div>
          <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {JOURNEY.map((step, i) => (
              <li key={step.title} className="klaros-card flex gap-4 p-5">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold text-accent-foreground" aria-hidden="true">
                  {i + 1}
                </span>
                <div>
                  <h3 className="flex items-center gap-2 font-display text-lg text-foreground">
                    <step.icon className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
                    {step.title}
                  </h3>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="product" className="scroll-mt-24 px-6 py-20">
        <div className="mx-auto max-w-6xl">
          <div className="mx-auto max-w-xl text-center">
            <h2 className="font-display text-3xl text-foreground">What's inside</h2>
          </div>
          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITIES.map((c) => (
              <div key={c.title} className="klaros-card p-6 transition-shadow hover:shadow-raised">
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-accent-soft">
                  <c.icon className="h-5 w-5 text-accent" strokeWidth={1.75} aria-hidden="true" />
                </div>
                <h3 className="font-display text-lg text-foreground">{c.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted">{c.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="one-engine" className="scroll-mt-24 border-y border-border bg-surface px-6 py-20">
        <div className="mx-auto max-w-5xl">
          <div className="mx-auto max-w-xl text-center">
            <h2 className="font-display text-3xl text-foreground">One engine. Any business.</h2>
            <p className="mt-3 text-sm text-muted">
              Klaros doesn't have a template per industry. The same steps produce a different blueprint, different requirements and a different map for each business.
            </p>
          </div>
          <div className="mt-12 grid gap-6 md:grid-cols-2">
            {[
              {
                title: "Medical tourism",
                note: "The most developed example today.",
                items: ["Provider & hospital directory", "Patient lead capture & qualification", "Consultations", "Referral & commission tracking"],
              },
              {
                title: "Dropshipping",
                note: "Mapped end to end; supplier and storefront pieces are planned.",
                items: ["Storefront", "Supplier connection", "Product catalog & inventory", "Orders & fulfilment"],
              },
            ].map((b) => (
              <div key={b.title} className="klaros-card p-6">
                <h3 className="font-display text-xl text-foreground">{b.title}</h3>
                <p className="mt-1 text-xs text-muted-foreground">{b.note}</p>
                <ul className="mt-4 space-y-2 text-sm text-muted">
                  {b.items.map((it) => (
                    <li key={it} className="flex items-center gap-2">
                      <Boxes className="h-4 w-4 text-accent" strokeWidth={1.75} aria-hidden="true" />
                      {it}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="faq" className="scroll-mt-24 px-6 py-20">
        <div className="mx-auto max-w-3xl">
          <h2 className="text-center font-display text-3xl text-foreground">Questions worth answering honestly</h2>
          <div className="mt-12 space-y-4">
            {FAQS.map((item) => (
              <details key={item.q} className="klaros-card group p-5">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-medium text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  {item.q}
                  <Settings2 className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
                </summary>
                <p className="mt-3 text-sm leading-relaxed text-muted">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-border bg-surface px-6 py-20 text-center">
        <h2 className="font-display text-3xl italic text-foreground">What are you building?</h2>
        <p className="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-muted">
          Tell Klaros your idea and see what your business needs — it takes a few minutes.
        </p>
        <div className="mt-8">
          <BuildCta>Build My Business</BuildCta>
        </div>
      </section>

      <MarketingFooter />
    </main>
  );
}
