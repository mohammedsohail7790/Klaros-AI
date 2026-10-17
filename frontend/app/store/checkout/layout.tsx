import type { Metadata } from "next";

// Private to the shopper: never indexed by search engines.
export const metadata: Metadata = { title: "Checkout", robots: { index: false, follow: false } };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
