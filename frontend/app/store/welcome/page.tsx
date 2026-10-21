import type { Metadata } from "next";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { PantryScene, ParcelRoute, SecureCard, ShelfMark } from "@/components/store/Illustrations";
import { buttonClass, linkClass } from "@/components/store/styles";
import { getStoreInfo } from "@/lib/storeApi";
import { STORE_CONTACT_EMAIL, STORE_NAME } from "@/lib/storeConfig";

export const metadata: Metadata = {
  title: { absolute: `${STORE_NAME}: pantry staples, delivered` },
  description: `${STORE_NAME} ships shelf-stable groceries to the continental United States.`,
};

const STEPS = [
  { title: "Choose", body: "Fill your basket from the shelves." },
  { title: "Pay on Stripe", body: "Check out on Stripe's own page." },
  { title: "We order it", body: "Our supplier packs your order." },
  { title: "Follow it home", body: "Track it on your order page until it arrives." },
];

// Each band spans the full width of the window, even though the shop's pages sit in a narrower column: the wrapper below cancels the column's padding.
const BAND = "relative left-1/2 w-screen -translate-x-1/2";
const INNER = "mx-auto grid w-full max-w-[1180px] items-center gap-10 px-6 sm:px-10 lg:grid-cols-2 lg:gap-20";
const H2 = "text-[clamp(34px,5.2vw,64px)] font-semibold leading-[1.04] tracking-[-0.035em]";
const LEAD = "mt-6 max-w-[34ch] text-[19px] leading-[1.45] sm:text-[22px]";

/** The brand's front door. It never depends on the shop being open; the shop entry changes with the store's real state. */
export default async function Welcome() {
  let open = false;
  try {
    open = (await getStoreInfo()) !== null;
  } catch {
    // an unreachable shop reads as "not open yet" here; the home page itself must always render
  }
  return (
    <div className="-my-10 sm:-my-14">
      <section aria-label="Welcome" className={`${BAND} cr-on-dark overflow-hidden bg-[rgb(var(--color-brand))] text-[rgb(var(--color-background))]`}>
        <div className="cr-hero mx-auto max-w-[1180px] px-6 pb-6 pt-20 text-center sm:px-10 sm:pt-28">
          <h1 className="mx-auto max-w-[14ch] text-[clamp(52px,10.5vw,124px)] font-semibold leading-[0.96] tracking-[-0.05em]">Pantry staples, delivered.</h1>
          <p className="mx-auto mt-7 max-w-[30ch] text-[21px] leading-[1.38] text-[rgb(var(--color-background))]/85 sm:text-[28px]">
            Shelf-stable groceries shipped to your door, anywhere in the continental United States.
          </p>
          <div className="mt-10 flex flex-wrap items-center justify-center gap-x-8 gap-y-4">
            {open ? (
              <Link href="/store#products" className={buttonClass("primary", "lg")}>Shop now</Link>
            ) : (
              <span className="inline-flex h-[52px] items-center rounded-full border border-[rgb(var(--color-background))] px-8 text-[19px]">The shop opens soon</span>
            )}
            <a href="#how-it-works" className={linkClass(true)}>How it works <ChevronRight className="h-5 w-5" aria-hidden /></a>
          </div>
        </div>
        <PantryScene className="relative left-1/2 mt-14 block h-auto w-[860px] max-w-none -translate-x-1/2 sm:static sm:mt-20 sm:w-full sm:translate-x-0" />
      </section>

      <section aria-label="What we sell" className={`${BAND} bg-background px-6 py-24 text-center sm:px-10 sm:py-32`}>
        <h2 className="mx-auto max-w-[16ch] text-[clamp(44px,8vw,104px)] font-semibold leading-[0.98] tracking-[-0.045em] text-[rgb(var(--color-brand))]">No fridge. No freezer. No rush.</h2>
        <p className="mx-auto mt-8 max-w-[36ch] text-[21px] leading-[1.42] text-muted sm:text-[26px]">We sell groceries that keep, so your order can travel and your cupboard stays stocked.</p>
      </section>

      <section aria-label="Shelf-stable" className={`${BAND} bg-surface-muted py-24 sm:py-32`}>
        <div className={INNER}>
          <div>
            <h2 className={`${H2} text-[rgb(var(--color-brand))]`}>Everything on our shelves is shelf-stable.</h2>
            <p className={`${LEAD} text-foreground/80`}>A food product can only be listed once it is marked non-perishable. What arrives is what keeps.</p>
          </div>
          <ShelfMark className="mx-auto h-auto w-full max-w-[520px]" />
        </div>
      </section>

      <section aria-label="Shipped by our supplier" className={`${BAND} cr-on-dark bg-[rgb(var(--color-foreground))] py-24 text-[rgb(var(--color-background))] sm:py-32`}>
        <div className={INNER}>
          <ParcelRoute className="mx-auto h-auto w-full max-w-[520px] lg:order-first" />
          <div>
            <h2 className={H2}>Packed and sent by our supplier.</h2>
            <p className={`${LEAD} text-[rgb(var(--color-background))]/75`}>Your order ships straight from the supplier to your door, and every order has its own page where you can follow it.</p>
          </div>
        </div>
      </section>

      <section aria-label="Secure payment" className={`${BAND} bg-background py-24 sm:py-32`}>
        <div className={INNER}>
          <div>
            <h2 className={`${H2} text-foreground`}>You pay on Stripe&apos;s page, not ours.</h2>
            <p className={`${LEAD} text-muted`}>Checkout happens on Stripe&apos;s secure page. We never see or store your card details.</p>
          </div>
          <SecureCard className="mx-auto h-auto w-full max-w-[520px]" />
        </div>
      </section>

      <section aria-label="Where we deliver" className={`${BAND} cr-on-dark bg-[rgb(var(--color-brand))] px-6 py-24 text-center text-[rgb(var(--color-background))] sm:px-10 sm:py-36`}>
        <h2 className="mx-auto max-w-[14ch] text-[clamp(40px,7vw,92px)] font-semibold leading-[1] tracking-[-0.045em]">Delivered across the continental US.</h2>
        <p className="mx-auto mt-7 max-w-[40ch] text-[19px] leading-[1.45] text-[rgb(var(--color-background))]/80 sm:text-[22px]">We can&apos;t ship to Alaska, Hawaii or US territories.</p>
      </section>

      <section id="how-it-works" aria-label="How it works" className={`${BAND} scroll-mt-16 bg-surface-muted px-6 py-24 sm:px-10 sm:py-32`}>
        <div className="mx-auto max-w-[1180px]">
          <h2 className={`${H2} max-w-[14ch] text-[rgb(var(--color-brand))]`}>From shelf to door.</h2>
          <ol className="relative mt-16 grid gap-10 sm:grid-cols-2 lg:grid-cols-4 lg:gap-8">
            <span aria-hidden className="pointer-events-none absolute left-0 right-0 top-[21px] hidden h-[3px] rounded bg-[rgb(var(--color-brand))]/25 lg:block" />
            {STEPS.map((s, i) => (
              <li key={s.title} className="relative">
                <span aria-hidden className="relative z-10 flex h-11 w-11 items-center justify-center rounded-full bg-[rgb(var(--color-brand))] text-[19px] font-semibold text-[rgb(var(--color-background))]">{i + 1}</span>
                <p className="mt-6 text-[26px] font-semibold tracking-[-0.025em] text-foreground">{s.title}</p>
                <p className="mt-2 max-w-[26ch] text-[18px] leading-[1.45] text-muted">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section aria-label="Get in touch" className={`${BAND} cr-on-dark bg-[rgb(var(--color-foreground))] px-6 py-24 text-center text-[rgb(var(--color-background))] sm:px-10 sm:py-32`}>
        <h2 className="mx-auto max-w-[12ch] text-[clamp(44px,8vw,104px)] font-semibold leading-[0.98] tracking-[-0.045em]">{open ? "The shelves are stocked." : "The shelves are being stocked."}</h2>
        <p className="mx-auto mt-7 max-w-[34ch] text-[19px] leading-[1.45] text-[rgb(var(--color-background))]/75 sm:text-[24px]">
          {open ? "Come and take a look." : "Questions before we open?"} Write to{" "}
          <a className="underline underline-offset-4" href={`mailto:${STORE_CONTACT_EMAIL}`}>{STORE_CONTACT_EMAIL}</a>.
        </p>
        {open && <div className="mt-10"><Link href="/store#products" className={buttonClass("primary", "lg")}>Shop all products</Link></div>}
      </section>
    </div>
  );
}
