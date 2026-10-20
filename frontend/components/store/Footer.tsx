import Link from "next/link";
import { PackageCheck, ShieldCheck, Truck } from "lucide-react";
import { STORE_CONTACT_EMAIL, STORE_NAME } from "@/lib/storeConfig";
import type { StoreInfo } from "@/lib/storeApi";

/** The slim line under the bar: the owner's real shipping rule, nothing else. */
export function PromoStrip({ info }: { info: StoreInfo | null }) {
  if (!info?.shipping) return null;
  return (
    <div className="bg-surface-muted">
      <p className="mx-auto flex w-full max-w-[1080px] items-center justify-center gap-2 px-4 py-3 text-center text-[14px] text-foreground sm:px-6">
        <Truck className="h-4 w-4 shrink-0 text-[rgb(var(--color-brand))]" aria-hidden /> <span>{info.shipping}</span>
      </p>
    </div>
  );
}

/** Three plain facts about buying here. Every one is true today: where we deliver, who takes payment, and that the supplier ships to you. */
export function TrustStrip() {
  const items = [
    { icon: Truck, title: "Delivered across the continental US", body: "We cannot ship to Alaska, Hawaii or US territories." },
    { icon: ShieldCheck, title: "Secure card payment", body: "You pay on Stripe's own page. We never see your card." },
    { icon: PackageCheck, title: "Shipped straight to you", body: "Our supplier packs and sends your order, and you can follow it." },
  ];
  return (
    <ul className="grid gap-10 text-center sm:grid-cols-3 sm:gap-8" aria-label="Why shop here">
      {items.map(({ icon: Icon, title, body }) => (
        <li key={title} className="flex flex-col items-center">
          <Icon className="mb-4 h-8 w-8 text-[rgb(var(--color-brand))]" strokeWidth={1.5} aria-hidden />
          <p className="text-[21px] font-semibold leading-tight tracking-[-0.02em] text-foreground">{title}</p>
          <p className="mt-2 max-w-[280px] text-[17px] text-muted">{body}</p>
        </li>
      ))}
    </ul>
  );
}

/** Quiet and dense, like apple.com's: small type in a few columns on a light band. Only what is already true is stated here; no delivery times or return terms. */
export function StoreFooter({ info, categories = [] }: { info: StoreInfo | null; categories?: string[] }) {
  const head = "mb-3 text-[12px] font-semibold text-foreground";
  const small = "space-y-2 text-[12px] leading-snug text-muted";
  return (
    <footer className="mt-24 bg-surface-muted">
      <div className="mx-auto w-full max-w-[1080px] px-4 pb-8 pt-12 sm:px-6">
        <div className="grid gap-10 border-b border-[rgb(var(--color-border))] pb-10 sm:grid-cols-[1.3fr_1fr_1fr_1fr]">
          <div>
            <p className="text-[21px] font-semibold tracking-[-0.03em] text-foreground">{STORE_NAME}</p>
            <p className="mt-2 max-w-[220px] text-[12px] text-muted">Pantry staples, shipped to your door.</p>
          </div>
          <div>
            <h2 className={head}>Shop</h2>
            <ul className={small}>
              <li><Link href="/store#products" className="hover:text-foreground hover:underline">All products</Link></li>
              {categories.map((c) => <li key={c}><Link href={`/store?category=${encodeURIComponent(c)}#products`} className="hover:text-foreground hover:underline">{c}</Link></li>)}
            </ul>
          </div>
          {info && (
            <div>
              <h2 className={head}>Delivery</h2>
              <ul className={small}>
                <li>We deliver to the continental United States only.</li>
                {info.shipping && <li>{info.shipping}</li>}
                <li>{info.sales_tax_notice}</li>
              </ul>
            </div>
          )}
          {STORE_CONTACT_EMAIL && (
            <div>
              <h2 className={head}>Questions</h2>
              <p className="text-[12px] text-muted">Email <a className="text-[rgb(var(--color-brand))] hover:underline" href={`mailto:${STORE_CONTACT_EMAIL}`}>{STORE_CONTACT_EMAIL}</a></p>
            </div>
          )}
        </div>
        <p className="pt-5 text-[12px] text-muted">© {new Date().getFullYear()} {STORE_NAME}. Payments are processed by Stripe.</p>
      </div>
    </footer>
  );
}

export function StoreClosed() {
  return (
    <div className="mx-auto max-w-md py-24 text-center">
      <h1 className="text-5xl font-semibold">The store is not open yet</h1>
      <p className="mt-4 text-[19px] text-muted">We are getting the shelves ready. Please check back soon.</p>
    </div>
  );
}

export function StoreUnavailable() {
  return (
    <div className="mx-auto max-w-md py-24 text-center" role="alert">
      <h1 className="text-5xl font-semibold">We cannot reach the store right now</h1>
      <p className="mt-4 text-[19px] text-muted">Please try again in a few minutes.</p>
    </div>
  );
}
