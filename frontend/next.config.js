/** @type {import('next').NextConfig} */
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  // Not a full script policy (that needs per-request nonces); these directives are safe with the current pages and close framing, base-tag and plugin abuse.
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'" },
];

// Pages that only make sense signed in: never index them.
const appPaths = ["/dashboard", "/business/:path*", "/medical-tourism/:path*", "/leads/:path*", "/customers/:path*", "/settings/:path*", "/setup", "/onboarding", "/login", "/register", "/forgot-password", "/reset-password", "/accept-invite", "/dropshipping/:path*", "/store/cart", "/store/checkout", "/store/orders/:path*"];

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Pages that only duplicated another page. They stay out of the navigation and send anyone with an old link to the page that replaced them.
  async redirects() {
    return [
      { source: "/dashboard", destination: "/business/home", permanent: false },
      { source: "/business/analytics", destination: "/business/home", permanent: false },
      { source: "/business/workflows", destination: "/automations", permanent: false },
      { source: "/business/integrations", destination: "/settings/integrations", permanent: false },
      { source: "/finance", destination: "/finance/invoices", permanent: false },
      { source: "/medical-tourism/leads", destination: "/medical-tourism/pipeline", permanent: false },
    ];
  },
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      ...appPaths.map((source) => ({ source, headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }] })),
    ];
  },
};

module.exports = nextConfig;
