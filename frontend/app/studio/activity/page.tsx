"use client";

/**
 * Studio /activity — lente of recent pipeline events.
 *
 * Owner question this module answers: "что система делала сегодня, что
 * сломалось, что ещё крутится?"
 *
 * Backend contract: backend/app/api/v1/activity.py
 *   GET /admin/sites/{id}/activity              → last N events (history)
 *   GET /admin/sites/{id}/activity/last         → per-stage latest event
 *   GET /admin/sites/{id}/activity/current-run  → only current run events
 *
 * This page reuses the existing `<ActivityFeed />` component from the
 * dashboard surface — same renderer, dedicated route.
 */

import Link from "next/link";

import { useSite } from "@/lib/site-context";
import { ActivityFeed } from "@/components/dashboard/activity-feed";
import { Card, CardContent } from "@/components/ui/card";

export default function StudioActivityPage() {
  const { currentSite, loading } = useSite();
  const siteId = currentSite?.id || "";

  if (loading || !siteId) {
    return (
      <div className="container mx-auto px-4 py-8 max-w-4xl">
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">
              Сайт не выбран. Открой главную и выбери сайт.
            </p>
            <Link
              href="/studio"
              className="mt-4 inline-flex h-9 items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
            >
              На главную
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <header className="mb-6">
        <h1 className="text-3xl font-bold">Активность системы</h1>
        <p className="text-muted-foreground mt-2">
          Что коллекторы делали последние дни — какие стадии прошли, какие
          упали, какие ещё крутятся. Если на главной видишь карточку
          «технический сбой», здесь её первоисточник.
        </p>
      </header>

      <ActivityFeed siteId={siteId} />

      <div className="mt-6 text-sm text-muted-foreground">
        Подсказка: упавшие стадии (badge «failed») попадают в общий список
        советов на{" "}
        <Link href="/studio" className="underline">главной</Link>{" "}
        как «технические» карточки — туда сначала.
      </div>
    </div>
  );
}
