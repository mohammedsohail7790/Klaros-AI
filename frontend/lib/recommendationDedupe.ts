import type { Recommendation } from "./api";

/**
 * The backend keeps every generation run, so a business that was generated
 * more than once lists the same recommendation several times. Collapse rows
 * that mean the same thing (type + capability + provider + tool) into one:
 * a row the owner already decided on wins over an undecided one, otherwise the
 * newest wins. Superseded rows are never shown, and first-seen order is kept.
 *
 * `withinOneRequirement`: the rows are already known to serve the same requirement
 * (one group on the page), so two runs that tagged the same provider or tool with
 * different capability keys are still the same suggestion and the capability key is
 * left out of the identity.
 */
export function dedupeRecommendations(recs: Recommendation[], withinOneRequirement = false): Recommendation[] {
  const rank = (r: Recommendation) => (r.status === "PROPOSED" ? 0 : 1);
  const best = new Map<string, Recommendation>();
  const order: string[] = [];
  for (const rec of recs) {
    if (rec.status === "SUPERSEDED") continue;
    const capability = withinOneRequirement && rec.type !== "CAPABILITY" ? "" : rec.capability_key ?? "";
    const key = [rec.type, capability, rec.provider_key ?? "", rec.tool_name ?? ""].join("|");
    const current = best.get(key);
    if (!current) {
      best.set(key, rec);
      order.push(key);
    } else if (rank(rec) > rank(current) || (rank(rec) === rank(current) && rec.created_at > current.created_at)) {
      best.set(key, rec);
    }
  }
  return order.map((k) => best.get(k)!);
}
