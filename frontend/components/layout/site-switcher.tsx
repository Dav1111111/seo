"use client";

import { useSite } from "@/lib/site-context";
import { ChevronDown, Globe } from "lucide-react";
import { useState } from "react";

export function SiteSwitcher() {
  const { sites, currentSite, setSiteId } = useSite();
  const [open, setOpen] = useState(false);

  if (sites.length <= 1) {
    return (
      <div className="px-3 py-2 text-xs text-muted-foreground">
        {currentSite?.display_name || currentSite?.domain || "—"}
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs rounded-md hover:bg-accent transition-colors"
      >
        <Globe className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="truncate font-medium">
          {currentSite?.display_name || currentSite?.domain || "Выбрать сайт"}
        </span>
        <ChevronDown className={`h-3 w-3 ml-auto text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute bottom-full left-0 right-0 mb-1 bg-popover border rounded-md shadow-lg z-50">
          {sites.map((site) => (
            <button
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
