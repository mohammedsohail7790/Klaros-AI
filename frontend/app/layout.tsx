import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { ToastProvider } from "@/components/ui/Toast";
import { SITE_URL } from "@/lib/site";

const TITLE = "Klaros AI — Build and operate your business with AI";
const DESCRIPTION = "Describe what you want to build. Klaros works out what your business needs, then helps you build and operate it.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: TITLE, template: "%s · Klaros AI" },
  description: DESCRIPTION,
  applicationName: "Klaros AI",
  openGraph: { type: "website", siteName: "Klaros AI", title: TITLE, description: DESCRIPTION, url: SITE_URL },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="font-sans">
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
