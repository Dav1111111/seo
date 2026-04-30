"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { SiteSwitcher } from "@/components/layout/site-switcher";
import {
  Sparkles,
  MessageCircle,
  UserSquare,
  Search,
  Telescope,
  FileText,
  Swords,
  BarChart3,
  History,
  Activity,
  Settings,
  TrendingUp,
  PanelLeftClose,
} from "lucide-react";

/**
 * Sidebar for the new Studio-first world.
 *
 * Three groups (purely visual headers, no collapse):
 *   СТУДИЯ           — entry points (План, Помощник, Профиль)
 *   РАБОТА С САЙТОМ  — modules (Запросы / Индексация / Страницы / …)
 *   ПОДКЛЮЧЕНИЯ      — Коннекторы + Настройки
 *
 * Legacy routes (`/`, `/priorities`, `/competitors`, `/reports`,
 * `/playground`) are intentionally NOT in the sidebar. They keep
 * working by direct URL until PR-S9 removes them physically (per
 * IMPLEMENTATION.md §2.6, no earlier than 2026-05-11). Hiding them
 * from the menu now stops the «mess of two parallel worlds» without
 * touching code.
 *
 * `exact` keeps a parent route from staying highlighted when the user
 * navigates into a sub-route. Today /studio needs it (so that
 * /studio/chat doesn't double-highlight «План работ»). For each module
 * with sub-routes (e.g. /studio/queries → /studio/queries/harmful) we
 * leave `exact` off so the parent stays active.
 */
type NavItem = {
  href: string;
  label: string;
  icon: typeof Sparkles;
  exact?: boolean;
};

type NavGroup = {
  title: string;
  items: NavItem[];
};

const NAV: NavGroup[] = [
  {
    title: "Студия",
    items: [
      { href: "/studio",         label: "План работ",      icon: Sparkles, exact: true },
      { href: "/studio/chat",    label: "Помощник",        icon: MessageCircle },
      { href: "/studio/profile", label: "Профиль бизнеса", icon: UserSquare },
    ],
  },
  {
    title: "Работа с сайтом",
    items: [
      { href: "/studio/queries",     label: "Запросы",     icon: Search },
      { href: "/studio/indexation",  label: "Индексация",  icon: Telescope },
      { href: "/studio/pages",       label: "Страницы",    icon: FileText },
      { href: "/studio/competitors", label: "Конкуренты",  icon: Swords },
      { href: "/studio/analytics",   label: "Аналитика",   icon: BarChart3 },
      { href: "/studio/outcomes",    label: "До / После",  icon: History },
    ],
  },
  {
    title: "Подключения",
    items: [
      { href: "/studio/connections", label: "Коннекторы", icon: Activity },
      { href: "/settings",           label: "Настройки",  icon: Settings },
    ],
  },
];


export function Sidebar({
  onNavigate,
  onClose,
}: {
  onNavigate?: () => void;
  onClose?: () => void;
}) {
  const path = usePathname();
  return (
    <aside className="w-64 md:w-56 shrink-0 border-r bg-muted/40 flex flex-col h-full">
      <div className="px-4 py-5 flex items-center gap-2 border-b">
        <TrendingUp className="h-5 w-5 text-primary" />
        <span className="font-semibold text-sm">Growth Tower</span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Скрыть меню"
            title="Скрыть меню"
            className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground transition-colors cursor-pointer"
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        )}
      </div>
      <nav className="flex-1 p-3 overflow-y-auto">
        {NAV.map((group, gIdx) => (
          <div
            key={group.title}
            className={cn("space-y-1", gIdx > 0 && "mt-4")}
          >
            <div className="px-3 pb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/60">
              {group.title}
            </div>
            {group.items.map(({ href, label, icon: Icon, exact }) => {
              const active = exact
                ? path === href
                : path === href || path.startsWith(href + "/");
              return (
                <Link
                  key={href}
                  href={href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors cursor-pointer",
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
          </div>
        ))}
      </nav>
      <div className="border-t">
        <SiteSwitcher />
      </div>
    </aside>
  );
}
