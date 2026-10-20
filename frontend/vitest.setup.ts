import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// next/font/google is resolved by Next's compiler at build time; in tests every family just returns an empty class.
vi.mock("next/font/google", () => {
  const family = () => () => ({ className: "", variable: "", style: {} });
  return {
    Bricolage_Grotesque: family(),
    Instrument_Sans: family(),
    Cairo: family(),
    DM_Sans: family(),
    Inter: family(),
    Merriweather: family(),
    Playfair_Display: family(),
    Poppins: family(),
    Roboto: family(),
    Source_Serif_4: family(),
    Tajawal: family(),
  };
});

// jsdom lacks pointer-capture and scroll APIs that Sonner, Radix and cmdk call.
if (typeof Element !== "undefined") {
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.scrollIntoView ??= () => {};
}

// jsdom has no IntersectionObserver; framer-motion's scroll-reveal (whileInView) needs one. A no-op is enough: nothing scrolls in a test.
if (typeof globalThis.IntersectionObserver === "undefined") {
  class NoopIntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
    root = null;
    rootMargin = "";
    thresholds = [];
  }
  globalThis.IntersectionObserver = NoopIntersectionObserver as unknown as typeof IntersectionObserver;
}
