"use client";

/**
 * Studio v2 etap 7 Phase C+D — free chat with the brain, persisted.
 *
 * Phase C made the chat work; Phase D makes it survive a page reload.
 * Conversations live in DB. URL is the source of truth for «which one
 * is open»: /studio/chat (no `?c`) → blank slate that becomes a new
 * conversation on first send. /studio/chat?c=<uuid> → hydrate from DB.
 *
 * Layout:
 *   ┌──────────┬─────────────────────────┐
 *   │ history  │  active conversation    │
 *   │  list    │  ───────────────────    │
 *   │  (24%)   │  composer at bottom     │
 *   └──────────┴─────────────────────────┘
 *
 * History list = past conversations. Click → URL flips, content reloads.
 * «Новый чат» = router.push('/studio/chat').
 *
 * Hard rules from SYSTEM_PROMPT still hold (no fabrication, refer to
 * plan, explain terms, trust owner overrides) — they're in
 * core_audit/brain/free_chat.py.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  ArrowLeft,
  Send,
  Sparkles,
  User,
  Trash2,
  Info,
  Plus,
  MessageCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, getErrorMessage } from "@/lib/utils";


type ChatTurn = {
  role: "user" | "assistant";
  content: string;
};


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
  const router = useRouter();
  const searchParams = useSearchParams();
  const conversationId = searchParams.get("c");

  // Local thread state. Hydrated from DB when `conversationId` is set.
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [pending, setPending] = useState(false);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [totalCost, setTotalCost] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // List of past conversations for the sidebar — refreshes after every
  // turn so a brand-new conversation appears immediately at the top.
  const {
    data: conversationList,
    mutate: mutateList,
  } = useSWR(
    siteId ? studioKey("conversations", siteId) : null,
    () => api.studioListConversations(siteId, 30),
  );

  // Hydrate the active thread from DB whenever conversationId changes.
  // Handles initial load, click on sidebar item, browser back/forward.
  const {
    data: activeConversation,
    isLoading: loadingConversation,
    mutate: mutateActive,
  } = useSWR(
    siteId && conversationId
      ? studioKey("conversation", siteId, conversationId)
      : null,
    () => api.studioGetConversation(siteId, conversationId!),
    { revalidateOnFocus: false },
  );

  // Sync DB → local state when the active conversation arrives. The
  // local state is what render uses; SWR data is the source of truth.
  useEffect(() => {
    if (activeConversation) {
      setHistory(
        activeConversation.messages.map((m) => ({
          role: m.role,
          content: m.content,
        })),
      );
      setTotalCost(activeConversation.total_cost_usd || 0);
    } else if (!conversationId) {
      // Blank slate (no `?c=`): clear local state.
      setHistory([]);
      setTotalCost(0);
    }
  }, [activeConversation, conversationId]);

  // Auto-scroll to bottom on new turns / pending state.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, pending]);

  // Focus the input on first mount + whenever the active conversation
  // switches, so owner can keep typing without a manual click.
  useEffect(() => {
    inputRef.current?.focus();
  }, [conversationId]);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || pending || !siteId) return;
      setErr(null);
      // Optimistic user bubble. The assistant turn arrives after the
      // round-trip; on error we keep the user bubble visible so retry
      // is possible.
      setHistory((h) => [...h, { role: "user", content: message }]);
      setDraft("");
      setPending(true);
      try {
        const res = await api.studioBrainFreeChat(
          siteId,
          message,
          conversationId,
        );
        setHistory((h) => [
          ...h,
          { role: "assistant", content: res.reply || "" },
        ]);
        setTotalCost((c) => c + (res.cost_usd || 0));
        // If this was the first message in a brand-new conversation,
        // the server just minted the id — pin it in the URL so a
        // refresh / share preserves the thread.
        if (!conversationId && res.conversation_id) {
          router.replace(`/studio/chat?c=${res.conversation_id}`);
        }
        // Sidebar list needs to refresh: new conversation appears,
        // existing one's last_message_at moves it to the top.
        await mutateList();
        if (conversationId) await mutateActive();
      } catch (e: unknown) {
        setErr(getErrorMessage(e));
      } finally {
        setPending(false);
        inputRef.current?.focus();
      }
    },
    [conversationId, pending, siteId, router, mutateList, mutateActive],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send(draft);
      }
    },
    [draft, send],
  );

  async function deleteConversation(id: string) {
    if (!siteId) return;
    if (!confirm("Удалить этот чат? Это действие нельзя отменить.")) return;
    try {
      await api.studioDeleteConversation(siteId, id);
    } catch (e: unknown) {
      setErr(getErrorMessage(e));
      return;
    }
    await mutateList();
    if (conversationId === id) {
      // We just nuked the open conversation; back to blank slate.
      router.replace("/studio/chat");
    }
  }

  if (siteLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-3 max-w-6xl">
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

  const showStarters =
    !conversationId && history.length === 0 && !pending && !loadingConversation;

  return (
    <div className="p-4 sm:p-6 max-w-6xl flex flex-col gap-4 h-[calc(100vh-4rem)]">
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

      {/* Two-column: history list + active conversation */}
      <div className="grid gap-4 lg:grid-cols-[260px_1fr] flex-1 min-h-0">
        {/* History sidebar */}
        <Card className="hidden lg:flex flex-col min-h-0 overflow-hidden">
          <div className="px-3 py-3 border-b flex items-center justify-between gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Мои чаты
            </span>
            <Link
              href="/studio/chat"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline cursor-pointer"
              title="Начать новый чат"
            >
              <Plus className="h-3.5 w-3.5" />
              Новый
            </Link>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto py-1">
            {!conversationList ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                загружаю…
              </div>
            ) : conversationList.length === 0 ? (
              <div className="px-3 py-3 text-xs text-muted-foreground leading-snug">
                Чатов пока нет. Задай первый вопрос — он появится в
                списке.
              </div>
            ) : (
              <ul className="space-y-0.5 px-1">
                {conversationList.map((c) => (
                  <ConversationListItem
                    key={c.id}
                    conv={c}
                    active={c.id === conversationId}
                    onDelete={() => deleteConversation(c.id)}
                  />
                ))}
              </ul>
            )}
          </div>
        </Card>

        {/* Active conversation */}
        <Card className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div
            ref={scrollRef}
            className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-4 space-y-4"
          >
            {loadingConversation && history.length === 0 && (
              <div className="text-sm text-muted-foreground">
                Загружаю чат…
              </div>
            )}

            {showStarters && (
              <>
                <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground inline-flex items-start gap-2 max-w-fit">
                  <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                  <span>
                    Помощник опирается только на данные из твоего сайта.
                    На вопросы вне данных он честно ответит «не знаю,
                    проверь в [модуль X]» — он не выдумывает. Чаты
                    сохраняются — можно вернуться через день.
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
      </div>

      {/* Footer: cost + new chat shortcut */}
      <div className="flex items-center justify-between text-xs text-muted-foreground gap-3 flex-wrap">
        <span>
          {history.length === 0
            ? "Стоимость пока ноль — диалог ещё не начался."
            : `Сообщений в чате: ${history.length} · потрачено $${totalCost.toFixed(4)}`}
        </span>
        {conversationId && (
          <Link
            href="/studio/chat"
            className="inline-flex items-center gap-1 hover:text-foreground cursor-pointer"
          >
            <Plus className="h-3.5 w-3.5" />
            Новый чат
          </Link>
        )}
      </div>
    </div>
  );
}


function ConversationListItem({
  conv,
  active,
  onDelete,
}: {
  conv: {
    id: string;
    title: string | null;
    message_count: number;
    last_message_at: string | null;
    created_at: string;
  };
  active: boolean;
  onDelete: () => void;
}) {
  const ts = conv.last_message_at || conv.created_at;
  const label = conv.title?.trim() || "Без названия";
  return (
    <li>
      <div
        className={cn(
          "group rounded-md px-2 py-1.5 flex items-start gap-2 transition-colors",
          active
            ? "bg-primary/10 text-foreground"
            : "hover:bg-accent text-foreground/80",
        )}
      >
        <Link
          href={`/studio/chat?c=${conv.id}`}
          className="flex-1 min-w-0 cursor-pointer"
        >
          <div className="flex items-start gap-1.5">
            <MessageCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-muted-foreground" />
            <div className="flex-1 min-w-0">
              <div className="text-sm leading-snug truncate">{label}</div>
              <div className="text-[10px] text-muted-foreground tabular-nums">
                {formatAge(ts)} · {conv.message_count}{" "}
                {pluralRu(conv.message_count, ["сообщ.", "сообщ.", "сообщ."])}
              </div>
            </div>
          </div>
        </Link>
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onDelete();
          }}
          aria-label="Удалить чат"
          className={cn(
            "opacity-0 group-hover:opacity-100 transition-opacity",
            "text-muted-foreground hover:text-red-700 flex-shrink-0 cursor-pointer",
            "p-0.5",
          )}
          title="Удалить чат"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </li>
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


function formatAge(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.round(diff / 1000);
    if (sec < 60) return "только что";
    const min = Math.round(sec / 60);
    if (min < 60) return `${min} мин назад`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr} ч назад`;
    const days = Math.round(hr / 24);
    if (days < 30) return `${days} д назад`;
    const months = Math.round(days / 30);
    return `${months} мес назад`;
  } catch {
    return iso;
  }
}


function pluralRu(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n);
  const n10 = abs % 10;
  const n100 = abs % 100;
  if (n10 === 1 && n100 !== 11) return forms[0];
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return forms[1];
  return forms[2];
}
