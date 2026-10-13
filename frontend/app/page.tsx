import Link from "next/link";
import {
  BarChart3,
  Bot,
  Boxes,
  Plus,
  ClipboardList,
  Compass,
  FileText,
  Globe,
  ListChecks,
  Network,
  Rocket,
  Sparkles,
  Workflow,
} from "lucide-react";
import MarketingHeader from "@/components/MarketingHeader";
import MarketingFooter from "@/components/MarketingFooter";
import { BuildCta } from "@/components/marketing/BuildCta";
import { HeroIdeaForm } from "@/components/marketing/HeroIdeaForm";
import { BuilderPreview } from "@/components/marketing/BuilderPreview";
import { Parallax, Reveal, ScrollStage, Stagger, StaggerItem, WordReveal } from "@/components/marketing/motion";

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
    body: "Halla AI is our separate voice and conversation platform for inbound and outbound calls, qualification and booking. Klaros receives its enquiries together with the caller's consent choices and turns them into leads and tasks for your team. The connection is in pilot: it is switched on per business, and nothing flows until you turn it on.",
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
    a: "Halla AI is a separate product with its own voice and calling platform. Klaros receives its enquiries with the caller's consent choices and shows the workforce on your Business Map. The connection is in pilot and is switched on per business, so until you turn it on Klaros shows it as not connected.",
  },
  {
    q: "What kinds of business can I build?",
    a: "Any. The engine doesn't assume a business type. Medical tourism is the most developed example today; a dropshipping store works through the same steps, with different requirements and a different map — though parts of ecommerce, like supplier connections and a storefront, are still planned.",
  },
];

const BENTO_SPAN = ["md:col-span-4", "md:col-span-2", "md:col-span-2", "md:col-span-2", "md:col-span-2", "md:col-span-3", "md:col-span-3", "md:col-span-6"];

export default function Home() {
  return (
    <main className="min-h-screen overflow-x-hidden bg-background">
      <MarketingHeader />

      {/* HERO */}
      <section className="relative px-6 pb-10 pt-16 text-center sm:pt-24">
        <Parallax distance={30} className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[620px]">
          <div
            aria-hidden="true"
            className="mx-auto h-full max-w-4xl"
            style={{ background: "radial-gradient(55% 55% at 50% 30%, rgb(var(--color-accent) / 0.10) 0%, transparent 70%)" }}
          />
        </Parallax>
        <Reveal y={16}>
          <p className="text-[15px] font-semibold text-accent sm:text-lg">Klaros</p>
        </Reveal>
        <Reveal delay={0.06}>
          <h1 className="mx-auto mt-3 max-w-5xl text-balance font-display text-[2.75rem] font-semibold leading-[1.04] tracking-[-0.03em] text-foreground sm:text-7xl lg:text-[6rem]">
            Build and operate
            <br />
            <span className="bg-gradient-to-b from-foreground to-foreground/60 bg-clip-text text-transparent">your business with AI.</span>
          </h1>
        </Reveal>
        <Reveal delay={0.14}>
          <p className="mx-auto mt-6 max-w-2xl text-balance text-lg leading-snug text-muted sm:text-2xl">
            Describe what you want to build. Klaros works out what your business needs, then helps you build it and run it.
          </p>
        </Reveal>
        <Reveal delay={0.22}>
          <HeroIdeaForm />
          <div className="mt-6 flex items-center justify-center gap-6 text-[17px]">
            <Link href="#how-it-works" className="text-accent transition-colors hover:underline">
              See How It Works <span aria-hidden="true">&rsaquo;</span>
            </Link>
          </div>
        </Reveal>

        <ScrollStage className="mx-auto mt-16 max-w-5xl">
          <BuilderPreview />
        </ScrollStage>
      </section>

      {/* STATEMENT */}
      <section className="klaros-dark px-6 py-28 sm:py-44">
        <div className="mx-auto max-w-4xl">
          <WordReveal
            className="text-balance font-display text-3xl font-semibold leading-[1.12] tracking-tight sm:text-5xl lg:text-6xl"
            text="Tell Klaros the idea. It asks the few questions that matter, writes the blueprint, and becomes the place where the business runs."
          />
        </div>
      </section>

      {/* JOURNEY */}
      <section id="how-it-works" className="scroll-mt-16 px-6 py-24 sm:py-36">
        <div className="mx-auto max-w-5xl">
          <Reveal className="max-w-2xl">
            <h2 className="text-balance font-display text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">From idea to operating business.</h2>
            <p className="mt-5 text-xl leading-snug text-muted">One continuous journey, not a pile of unrelated admin pages.</p>
          </Reveal>
          <ol className="mt-16 divide-y divide-border border-y border-border">
            {JOURNEY.map((step, i) => (
              <Reveal as="li" key={step.title} className="group grid grid-cols-[3.25rem_1fr] items-baseline gap-x-4 py-7 sm:grid-cols-[6rem_16rem_1fr] sm:py-9">
                <span className="font-display text-2xl font-semibold tabular-nums text-muted-foreground transition-colors duration-500 group-hover:text-accent sm:text-4xl">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <h3 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">{step.title}</h3>
                <p className="col-start-2 mt-2 text-lg leading-snug text-muted sm:col-start-3 sm:mt-0">{step.body}</p>
              </Reveal>
            ))}
          </ol>
        </div>
      </section>

      {/* BENTO */}
      <section id="product" className="scroll-mt-16 bg-surface-muted px-6 py-24 sm:py-36">
        <div className="mx-auto max-w-6xl">
          <Reveal className="mx-auto max-w-3xl text-center">
            <h2 className="text-balance font-display text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">What&apos;s inside.</h2>
          </Reveal>
          <Stagger className="mt-16 grid gap-4 md:grid-cols-6">
            {CAPABILITIES.map((c, i) => (
              <StaggerItem key={c.title} className={BENTO_SPAN[i] ?? "md:col-span-2"}>
                <div className="group flex h-full flex-col rounded-3xl bg-surface p-7 transition-all duration-500 ease-apple hover:-translate-y-1 hover:shadow-popover sm:p-9">
                  <div className="mb-6 flex h-11 w-11 items-center justify-center rounded-full bg-accent-soft transition-transform duration-500 ease-apple group-hover:scale-110">
                    <c.icon className="h-5 w-5 text-accent" strokeWidth={1.75} aria-hidden="true" />
                  </div>
                  <h3 className="font-display text-2xl font-semibold leading-tight tracking-tight">{c.title}</h3>
                  <p className="mt-3 text-[17px] leading-snug text-muted">{c.body}</p>
                </div>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      {/* ONE ENGINE */}
      <section id="one-engine" className="klaros-dark scroll-mt-16 px-6 py-28 sm:py-40">
        <div className="mx-auto max-w-5xl">
          <Reveal className="mx-auto max-w-3xl text-center">
            <h2 className="text-balance font-display text-4xl font-semibold leading-[1.06] tracking-tight sm:text-7xl">One engine. Any business.</h2>
            <p className="mt-6 text-xl leading-snug text-muted">
              Klaros has no template per industry. The same steps produce a different blueprint, different requirements and a different map for each business.
            </p>
          </Reveal>
          <Stagger className="mt-20 grid gap-4 md:grid-cols-2" step={0.12}>
            {[
              {
                title: "Medical tourism",
                note: "The most developed example today.",
                items: ["Clinic directory with verification status", "Patient pipeline with consent on record", "Referral to one clinic, only with the patient's approval", "Commission tracking backed by evidence"],
              },
              {
                title: "Dropshipping",
                note: "Mapped end to end; supplier and storefront pieces are planned.",
                items: ["Storefront", "Supplier connection", "Product catalog & inventory", "Orders & fulfilment"],
              },
            ].map((b) => (
              <StaggerItem key={b.title}>
                <div className="h-full rounded-3xl bg-surface p-8 sm:p-10">
                  <h3 className="font-display text-3xl font-semibold tracking-tight">{b.title}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">{b.note}</p>
                  <ul className="mt-8 space-y-3.5 text-[17px] text-muted">
                    {b.items.map((it) => (
                      <li key={it} className="flex items-start gap-3">
                        <Boxes className="mt-1 h-4 w-4 shrink-0 text-accent" strokeWidth={1.75} aria-hidden="true" />
                        {it}
                      </li>
                    ))}
                  </ul>
                </div>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="scroll-mt-16 px-6 py-24 sm:py-36">
        <div className="mx-auto max-w-3xl">
          <Reveal>
            <h2 className="text-balance text-center font-display text-4xl font-semibold leading-[1.08] tracking-tight sm:text-6xl">Questions worth answering honestly.</h2>
          </Reveal>
          <div className="mt-14 divide-y divide-border border-y border-border">
            {FAQS.map((item) => (
              <details key={item.q} className="group py-1">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 text-xl font-semibold tracking-tight text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  {item.q}
                  <Plus className="h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-500 ease-apple group-open:rotate-45" aria-hidden="true" />
                </summary>
                <p className="pb-6 pr-10 text-[17px] leading-relaxed text-muted">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="bg-surface-muted px-6 py-28 text-center sm:py-40">
        <Reveal>
          <h2 className="text-balance font-display text-4xl font-semibold tracking-tight sm:text-7xl">What are you building?</h2>
          <p className="mx-auto mt-5 max-w-xl text-xl leading-snug text-muted">Tell Klaros your idea and see what your business needs. It takes a few minutes.</p>
          <div className="mt-10">
            <BuildCta>Build My Business</BuildCta>
          </div>
        </Reveal>
      </section>

      <MarketingFooter />
    </main>
  );
}
