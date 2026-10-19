import type { Metadata } from "next";
import LegalPage, { LegalSection } from "@/components/LegalPage";

export const metadata: Metadata = {
  title: "Delete your data",
  description: "How to remove the data Klaros holds about you, including the access you gave through Facebook, Instagram or Google.",
};

const SECTIONS: LegalSection[] = [
  {
    heading: "If you connected a Facebook or Meta ad account",
    body: [
      "Klaros only reads advertising results (spend, campaigns) from the ad account you chose to connect. It never posts, edits or spends on your behalf.",
      "You can remove that access at any time, without contacting us:",
    ],
    bullets: [
      "In Klaros, open Settings, then Integrations, find the connection and choose Disconnect. Klaros deletes the stored access token immediately.",
      "On Facebook, open Settings, then Business Integrations, find Klaros Connectors and choose Remove. Meta then stops all access from its side.",
    ],
  },
  {
    heading: "Delete the data Klaros holds about you",
    body: [
      "Send a request from the email address you signed up with to the contact page on this site, saying you want your data deleted. We confirm the request came from you, then delete your account details and the workspace data you own, and tell you when it is done, normally within 30 days.",
      "If you are a customer, lead or patient of a business that uses Klaros, that business decides what it keeps. Ask it first and we will help it carry out your request.",
    ],
  },
  {
    heading: "What we may have to keep",
    body: [
      "We may keep a record of the deletion request itself, and records we are legally required to retain, such as invoices. The audit log entry that shows an access was removed stays, but it holds no personal details beyond a reference.",
    ],
  },
];

export default function DataDeletionPage() {
  return (
    <LegalPage
      title="Delete your data"
      updated="19 October 2026"
      intro="You are in control of what Klaros holds about you and of the access you have given it. This page explains how to remove either."
      sections={SECTIONS}
    />
  );
}
