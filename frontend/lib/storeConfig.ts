// The one business's storefront settings. Environment values override these, so the name, contact and address can change without a code change.
export const STORE_NAME = (process.env.NEXT_PUBLIC_STORE_NAME ?? "").trim() || "Clean Room";
/** The legal business behind the brand (shown in the footer). */
export const STORE_COMPANY = (process.env.NEXT_PUBLIC_STORE_COMPANY ?? "").trim() || "Clean Room LLC";
export const STORE_CONTACT_EMAIL = (process.env.NEXT_PUBLIC_STORE_CONTACT_EMAIL ?? "").trim() || "info@yourcleanroom.com";
/** The public address of the store: used for social previews and canonical links. */
export const STORE_URL = (process.env.NEXT_PUBLIC_STORE_URL ?? "").trim().replace(/\/$/, "") || "https://www.yourcleanroom.com";
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
