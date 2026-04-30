import { redirect } from "next/navigation";

/**
 * Root route now redirects to the Studio. Sidebar no longer surfaces
 * the legacy Overview either, but a bookmark / inbound link to `/`
 * shouldn't dump the owner onto the old dashboard.
 *
 * Server-side redirect (308 Permanent) so:
 *   - bots and crawlers see the canonical destination
 *   - no flash of the old page on slow connections
 *
 * The legacy Overview component still exists at
 * `@/components/dashboard/overview` and is kept until PR-S9
 * (no earlier than 2026-05-11, per IMPLEMENTATION.md §2.6) — it
 * just no longer has a route mounted on `/`.
 */
export default function Home() {
  redirect("/studio");
}
