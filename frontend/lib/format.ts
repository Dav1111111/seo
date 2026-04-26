/**
 * Shared formatting helpers for Studio module pages.
 *
 * Why centralize:
 *   - `fmtAge` had 5 near-identical copies, all of which truncated at
 *     "N дн" — useless past 30 days. One source of truth, branches
 *     all the way out to years.
 *   - Russian plural agreement was hand-rolled (or skipped) per call
 *     site, producing things like "1 запросов" / "2 запросов". Use
 *     Intl.PluralRules to delegate the rule to the platform.
 *
 * Concept: docs/studio/CONCEPT.md §5 — be honest, no broken text.
 */

const PLURAL_RULES = new Intl.PluralRules("ru");

/**
 * Pick the correct Russian noun form for a count.
 *
 * @param n count
 * @param forms tuple [one, few, many] — e.g. ["запрос", "запроса", "запросов"]
 *
 * Examples:
 *   pluralRu(1, ["запрос", "запроса", "запросов"])  → "запрос"
 *   pluralRu(2, ["запрос", "запроса", "запросов"])  → "запроса"
 *   pluralRu(5, ["запрос", "запроса", "запросов"])  → "запросов"
 *   pluralRu(21, ["запрос", "запроса", "запросов"]) → "запрос"  (paucal)
 */
export function pluralRu(n: number, forms: [string, string, string]): string {
  const cat = PLURAL_RULES.select(n);
  if (cat === "one") return forms[0];
  if (cat === "few") return forms[1];
  return forms[2];
}

/**
 * Owner-facing relative age based on a UTC ISO timestamp.
 *
 * Branches:
 *   null / falsy           → "—"
 *   negative (clock skew)  → "только что"
 *   < 60 sec               → "только что"
 *   < 60 min               → "N мин назад"
 *   < 24 h                 → "N ч назад"
 *   < 30 days              → "N дн назад"
 *   < 365 days             → "N мес назад"
 *   else                   → "N лет назад"
 *
 * Uses 30 d = 1 month, 365 d = 1 year — close enough for an owner-facing
 * "когда последний раз обновлялось". For exact calendar-day diffs use
 * `fmtDayAge` instead (which works on yyyy-mm-dd strings).
 */
export function fmtAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "только что";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "только что";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} ${pluralRu(min, ["минута", "минуты", "минут"])} назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ${pluralRu(hr, ["час", "часа", "часов"])} назад`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} ${pluralRu(day, ["день", "дня", "дней"])} назад`;
  const month = Math.floor(day / 30);
  if (month < 12) return `${month} ${pluralRu(month, ["месяц", "месяца", "месяцев"])} назад`;
  const year = Math.floor(day / 365);
  return `${year} ${pluralRu(year, ["год", "года", "лет"])} назад`;
}

/**
 * Calendar-day age for a yyyy-mm-dd date string.
 *
 * Backend often returns "data freshness" as a date (no time), e.g.
 * "Webmaster has data through 2026-04-25". Treating that as
 * "2026-04-25T00:00:00Z" and taking ms-diff produces wrong results
 * for users in non-UTC zones — e.g. a Moscow user (UTC+3) at 23:00
 * local time sees the date as "tomorrow 03:00 UTC" and the lag reads
 * "1 день назад" instead of "сегодня".
 *
 * Fix: parse both as local-midnight, diff in calendar days.
 *
 *   fmtDayAge("2026-04-25") on 2026-04-25 (local) → "сегодня"
 *   fmtDayAge("2026-04-24") on 2026-04-25         → "1 день назад"
 *   fmtDayAge("2026-04-20") on 2026-04-25         → "5 дней назад"
 *   fmtDayAge("2026-03-25") on 2026-04-25         → "1 месяц назад"
 */
export function fmtDayAge(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return "—";
  const [, y, mo, d] = m;
  // Local-midnight parse for both target and "today" so the diff is
  // always a clean integer number of calendar days.
  const target = new Date(Number(y), Number(mo) - 1, Number(d));
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dayMs = 24 * 60 * 60 * 1000;
  const diff = Math.round((today.getTime() - target.getTime()) / dayMs);

  if (diff <= 0) return "сегодня";
  if (diff < 30) return `${diff} ${pluralRu(diff, ["день", "дня", "дней"])} назад`;
  const month = Math.floor(diff / 30);
  if (month < 12)
    return `${month} ${pluralRu(month, ["месяц", "месяца", "месяцев"])} назад`;
  const year = Math.floor(diff / 365);
  return `${year} ${pluralRu(year, ["год", "года", "лет"])} назад`;
}
