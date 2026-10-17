// The one business's storefront settings. Environment values, so the name and contact can be set without a code change; the defaults are neutral on purpose.
// (When a name is chosen it can move into the store settings in Klaros.)
export const STORE_NAME = (process.env.NEXT_PUBLIC_STORE_NAME ?? "").trim() || "Online Grocery";
export const STORE_CONTACT_EMAIL = (process.env.NEXT_PUBLIC_STORE_CONTACT_EMAIL ?? "").trim();
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
