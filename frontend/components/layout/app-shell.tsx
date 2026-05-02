"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Sidebar } from "@/components/layout/sidebar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSite } from "@/lib/site-context";
import { cn } from "@/lib/utils";
import { Globe, Menu, TrendingUp, X } from "lucide-react";

const SIDEBAR_STORAGE_KEY = "gt-sidebar-collapsed";

function WorkspaceBootState() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <Card className="border-dashed">
        <CardHeader className="gap-2">
          <CardTitle className="flex items-center gap-2">
            <Globe className="h-4 w-4" aria-hidden="true" />
            Подключаю рабочее пространство
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Загружаю сайт, последние метрики и состояние дашборда…
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[...Array(4)].map((_, i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
          <Skeleton className="h-32" />
          <Skeleton className="h-48" />
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Hides the Sidebar on the onboarding wizard so the wizard gets the full
 * viewport without competing chrome. Everywhere else, renders the normal
 * dashboard shell.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { loading } = useSite();
  const isOnboarding = pathname?.startsWith("/onboarding");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [desktopCollapsed, setDesktopCollapsed] = useState(false);

  useEffect(() => {
    if (localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1") {
      setDesktopCollapsed(true);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, desktopCollapsed ? "1" : "0");
  }, [desktopCollapsed]);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  function onTopBarToggle() {
    if (typeof window !== "undefined" && window.matchMedia("(min-width: 768px)").matches) {
      setDesktopCollapsed((v) => !v);
    } else {
      setMobileOpen((v) => !v);
    }
  }

  if (isOnboarding) {
    return <div className="min-h-full bg-background">{children}</div>;
  }

  return (
    <div className="flex h-full">
      {/* Desktop sidebar — collapsible */}
      <div className={cn("hidden", !desktopCollapsed && "md:flex")}>
        <Sidebar onClose={() => setDesktopCollapsed(true)} />
      </div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}
      <div
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex md:hidden transition-transform duration-200 ease-out",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
        role="dialog"
        aria-modal="true"
        aria-label="Меню навигации"
      >
        <Sidebar
          onNavigate={() => setMobileOpen(false)}
          onClose={() => setMobileOpen(false)}
        />
      </div>

      <div className="flex flex-1 flex-col min-w-0">
        {/* Top bar — always visible on mobile, on desktop only when sidebar is collapsed */}
        <header
          className={cn(
            "sticky top-0 z-30 flex items-center gap-3 border-b bg-background/90 px-4 py-3 backdrop-blur print:hidden",
            !desktopCollapsed && "md:hidden",
          )}
        >
          <button
            type="button"
            onClick={onTopBarToggle}
            aria-label={mobileOpen ? "Закрыть меню" : "Открыть меню"}
            aria-expanded={mobileOpen}
            className="-ml-1 inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-accent transition-colors"
          >
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-primary" aria-hidden="true" />
            <span className="font-semibold text-sm">Growth Tower</span>
          </div>
        </header>

        <main className="flex-1 overflow-auto">
          {loading ? <WorkspaceBootState /> : children}
        </main>
      </div>
    </div>
  );
}
