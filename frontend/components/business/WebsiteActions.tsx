import Link from "next/link";
import { ExternalLink } from "lucide-react";
import type { BuilderOverview } from "@/lib/api";

/** The website call-to-action(s), chosen strictly from real state:
 * none → Generate · draft → Open + Publish · published → View live + Open. */
export function websiteCtaLabel(w: BuilderOverview["website"]): string {
  return w.published ? "View Live Website" : w.exists ? "Publish Website" : "Generate Website";
}

export function WebsiteActions({ website, tenantId }: { website: BuilderOverview["website"]; tenantId?: string | null }) {
  return (
    <div className="flex flex-wrap gap-2">
      {website.published && tenantId ? (
        <>
          <a href={`/w/${tenantId}`} target="_blank" rel="noopener noreferrer" className="klaros-btn-primary inline-flex items-center gap-1.5 text-sm">
            View Live Website <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
          <Link href="/website" className="klaros-btn-secondary text-sm">Open Website</Link>
        </>
      ) : website.exists ? (
        <>
          <Link href="/website" className="klaros-btn-primary text-sm">Publish Website</Link>
          <Link href="/website" className="klaros-btn-secondary text-sm">Open Website</Link>
        </>
      ) : (
        <Link href="/website" className="klaros-btn-primary text-sm">Generate Website</Link>
      )}
    </div>
  );
}
