/**
 * Stable cache-key helper for Studio.
 *
 * Old pages (/competitors, /priorities) still call the same backend
 * endpoints Studio surfaces. If both use the same SWR key, mutating
 * one tree can silently invalidate the other and leak stale data
 * across sections. Studio always wraps its keys with this helper so
 * the namespaces never collide.
 *
 * Decision: docs/studio/IMPLEMENTATION.md §2.2.
 */
export function studioKey(...parts: Array<string | number | null | undefined>): string {
  return ["studio", ...parts.filter((p) => p !== null && p !== undefined)].join(":");
}
