"use client";

import { useState, useRef, useEffect } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Search, TrendingUp, TrendingDown, MessageCircle,
  Send, Loader2, BarChart3,
} from "lucide-react";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from "recharts";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export function QueryDetailDialog({
  query,
  open,
  onClose,
}: {
  query: any | null;
  open: boolean;
  onClose: () => void;
}) {
  const siteId = useCurrentSiteId();
  const [days, setDays] = useState(30);
  const [chatOpen, setChatOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: historyData, isLoading: historyLoading } = useSWR(
    query && open ? `query-history-${query.id}-${days}` : null,
    () => api.queryHistory(siteId, query.id, days),
  );

  // Reset chat when query changes
  useEffect(() => {
    setMessages([]);
    setChatOpen(false);
    setInput("");
  }, [query?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading || !query) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      // Send query context as part of the message
      const contextMsg = messages.length === 0
        ? `Контекст: запрос "${query.query_text}", позиция ${query.current?.avg_position ?? "н/д"}, показов ${query.current?.impressions ?? 0}, кликов ${query.current?.clicks ?? 0}. Мой вопрос: ${text}`
        : text;
      const res = await api.chat(siteId, contextMsg, history);
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Ошибка при обращении к AI." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  if (!query) return null;

  const history = historyData?.history ?? [];
  const chartData = history.map((p: any) => ({
    date: p.date.slice(5), // MM-DD
    position: p.avg_position,
    impressions: p.impressions,
    clicks: p.clicks,
  }));

  const posDelta = query.changes?.position_delta;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <Search className="h-5 w-5 text-primary mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <DialogTitle className="text-base leading-snug">{query.query_text}</DialogTitle>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                {query.cluster && (
                  <Badge variant="secondary">{query.cluster}</Badge>
                )}
                {query.wordstat_volume != null && (
                  <Badge variant="outline">
                    <BarChart3 className="h-3 w-3 mr-1" />
                    {query.wordstat_volume.toLocaleString("ru")} / мес
                  </Badge>
                )}
                {query.is_branded && (
                  <Badge variant="outline">Бренд</Badge>
                )}
              </div>
            </div>
          </div>
        </DialogHeader>

        {/* Metrics summary */}
        <div className="grid grid-cols-4 gap-3 mt-2">
          <MetricCard
            label="Позиция"
            value={query.current?.avg_position?.toFixed(1) ?? "—"}
            delta={posDelta}
            deltaLabel={posDelta != null ? (posDelta > 0 ? "лучше" : "хуже") : undefined}
            good={posDelta != null && posDelta > 0}
          />
          <MetricCard
            label="Показы"
            value={query.current?.impressions ?? 0}
            pctChange={query.changes?.impressions_pct}
          />
          <MetricCard
            label="Клики"
            value={query.current?.clicks ?? 0}
            pctChange={query.changes?.clicks_pct}
          />
          <MetricCard
            label="CTR"
            value={query.current?.impressions > 0
              ? (query.current.ctr * 100).toFixed(1) + "%"
              : "—"}
          />
        </div>

        {/* Period selector */}
        <div className="flex gap-1 mt-3">
          {[7, 14, 30].map((d) => (
            <Button
              key={d}
              size="xs"
              variant={days === d ? "default" : "outline"}
              onClick={() => setDays(d)}
              className="text-xs"
            >
              {d} дней
            </Button>
          ))}
        </div>

        {/* Position chart */}
        <div className="mt-2">
          {historyLoading ? (
            <Skeleton className="h-48 rounded-lg" />
          ) : chartData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
              Нет данных за выбранный период
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis
                  yAxisId="pos"
                  reversed
                  domain={["auto", "auto"]}
                  tick={{ fontSize: 10 }}
                  label={{ value: "Позиция", angle: -90, position: "insideLeft", style: { fontSize: 10 } }}
                />
                <YAxis
                  yAxisId="imp"
                  orientation="right"
                  tick={{ fontSize: 10 }}
                  label={{ value: "Показы", angle: 90, position: "insideRight", style: { fontSize: 10 } }}
                />
                <Tooltip contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line
                  yAxisId="pos"
                  type="monotone"
                  dataKey="position"
                  stroke="#6366f1"
                  name="Позиция"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  yAxisId="imp"
                  type="monotone"
                  dataKey="impressions"
                  stroke="#22c55e"
                  name="Показы"
                  strokeWidth={1.5}
                  dot={false}
                />
                <Line
                  yAxisId="imp"
                  type="monotone"
                  dataKey="clicks"
                  stroke="#f59e0b"
                  name="Клики"
                  strokeWidth={1.5}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        <Separator className="my-2" />

        {/* AI Chat button */}
        <div className="flex justify-end">
          <Button size="sm" variant="outline" onClick={() => setChatOpen(!chatOpen)}>
            <MessageCircle className="h-3.5 w-3.5 mr-1" />
            {chatOpen ? "Скрыть чат" : "Обсудить с AI"}
          </Button>
        </div>

        {/* Inline AI Chat */}
        {chatOpen && (
          <div className="border rounded-lg overflow-hidden">
            <div className="bg-muted/30 px-3 py-2 flex items-center gap-2 border-b">
              <MessageCircle className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-medium">AI-консультант — контекст запроса загружен</span>
            </div>

            <div className="max-h-56 overflow-y-auto px-3 py-2 space-y-2">
              {messages.length === 0 && (
                <div className="text-center py-3 space-y-2">
                  <p className="text-xs text-muted-foreground">Спросите AI про этот запрос:</p>
                  <div className="flex flex-wrap gap-1 justify-center">
                    {[
                      "Как улучшить позицию по этому запросу?",
                      "Почему мало кликов при хорошей позиции?",
                      "Какую страницу оптимизировать?",
                    ].map((q) => (
                      <button key={q} onClick={() => { setInput(q); setChatOpen(true); }}
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
                placeholder="Спросите про этот запрос..."
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

function MetricCard({
  label,
  value,
  delta,
  deltaLabel,
  good,
  pctChange,
}: {
  label: string;
  value: string | number;
  delta?: number | null;
  deltaLabel?: string;
  good?: boolean;
  pctChange?: number | null;
}) {
  return (
    <div className="bg-muted/40 rounded-lg p-2.5 text-center">
      <div className="text-[10px] text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className="text-lg font-semibold mt-0.5 tabular-nums">{value}</div>
      {delta != null && (
        <div className={`text-[10px] flex items-center justify-center gap-0.5 ${good ? "text-green-600" : "text-red-500"}`}>
          {good ? <TrendingUp className="h-2.5 w-2.5" /> : <TrendingDown className="h-2.5 w-2.5" />}
          {Math.abs(delta).toFixed(1)} {deltaLabel}
        </div>
      )}
      {pctChange != null && (
        <div className={`text-[10px] ${pctChange > 0 ? "text-green-600" : pctChange < 0 ? "text-red-500" : "text-muted-foreground"}`}>
          {pctChange > 0 ? "+" : ""}{pctChange}%
        </div>
      )}
    </div>
  );
}
