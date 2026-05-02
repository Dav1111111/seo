import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: process.cwd(),
  },
  allowedDevOrigins: ["c6b32ea267b.vps.myjino.ru"],
};

export default nextConfig;
