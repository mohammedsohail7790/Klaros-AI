import type { Metadata } from "next";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { TrustStrip } from "@/components/store/Footer";
import { buttonClass, linkClass } from "@/components/store/styles";
import { getStoreInfo } from "@/lib/storeApi";
import { STORE_CONTACT_EMAIL, STORE_NAME } from "@/lib/storeConfig";

export const metadata: Metadata = {
  title: { absolute: `${STORE_NAME}: pantry staples, delivered` },
  description: `${STORE_NAME} ships shelf-stable groceries to the continental United States.`,
};

const STEPS = [
  { n: "1", title: "Choose", body: "Browse the shelves and add what you need to your basket." },
  { n: "2", title: "Pay securely", body: "Check out on Stripe's own page. We never see your card." },
  { n: "3", title: "We ship it", body: "Our supplier packs your order and sends it to your door, and you can follow it online." },
];

/** The brand's front door: who we are and how it works. It never depends on the shop being open; the shop entry below changes with the store's real state. */
export default async function Welcome() {
  let open = false;
  try {
    open = (await getStoreInfo()) !== null;
  } catch {
    // an unreachable shop reads as "not open yet" here; the home page itself must always render
  }
  return (
    <div className="space-y-20 sm:space-y-28">
      <section aria-label="Welcome" className="cr-on-dark overflow-hidden rounded-[32px] bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]">
        <div className="px-6 py-16 text-center sm:px-12 sm:py-24">
          <h1 className="mx-auto max-w-3xl text-[44px] font-semibold leading-[1.04] sm:text-[72px]">Pantry staples, delivered.</h1>
          <p className="mx-auto mt-5 max-w-xl text-[19px] leading-snug text-[rgb(var(--color-background))]/85 sm:text-[24px]">Shelf-stable groceries shipped straight to you, anywhere in the continental United States.</p>
          <div className="mt-9 flex flex-wrap items-center justify-center gap-x-6 gap-y-4">
            {open ? (
              <Link href="/store#products" className={buttonClass("primary", "lg")}>Shop now</Link>
            ) : (
              <span className="inline-flex h-[52px] items-center rounded-full border border-[rgb(var(--color-background))] px-8 text-[19px]">The shop opens soon</span>
            )}
            <a href="#how-it-works" className={linkClass(true)}>How it works <ChevronRight className="h-5 w-5" aria-hidden /></a>
          </div>
        </div>
      </section>

      <TrustStrip />

      <section id="how-it-works" aria-label="How it works" className="scroll-mt-20">
        <h2 className="mb-10 text-center text-[32px] sm:text-[40px]">How it works</h2>
        <ol className="grid gap-4 sm:grid-cols-3 sm:gap-5">
          {STEPS.map((s) => (
            <li key={s.n} className="rounded-[28px] bg-surface-muted px-7 py-9">
              <span aria-hidden className="text-[44px] font-semibold leading-none text-[rgb(var(--color-brand))]">{s.n}</span>
              <p className="mt-4 text-[24px] font-semibold tracking-[-0.02em]">{s.title}</p>
              <p className="mt-2 text-[17px] text-muted">{s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-label="Get in touch" className="rounded-[32px] bg-[rgb(var(--color-foreground))] px-6 py-14 text-center text-[rgb(var(--color-background))] sm:px-12">
        <h2 className="text-[32px] sm:text-[40px]" style={{ color: "inherit" }}>{open ? "Ready when you are." : "Be the first to know."}</h2>
        <p className="mx-auto mt-3 max-w-md text-[19px] text-[rgb(var(--color-background))]/75">
          {open ? "The shelves are stocked." : "We are getting the shelves ready."} Questions? Write to{" "}
          <a className="underline underline-offset-4" href={`mailto:${STORE_CONTACT_EMAIL}`}>{STORE_CONTACT_EMAIL}</a>.
        </p>
        {open && <div className="mt-7"><Link href="/store#products" className={buttonClass("primary", "lg")}>Shop all products</Link></div>}
      </section>
    </div>
  );
}
