import type { Metadata } from "next";
import { connection } from "next/server";
import { StoreHeader } from "@/components/store/Chrome";
import { StoreFooter, StoreUnavailable } from "@/components/store/Footer";
import { StoreProvider } from "@/components/store/StoreProvider";
import { StoreInfo, getStoreInfo } from "@/lib/storeApi";
import { STORE_NAME } from "@/lib/storeConfig";

export const metadata: Metadata = {
  title: { default: STORE_NAME, template: `%s · ${STORE_NAME}` },
  description: `Shop groceries online at ${STORE_NAME}. We deliver to the continental United States.`,
};

/** The storefront shell: header with the basket, footer with the standing notices, and the shopper's basket for every page below. */
export default async function StoreLayout({ children }: { children: React.ReactNode }) {
  // Render when a shopper asks, never at build time: the store's state (open or closed) must not be frozen into the build, and a build must not need the API.
  // (Unlike `dynamic = "force-dynamic"`, this leaves the one-minute cache on the catalog reads in lib/storeApi.ts alone, which keeps the API's per-address rate limit safe.)
  await connection();
  let info: StoreInfo | null = null;
  let unreachable = false;
  try {
    info = await getStoreInfo();
  } catch {
    unreachable = true;
  }
  return (
    <StoreProvider info={info}>
      <div className="flex min-h-screen flex-col bg-background">
        <StoreHeader />
        <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 sm:px-8">{unreachable ? <StoreUnavailable /> : children}</main>
        <StoreFooter info={info} />
      </div>
    </StoreProvider>
  );
}
