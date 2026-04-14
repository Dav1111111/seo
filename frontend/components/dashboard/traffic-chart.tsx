"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from "recharts";

export function TrafficChart({ siteId }: { siteId: string }) {
  const { data, isLoading } = useSWR(
    `traffic-${siteId}`,
    () => api.trafficMetrics(siteId, 30),
    { refreshInterval: 300_000 }
  );

  if (isLoading) return <Skeleton className="h-64 w-full" />;

  const rows: any[] = data?.data ?? [];
  if (!rows.length) {
    return (
      <Card>
        <CardHeader><CardTitle>Трафик (30 дней)</CardTitle></CardHeader>
        <CardContent className="flex items-center justify-center h-40 text-muted-foreground text-sm">
          Нет данных. Запустите сбор через Pipeline.
        </CardContent>
      </Card>
    );
  }

  const formatted = rows.map((r) => ({
    date: r.date.slice(5),
    Показы: r.impressions,
    Клики: r.clicks,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Трафик — показы и клики (30 дней)</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={formatted} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="Показы" stroke="#6366f1" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="Клики" stroke="#22c55e" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
