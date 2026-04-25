"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { SiteSwitcher } from "@/components/layout/site-switcher";
import {
  LayoutDashboard,
  Flame,
  FileText,
  Settings,
  TrendingUp,
  Swords,
  Activity,
  FlaskConical,
  Sparkles,
} from "lucide-react";

// Two-section nav: Studio (the new world we're building) + Legacy
// (the existing screens). Legacy entries are kept fully working but
// labelled `(старая)` so the owner knows which set is the source of
// truth as we build out Studio. Removed atomically in PR-S9 once
// every Studio module covers its old counterpart — see
// docs/studio/CONCEPT.md §2.6.
const studioNav = [
  { href: "/studio",      label: "Студия",      icon: Sparkles },
];

const legacyNav = [
  { href: "/",            label: "Обзор (старый)",       icon: LayoutDashboard },
  { href: "/priorities",  label: "Приоритеты (старые)",  icon: Flame },
  { href: "/competitors", label: "Конкуренты (старые)",  icon: Swords },
  { href: "/reports",     label: "Отчёты (старые)",      icon: FileText },
  { href: "/connectors",  label: "Коннекторы (старые)",  icon: Activity },
  { href: "/playground",  label: "Playground",           icon: FlaskConical },
  { href: "/settings",    label: "Настройки",            icon: Settings },
];

export function Sidebar() {
  const path = usePathname();
  return (
    <aside className="w-56 shrink-0 border-r bg-muted/40 flex flex-col">
      <div className="px-4 py-5 flex items-center gap-2 border-b">
        <TrendingUp className="h-5 w-5 text-primary" />
        <span className="font-semibold text-sm">Growth Tower</span>
      </div>
      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        {studioNav.map(({ href, label, icon: Icon }) => {
          const active = path === href || path.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}

        <div className="pt-3 pb-1 px-3 text-[10px] uppercase tracking-wider text-muted-foreground/60">
          Старый интерфейс
        </div>

        {legacyNav.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              path === href
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="border-t">
        <SiteSwitcher />
      </div>
    </aside>
  );
}
