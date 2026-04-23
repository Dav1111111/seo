"use client";

import { useSite } from "@/lib/site-context";
import { ChevronDown, Globe, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function SiteSwitcher() {
  const { sites, currentSite, setSiteId, loading } = useSite();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  if (loading) {
    return (
      <div className="px-3 py-2 text-xs text-muted-foreground flex items-center gap-2">
        <Loader2 className="h-3 w-3 animate-spin shrink-0" aria-hidden="true" />
        <span>Загружаю сайт…</span>
      </div>
    );
  }

  if (sites.length <= 1) {
    return (
      <div className="px-3 py-2 text-xs text-muted-foreground">
        {currentSite?.display_name || currentSite?.domain || "Сайт не выбран"}
      </div>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="Выбрать сайт"
        className="w-full flex items-center gap-2 px-3 py-2 text-xs rounded-md hover:bg-accent transition-colors"
      >
        <Globe className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="truncate font-medium">
          {currentSite?.display_name || currentSite?.domain || "Выбрать сайт"}
        </span>
        <ChevronDown className={`h-3 w-3 ml-auto text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Список сайтов"
          className="absolute bottom-full left-0 right-0 mb-1 bg-popover border rounded-md shadow-lg z-50"
        >
          {sites.map((site) => (
            <button
              type="button"
              key={site.id}
              onClick={() => { setSiteId(site.id); setOpen(false); }}
              className={`w-full text-left px-3 py-2 text-xs hover:bg-accent transition-colors first:rounded-t-md last:rounded-b-md ${
                site.id === currentSite?.id ? "bg-accent font-medium" : "text-muted-foreground"
              }`}
            >
              <div className="font-medium text-foreground">{site.display_name || site.domain}</div>
              <div className="text-[10px] text-muted-foreground">{site.domain}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
