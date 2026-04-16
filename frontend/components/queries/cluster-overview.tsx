"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Layers, Search, MousePointer, MapPin } from "lucide-react";

export function ClusterOverview({
  activeCluster,
  onSelectCluster,
}: {
  activeCluster: string;
  onSelectCluster: (name: string) => void;
}) {
  const siteId = useCurrentSiteId();

  const { data, isLoading } = useSWR(
    `clusters-${siteId}`,
    () => api.queryClusters(siteId),
    { refreshInterval: 120_000 }
  );

  const clusters = data?.clusters ?? [];
  const unclustered = data?.unclustered_count ?? 0;

  if (isLoading) {
    return (
      <div className="flex gap-2 overflow-x-auto pb-2">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-20 w-44 shrink-0 rounded-lg" />
        ))}
      </div>
    );
  }

  if (clusters.length === 0 && unclustered === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Layers className="h-3.5 w-3.5" />
          <span>Кластеры запросов</span>
        </div>
        {activeCluster && (
          <button
            onClick={() => onSelectCluster("")}
            className="text-xs text-primary hover:underline"
          >
            Сбросить фильтр
          </button>
        )}
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {clusters.map((cl: any) => {
          const isActive = activeCluster === cl.name;
          return (
            <Card
              key={cl.name}
              className={`shrink-0 cursor-pointer transition-all hover:shadow-sm ${
                isActive
                  ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                  : "hover:border-primary/20"
              }`}
              onClick={() => onSelectCluster(isActive ? "" : cl.name)}
            >
              <CardContent className="p-3 w-44">
                <div className="font-medium text-xs truncate">{cl.name}</div>
                <div className="flex items-center gap-3 mt-1.5 text-[10px] text-muted-foreground">
                  <span className="flex items-center gap-0.5">
                    <Search className="h-2.5 w-2.5" />
                    {cl.query_count}
                  </span>
                  <span className="flex items-center gap-0.5">
                    <MousePointer className="h-2.5 w-2.5" />
                    {cl.total_clicks}
                  </span>
                  {cl.avg_position != null && (
                    <span className="flex items-center gap-0.5">
                      <MapPin className="h-2.5 w-2.5" />
                      {cl.avg_position.toFixed(1)}
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground mt-1">
                  {cl.total_impressions.toLocaleString("ru")} показов
                </div>
              </CardContent>
            </Card>
          );
        })}

        {unclustered > 0 && (
          <Card
            className={`shrink-0 cursor-pointer transition-all hover:shadow-sm border-dashed ${
              activeCluster === "__none" ? "border-primary" : ""
            }`}
            onClick={() => onSelectCluster(activeCluster === "__none" ? "" : "__none")}
          >
            <CardContent className="p-3 w-36">
              <div className="text-xs text-muted-foreground">Без кластера</div>
              <div className="text-lg font-semibold mt-1">{unclustered}</div>
              <div className="text-[10px] text-muted-foreground">запросов</div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
