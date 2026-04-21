"use client";

import { useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ReportView } from "@/components/reports/report-view";
import { ArrowLeft, Printer, Download, Presentation, LayoutDashboard } from "lucide-react";

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qs = useSearchParams();
  const present = qs.get("present") === "1";
  const [downloading, setDownloading] = useState(false);

  const { data, isLoading, error } = useSWR(
    id ? `report-${id}` : null,
    () => api.report(id),
  );

  function togglePresent() {
    const params = new URLSearchParams(qs.toString());
    if (present) params.delete("present");
    else params.set("present", "1");
    router.replace(`/reports/${id}${params.toString() ? `?${params}` : ""}`);
  }

  async function downloadMd() {
    if (!data) return;
    setDownloading(true);
    try {
      const res = await fetch(api.reportMarkdownUrl(id), {
        headers: { "ngrok-skip-browser-warning": "true" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const md = await res.text();
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report-${data.week_start}-${data.week_end}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error(e);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="space-y-4">
      {!present && (
        <div className="flex items-center justify-between gap-2 flex-wrap print:hidden">
          <Button size="sm" variant="ghost" onClick={() => router.push("/reports")}>
            <ArrowLeft className="h-4 w-4 mr-2" /> К списку
          </Button>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={togglePresent}>
              <Presentation className="h-4 w-4 mr-2" /> Презентация
            </Button>
            <Button size="sm" variant="outline" onClick={downloadMd} disabled={downloading || !data}>
              <Download className="h-4 w-4 mr-2" /> {downloading ? "…" : "Markdown"}
            </Button>
            <Button size="sm" onClick={() => window.print()} disabled={!data}>
              <Printer className="h-4 w-4 mr-2" /> Печать / PDF
            </Button>
          </div>
        </div>
      )}

      {present && (
        <div className="fixed top-4 right-4 z-50 print:hidden">
          <Button size="sm" variant="outline" onClick={togglePresent}>
            <LayoutDashboard className="h-4 w-4 mr-2" /> Выйти
          </Button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : error ? (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          {String((error as any)?.message || error)}
        </div>
      ) : data?.payload ? (
        <ReportView payload={data.payload} present={present} />
      ) : (
        <div className="text-sm text-muted-foreground">Отчёт не найден.</div>
      )}
    </div>
  );
}
