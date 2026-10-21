import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { connection } from "next/server";
import { StoreHeader } from "@/components/store/Chrome";
import { PromoStrip, StoreFooter, StoreUnavailable } from "@/components/store/Footer";
import { StoreProvider } from "@/components/store/StoreProvider";
import { StoreInfo, getStoreInfo, listStoreProducts } from "@/lib/storeApi";
import { STORE_NAME, STORE_URL } from "@/lib/storeConfig";

// Type: the system face first (San Francisco on Apple devices, see globals.css), Inter as the close match everywhere else.
const inter = Inter({ subsets: ["latin"], variable: "--font-cr-inter", display: "swap" });

const DESCRIPTION = `Shop groceries online at ${STORE_NAME}. We deliver to the continental United States.`;

export const metadata: Metadata = {
  metadataBase: new URL(STORE_URL),
  // `absolute` keeps the platform's "· Klaros AI" suffix off the store's own pages; `template` names the store on its product pages.
  title: { absolute: STORE_NAME, template: `%s · ${STORE_NAME}` },
  description: DESCRIPTION,
  applicationName: STORE_NAME,
  openGraph: { type: "website", siteName: STORE_NAME, title: STORE_NAME, description: DESCRIPTION, url: STORE_URL },
};

/** The storefront shell: translucent top bar with the categories and the basket, the shipping line, and a quiet footer with the standing notices. */
export default async function StoreLayout({ children }: { children: React.ReactNode }) {
  // Render when a shopper asks, never at build time: the store's state (open or closed) must not be frozen into the build, and a build must not need the API.
  // (Unlike `dynamic = "force-dynamic"`, this leaves the one-minute cache on the catalog reads in lib/storeApi.ts alone, which keeps the API's per-address rate limit safe.)
  await connection();
  let info: StoreInfo | null = null;
  let unreachable = false;
  let categories: string[] = [];
  try {
    info = await getStoreInfo();
  } catch {
    unreachable = true;
  }
  if (info) {
    try {
      categories = [...new Set((await listStoreProducts({ limit: 100 })).map((p) => p.category).filter((c): c is string => !!c))].sort();
    } catch {
      // the bar and footer simply have no category links
    }
  }
  return (
    <StoreProvider info={info}>
      <div className={`cleanroom-theme ${inter.variable} flex min-h-screen flex-col overflow-x-clip bg-background`}>
        <StoreHeader categories={categories} />
        <PromoStrip info={info} />
        <main className="mx-auto w-full max-w-[1080px] flex-1 px-4 py-10 sm:px-6 sm:py-14">{unreachable ? <StoreUnavailable /> : children}</main>
        <StoreFooter info={info} categories={categories} />
      </div>
    </StoreProvider>
  );
}
