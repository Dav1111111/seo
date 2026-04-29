"use client";

/**
 * Studio v2 etap 7 Phase C — free chat with the brain.
 *
 * Owner asks anything about the site — «почему Webmaster такое
 * показывает», «что такое индексация», «с чего начать», «где у меня
 * самая большая дыра». LLM gets the WHOLE site context (business
 * profile + understanding + full snapshot + current plan) and answers
 * grounded in that data. Hard rules in SYSTEM_PROMPT enforce no
 * fabrication.
 *
 * Different from /studio plan card chat (which is per-action) — this
 * is wider, free-form. Owner can navigate between modules from here
 * (assistant references plan items by title).
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Send,
  Sparkles,
  User,
  Trash2,
  Info,
} from "lucide-react";

import { api } from "@/lib/api";
import { useSite } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, getErrorMessage } from "@/lib/utils";


type ChatTurn = {
  role: "user" | "assistant";
  content: string;
};


// Starter questions tuned to what an owner actually wants to know
// when they first land on the chat. Three rows so they don't crowd.
const STARTER_QUESTIONS = [
  "С чего мне начать на этой неделе?",
  "Почему у меня в Webmaster показывается мало страниц в индексе?",
  "Что такое «вредная видимость» и почему это плохо?",
  "Какой запрос приоритетнее всего для меня?",
  "Что такое канонический URL?",
  "Какие конкретно услуги у меня без отдельной страницы?",
];


export default function StudioChatPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [pending, setPending] = useState(false);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [totalCost, setTotalCost] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, pending]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function send(text: string) {
    const message = text.trim();
    if (!message || pending || !siteId) return;
    setErr(null);
    const next: ChatTurn[] = [
      ...history,
      { role: "user", content: message },
    ];
    setHistory(next);
    setDraft("");
    setPending(true);
    try {
      const res = await api.studioBrainFreeChat(siteId, message, history);
      setHistory((h) => [
        ...h,
        { role: "assistant", content: res.reply || "" },
      ]);
      setTotalCost((c) => c + (res.cost_usd || 0));
    } catch (e: unknown) {
      setErr(getErrorMessage(e));
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

  function clearChat() {
    setHistory([]);
    setErr(null);
    setTotalCost(0);
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(draft);
    }
  }

  if (siteLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-3 max-w-4xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!currentSite) {
    return (
      <div className="p-4 sm:p-6 max-w-4xl">
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Сайт не выбран</div>
            <p className="text-sm text-muted-foreground">
              Выбери сайт в свитчере слева — помощник работает в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const showStarters = history.length === 0 && !pending;

  return (
    <div className="p-4 sm:p-6 max-w-4xl flex flex-col gap-4 h-[calc(100vh-4rem)]">
      {/* Header */}
      <div>
        <Link
          href="/studio"
          className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1 cursor-pointer"
        >
          <ArrowLeft className="h-3 w-3 mr-1" /> К Студии
        </Link>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Sparkles className="h-6 w-6 text-primary" />
          Помощник
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Спрашивай свободно — про индексацию, запросы, страницы, что
          показывает Webmaster, что такое любой термин. Помощник видит
          всё, что собрано по сайту, и отвечает только из этих данных.
        </p>
      </div>

      {/* Conversation */}
      <Card className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div
          ref={scrollRef}
          className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-4 space-y-4"
        >
          {showStarters && (
            <>
              <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground inline-flex items-start gap-2 max-w-fit">
                <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>
                  Помощник опирается только на данные из твоего сайта.
                  На вопросы вне данных он честно ответит «не знаю,
                  проверь в [модуль X]» — он не выдумывает.
                </span>
              </div>
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-2">
                  Можно начать с этих вопросов:
                </div>
                <div className="flex flex-wrap gap-2">
                  {STARTER_QUESTIONS.map((q, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => send(q)}
                      className={cn(
                        "text-sm rounded-full border px-3 py-1.5",
                        "border-foreground/15 hover:border-primary/40 hover:bg-primary/5",
                        "text-foreground/80 hover:text-foreground cursor-pointer",
                        "transition-colors",
                      )}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {history.map((t, i) => (
            <ChatBubble key={i} turn={t} />
          ))}

          {pending && (
            <div className="flex items-start gap-2 text-muted-foreground">
              <Sparkles className="h-4 w-4 mt-0.5 text-primary animate-pulse" />
              <span className="italic text-sm">помощник думает…</span>
            </div>
          )}

          {err && (
            <div className="rounded-md border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-sm">
              Не получилось спросить: {err}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t px-3 py-3 flex items-end gap-2">
          <textarea
            ref={inputRef}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Напиши свой вопрос. Enter — отправить, Shift+Enter — перенос строки."
            disabled={pending}
            className={cn(
              "flex-1 min-w-0 resize-none rounded-md border bg-background",
              "text-sm px-3 py-2 leading-snug max-h-40",
              "focus:outline-none focus:ring-2 focus:ring-primary/40",
              "disabled:opacity-50",
            )}
          />
          <Button
            type="button"
            size="default"
            onClick={() => send(draft)}
            disabled={pending || !draft.trim()}
            aria-label="Отправить"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </Card>

      {/* Footer: cost + clear */}
      <div className="flex items-center justify-between text-xs text-muted-foreground gap-3 flex-wrap">
        <span>
          {history.length === 0
            ? "Стоимость пока ноль — диалог ещё не начался."
            : `Сообщений: ${history.length} · потрачено $${totalCost.toFixed(4)}`}
        </span>
        {history.length > 0 && (
          <button
            type="button"
            onClick={clearChat}
            disabled={pending}
            className="inline-flex items-center gap-1 hover:text-foreground cursor-pointer disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Начать заново
          </button>
        )}
      </div>
    </div>
  );
}


function ChatBubble({ turn }: { turn: ChatTurn }) {
  const isUser = turn.role === "user";
  return (
    <div
      className={cn(
        "flex items-start gap-2",
        isUser && "flex-row-reverse",
      )}
    >
      <div
        className={cn(
          "h-7 w-7 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5",
          isUser
            ? "bg-primary/10 text-primary"
            : "bg-muted text-muted-foreground",
        )}
        aria-hidden="true"
      >
        {isUser ? (
          <User className="h-4 w-4" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
      </div>
      <div
        className={cn(
          "rounded-lg px-3 py-2 leading-snug whitespace-pre-wrap text-sm",
          "max-w-[85%]",
          isUser
            ? "bg-primary/10 text-foreground"
            : "bg-muted/50 text-foreground",
        )}
      >
        {turn.content}
      </div>
    </div>
  );
}
