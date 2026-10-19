/**
 * Keeps the backend awake. The backend runs on a plan that puts it to sleep after about 15 quiet minutes, and the first request after that waits 15 to 50 seconds. Every public website
 * page is fetched from the backend, so a sleeping backend would make a visitor wait. Vercel calls this route on the schedule in vercel.json, and the route simply asks the backend how it is.
 * It always answers 200 (the JSON says whether the backend did), so a slow backend never turns into a failing scheduled job.
 */
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function GET() {
  const started = Date.now();
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store", signal: AbortSignal.timeout(55_000) });
    return Response.json({ ok: res.ok, status: res.status, ms: Date.now() - started });
  } catch {
    return Response.json({ ok: false, status: null, ms: Date.now() - started });
  }
}
