import type { Metadata } from "next";
import { Fraunces, Hanken_Grotesk } from "next/font/google";
import "./globals.css";
import { ToastProvider } from "@/components/ui/Toast";
import { SITE_URL } from "@/lib/site";

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  display: "swap",
});

// Shared with the Halla AI family's own marketing/product design system —
// same body typeface across Consultancy, AI Receptionist, and Klaros
// itself, so the three read as one family while keeping their own accent
// colors and Klaros's own Fraunces display/wordmark identity.
const hankenGrotesk = Hanken_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});

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
    <html lang="en" className={`${fraunces.variable} ${hankenGrotesk.variable}`}>
      <body className="font-sans">
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
