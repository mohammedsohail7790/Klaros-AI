import type { Metadata } from "next";
import LegalPage, { LegalSection } from "@/components/LegalPage";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "The agreement that applies when you use Klaros AI.",
};

const SECTIONS: LegalSection[] = [
  {
    heading: "Agreement",
    body: [
      "These terms are an agreement between you (and the business you represent) and Klaros AI (\"Klaros\"). By creating an account or using Klaros you accept them. If you use Klaros on behalf of a business, you confirm you are allowed to bind it.",
    ],
  },
  {
    heading: "The service",
    body: [
      "Klaros is software that helps a business plan, set up and operate itself: customer and lead management, provider and referral tracking, consent records, tasks, finance tracking, a website builder, marketing tools and AI assistance. Features marked as planned or not connected are not part of the service until they are available.",
    ],
  },
  {
    heading: "Your account",
    body: [
      "Give accurate information, keep your password private, and tell us promptly if you think your account was accessed without permission. You are responsible for what happens under your account, including what people you invite do.",
    ],
  },
  {
    heading: "What you are responsible for",
    body: ["Klaros is a tool. The business using it remains responsible for how it runs. In particular you are responsible for:"],
    bullets: [
      "Having the legal right and the person's permission to record, use and contact the people whose details you put in Klaros, and honouring withdrawal of that permission.",
      "Following the laws and professional rules that apply to your business, including advertising, privacy, consumer and, where relevant, health-related rules in the places you operate.",
      "Checking any clinic, provider, price, claim or document before relying on or publishing it. Klaros records the checks you make; it does not certify a provider.",
      "Reviewing anything drafted by AI before it is sent, published or relied on.",
    ],
  },
  {
    heading: "Not medical, legal or financial advice",
    body: [
      "Klaros is not a healthcare provider, does not give medical advice and must not be used for emergencies. AI output can be wrong or incomplete. Nothing in Klaros is legal, tax or financial advice.",
    ],
  },
  {
    heading: "Acceptable use",
    body: ["You agree not to:"],
    bullets: [
      "Use Klaros to break the law, to deceive people, or to send unsolicited or deceptive messages.",
      "Upload data you have no right to use, or contact people who have not agreed or who have withdrawn agreement.",
      "Attempt to access another workspace's data, probe or disrupt the service, or reverse engineer it.",
      "Use Klaros to make guarantees about medical outcomes or to impersonate a clinic or professional.",
    ],
  },
  {
    heading: "Plans, trial and payment",
    body: [
      "New workspaces start with a 14-day free trial. Paid plans are billed through our payment provider at the price shown when you subscribe, and renew until cancelled. You can cancel at any time and the plan then ends at the close of the paid period. Taxes may apply. We may change prices with advance notice before your next renewal.",
    ],
  },
  {
    heading: "Your data",
    body: [
      "You own the data you put into Klaros. You give us permission to host and process it only to provide the service, as described in our Privacy Policy. You can export or delete your data, and on closure we handle it as the Privacy Policy describes.",
    ],
  },
  {
    heading: "Availability and changes",
    body: [
      "We work to keep Klaros available but do not promise it will be uninterrupted or error free. We may improve, change or retire features, giving reasonable notice when a change materially affects you.",
    ],
  },
  {
    heading: "Ending the agreement",
    body: [
      "You may stop using Klaros at any time. We may suspend or end access if these terms are broken, if required by law, or if use puts people or the service at risk. Sections that by their nature should continue (for example data, liability and payment) survive ending.",
    ],
  },
  {
    heading: "Disclaimer and liability",
    body: [
      "Klaros is provided \"as is\". To the extent the law allows, we exclude implied warranties, and we are not liable for indirect or consequential loss, lost profits, or decisions made on AI output or on a provider's information. Where liability cannot be excluded, it is limited to the amount you paid for Klaros in the twelve months before the claim. Nothing here limits liability that cannot lawfully be limited.",
    ],
  },
  {
    heading: "Changes to these terms and contact",
    body: [
      "We will post updated terms on this page and change the date above; continuing to use Klaros after a change means you accept it. Questions can be sent through our contact page.",
    ],
  },
];

export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of Service"
      updated="12 October 2026"
      intro="Please read these terms. They are written to be clear about what Klaros does, what it does not do, and what each side is responsible for."
      sections={SECTIONS}
    />
  );
}
