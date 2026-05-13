import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: process.cwd(),
  },
  allowedDevOrigins: ["c6b32ea267b.vps.myjino.ru"],
  // Prevent nginx/CDN/browsers from serving cached HTML shells after
  // deploys. Static assets under /_next/static still get aggressive
  // immutable caching (Next.js sets that itself and overrides here are
  // ignored per docs). These rules cover owner-facing HTML routes only.
  // Syntax verified against node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/headers.md.
  async headers() {
    const noStore = [
      { key: "Cache-Control", value: "no-store, must-revalidate" },
    ];
    return [
      { source: "/", headers: noStore },
      { source: "/studio/:path*", headers: noStore },
      { source: "/competitors", headers: noStore },
      { source: "/competitors/:path*", headers: noStore },
    ];
  },
};

export default nextConfig;
