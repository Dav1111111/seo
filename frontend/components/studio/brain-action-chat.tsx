"use client";

/**
 * Studio v2 etap 7 Phase B — inline chat for a specific brain action.
 *
 * Owner clicks «Спросить» on an ActionCard, this component expands
 * inline. Stateless on the server — we hold conversation history in
 * local state and POST the full history each turn. New conversation
 * every time the panel re-mounts (when the owner closes and reopens
 * the chat).
 *
 * Hard rules from the system prompt:
 *   - LLM only answers from the action+snapshot context
 *   - never invents new recommendations
 *   - says «не знаю» when data doesn't cover the question
 * UI surfaces this honestly — we don't claim AI-magic.
 */

import { useEffect, useRef, useState } from "react";
import { Send, Sparkles, User, X } from "lucide-react";

import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn, getErrorMessage } from "@/lib/utils";


type ChatTurn = {
  role: "user" | "assistant";
  content: string;
};


// Per-action suggested follow-ups. Tuned per id to be the questions
// owners actually ask (verified against grandtourspirit feedback).
const SUGGESTED_BY_ID: Record<string, string[]> = {
  "indexation:not_indexed": [
    "А почему именно эти страницы выпали?",
    "Что значит «excluded»?",
    "Можно ли вернуть быстро?",
  ],
  "queries:harmful": [
    "А почему Яндекс думает, что это про меня?",
    "Какой запрос выбросить первым?",
    "А вдруг это всё-таки мой запрос?",
  ],
  "missing_landings:create": [
    "С какой страницы начать?",
    "Что должно быть на новой странице?",
    "А может объединить две услуги в одну?",
  ],
  "review:unreviewed": [
    "Что вообще даёт ревью?",
    "С каких страниц лучше начать?",
    "Сколько это стоит?",
  ],
  "review:pending_recs": [
    "Какие применять первыми?",
    "Что значит «применил & замерить»?",
    "Можно ли отложить все сразу?",
  ],
  "outcomes:followup": [
    "А когда конкретно будут результаты?",
    "Что замеряется?",
    "А если позиции не выросли?",
  ],
};

const DEFAULT_SUGGESTED = [
  "Что это значит простыми словами?",
  "А с чего начать?",
  "Это точно важно?",
];


export function BrainActionChat({
  siteId,
  actionId,
  onClose,
}: {
  siteId: string;
  actionId: string;
  onClose: () => void;
}) {
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [pending, setPending] = useState(false);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-scroll to bottom on every new turn so the latest message
  // is always in view.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, pending]);

  // Focus textarea on mount so owner can start typing immediately.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const suggestions = SUGGESTED_BY_ID[actionId] || DEFAULT_SUGGESTED;
  const showSuggestions = history.length === 0 && !pending;

  async function send(text: string) {
    const message = text.trim();
    if (!message || pending) return;
    setErr(null);
    // Optimistically add the user turn so the input feels live.
    const next: ChatTurn[] = [...history, { role: "user", content: message }];
    setHistory(next);
    setDraft("");
    setPending(true);
    try {
      const res = await api.studioBrainActionChat(
        siteId,
        actionId,
        message,
        // Send the conversation BEFORE the new message — server appends.
        history,
      );
      setHistory((h) => [
        ...h,
        { role: "assistant", content: res.reply || "" },
      ]);
    } catch (e: unknown) {
      // Roll back the optimistic user turn? No — leave it visible so
      // owner sees what they asked, but show the error so retry is
      // possible.
      setErr(getErrorMessage(e));
    } finally {
      setPending(false);
      // Refocus textarea for the next question.
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter inserts newline. Standard chat UX.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(draft);
    }
  }

  return (
    <div className="rounded-lg border bg-background/80 mt-2 overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b bg-muted/30">
        <div className="text-xs font-medium text-foreground/80 inline-flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-primary" />
          Спросить про это действие
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Закрыть чат"
          className="text-muted-foreground hover:text-foreground cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* History */}
      <div
        ref={scrollRef}
        className="max-h-[360px] overflow-y-auto px-3 py-2 space-y-3 text-sm"
      >
        {history.length === 0 && !pending && (
          <div className="text-xs text-muted-foreground italic">
            Помощник видит ровно это действие и связанную с ним статистику
            по сайту — ничего больше. Не выдумывает: на вопросы вне данных
            честно отвечает «не знаю, проверь в модуле X».
          </div>
        )}

        {history.map((t, i) => (
          <ChatBubble key={i} turn={t} />
        ))}

        {pending && (
          <div className="flex items-start gap-2 text-muted-foreground">
            <Sparkles className="h-4 w-4 mt-0.5 text-primary animate-pulse" />
            <span className="italic">помощник думает…</span>
          </div>
        )}

        {err && (
          <div className="rounded-md border border-red-300 bg-red-50 text-red-900 px-2 py-1.5 text-xs">
            Не получилось спросить: {err}
          </div>
        )}
      </div>

      {/* Suggested questions on first turn */}
      {showSuggestions && (
        <div className="px-3 py-2 border-t flex flex-wrap gap-1.5">
          {suggestions.map((q, i) => (
            <button
              key={i}
              type="button"
              onClick={() => send(q)}
              disabled={pending}
              className={cn(
                "text-xs rounded-full border px-2.5 py-1",
                "border-foreground/15 hover:border-primary/40 hover:bg-primary/5",
                "text-foreground/80 hover:text-foreground cursor-pointer",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Composer */}
      <div className="px-3 py-2 border-t flex items-end gap-2">
        <textarea
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Спроси своими словами…"
          disabled={pending}
          className={cn(
            "flex-1 min-w-0 resize-none rounded-md border bg-background",
            "text-sm px-2.5 py-1.5 leading-snug",
            "focus:outline-none focus:ring-2 focus:ring-primary/40",
            "disabled:opacity-50",
          )}
        />
        <Button
          type="button"
          size="sm"
          onClick={() => send(draft)}
          disabled={pending || !draft.trim()}
          aria-label="Отправить"
        >
          <Send className="h-3.5 w-3.5" />
        </Button>
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
          "h-6 w-6 rounded-full flex items-center justify-center flex-shrink-0",
          isUser
            ? "bg-primary/10 text-primary"
            : "bg-muted text-muted-foreground",
        )}
        aria-hidden="true"
      >
        {isUser ? (
          <User className="h-3.5 w-3.5" />
        ) : (
          <Sparkles className="h-3.5 w-3.5" />
        )}
      </div>
      <div
        className={cn(
          "rounded-lg px-3 py-2 max-w-[85%] leading-snug whitespace-pre-wrap",
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
