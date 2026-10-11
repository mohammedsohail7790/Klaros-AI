import type { Metadata } from "next";
import LegalPage, { LegalSection } from "@/components/LegalPage";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description: "What Klaros AI collects, why, who it is shared with, and the controls you have.",
};

const SECTIONS: LegalSection[] = [
  {
    heading: "Who this covers",
    body: [
      "Klaros AI (\"Klaros\", \"we\") is a business operating system. This policy covers two groups of people: the owners and team members who sign in to Klaros, and the people whose details those businesses record in Klaros, such as customers, leads and patients.",
      "Halla AI is a separate voice and conversation platform that can send enquiries to Klaros. Halla publishes its own policy for the calls it handles.",
    ],
  },
  {
    heading: "What we collect",
    body: ["We collect only what is needed to run the service:"],
    bullets: [
      "Account details: your name, email address and a one-way hash of your password (we never store the password itself).",
      "Workspace data you or your team enter or import: for example customers, leads, patient enquiries, providers, tasks, quotes, invoices and notes.",
      "Enquiries that arrive from connected channels such as a website form or Halla AI, including the consent choices the person made.",
      "Activity and audit records: what was done, by whom and when, so that every automated or AI action can be reviewed.",
      "Messages you send us through the contact form.",
      "Billing details are handled by our payment provider; we do not store card numbers.",
      "Basic technical data such as IP address and browser type, used for security and abuse prevention.",
    ],
  },
  {
    heading: "Our role: processor for your business data",
    body: [
      "For the customer, lead and patient information a business puts into Klaros, that business decides why and how it is used. Klaros processes it on the business's behalf and only to provide the service. For account and billing data about the people who sign in, Klaros decides the purpose.",
      "If you are a person whose details a business holds in Klaros and you want to see, correct or delete them, please contact that business first. We will help it respond.",
    ],
  },
  {
    heading: "Health and patient information",
    body: [
      "Klaros is built so that sensitive information moves only with the person's permission. Personal details and any health-related information are stored only if the person agreed to that, and withdrawing agreement is recorded and respected.",
      "Klaros never sends a person's identifiable details to a clinic or other provider on its own. A coordinator records the person's own decision to share with one named provider, the provider and the information allowed are recorded, and anything not covered is not shared. Sharing stops if the decision is withdrawn.",
      "Klaros is not a healthcare provider and does not give medical advice.",
    ],
  },
  {
    heading: "Who we share data with",
    body: [
      "We do not sell personal data. We use a small number of service providers to run Klaros, each bound to protect the data and use it only for that purpose:",
    ],
    bullets: [
      "Hosting and infrastructure: Vercel (web app), Render (application servers) and our managed database provider.",
      "AI processing: OpenAI, for features such as drafting, summarising and the voice assistant, only when those features are used.",
      "Payments: Stripe, for subscriptions.",
      "Email and messaging: SendGrid for email and Twilio for voice and text, when enabled.",
      "Professional advisers or authorities when the law requires it.",
    ],
  },
  {
    heading: "How long we keep data",
    body: [
      "Workspace data is kept while the workspace is active so that the business can use it. When a workspace is closed we delete or anonymise its data within a reasonable period, except where the law requires us to keep a record. A person's personal data in a patient enquiry can be erased on request; a minimal, non-identifying history is kept so that consent and audit records stay intact.",
    ],
  },
  {
    heading: "How we protect it",
    body: [
      "Data is encrypted in transit. Passwords are hashed. Each workspace's data is separated from every other workspace's at the database level. Access is limited by role, sign-in sessions can be revoked, and actions taken by people and by AI are recorded in an audit log. No system is perfectly secure, and we will tell affected customers promptly if a breach affects their data.",
    ],
  },
  {
    heading: "Your choices and rights",
    body: [
      "Depending on where you live, you may have the right to access, correct, delete or export your personal data, to object to or restrict some uses, and to complain to your local data-protection authority. To exercise a right over your own account data, contact us using the contact page. You can change your password at any time and sign out of every device by changing it.",
    ],
  },
  {
    heading: "Cookies and local storage",
    body: [
      "Klaros uses browser storage only to keep you signed in and to remember interface preferences. We do not use advertising cookies.",
    ],
  },
  {
    heading: "Changes and contact",
    body: [
      "We will post any change to this policy on this page and update the date above. Questions or requests can be sent through our contact page.",
    ],
  },
];

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      updated="12 October 2026"
      intro="This policy explains in plain language what Klaros AI does with information, who sees it, and the controls you have."
      sections={SECTIONS}
    />
  );
}
