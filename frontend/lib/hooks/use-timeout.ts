"use client";

/**
 * `useTimeoutSetter` — small helper for the "click → optimistic state
 * → reset after Nms" pattern used by Studio trigger buttons.
 *
 * Without this, naive `setTimeout(() => setBusy(false), 3000)` calls
 * `setBusy` after the component has unmounted, which React 19 warns
 * about (and which can fire if the user clicks a trigger then quickly
 * navigates away).
 *
 * Returns a stable `set` function. All scheduled timeouts are cleared
 * on unmount.
 */

import { useCallback, useEffect, useRef } from "react";

export function useTimeoutSetter(): (cb: () => void, ms: number) => void {
  // Browser timer ids are numbers; `unknown` keeps it safe across
  // node/jsdom test environments without pulling in `@types/node`.
  const timers = useRef<Array<ReturnType<typeof setTimeout>>>([]);

  useEffect(() => {
    return () => {
      for (const t of timers.current) clearTimeout(t);
      timers.current = [];
    };
  }, []);

  return useCallback((cb: () => void, ms: number) => {
    const id = setTimeout(() => {
      // Drop our reference once it's fired.
      timers.current = timers.current.filter((t) => t !== id);
      cb();
    }, ms);
    timers.current.push(id);
  }, []);
}
