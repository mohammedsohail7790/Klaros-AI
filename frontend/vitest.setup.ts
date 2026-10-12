import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// next/font/google is resolved by Next's compiler at build time; in tests every family just returns an empty class.
vi.mock("next/font/google", () => {
  const family = () => () => ({ className: "", variable: "", style: {} });
  return {
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
