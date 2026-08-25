import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Klaros AI",
  description: "The AI operating system for the one-person company.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
