/**
 * @type {import('next').NextConfig}
 * Security headers (incl. CSP) are set here so they apply to every route,
 * whether served directly or behind the nginx reverse proxy.
 */
// CSP connect-src is host-based: a path like /backend would only match the
// exact path, silently breaking /backend/api/* calls (found via browser E2E).
const apiOrigin = (() => {
  if (!process.env.NEXT_PUBLIC_API_URL) return "";
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL).origin;
  } catch {
    return "";
  }
})();

const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'" + (process.env.NODE_ENV === "production" ? "" : " 'unsafe-eval'"),
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'" + (apiOrigin ? ` ${apiOrigin}` : ""),
  "frame-ancestors 'none'",
  "form-action 'self'",
  "base-uri 'self'",
  "object-src 'none'",
].join("; ");

// Where the browser reaches the API. Compose/nginx deployments proxy
// /backend/* to the internal service name; Vercel is outside that network, so
// there the proxy only exists when a real API base URL is configured.
const apiBase = (() => {
  const explicit = (process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "").trim();
  if (explicit) return explicit;
  if (!process.env.VERCEL) {
    return process.env.NODE_ENV === "production" ? "http://api:8000" : "http://localhost:8000";
  }
  console.warn(
    "[next.config] NEXT_PUBLIC_API_URL is unset on Vercel: /backend/* cannot be proxied to the FastAPI service.",
  );
  return "";
})();

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (!apiBase) return [];
    return [
      { source: "/backend/:path*", destination: `${apiBase}/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
