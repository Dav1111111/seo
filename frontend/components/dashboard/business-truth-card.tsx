"use client";

import useSWR from "swr";
import { useState } from "react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Brain, CheckCircle2, AlertTriangle, Inbox, RefreshCw,
  Target, Globe, TrendingUp,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Direction = {
  service: string;
  geo: string;
  strength_understanding: number;
  strength_content: number;
  strength_traffic: number;
  pages: string[];
  queries_sample: string[];
  mentioned_in: string[];
  is_confirmed: boolean;
  is_blind_spot: boolean;
  is_content_only: boolean;
  is_traffic_only: boolean;
  divergence_ru: string | null;
};

export function BusinessTruthCard({ siteId }: { siteId: string }) {
  const { data, isLoading, mutate } = useSWR(
    siteId ? `business-truth-${siteId}` : null,
    () => api.getBusinessTruth(siteId),
    { refreshInterval: 0 },
  );

  const [rebuilding, setRebuilding] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  async function onRebuild() {
    if (!siteId || rebuilding) return;
    setRebuilding(true);
    setBanner(null);
    try {
      await api.rebuildBusinessTruth(siteId);
      setBanner("Пересчёт запущен — обнови через 5–10 секунд.");
      setTimeout(() => mutate(), 8_000);
    } catch (e: any) {
      setBanner(e?.message ?? String(e));
    } finally {
      setRebuilding(false);
    }
  }

  if (isLoading) return <Skeleton className="h-64" />;

  const directions: Direction[] = data?.directions ?? [];

  if (directions.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Brain className="h-4 w-4" />
            Понимание бизнеса
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Платформа ещё не собрала понимание бизнеса. Это сводит вместе
            три картины: что ты сам сказал в онбординге, что реально лежит
            на страницах сайта, по чему к тебе идёт трафик из Яндекса.
          </p>
          <Button size="sm" onClick={onRebuild} disabled={rebuilding}>
            <RefreshCw className={cn("mr-2 h-4 w-4", rebuilding && "animate-spin")} />
            {rebuilding ? "Запускаю…" : "Собрать понимание"}
          </Button>
          {banner && <p className="text-xs text-muted-foreground">{banner}</p>}
        </CardContent>
      </Card>
    );
  }

  const confirmed = directions.filter((d) => d.is_confirmed && !d.divergence_ru);
  const blindSpots = directions.filter((d) => d.is_blind_spot);
  const trafficOnly = directions.filter((d) => d.is_traffic_only);
  const contentOnly = directions.filter((d) => d.is_content_only);
  const aspirations = directions.filter((d) =>
    d.strength_understanding > 0 &&
    d.strength_content === 0 &&
    d.strength_traffic === 0,
  );

  const coverage = data?.traffic_coverage;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <CardTitle className="text-base flex items-center gap-2">
            <Brain className="h-4 w-4" />
            Понимание бизнеса
            <Badge variant="outline" className="text-[10px]">
              {directions.length} направлений
            </Badge>
          </CardTitle>
          <Button size="sm" variant="outline" onClick={onRebuild} disabled={rebuilding}>
            <RefreshCw className={cn("mr-2 h-4 w-4", rebuilding && "animate-spin")} />
            {rebuilding ? "Пересчёт…" : "Пересобрать"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          Сведение трёх картин: что ты говоришь · что на сайте · куда идёт трафик.
          Расхождения — подсказки, что чинить.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner && (
          <p className="text-xs text-muted-foreground italic">{banner}</p>
        )}

        {/* Top-line counts */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <CountTile
            icon={CheckCircle2}
            label="Подтверждено"
            value={confirmed.length}
            tone="ok"
          />
          <CountTile
            icon={AlertTriangle}
            label="Слепые пятна"
            value={blindSpots.length}
            tone="warn"
          />
          <CountTile
            icon={Inbox}
            label="Незакрытый спрос"
            value={trafficOnly.length}
            tone="info"
          />
          <CountTile
            icon={TrendingUp}
            label="Покрытие трафика"
            value={coverage ? `${Math.round((coverage.coverage_share ?? 0) * 100)}%` : "—"}
            tone="slate"
          />
        </div>

        {/* Sections */}
        {confirmed.length > 0 && (
          <Section
            icon={CheckCircle2}
            label="Подтверждено всеми источниками"
            hint="Онбординг, сайт и трафик согласуются — копаем глубже здесь."
            directions={confirmed}
            tone="ok"
          />
        )}

        {blindSpots.length > 0 && (
          <Section
            icon={AlertTriangle}
            label="Слепые пятна SEO"
            hint="Страница есть, ты её подтвердил в онбординге, но трафика из Яндекса нет. Надо усилить страницу, чтобы начала ранжироваться."
            directions={blindSpots}
            tone="warn"
          />
        )}

        {trafficOnly.length > 0 && (
          <Section
            icon={Inbox}
            label="Незакрытый спрос"
            hint="Трафик приходит по этим направлениям, но отдельной страницы нет — люди уходят. Создай посадочную."
            directions={trafficOnly}
            tone="info"
          />
        )}

        {contentOnly.length > 0 && (
          <Section
            icon={Globe}
            label="Страницы без контекста"
            hint="Страница есть, но ты её не подтвердил в онбординге и трафика нет. Стоит уточнить — направление актуально или страница устарела."
            directions={contentOnly}
            tone="slate"
          />
        )}

        {aspirations.length > 0 && (
          <Section
            icon={Target}
            label="Амбиции без исполнения"
            hint="В онбординге указано, но на сайте страницы нет и трафика тоже. Либо создай страницу, либо убери из описания бизнеса."
            directions={aspirations}
            tone="slate"
          />
        )}

        {/* Coverage footnote */}
        {coverage && coverage.total_impressions > 0 && (
          <p className="text-[11px] text-muted-foreground pt-1 border-t">
            За последние 30 дней Яндекс показал сайт{" "}
            <b>{coverage.total_impressions.toLocaleString("ru")}</b> раз. Из них{" "}
            <b>{Math.round((coverage.coverage_share ?? 0) * 100)}%</b> запросов
            попадает под твои подтверждённые направления — остальное платформа
            пока не видит как «твой бизнес». Если процент низкий, это сигнал
            что словарь в онбординге узкий.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function CountTile({
  icon: Icon, label, value, tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | number;
  tone: "ok" | "warn" | "info" | "slate";
}) {
  const tones = {
    ok:    "bg-emerald-50 border-emerald-200 text-emerald-900",
    warn:  "bg-amber-50 border-amber-200 text-amber-900",
    info:  "bg-blue-50 border-blue-200 text-blue-900",
    slate: "bg-slate-50 border-slate-200 text-slate-700",
  }[tone];
  return (
    <div className={cn("rounded-lg border px-3 py-2 flex items-start gap-2", tones)}>
      <Icon className="h-4 w-4 shrink-0 mt-0.5" />
      <div>
        <div className="text-xl font-bold leading-none">{value}</div>
        <div className="text-[11px] mt-1 leading-tight">{label}</div>
      </div>
    </div>
  );
}

function Section({
  icon: Icon,
  label,
  hint,
  directions,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  hint: string;
  directions: Direction[];
  tone: "ok" | "warn" | "info" | "slate";
}) {
  const tones = {
    ok:    "border-emerald-200",
    warn:  "border-amber-300 bg-amber-50/30",
    info:  "border-blue-200 bg-blue-50/30",
    slate: "border-slate-200",
  }[tone];
  return (
    <details className={cn("rounded-lg border p-3", tones)} open={tone !== "ok"}>
      <summary className="cursor-pointer text-sm font-medium flex items-center gap-2">
        <Icon className="h-4 w-4" />
        {label}
        <Badge variant="outline" className="text-[10px]">
          {directions.length}
        </Badge>
      </summary>
      <p className="text-xs text-muted-foreground mt-1.5 leading-snug">{hint}</p>
      <ul className="mt-2 space-y-1.5">
        {directions.map((d) => (
          <li key={`${d.service}-${d.geo}`} className="rounded border bg-background p-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium">
                {d.service} · {d.geo}
              </span>
              <SourceStrengths d={d} />
            </div>
            {d.divergence_ru && (
              <p className="text-xs text-muted-foreground mt-1 leading-snug">
                {d.divergence_ru}
              </p>
            )}
            {d.pages.length > 0 && (
              <div className="text-[11px] text-muted-foreground mt-1 truncate">
                Страниц: {d.pages.length} · первая:{" "}
                <a
                  href={d.pages[0]}
                  target="_blank"
                  rel="noreferrer"
                  className="hover:underline"
                >
                  {d.pages[0].replace(/^https?:\/\/[^/]+/, "") || "/"}
                </a>
              </div>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function SourceStrengths({ d }: { d: Direction }) {
  const parts: Array<[string, number]> = [
    ["онб", d.strength_understanding],
    ["сайт", d.strength_content],
    ["трафик", d.strength_traffic],
  ];
  return (
    <span className="flex items-center gap-1">
      {parts.map(([label, value]) => (
        <Badge
          key={label}
          variant="outline"
          className={cn(
            "text-[10px] font-mono",
            value > 0
              ? "bg-slate-100 text-slate-800"
              : "bg-rose-50 text-rose-700 border-rose-200",
          )}
        >
          {label} {value > 0 ? `${Math.round(value * 100)}%` : "—"}
        </Badge>
      ))}
    </span>
  );
}
