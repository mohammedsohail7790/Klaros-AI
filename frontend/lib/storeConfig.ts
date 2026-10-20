// The one business's storefront settings. Environment values override these, so the name, contact and address can change without a code change.
export const STORE_NAME = (process.env.NEXT_PUBLIC_STORE_NAME ?? "").trim() || "Clean Room";
export const STORE_CONTACT_EMAIL = (process.env.NEXT_PUBLIC_STORE_CONTACT_EMAIL ?? "").trim() || "info@cleanroom.com";
/** The public address of the store: used for social previews and canonical links. */
export const STORE_URL = (process.env.NEXT_PUBLIC_STORE_URL ?? "").trim().replace(/\/$/, "") || "https://www.cleanroom.com";
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
