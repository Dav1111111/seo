"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Zap, Copy, CheckCircle2, AlertCircle, Download } from "lucide-react";

/**
 * IndexNow setup section — three states:
 *
 *  1. Not configured: shows key + upload instructions + "Verify" button
 *  2. Verified but not pinged: "Push URLs to Yandex now" button
 *  3. Verified and pinged: last ping status + re-ping button
 *
 * Flow is explicit so the owner understands what they're doing — we
 * never auto-configure or lie about the setup state.
 */
export function IndexNowSetup({ siteId }: { siteId: string }) {
  const [verifying, setVerifying] = useState(false);
  const [pinging, setPinging] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [copied, setCopied] = useState<"key" | "url" | null>(null);

  const { data, mutate, isLoading } = useSWR(
    siteId ? ["indexnow-setup", siteId] : null,
    () => api.indexnowSetup(siteId),
    { revalidateOnFocus: false },
  );

  function copyToClipboard(text: string, label: "key" | "url") {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(label);
      setTimeout(() => setCopied(null), 1500);
    });
  }

  async function verify() {
    setVerifying(true);
    setMessage(null);
    try {
      const res = await api.indexnowVerify(siteId);
      if (res.verified) {
        setMessage({ kind: "ok", text: "Файл найден и совпадает — IndexNow подключён." });
        await mutate();
      } else {
        setMessage({
          kind: "err",
          text: res.hint_ru || `Проверка не прошла: ${res.reason ?? "неизвестная причина"}`,
        });
      }
    } catch (e: unknown) {
      setMessage({
        kind: "err",
        text: e instanceof Error ? e.message : "Проверка не удалась",
      });
    } finally {
      setVerifying(false);
    }
  }

  async function ping() {
    setPinging(true);
    setMessage(null);
    try {
      await api.indexnowPing(siteId);
      setMessage({
        kind: "ok",
        text: "URL отправлены в Яндекс. Обычно краулинг происходит в течение 24 часов.",
      });
      setTimeout(() => void mutate(), 8_000);
    } catch (e: unknown) {
      setMessage({
        kind: "err",
        text: e instanceof Error ? e.message : "Запуск не удался",
      });
    } finally {
      setTimeout(() => setPinging(false), 2_000);
    }
  }

  function downloadKeyFile() {
    if (!data) return;
    const blob = new Blob([data.file_content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${data.key}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  if (isLoading || !data) {
    return null;
  }

  const isVerified = Boolean(data.verified_at);
  const lastResult = data.last_result;

  return (
    <div className="mt-4 pt-4 border-t space-y-3">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-amber-600" />
          <span className="text-sm font-medium">Ускорить индексацию (IndexNow)</span>
          {isVerified ? (
            <Badge variant="outline" className="bg-emerald-50 text-emerald-800 border-emerald-300 text-xs">
              настроено
            </Badge>
          ) : (
            <Badge variant="outline" className="text-xs">
              не настроено
            </Badge>
          )}
        </div>
        {isVerified && (
          <Button size="sm" variant="outline" onClick={ping} disabled={pinging}>
            {pinging ? "Отправляю…" : "Пнуть Яндекс сейчас"}
          </Button>
        )}
      </div>

      {!isVerified && (
        <div className="rounded border bg-muted/30 p-3 space-y-3 text-xs">
          <p className="text-sm">
            Это прямой способ попросить Яндекс переобойти сайт — даже если Вебмастер
            висит в <code>HOST_NOT_LOADED</code>. Нужно один раз загрузить файл на сайт.
          </p>

          <div className="space-y-2">
            <div>
              <div className="text-muted-foreground mb-1">1. Скачай файл</div>
              <Button size="sm" variant="outline" onClick={downloadKeyFile}>
                <Download className="h-3 w-3 mr-2" />
                {data.key}.txt
              </Button>
            </div>

            <div>
              <div className="text-muted-foreground mb-1">2. Загрузи его в корень сайта так, чтобы открывался:</div>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded bg-background px-2 py-1 text-[11px] truncate border">
                  {data.file_url}
                </code>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => copyToClipboard(data.file_url, "url")}
                >
                  <Copy className="h-3 w-3" />
                  {copied === "url" ? "скопировано" : ""}
                </Button>
              </div>
            </div>

            <div>
              <div className="text-muted-foreground mb-1">3. Когда файл откроется, нажми:</div>
              <Button size="sm" onClick={verify} disabled={verifying}>
                {verifying ? "Проверяю…" : "Проверить установку"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {isVerified && (
        <div className="text-xs text-muted-foreground space-y-0.5">
          {data.last_pinged_at ? (
            <div>
              Последний пинг:{" "}
              <span className="text-foreground">
                {new Date(data.last_pinged_at).toLocaleString("ru")}
              </span>
              {lastResult && (
                <>
                  {" · "}
                  {lastResult.accepted ? (
                    <span className="text-emerald-700">
                      принято {lastResult.url_count} URL
                    </span>
                  ) : (
                    <span className="text-red-700">
                      отказано ({lastResult.error ?? "unknown"})
                    </span>
                  )}
                </>
              )}
            </div>
          ) : (
            <div>Подключено, но ни разу не пинговали. Нажми «Пнуть Яндекс сейчас».</div>
          )}
          <div>
            После каждого краулинга мы автоматически отправляем свежие URL в Яндекс.
          </div>
        </div>
      )}

      {message && (
        <div
          className={`rounded border px-2 py-1.5 text-xs ${
            message.kind === "ok"
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : "border-red-300 bg-red-50 text-red-900"
          }`}
        >
          <div className="flex items-start gap-1.5">
            {message.kind === "ok" ? (
              <CheckCircle2 className="h-3 w-3 mt-0.5 flex-shrink-0" />
            ) : (
              <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
            )}
            <span>{message.text}</span>
          </div>
        </div>
      )}
    </div>
  );
}
