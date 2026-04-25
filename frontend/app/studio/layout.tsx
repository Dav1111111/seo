import { ReactNode } from "react";

/**
 * Studio shell. Intentionally light — the global app-shell already
 * provides the sidebar + site switcher. We just give Studio pages a
 * shared max-width and breathing room so per-page layouts stay simple.
 *
 * Concept doc: docs/studio/CONCEPT.md
 */
export default function StudioLayout({ children }: { children: ReactNode }) {
  return <div className="max-w-6xl">{children}</div>;
}
