import type { BuilderFeature, BuilderRunState, BuilderVersion } from "@/lib/api";

export const STATE_LABEL: Record<BuilderRunState, string> = {
  queued: "Starting",
  planning: "Planning",
  generating: "Writing files",
  validating: "Checking",
  repairing: "Repairing",
  ready: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const FEATURE_STATUS: Record<BuilderFeature["status"], { label: string; tone: string; hint: string }> = {
  implemented: { label: "Works", tone: "bg-success/10 text-success", hint: "Built into the project and checked." },
  client_side: { label: "In the visitor's browser", tone: "bg-sky-50 text-sky-700", hint: "Works on each visitor's own device only." },
  needs_setup: { label: "Needs publishing", tone: "bg-amber-50 text-amber-700", hint: "Works once the project is published from Klaros." },
  unsupported: { label: "Not possible here", tone: "bg-danger/10 text-danger", hint: "Needs a server, so it was not faked." },
};

export const VERSION_KIND: Record<BuilderVersion["kind"], string> = {
  create: "First build",
  edit: "Chat edit",
  file_edit: "Code edit",
  rollback: "Restored",
};

export const CHECK_LABEL: Record<string, string> = {
  entry_point: "Has a home page",
  routes_generated: "Every planned page exists",
  html_wellformed: "Pages are well-formed",
  links_resolve: "Every link leads somewhere",
  assets_exist: "Images and files exist",
  accessibility_basics: "Accessibility basics (titles, labels, headings, alt text)",
  no_inline_or_external_scripts: "Only the project's own script runs",
  scripts_safe: "No unsafe code patterns",
  no_secrets: "No keys or secrets in files",
  colour_contrast: "Text is readable (contrast)",
};

export const EXAMPLE_PROMPTS = [
  "Build a premium AI SaaS website with a homepage, features, pricing, blog and contact page.",
  "Create a real-estate marketplace with property search, filters, property detail pages and enquiry forms.",
  "Build a restaurant website called Olive & Ash with a menu, reservations and opening hours.",
  "Create an architecture studio portfolio with project galleries and a contact form.",
];

export const EDIT_SUGGESTIONS = ["Add a pricing page", "Make it more premium", "Switch to a dark theme", "Add a blog page", "Use a serif typeface", "Make the header not sticky"];

export function routeDir(path: string): string {
  return path === "/" ? "" : path.replace(/^\//, "") + "/";
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  // The API sends timestamps in UTC; some databases drop the zone marker, which a browser would read as local time.
  const stamp = /(?:Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`;
  const s = Math.max(0, (Date.now() - new Date(stamp).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(stamp).toLocaleDateString();
}
