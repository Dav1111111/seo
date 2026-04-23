"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSite } from "@/lib/site-context";
import { Globe } from "lucide-react";

function WorkspaceBootState() {
  return (
    <div className="mx-auto max-w-4xl py-8">
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

  if (isOnboarding) {
    return <div className="min-h-full bg-background">{children}</div>;
  }

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6">
        {loading ? <WorkspaceBootState /> : children}
      </main>
    </div>
  );
}
