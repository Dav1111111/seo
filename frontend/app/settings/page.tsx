"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { CheckCircle, XCircle } from "lucide-react";

const MODES = [
  {
    value: "readonly",
    label: "Read-only",
    desc: "Только мониторинг и сигналы. Никаких действий.",
  },
  {
    value: "recommend",
    label: "Рекомендации",
    desc: "Показывает рекомендации, но ничего не делает.",
  },
  {
    value: "propose",
    label: "Предложения",
    desc: "Создаёт черновики задач для одобрения.",
  },
  {
    value: "autoexecute",
    label: "Автопилот",
    desc: "Выполняет low-risk действия автоматически.",
  },
];

export default function SettingsPage() {
  const siteId = useCurrentSiteId();
  const { data: site, isLoading, mutate } = useSWR(
    `sites`,
    async () => {
      const sites = await api.sites();
      return sites.find((s: any) => s.id === siteId) || sites[0];
    }
  );

  const { data: health } = useSWR("health", api.health, { refreshInterval: 30_000 });

  const [mode, setMode] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (site?.operating_mode) setMode(site.operating_mode);
  }, [site]);

  async function saveMode() {
    if (!mode) return;
    setSaving(true);
    try {
      await api.updateSite(siteId, { operating_mode: mode });
      mutate();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-2xl font-bold">Настройки</h1>

      {/* System health */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Статус системы</CardTitle>
        </CardHeader>
        <CardContent className="flex gap-6 text-sm">
          {(["db", "redis"] as const).map((key) => (
            <div key={key} className="flex items-center gap-2">
              {health?.[key] === "connected"
                ? <CheckCircle className="h-4 w-4 text-green-500" />
                : <XCircle className="h-4 w-4 text-red-500" />}
              <span className="capitalize text-muted-foreground">{key}</span>
              <Badge variant={health?.[key] === "connected" ? "outline" : "destructive"} className="text-xs">
                {health?.[key] ?? "—"}
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Site info */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Сайт</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {isLoading ? <Skeleton className="h-16" /> : (
            <>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Домен</span>
                <span className="font-medium">{site?.domain ?? "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Webmaster host</span>
                <span className="font-mono text-xs">{site?.yandex_webmaster_host_id ?? "не задан"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Метрика counter</span>
                <span className="font-mono text-xs">{site?.yandex_metrica_counter_id ?? "не задан"}</span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Operating mode */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Режим работы</CardTitle>
          <CardDescription>Определяет что система делает автоматически.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? <Skeleton className="h-24" /> : (
            <>
              <div className="grid gap-2">
                {MODES.map((m) => (
                  <button
                    key={m.value}
                    onClick={() => setMode(m.value)}
                    className={`flex items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
                      mode === m.value ? "border-primary bg-primary/5" : "border-border hover:bg-accent"
                    }`}
                  >
                    <div className={`mt-0.5 h-4 w-4 rounded-full border-2 shrink-0 ${
                      mode === m.value ? "border-primary bg-primary" : "border-muted-foreground"
                    }`} />
                    <div>
                      <p className="font-medium text-sm">{m.label}</p>
                      <p className="text-xs text-muted-foreground">{m.desc}</p>
                    </div>
                  </button>
                ))}
              </div>
              <Button onClick={saveMode} disabled={saving || mode === site?.operating_mode}>
                {saved ? "Сохранено ✓" : saving ? "Сохраняю..." : "Сохранить"}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
