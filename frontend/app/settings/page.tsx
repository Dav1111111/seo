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
import { CheckCircle, XCircle, X as XIcon, Plus, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";

const MODES = [
  {
    value: "readonly",
    label: "Только мониторинг",
    desc: "Система собирает данные, но ничего не советует.",
  },
  {
    value: "recommend",
    label: "Рекомендации",
    desc: "Собирает данные и выдаёт план действий. Ничего не делает сама.",
  },
];

export default function SettingsPage() {
  const siteId = useCurrentSiteId();
  const router = useRouter();
  const { data: site, isLoading, mutate } = useSWR(
    siteId ? `sites-${siteId}` : null,
    async () => {
      const sites = await api.sites();
      return sites.find((s: any) => s.id === siteId) || sites[0];
    }
  );

  const { data: health } = useSWR("health", api.health, { refreshInterval: 30_000 });

  const [mode, setMode] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Competitor list editing
  const [competitors, setCompetitors] = useState<string[]>([]);
  const [newCompetitor, setNewCompetitor] = useState("");
  const [compSaving, setCompSaving] = useState(false);
  const [compSaved, setCompSaved] = useState(false);

  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    if (site?.operating_mode) setMode(site.operating_mode);
    if (Array.isArray(site?.competitor_domains)) {
      setCompetitors(site.competitor_domains);
    }
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

  function cleanDomain(d: string): string {
    return d.trim().toLowerCase()
      .replace(/^https?:\/\//, "")
      .replace(/^www\./, "")
      .replace(/\/+$/, "");
  }

  function addCompetitor() {
    const d = cleanDomain(newCompetitor);
    if (!d) return;
    if (competitors.includes(d)) {
      setNewCompetitor("");
      return;
    }
    setCompetitors([...competitors, d]);
    setNewCompetitor("");
  }

  function removeCompetitor(d: string) {
    setCompetitors(competitors.filter((x) => x !== d));
  }

  async function saveCompetitors() {
    if (!siteId) return;
    setCompSaving(true);
    try {
      await api.updateCompetitorsList(siteId, competitors);
      mutate();
      setCompSaved(true);
      setTimeout(() => setCompSaved(false), 2000);
    } finally {
      setCompSaving(false);
    }
  }

  async function onRestartOnboarding() {
    if (!siteId) return;
    const ok = window.confirm(
      "Запустить онбординг заново? Собранная аналитика сохранится, " +
      "но LLM-понимание бизнеса будет построено с нуля.",
    );
    if (!ok) return;
    setRestarting(true);
    try {
      await api.restartOnboarding(siteId);
      router.push(`/onboarding/${siteId}`);
    } catch (e) {
      setRestarting(false);
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

      {/* Competitors manual edit */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Конкуренты (ручной список)</CardTitle>
          <CardDescription>
            Автоматически находятся при разведке по Яндекс-выдаче. Здесь можно добавить
            тех, кого алгоритм пропустил, или удалить лишних.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {competitors.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              Список пуст. Конкуренты появятся после запуска разведки на странице «Конкуренты».
            </p>
          ) : (
            <ul className="space-y-1.5">
              {competitors.map((d) => (
                <li
                  key={d}
                  className="flex items-center justify-between gap-2 rounded border px-3 py-1.5 text-sm"
                >
                  <a
                    href={`https://${d}`}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono hover:underline"
                  >
                    {d}
                  </a>
                  <button
                    type="button"
                    onClick={() => removeCompetitor(d)}
                    className="text-muted-foreground hover:text-rose-600"
                    title="Удалить"
                  >
                    <XIcon className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={newCompetitor}
              onChange={(e) => setNewCompetitor(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addCompetitor();
                }
              }}
              placeholder="example.ru"
              className="flex-1 rounded border px-3 py-1.5 text-sm bg-background"
            />
            <Button size="sm" variant="outline" onClick={addCompetitor} disabled={!newCompetitor.trim()}>
              <Plus className="mr-1 h-4 w-4" /> Добавить
            </Button>
          </div>
          <Button onClick={saveCompetitors} disabled={compSaving}>
            {compSaved ? "Сохранено ✓" : compSaving ? "Сохраняю…" : "Сохранить список"}
          </Button>
        </CardContent>
      </Card>

      {/* Danger zone */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Запустить онбординг заново</CardTitle>
          <CardDescription>
            Если LLM-понимание бизнеса устарело (изменились услуги, регион, позиционирование) —
            пройди 7 шагов ещё раз. Собранная аналитика не пострадает.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={onRestartOnboarding}
            disabled={restarting}
          >
            <RotateCcw className={`mr-2 h-4 w-4 ${restarting ? "animate-spin" : ""}`} />
            {restarting ? "Готовлю…" : "Начать онбординг заново"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
