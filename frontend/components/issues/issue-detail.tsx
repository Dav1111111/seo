"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  AlertTriangle, CheckCircle, MessageCircle, Send, Loader2,
  ArrowRight, Calendar, Brain, Shield,
} from "lucide-react";

interface Issue {
  id: string;
  agent_name: string;
  issue_type: string;
  severity: string;
  confidence: number;
  title: string;
  description: string;
  recommendation: string;
  status: string;
  evidence: any;
  created_at: string | null;
  resolved_at: string | null;
}

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: "destructive",
  high: "destructive",
  medium: "secondary",
  low: "outline",
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: "Критическая",
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
};

const STATUS_LABEL: Record<string, string> = {
  open: "Открыта",
  review: "На проверке",
  acknowledged: "Принято",
  in_progress: "В работе",
  resolved: "Решена",
  false_positive: "Ложная",
  suppressed: "Скрыта",
};

export function IssueDetailDialog({
  issue,
  open,
  onClose,
  onStatusChange,
}: {
  issue: Issue | null;
  open: boolean;
  onClose: () => void;
  onStatusChange: (issueId: string, status: string) => void;
}) {
  const siteId = useCurrentSiteId();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Reset chat when issue changes
  useEffect(() => {
    setMessages([]);
    setChatOpen(false);
    setInput("");
  }, [issue?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading || !issue) return;

    const userMsg: Message = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      const res = await api.chat(siteId, text, history, issue.id);
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Ошибка при обращении к AI. Попробуйте ещё раз." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function startChat(prompt?: string) {
    setChatOpen(true);
    if (prompt) {
      setInput(prompt);
    }
  }

  if (!issue) return null;

  const evidenceAffected = issue.evidence?.affected ?? [];

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div className="mt-0.5">
              <AlertTriangle className={`h-5 w-5 ${
                issue.severity === "critical" || issue.severity === "high"
                  ? "text-destructive" : "text-muted-foreground"
              }`} />
            </div>
            <div className="flex-1 min-w-0">
              <DialogTitle className="text-base leading-snug">{issue.title}</DialogTitle>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <Badge variant={SEVERITY_COLOR[issue.severity] as any}>
                  {SEVERITY_LABEL[issue.severity] ?? issue.severity}
                </Badge>
                <Badge variant="outline">{STATUS_LABEL[issue.status] ?? issue.status}</Badge>
                <span className="text-xs text-muted-foreground flex items-center gap-1">
                  <Brain className="h-3 w-3" />
                  {Math.round(issue.confidence * 100)}% уверенность
                </span>
                {issue.created_at && (
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    <Calendar className="h-3 w-3" />
                    {new Date(issue.created_at).toLocaleDateString("ru")}
                  </span>
                )}
              </div>
            </div>
          </div>
        </DialogHeader>

        {/* Description */}
        {issue.description && (
          <div className="mt-2">
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">Описание</h4>
            <p className="text-sm leading-relaxed">{issue.description}</p>
          </div>
        )}

        {/* Recommendation */}
        {issue.recommendation && (
          <div className="mt-3 bg-muted/50 rounded-lg p-3">
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1 flex items-center gap-1">
              <ArrowRight className="h-3 w-3" />
              Рекомендация
            </h4>
            <p className="text-sm leading-relaxed">{issue.recommendation}</p>
          </div>
        )}

        {/* Evidence */}
        {evidenceAffected.length > 0 && (
          <div className="mt-3">
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              Затронутые запросы / страницы
            </h4>
            <div className="flex flex-wrap gap-1">
              {evidenceAffected.map((item: string, i: number) => (
                <span key={i} className="text-xs bg-muted rounded px-2 py-0.5">{item}</span>
              ))}
            </div>
          </div>
        )}

        {/* Meta */}
        <div className="mt-3 flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Shield className="h-3 w-3" />
            Агент: {issue.agent_name}
          </span>
          <span>Тип: {issue.issue_type}</span>
        </div>

        <Separator className="my-3" />

        {/* Actions */}
        <div className="flex items-center gap-2 flex-wrap">
          {issue.status === "open" && (
            <Button size="sm" variant="outline" onClick={() => onStatusChange(issue.id, "acknowledged")}>
              Принять
            </Button>
          )}
          {["open", "acknowledged", "in_progress"].includes(issue.status) && (
            <Button size="sm" onClick={() => onStatusChange(issue.id, "resolved")}>
              <CheckCircle className="h-3.5 w-3.5 mr-1" />
              Решено
            </Button>
          )}
          {issue.status !== "false_positive" && (
            <Button size="sm" variant="ghost" className="text-muted-foreground"
              onClick={() => onStatusChange(issue.id, "false_positive")}>
              Ложная
            </Button>
          )}

          <div className="flex-1" />

          <Button size="sm" variant="outline" onClick={() => startChat()}>
            <MessageCircle className="h-3.5 w-3.5 mr-1" />
            Обсудить с AI
          </Button>
        </div>

        {/* Inline AI Chat */}
        {chatOpen && (
          <div className="mt-3 border rounded-lg overflow-hidden">
            <div className="bg-muted/30 px-3 py-2 flex items-center gap-2 border-b">
              <MessageCircle className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-medium">AI-консультант — контекст проблемы загружен</span>
            </div>

            <div className="max-h-64 overflow-y-auto px-3 py-2 space-y-2">
              {messages.length === 0 && (
                <div className="text-center py-3 space-y-2">
                  <p className="text-xs text-muted-foreground">AI уже знает контекст этой проблемы. Спросите:</p>
                  <div className="flex flex-wrap gap-1 justify-center">
                    {[
                      "Объясни подробнее, что это значит?",
                      "Как это исправить пошагово?",
                      "Насколько это критично для бизнеса?",
                    ].map((q) => (
                      <button key={q} onClick={() => startChat(q)}
                        className="text-xs px-2 py-1 rounded-full border hover:bg-accent transition-colors">
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] rounded-lg px-3 py-1.5 text-xs whitespace-pre-wrap ${
                    msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
                  }`}>
                    {msg.content}
                  </div>
                </div>
              ))}

              {loading && (
                <div className="flex justify-start">
                  <div className="bg-muted rounded-lg px-3 py-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Думаю...
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            <div className="border-t px-2 py-1.5 flex gap-1.5">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                placeholder="Спросите про эту проблему..."
                className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
                disabled={loading}
              />
              <Button size="xs" variant="ghost" onClick={sendMessage} disabled={loading || !input.trim()}>
                <Send className="h-3 w-3" />
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
