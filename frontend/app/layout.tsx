import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/layout/sidebar";
import { ChatPanel } from "@/components/chat/chat-panel";
import { SiteProvider } from "@/lib/site-context";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Yandex Growth Tower",
  description: "SEO Control Tower — мониторинг и аналитика",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className="h-full">
      <body className={`${inter.className} h-full bg-background text-foreground antialiased`}>
        <SiteProvider>
          <div className="flex h-full">
            <Sidebar />
            <main className="flex-1 overflow-auto p-6">
              {children}
            </main>
            <ChatPanel />
          </div>
        </SiteProvider>
      </body>
    </html>
  );
}
