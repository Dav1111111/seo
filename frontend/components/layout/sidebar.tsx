"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  AlertTriangle,
  Workflow,
  Settings,
  TrendingUp,
} from "lucide-react";

const nav = [
  { href: "/",         label: "Обзор",    icon: LayoutDashboard },
  { href: "/issues",   label: "Проблемы", icon: AlertTriangle },
  { href: "/pipeline", label: "Pipeline", icon: Workflow },
  { href: "/settings", label: "Настройки",icon: Settings },
];

export function Sidebar() {
  const path = usePathname();
  return (
    <aside className="w-56 shrink-0 border-r bg-muted/40 flex flex-col">
      <div className="px-4 py-5 flex items-center gap-2 border-b">
        <TrendingUp className="h-5 w-5 text-primary" />
        <span className="font-semibold text-sm">Growth Tower</span>
      </div>
      <nav className="flex-1 p-3 space-y-1">
        {nav.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              path === href
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="p-3 border-t">
        <p className="text-xs text-muted-foreground">grandtourspirit.ru</p>
      </div>
    </aside>
  );
}
