import type { Metadata } from "next";
import "./globals.css";
import { SiteProvider } from "@/lib/site-context";
import { AppShell } from "@/components/layout/app-shell";

export const metadata: Metadata = {
  title: "Yandex Growth Tower",
  description: "SEO Control Tower — мониторинг и аналитика",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className="h-full">
      <body className="h-full bg-background text-foreground antialiased">
        <SiteProvider>
          <AppShell>{children}</AppShell>
        </SiteProvider>
      </body>
    </html>
  );
}
