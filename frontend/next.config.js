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
const appPaths = ["/dashboard", "/business/:path*", "/medical-tourism/:path*", "/leads/:path*", "/customers/:path*", "/settings/:path*", "/setup", "/onboarding", "/login", "/register", "/forgot-password", "/reset-password", "/accept-invite"];

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      ...appPaths.map((source) => ({ source, headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }] })),
    ];
  },
};

module.exports = nextConfig;
