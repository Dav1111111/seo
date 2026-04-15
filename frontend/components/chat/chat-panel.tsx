"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Button } from "@/components/ui/button";
import { MessageCircle, X, Send, Loader2 } from "lucide-react";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export function ChatPanel() {
  const siteId = useCurrentSiteId();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: Message = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      const res = await api.chat(siteId, text, history);
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Ошибка при обращении к AI. Попробуйте ещё раз." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 h-14 w-14 rounded-full bg-primary text-primary-foreground shadow-lg flex items-center justify-center hover:scale-105 transition-transform z-50"
      >
        <MessageCircle className="h-6 w-6" />
      </button>
    );
  }

  return (
    <div className="fixed bottom-6 right-6 w-96 h-[500px] bg-background border rounded-xl shadow-2xl flex flex-col z-50">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <div className="flex items-center gap-2">
          <MessageCircle className="h-4 w-4 text-primary" />
          <span className="font-semibold text-sm">AI-консультант</span>
        </div>
        <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-muted-foreground text-sm mt-8">
            <p className="mb-2">Привет! Я AI-помощник.</p>
            <p>Спроси меня о любой проблеме на сайте — объясню простым языком.</p>
            <div className="mt-4 space-y-1 text-xs">
              <button onClick={() => setInput("Почему мало кликов?")} className="block mx-auto px-3 py-1 rounded-full border hover:bg-accent">Почему мало кликов?</button>
              <button onClick={() => setInput("Что такое позиция в поиске?")} className="block mx-auto px-3 py-1 rounded-full border hover:bg-accent">Что такое позиция в поиске?</button>
              <button onClick={() => setInput("Что мне сделать первым?")} className="block mx-auto px-3 py-1 rounded-full border hover:bg-accent">Что мне сделать первым?</button>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                msg.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted"
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-muted rounded-lg px-3 py-2 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Думаю...
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t px-3 py-2 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Спросите про SEO..."
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          disabled={loading}
        />
        <Button size="sm" variant="ghost" onClick={send} disabled={loading || !input.trim()}>
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
