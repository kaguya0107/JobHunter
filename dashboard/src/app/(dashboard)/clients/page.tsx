"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLinkIcon, UsersRoundIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { absoluteProfileUrl, type ClientAnalysisRow, type ClientAnalysisSummary } from "@/lib/client-analysis";

type Payload = {
  summary: ClientAnalysisSummary;
  clients: ClientAnalysisRow[];
  scan: { limit: number; orderedBy: "detectedAt_desc" };
};

export default function ClientsAnalysisPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["client-analysis"],
    queryFn: async () => {
      const res = await fetch("/api/client-analysis", { cache: "no-store" });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error?.message ?? "読み込みに失敗しました");
      return json.data as Payload;
    },
  });

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <div className="flex items-center gap-2">
          <UsersRoundIcon className="size-7 text-zinc-600 dark:text-zinc-300" aria-hidden />
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">クライアント分析</h1>
        </div>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          検出求人に保存されている発注者・クライアント情報（名前、プロフィールURL、発注/契約数、評価、補足）を集約します。
          {data?.scan?.limit != null ? (
            <span className="block pt-1 text-xs text-zinc-400">
              直近 <span className="tabular-nums font-medium">{data.scan.limit}</span>{" "}
              件の検出求人を対象にしています（検出日時の新しい順）。
            </span>
          ) : null}
        </p>
      </header>

      {isLoading ? (
        <Skeleton className="h-[560px] w-full rounded-xl" />
      ) : error ? (
        <Card className="border-red-200 dark:border-red-900">
          <CardHeader>
            <CardTitle className="text-red-700 dark:text-red-400">読み込みエラー</CardTitle>
            <CardDescription>{(error as Error).message}</CardDescription>
          </CardHeader>
        </Card>
      ) : data ? (
        <>
          <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <MetricCard title="ユニーククライアント" value={String(data.summary.uniqueClients)} />
            <MetricCard title="プロフィールURLあり" value={String(data.summary.clientsWithProfileUrl)} />
            <MetricCard title="クライアント紐付け求人" value={String(data.summary.jobsWithClientIdentity)} />
            <MetricCard title="スキャンした求人" value={String(data.summary.scannedJobs)} hint="対象件数上限内" />
            <MetricCard
              title="平均評価（一覧・プロフィール由来）"
              value={data.summary.avgRating != null ? data.summary.avgRating.toFixed(2) : "—"}
            />
          </section>

          {data.summary.platformMix.length > 0 ? (
            <Card className="border-zinc-200 dark:border-zinc-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-base">プラットフォーム別（スキャン内の求人）</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {data.summary.platformMix.map((p) => (
                  <Badge key={p.platform} variant="secondary" className="tabular-nums">
                    {p.platform}
                    <span className="ml-1.5 font-semibold">{p.jobCount}</span>
                  </Badge>
                ))}
              </CardContent>
            </Card>
          ) : null}

          <Card className="border-zinc-200 dark:border-zinc-800">
            <CardHeader>
              <CardTitle className="text-lg">クライアント一覧</CardTitle>
              <CardDescription>
                プロフィールURLがある場合はそれで同一視し、無い場合は「プラットフォーム × 表示名」でまとめています。
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0 sm:p-6">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="min-w-[140px]">表示名</TableHead>
                    <TableHead className="min-w-[100px]">種別</TableHead>
                    <TableHead className="min-w-[160px]">プラットフォーム</TableHead>
                    <TableHead className="text-right tabular-nums">求人件数</TableHead>
                    <TableHead className="min-w-[120px]">発注/契約</TableHead>
                    <TableHead className="text-right">評価</TableHead>
                    <TableHead className="min-w-[220px]">補足・詳細</TableHead>
                    <TableHead className="min-w-[110px]">最終検出</TableHead>
                    <TableHead className="min-w-[72px]" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.clients.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={9} className="text-center text-sm text-zinc-500">
                        クライアント情報がまだありません。モニターから ingest された求人が溜まると表示されます。
                      </TableCell>
                    </TableRow>
                  ) : (
                    data.clients.map((c) => <ClientTableRow key={c.key} row={c} />)
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}

function MetricCard(props: { title: string; value: string; hint?: string }) {
  return (
    <Card className="border-zinc-200 dark:border-zinc-800">
      <CardHeader className="pb-2">
        <CardDescription>{props.title}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{props.value}</CardTitle>
        {props.hint ? <p className="text-[11px] text-zinc-400">{props.hint}</p> : null}
      </CardHeader>
    </Card>
  );
}

function ClientTableRow({ row }: { row: ClientAnalysisRow }) {
  const profileHref = absoluteProfileUrl(row.platforms, row.profilePath);
  const kindLabel = row.kind === "profile" ? "プロフィール" : "名前のみ";

  return (
    <TableRow className="align-top">
      <TableCell className="font-medium text-zinc-900 dark:text-zinc-100">{row.displayName}</TableCell>
      <TableCell>
        <Badge variant="outline" className="font-normal">
          {kindLabel}
        </Badge>
      </TableCell>
      <TableCell className="text-xs text-zinc-600 dark:text-zinc-400">
        <div className="flex flex-wrap gap-1">
          {row.platforms.map((p) => (
            <span key={p} className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">
              {p}
            </span>
          ))}
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums font-medium">{row.jobCount}</TableCell>
      <TableCell className="tabular-nums text-sm text-zinc-700 dark:text-zinc-300">
        {row.ordersDisplay ?? "—"}
      </TableCell>
      <TableCell className="text-right tabular-nums text-sm">
        {row.ratingDisplay != null ? row.ratingDisplay.toFixed(1) : "—"}
      </TableCell>
      <TableCell className="max-w-[320px]">
        <p
          className="line-clamp-3 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400"
          title={row.extrasPreview ?? undefined}
        >
          {row.extrasPreview ?? "—"}
        </p>
      </TableCell>
      <TableCell className="whitespace-nowrap text-xs text-zinc-500 tabular-nums">
        {new Date(row.lastDetectedAt).toLocaleString()}
      </TableCell>
      <TableCell className="space-y-1">
        {profileHref ? (
          <a
            href={profileHref}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-sky-600 underline underline-offset-2 hover:text-sky-500 dark:text-sky-400"
          >
            プロフィール
            <ExternalLinkIcon className="size-3 shrink-0 opacity-80" aria-hidden />
          </a>
        ) : (
          <span className="text-xs text-zinc-400">—</span>
        )}
        <a
          href={row.latestJobUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="block truncate text-[11px] text-zinc-500 underline decoration-zinc-400/40 underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
          title={row.latestJobTitle}
        >
          最新求人
        </a>
      </TableCell>
    </TableRow>
  );
}
