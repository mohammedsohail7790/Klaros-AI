import { STORE_CONTACT_EMAIL, STORE_NAME } from "@/lib/storeConfig";
import type { StoreInfo } from "@/lib/storeApi";

/** Only what is already true: where we deliver, the shipping rule the owner set, and that sales tax is not collected. No shipping times or return terms are stated here. */
export function StoreFooter({ info }: { info: StoreInfo | null }) {
  return (
    <footer className="border-t border-border bg-surface-muted">
      <div className="mx-auto w-full max-w-6xl space-y-1 px-5 py-8 text-sm text-muted sm:px-8">
        {info && (
          <>
            <p>We deliver to the continental United States only.</p>
            {info.shipping && <p>{info.shipping}</p>}
            <p>{info.sales_tax_notice}</p>
          </>
        )}
        {STORE_CONTACT_EMAIL && <p>Questions? Email <a className="underline" href={`mailto:${STORE_CONTACT_EMAIL}`}>{STORE_CONTACT_EMAIL}</a>.</p>}
        <p>© {new Date().getFullYear()} {STORE_NAME}</p>
      </div>
    </footer>
  );
}

export function StoreClosed() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">The store is not open yet</h1>
      <p className="mt-3 text-muted">Please check back soon.</p>
    </div>
  );
}

export function StoreUnavailable() {
  return (
    <div className="mx-auto max-w-md py-16 text-center" role="alert">
      <h1 className="font-display text-3xl font-semibold tracking-tight text-foreground">We cannot reach the store right now</h1>
      <p className="mt-3 text-muted">Please try again in a few minutes.</p>
    </div>
  );
}
