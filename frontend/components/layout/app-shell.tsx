"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";

/**
 * Hides the Sidebar on the onboarding wizard so the wizard gets the full
 * viewport without competing chrome. Everywhere else, renders the normal
 * dashboard shell.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isOnboarding = pathname?.startsWith("/onboarding");

  if (isOnboarding) {
    return <div className="min-h-full bg-background">{children}</div>;
  }

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
