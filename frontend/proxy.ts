import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Custom domains. A business can attach a domain it owns (see the Website page); once the domain is verified, a visitor who arrives on it sees that business's site.
 * Requests on the platform's own hosts (the app, previews, local development) pass straight through. On any other host we ask the backend which site answers for it:
 * the business's own uploaded files, or the site Klaros generated for it. An unknown or not-yet-verified domain gets a plain 404 and never anything of ours.
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PLATFORM_HOSTS = [/^localhost(:\d+)?$/, /^127\.0\.0\.1(:\d+)?$/, /(^|\.)meetklaros\.com$/, /\.vercel\.app$/];
// The store's own domain(s): the home page of these hosts is the Clean Room welcome page (/store/welcome), with the shop at /store; every other path is served as it is, so /store/... works on them too.
// Set STORE_HOSTS (comma separated) to change them; the domain itself must still be pointed at this app in DNS and the hosting settings.
const STORE_HOSTS = (process.env.STORE_HOSTS ?? "yourcleanroom.com,www.yourcleanroom.com").split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
const TTL_MS = 60_000;
const cache = new Map<string, { at: number; value: { tenant_id: string; kind: "upload" | "klaros" } | null }>();

async function resolveHost(host: string) {
  const hit = cache.get(host);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value;
  let value: { tenant_id: string; kind: "upload" | "klaros" } | null = null;
  try {
    const res = await fetch(`${API_URL}/api/v1/public/domains/${encodeURIComponent(host)}`, { cache: "no-store" });
    if (res.ok) value = await res.json();
    else if (res.status !== 404) return null; // backend trouble: do not cache it as "unknown"
  } catch {
    return null;
  }
  cache.set(host, { at: Date.now(), value });
  return value;
}

export async function proxy(request: NextRequest) {
  const host = (request.headers.get("host") ?? "").toLowerCase();
  if (!host || PLATFORM_HOSTS.some((re) => re.test(host))) return NextResponse.next();
  if (STORE_HOSTS.includes(host.replace(/:\d+$/, ""))) return request.nextUrl.pathname === "/" ? NextResponse.rewrite(new URL("/store/welcome", request.url)) : NextResponse.next();
  const site = await resolveHost(host.replace(/:\d+$/, ""));
  if (!site) return new NextResponse("Not found", { status: 404 });
  const { pathname } = request.nextUrl;
  if (site.kind === "upload") {
    const path = pathname.replace(/^\/+/, "");
    return NextResponse.rewrite(new URL(`${API_URL}/api/v1/public/domains/${encodeURIComponent(host.replace(/:\d+$/, ""))}/${path}`));
  }
  // The generated Klaros site renders from its own page; its assets and API calls keep their normal paths.
  if (pathname === "/") return NextResponse.rewrite(new URL(`/w/${site.tenant_id}`, request.url));
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/|favicon\\.ico|icon\\.svg).*)"] };
