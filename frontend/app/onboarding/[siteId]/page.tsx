"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Send, Check, RotateCcw, Loader2 } from "lucide-react";

type Msg = { role: "assistant" | "user"; content: string };

type Draft = {
  services: string[];
  geo_primary: string[];
  geo_secondary: string[];
  narrative_ru: string;
};

type ChatState = {
  messages: Msg[];
  current: Draft;
  round: number;
  status: "active" | "confirmed" | "capped" | "pending";
};

const SUGGESTIONS: { label: string; prefill: string }[] = [
  { label: "+ Добавить услугу", prefill: "Добавьте услугу: " },
  { label: "− Убрать услугу",   prefill: "Уберите: " },
  { label: "Изменить географию", prefill: "Основная география: " },
  { label: "Всё верно, поехали", prefill: "Всё верно, поехали." },
];


export default function OnboardingChatPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const router = useRouter();

  // Fetch site-level state first — need to decide whether to run
  // BusinessUnderstandingAgent or jump straight to chat.
  const onbSWR = useSWR(
    siteId ? `onb-state-${siteId}` : null,
    () => api.onboardingState(siteId),
    { refreshInterval: 3_000 },
  );

  const onb = onbSWR.data;
  const understanding = onb?.understanding;
  const needsUnderstanding = !understanding?.narrative_ru;

  const [starting, setStarting] = useState(false);
  const [chat, setChat] = useState<ChatState | null>(null);
  const [pending, setPending] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Once BusinessUnderstandingAgent finished, kick off chat/start — idempotent.
  useEffect(() => {
    if (!siteId || !understanding?.narrative_ru || chat) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.onboardingChatStart(siteId);
        if (!cancelled) setChat(r as ChatState);
      } catch (e) {
        console.error("chat/start failed", e);
      }
    })();
    return () => { cancelled = true; };
  }, [siteId, understanding?.narrative_ru, chat]);

  // Auto-scroll on new message.
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [chat?.messages.length, pending]);

  const startUnderstanding = useCallback(async () => {
    if (!siteId) return;
    setStarting(true);
    try {
      await api.triggerUnderstandingAnalyze(siteId);
      // Poll until understanding arrives — swr handles it.
    } catch (e) {
      console.error(e);
      setStarting(false);
    }
  }, [siteId]);

  const send = useCallback(async (override?: string) => {
    if (!siteId || (!override && !input.trim()) || pending) return;
    const text = (override ?? input).trim();
    if (!text) return;
    setPending(true);
    // Optimistic append of user message.
    setChat((prev) =>
      prev
        ? { ...prev, messages: [...prev.messages, { role: "user", content: text }] }
        : prev,
    );
    setInput("");
    try {
      const r = await api.onboardingChatMessage(siteId, text);
      setChat({
        messages: r.messages,
        current: r.current,
        round: r.round,
        status: r.status,
      });
    } catch (e) {
      console.error("chat/message failed", e);
      // Rollback optimistic — last assistant message had an error.
      setChat((prev) => prev && {
        ...prev,
        messages: [
          ...prev.messages,
          {
            role: "assistant",
            content: "Не получилось обработать. Попробуйте ещё раз?",
          },
        ],
      });
    } finally {
      setPending(false);
      textareaRef.current?.focus();
    }
  }, [siteId, input, pending]);

  const finalize = useCallback(async () => {
    if (!siteId) return;
    setFinalizing(true);
    try {
      await api.onboardingChatFinalize(siteId);
      router.push("/");
    } catch (e) {
      console.error("finalize failed", e);
      setFinalizing(false);
    }
  }, [siteId, router]);

  const restart = useCallback(async () => {
    if (!siteId) return;
    if (!window.confirm("Начать онбординг заново? Текущий диалог будет сброшен.")) {
      return;
    }
    try {
      // Restart wipes onboarding_step + understanding.
      await api.restartOnboarding(siteId);
      setChat(null);
      setStarting(false);
      onbSWR.mutate();
    } catch (e) {
      console.error(e);
    }
  }, [siteId, onbSWR]);

  const hasUserReply = !!chat?.messages.find((m) => m.role === "user");
  const confirmed = chat?.status === "confirmed";
  const canConfirm = hasUserReply && !finalizing && !pending;

  // ---------- Layout ----------

  return (
    <div className="min-h-dvh flex flex-col bg-neutral-50 dark:bg-neutral-950">
      <header className="sticky top-0 z-10 border-b bg-background/80 backdrop-blur">
        <div className="max-w-3xl mx-auto h-14 px-4 flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate">{onb?.domain ?? "…"}</div>
          </div>
          <Button variant="ghost" size="sm" onClick={restart} className="text-muted-foreground">
            <RotateCcw className="h-3.5 w-3.5 mr-1" />
            Начать заново
          </Button>
          <Link href="/" className="text-xs text-muted-foreground hover:text-foreground">
            На дашборд
          </Link>
        </div>
      </header>

      <main ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
          {/* State: need to run understanding first */}
          {onbSWR.isLoading && !onb && (
            <div className="space-y-3">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-32 w-full" />
            </div>
          )}

          {onb && needsUnderstanding && (
            <div className="rounded-2xl bg-white dark:bg-neutral-900 border p-6 space-y-4">
              <h2 className="text-lg font-semibold">Давайте посмотрим на ваш сайт</h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Я прочитаю до 15 страниц вашего сайта и опишу то, что вижу, своими словами.
                Вы подтвердите или поправите — и всё. Никаких форм, никаких семи шагов.
              </p>
              <Button onClick={startUnderstanding} disabled={starting} size="lg">
                {starting ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Читаю страницы…
                  </>
                ) : (
                  <>Начать знакомство</>
                )}
              </Button>
              {starting && (
                <p className="text-xs text-muted-foreground">
                  Обычно занимает 15–30 секунд.
                </p>
              )}
            </div>
          )}

          {/* State: understanding ready, but chat not initialized yet */}
          {onb && !needsUnderstanding && !chat && (
            <div className="rounded-2xl bg-white dark:bg-neutral-900 border p-6 space-y-3">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-11/12" />
              <Skeleton className="h-4 w-3/4" />
              <p className="text-xs text-muted-foreground mt-2">Готовлю первое сообщение…</p>
            </div>
          )}

          {/* Chat */}
          {chat?.messages.map((m, i) => (
            <MessageBubble key={i} role={m.role} content={m.content} />
          ))}

          {pending && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground pl-3">
              <div className="flex gap-1">
                <span className="h-2 w-2 bg-muted-foreground/60 rounded-full animate-bounce [animation-delay:-0.3s]" />
                <span className="h-2 w-2 bg-muted-foreground/60 rounded-full animate-bounce [animation-delay:-0.15s]" />
                <span className="h-2 w-2 bg-muted-foreground/60 rounded-full animate-bounce" />
              </div>
              <span>Обновляю понимание…</span>
            </div>
          )}

          {/* Draft preview — small, informational */}
          {chat?.current && (chat.current.services.length > 0 || chat.current.geo_primary.length > 0) && (
            <DraftPreview draft={chat.current} />
          )}

          {/* Confirmed state */}
          {confirmed && (
            <div className="rounded-2xl bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900 p-6 text-center space-y-3">
              <div className="inline-flex h-12 w-12 rounded-full bg-emerald-500 items-center justify-center">
                <Check className="h-6 w-6 text-white" />
              </div>
              <h2 className="text-lg font-semibold">Описание бизнеса зафиксировано</h2>
              <p className="text-sm text-muted-foreground">
                {chat.current.services.length} {pluralServices(chat.current.services.length)}
                {" · "}
                {chat.current.geo_primary.length + chat.current.geo_secondary.length}{" "}
                {pluralGeo(chat.current.geo_primary.length + chat.current.geo_secondary.length)}
              </p>
              <Button size="lg" onClick={finalize} disabled={finalizing}>
                {finalizing ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Сохраняю…
                  </>
                ) : (
                  <>Перейти к анализу запросов</>
                )}
              </Button>
            </div>
          )}
        </div>
      </main>

      {/* Composer — hidden once confirmed */}
      {chat && !confirmed && (
        <footer className="sticky bottom-0 border-t bg-background">
          <div className="max-w-3xl mx-auto px-4 py-3 space-y-2">
            {/* Suggestion chips */}
            <div className="flex gap-2 overflow-x-auto no-scrollbar">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  onClick={() => {
                    setInput(s.prefill);
                    textareaRef.current?.focus();
                  }}
                  className="shrink-0 rounded-full border px-3 py-1 text-xs hover:bg-accent whitespace-nowrap cursor-pointer"
                  disabled={pending}
                >
                  {s.label}
                </button>
              ))}
            </div>

            <div className="relative">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="Например: добавьте джип-туры, уберите прокат"
                disabled={pending}
                rows={2}
                className={cn(
                  "w-full rounded-xl border bg-background pl-4 pr-14 py-3 text-base",
                  "resize-none min-h-[60px] max-h-40",
                  "focus:outline-none focus:ring-2 focus:ring-primary/40",
                  "disabled:opacity-50",
                )}
              />
              <Button
                size="icon"
                onClick={() => send()}
                disabled={pending || !input.trim()}
                className="absolute right-2 bottom-2 h-9 w-9"
                aria-label="Отправить"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>

            {hasUserReply && (
              <Button
                onClick={finalize}
                variant="outline"
                size="sm"
                disabled={!canConfirm}
                className="w-full"
              >
                <Check className="h-4 w-4 mr-2" />
                Использовать это понимание
              </Button>
            )}

            <p className="text-[11px] text-muted-foreground text-center">
              Enter — отправить · Shift+Enter — новая строка
            </p>
          </div>
        </footer>
      )}
    </div>
  );
}

// ---------- Pieces ----------

function MessageBubble({ role, content }: Msg) {
  if (role === "assistant") {
    return (
      <div className="flex">
        <div className="rounded-2xl rounded-tl-sm bg-white dark:bg-neutral-900 border p-5 shadow-sm max-w-[92%] whitespace-pre-wrap text-[15px] leading-relaxed">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-end">
      <div className="rounded-2xl rounded-tr-sm bg-primary text-primary-foreground px-4 py-3 max-w-[80%] whitespace-pre-wrap text-[15px]">
        {content}
      </div>
    </div>
  );
}

function DraftPreview({ draft }: { draft: Draft }) {
  return (
    <div className="rounded-xl border border-dashed bg-muted/30 p-4 space-y-2">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Текущее понимание
      </div>
      {draft.services.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs text-muted-foreground mr-1">Услуги:</span>
          {draft.services.map((s) => (
            <Badge key={s} variant="secondary" className="text-[11px]">{s}</Badge>
          ))}
        </div>
      )}
      {draft.geo_primary.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs text-muted-foreground mr-1">Основная гео:</span>
          {draft.geo_primary.map((g) => (
            <Badge key={g} className="text-[11px]">{g}</Badge>
          ))}
        </div>
      )}
      {draft.geo_secondary.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs text-muted-foreground mr-1">Дополнительно:</span>
          {draft.geo_secondary.map((g) => (
            <Badge key={g} variant="outline" className="text-[11px]">{g}</Badge>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Helpers ----------

function pluralServices(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "услуг";
  if (mod10 === 1) return "услуга";
  if (mod10 >= 2 && mod10 <= 4) return "услуги";
  return "услуг";
}

function pluralGeo(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "направлений";
  if (mod10 === 1) return "направление";
  if (mod10 >= 2 && mod10 <= 4) return "направления";
  return "направлений";
}

