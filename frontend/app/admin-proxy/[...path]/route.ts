import { NextRequest } from "next/server";

const BACKEND = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";
const ADMIN_KEY = process.env.ADMIN_API_KEY || "";

async function proxy(req: NextRequest, path: string[]) {
  if (!ADMIN_KEY) {
    return new Response(
      JSON.stringify({ error: "ADMIN_API_KEY not configured on server" }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }
  const target = `${BACKEND}/api/v1/admin/${path.join("/")}${req.nextUrl.search}`;
  const body = ["GET", "HEAD"].includes(req.method)
    ? undefined
    : await req.arrayBuffer();

  const upstream = await fetch(target, {
    method: req.method,
    headers: {
      "X-Admin-Key": ADMIN_KEY,
      "Content-Type": req.headers.get("content-type") || "application/json",
    },
    body,
  });

  const respBody = await upstream.arrayBuffer();
  return new Response(respBody, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/json",
    },
  });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
