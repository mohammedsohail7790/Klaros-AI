// What this browser remembers about the shopper's last order, so they can find it again. Only the order number and its private link token; never an address or contact.
const KEY = "klaros_store_last_order";

export function rememberOrder(token: string, orderNumber: string): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ token, orderNumber }));
  } catch {
    // best-effort only
  }
}

export function lastOrder(): { token: string; orderNumber: string } | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return v && typeof v.token === "string" && typeof v.orderNumber === "string" ? v : null;
  } catch {
    return null;
  }
}

const KEY_STORE = "klaros_store_checkout_key";

/** One key per basket: a refresh, double click or flaky network gets the same order back; a changed basket gets a new key. */
export function checkoutKey(fingerprint: string): string {
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY_STORE) ?? "null");
    if (saved && saved.fp === fingerprint && typeof saved.key === "string") return saved.key;
  } catch {
    // fall through to a new key
  }
  const key = `web${crypto.randomUUID()}`;
  try {
    sessionStorage.setItem(KEY_STORE, JSON.stringify({ fp: fingerprint, key }));
  } catch {
    // best-effort only
  }
  return key;
}

/** The server's reasons name fields like `ship_to.postal_code`; show them in words. */
export function friendlyProblem(p: string): string {
  return p.replace(/ship_to\.postal_code/g, "ZIP code").replace(/ship_to\./g, "Delivery address: ").replace(/contact\./g, "Contact: ").replace(/_/g, " ");
}
