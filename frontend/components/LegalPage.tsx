import GradientBackdrop from "@/components/GradientBackdrop";
import MarketingHeader from "@/components/MarketingHeader";
import MarketingFooter from "@/components/MarketingFooter";

export interface LegalSection {
  heading: string;
  body: string[];
  bullets?: string[];
}

/** One layout for every long-form legal/policy page, so they read the same and stay easy to update. */
export default function LegalPage({ title, updated, intro, sections }: { title: string; updated: string; intro: string; sections: LegalSection[] }) {
  return (
    <main className="min-h-screen overflow-x-hidden">
      <GradientBackdrop />
      <MarketingHeader />
      <article className="mx-auto max-w-3xl px-6 pb-24 pt-16 sm:pt-20">
        <h1 className="text-4xl font-semibold tracking-tight text-foreground">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">Last updated {updated}</p>
        <p className="mt-6 text-base leading-relaxed text-muted">{intro}</p>
        <nav aria-label="On this page" className="mt-8 rounded-xl border border-border bg-surface p-4 text-sm">
          <ol className="list-decimal space-y-1 pl-5 text-muted">
            {sections.map((s, i) => (
              <li key={s.heading}>
                <a href={`#s${i + 1}`} className="hover:text-foreground">
                  {s.heading}
                </a>
              </li>
            ))}
          </ol>
        </nav>
        {sections.map((s, i) => (
          <section key={s.heading} id={`s${i + 1}`} className="mt-10 scroll-mt-24">
            <h2 className="text-2xl font-semibold tracking-tight text-foreground">
              {i + 1}. {s.heading}
            </h2>
            {s.body.map((p) => (
              <p key={p} className="mt-3 text-base leading-relaxed text-muted">
                {p}
              </p>
            ))}
            {s.bullets && (
              <ul className="mt-3 list-disc space-y-1.5 pl-6 text-base leading-relaxed text-muted">
                {s.bullets.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </article>
      <MarketingFooter />
    </main>
  );
}
