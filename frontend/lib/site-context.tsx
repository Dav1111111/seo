"use client";

import { createContext, useContext, useState, useEffect } from "react";
import { api } from "@/lib/api";

interface Site {
  id: string;
  domain: string;
  display_name: string | null;
  operating_mode: string;
  is_active: boolean;
}

interface SiteContextType {
  sites: Site[];
  currentSite: Site | null;
  setSiteId: (id: string) => void;
  loading: boolean;
}

const SiteContext = createContext<SiteContextType>({
  sites: [],
  currentSite: null,
  setSiteId: () => {},
  loading: true,
});

export function SiteProvider({ children }: { children: React.ReactNode }) {
  const [sites, setSites] = useState<Site[]>([]);
  const [siteId, setSiteId] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.sites().then((list) => {
      setSites(list);
      // Restore from localStorage or pick first
      const saved = localStorage.getItem("gt_site_id");
      const found = list.find((s: Site) => s.id === saved);
      setSiteId(found ? found.id : list[0]?.id || "");
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleSetSite = (id: string) => {
    setSiteId(id);
    localStorage.setItem("gt_site_id", id);
  };

  const currentSite = sites.find((s) => s.id === siteId) || null;

  return (
    <SiteContext.Provider value={{ sites, currentSite, setSiteId: handleSetSite, loading }}>
      {children}
    </SiteContext.Provider>
  );
}

export function useSite() {
  return useContext(SiteContext);
}

export function useCurrentSiteId(): string {
  const { currentSite } = useSite();
  return currentSite?.id || "";
}
