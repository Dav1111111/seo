import Link from "next/link";
import {
  Activity, Search, Telescope, FileText, Swords,
  BarChart3, Megaphone, History, ChevronRight, Sparkles,
} from "lucide-react";

/**
 * Studio index — 8 module cards.
 *
 * No data calls here on purpose. Each card links into the module's own
 * page where the heavy lifting happens. Status text is sourced from
 * docs/studio/IMPLEMENTATION.md §1 and is updated module-by-module
 * as PRs land (see "ready" flag below).
 *
 * Concept: docs/studio/CONCEPT.md
 */

type ModuleStatus = "ready" | "stub" | "blocked";

const MODULES: Array<{
  href: string;
  title: string;
  description: string;
  icon: typeof Activity;
  status: ModuleStatus;
  reason?: string;
}> = [
  {
    href: "/studio/connections",
    title: "Коннекторы",
    description: "Живой статус всех Яндекс / Google / LLM API. Что подключено, что молчит, что отвечает с задержкой.",
    icon: Activity,
    status: "ready",
  },
  {
    href: "/studio/queries",
    title: "Запросы",
    description: "Какие запросы у нас собраны, объёмы (Wordstat), тренды. По каким мы видны, по каким — нет.",
    icon: Search,
    status: "ready",
  },
  {
    href: "/studio/indexation",
    title: "Индексация",
    description: "Сколько страниц в индексе Яндекса. Если мало — диагностика причины и что чинить.",
    icon: Telescope,
    status: "ready",
  },
  {
    href: "/studio/pages",
    title: "Страницы",
    description: "Workspace для каждой страницы: контент, ревью, рекомендации с применением и замером эффекта.",
    icon: FileText,
    status: "ready",
  },
  {
    href: "/studio/competitors",
    title: "Конкуренты",
    description: "Реальные конкуренты, что у них есть, чего нет у нас. Opportunities + список + gap'ы + сравнение.",
    icon: Swords,
    status: "ready",
  },
  {
    href: "/studio/analytics",
    title: "Аналитика",
    description: "Видимость в Яндексе (показы / клики / позиции), поведение посетителей, тренд индексации — за 30/90/180/365 дней.",
    icon: BarChart3,
    status: "ready",
  },
  {
    href: "/studio/ads",
    title: "Реклама",
    description: "Яндекс.Директ — бюджеты и эффективность. Google Ads, когда подключим.",
    icon: Megaphone,
    status: "blocked",
    reason: "ждём интеграцию Direct API",
  },
  {
    href: "/studio/outcomes",
    title: "До / После",
    description: "Каждое применённое изменение фиксируется до и через 14 дней — автоматический замер эффекта.",
    icon: History,
    status: "stub",
    reason: "PR-S8 (после 14 дней работы PR-S4)",
  },
];

function StatusBadge({ status, reason }: { status: ModuleStatus; reason?: string }) {
  if (status === "ready") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-300 px-2 py-0.5 text-[10px] font-medium">
        работает
      </span>
    );
  }
  if (status === "blocked") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-rose-50 text-rose-800 border border-rose-300 px-2 py-0.5 text-[10px] font-medium"
        title={reason}
      >
        ждёт
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-muted text-muted-foreground border px-2 py-0.5 text-[10px] font-medium"
      title={reason ? `появится в ${reason}` : undefined}
    >
      скоро · {reason ?? ""}
    </span>
  );
}

export default function StudioIndexPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Sparkles className="h-6 w-6 text-primary" />
          Студия
        </h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
          Каждый модуль отвечает за одну сущность — запросы, индексацию, страницы, конкурентов.
          Их можно запускать отдельно, видеть статус каждого, тестировать пошагово и наблюдать
          эффект до и после правок.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-2">
        {MODULES.map((m) => {
          const Icon = m.icon;
          const isLink = m.status === "ready";
          const inner = (
            <div
              className={
                "rounded-lg border bg-card p-4 transition-colors " +
                (isLink ? "hover:border-primary/50 cursor-pointer" : "opacity-70")
              }
            >
              <div className="flex items-start gap-3">
                <Icon className="h-5 w-5 text-primary mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium">{m.title}</span>
                    <StatusBadge status={m.status} reason={m.reason} />
                  </div>
                  <p className="text-xs text-muted-foreground mt-1.5 leading-snug">
                    {m.description}
                  </p>
                </div>
                {isLink && (
                  <ChevronRight className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                )}
              </div>
            </div>
          );
          return isLink ? (
            <Link key={m.href} href={m.href}>
              {inner}
            </Link>
          ) : (
            <div key={m.href}>{inner}</div>
          );
        })}
      </div>

      <div className="text-xs text-muted-foreground border-t pt-4">
        Концепция и принципы: <code>docs/studio/CONCEPT.md</code>. Очередь PR-ов и журнал решений:{" "}
        <code>docs/studio/IMPLEMENTATION.md</code>.
      </div>
    </div>
  );
}
