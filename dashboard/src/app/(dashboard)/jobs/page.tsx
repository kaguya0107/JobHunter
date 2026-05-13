"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpDownIcon,
  CopyIcon,
  ExternalLinkIcon,
  FilterIcon,
  InfoIcon,
  MoreHorizontalIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { motion } from "framer-motion";

type JobRow = {
  id: string;
  title: string;
  description: string;
  budget: string;
  clientName: string;
  projectUrl: string;
  postedAt: string | null;
  detectedAt: string;
  aiScore: number | null;
  tags: string[];
  notificationSent: boolean;
  platform: string;
  sourceUrl: string;
  aiScoreNormalized: number | null;
  notificationStatus: string;
  aiAnalysis: {
    relevanceScore: number;
    profitabilityScore: number;
    spamScore: number;
    urgencyScore: number;
    analysisJson: Record<string, unknown>;
  } | null;
  rawData: unknown;
};

const NEW_MS = 2 * 3600 * 1000;

/** True when scores are monitor ingest placeholders — not LLM-ranked. */
function isListingParseOnly(ai: JobRow["aiAnalysis"]): boolean {
  if (!ai) return false;
  const note = ai.analysisJson?.note;
  return typeof note === "string" && note.toLowerCase().includes("placeholder");
}

export default function JobsPage() {
  const qc = useQueryClient();
  const [q, setQ] = React.useState("");
  const [platform, setPlatform] = React.useState<string>("");
  const [sort, setSort] = React.useState<"" | "score" | "posted">("");
  const [selected, setSelected] = React.useState<Record<string, boolean>>({});
  const [detail, setDetail] = React.useState<JobRow | null>(null);

  const qs = React.useMemo(() => {
    const u = new URLSearchParams();
    if (q.trim()) u.set("q", q.trim());
    if (platform) u.set("platform", platform);
    if (sort) u.set("sort", sort);
    return u.toString();
  }, [q, platform, sort]);

  const { data, isLoading } = useQuery({
    queryKey: ["jobs", qs],
    queryFn: async () => {
      const res = await fetch(`/api/detected-jobs${qs ? `?${qs}` : ""}`, { cache: "no-store" });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error.message);
      return json.data as JobRow[];
    },
    placeholderData: (prev) => prev,
  });

  const bulk = useMutation({
    mutationFn: async (payload: { ids: string[]; notificationSent: boolean }) => {
      const res = await fetch("/api/detected-jobs/bulk", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error.message);
      return json.data as { updated: number };
    },
    onSuccess: (r) => {
      toast.success(`Updated ${r.updated} rows`);
      setSelected({});
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const idsSelected = React.useMemo(() => Object.entries(selected).filter(([, v]) => v).map(([k]) => k), [selected]);

  const toggleAll = (checked: boolean) => {
    if (!data) return;
    const next: Record<string, boolean> = {};
    if (checked) for (const j of data) next[j.id] = true;
    setSelected(next);
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">Detected jobs</h1>
          <p className="text-sm text-zinc-500">Search, prioritize, queue Discord confirmations.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={idsSelected.length === 0 || bulk.isPending}
            onClick={() => bulk.mutate({ ids: idsSelected, notificationSent: true })}
          >
            Bulk mark notified
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={idsSelected.length === 0 || bulk.isPending}
            onClick={() => bulk.mutate({ ids: idsSelected, notificationSent: false })}
          >
            Bulk reset flag
          </Button>
        </div>
      </header>

      <Card>
        <CardHeader className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <FilterIcon className="size-4 text-zinc-500" /> Filters
            </CardTitle>
            <CardDescription>Server-side fuzzy search on title, budget, client.</CardDescription>
          </div>
          <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:flex-wrap xl:justify-end">
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Keyword…" className="sm:max-w-xs" />
            <Input value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="Platform" className="sm:w-36" />
            <Select value={sort || "__default"} onValueChange={(v) => setSort(v === "__default" ? "" : (v as typeof sort))}>
              <SelectTrigger className="sm:w-[200px]">
                <ArrowUpDownIcon className="mr-2 size-4 shrink-0" />
                <SelectValue placeholder="Sort" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__default">Newest detected</SelectItem>
                <SelectItem value="score">AI score ↓</SelectItem>
                <SelectItem value="posted">Posted time ↓</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardContent className="p-0">
          {isLoading && !data ? (
            <Skeleton className="h-96 w-full rounded-none rounded-b-xl" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[40px]">
                      <Checkbox
                        checked={Boolean(data?.length && idsSelected.length === data.length)}
                        onCheckedChange={(v) => toggleAll(!!v)}
                        aria-label="Select all rows"
                      />
                    </TableHead>
                    <TableHead>Job</TableHead>
                    <TableHead>Platform</TableHead>
                    <TableHead>Budget</TableHead>
                    <TableHead className="hidden sm:table-cell">AI</TableHead>
                    <TableHead>Notify</TableHead>
                    <TableHead className="w-[112px] text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.map((job) => {
                    const detected = new Date(job.detectedAt).getTime();
                    const fresh = Date.now() - detected < NEW_MS;
                    const raw = job.aiScoreNormalized ?? job.aiScore;
                    const scorePct = raw != null ? (raw <= 2 ? raw * 100 : raw) : null;
                    const parseOnly = isListingParseOnly(job.aiAnalysis);
                    return (
                      <motion.tr
                        layout
                        key={job.id}
                        className={fresh ? "bg-emerald-500/5 hover:bg-muted/70" : "hover:bg-muted/50"}
                      >
                        <TableCell>
                          <Checkbox
                            checked={!!selected[job.id]}
                            onCheckedChange={(v) => setSelected((s) => ({ ...s, [job.id]: !!v }))}
                            aria-label={`Select ${job.title}`}
                          />
                        </TableCell>
                        <TableCell className="max-w-[320px]">
                          <button
                            type="button"
                            className="text-left"
                            onClick={() => setDetail(job)}
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <p className={`line-clamp-2 font-medium ${fresh ? "text-emerald-800 dark:text-emerald-300" : "text-zinc-900 dark:text-zinc-100"}`}>
                                {job.title}
                              </p>
                              {fresh ? (
                                <Badge variant="outline" className="border-emerald-500/40 bg-emerald-500/10 text-[10px] uppercase">
                                  Fresh
                                </Badge>
                              ) : null}
                            </div>
                            <p className="truncate text-[11px] text-zinc-500">
                              Detected {formatDistanceToNow(new Date(job.detectedAt), { addSuffix: true })}
                            </p>
                          </button>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {job.tags.slice(0, 4).map((t) => (
                              <Badge key={t} variant="secondary" className="text-[10px]">
                                #{t}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm">{job.platform}</TableCell>
                        <TableCell className="max-w-[140px] truncate text-sm" title={job.budget}>
                          {job.budget || "—"}
                        </TableCell>
                        <TableCell className="hidden sm:table-cell">
                          {parseOnly ? (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="cursor-help text-xs text-zinc-500 underline decoration-dotted dark:text-zinc-400">
                                  Listing only
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="left" className="max-w-[220px] text-left">
                                Scores are not from an LLM yet — they come from the board listing parse (often 0).
                              </TooltipContent>
                            </Tooltip>
                          ) : scorePct != null ? (
                            <Badge variant="outline" className="font-mono text-xs font-normal tabular-nums">
                              {scorePct.toFixed(1)} pts
                            </Badge>
                          ) : (
                            <span className="text-zinc-400">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          {job.notificationStatus === "FAILED" ? (
                            <Badge variant="outline" className="border-red-500 text-red-600 dark:border-red-400 dark:text-red-300">
                              {job.notificationStatus.toLowerCase()}
                            </Badge>
                          ) : (
                            <Badge variant="secondary">{job.notificationStatus.toLowerCase()}</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-0.5">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button size="icon" variant="ghost" className="size-8 shrink-0" asChild>
                                  <a
                                    href={job.projectUrl}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    aria-label={`Open job posting: ${job.title.slice(0, 80)}`}
                                  >
                                    <ExternalLinkIcon className="size-4" />
                                  </a>
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Open posting in new tab</TooltipContent>
                            </Tooltip>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button size="icon" variant="ghost" className="size-8 shrink-0" aria-label={`More actions: ${job.title.slice(0, 60)}`}>
                                  <MoreHorizontalIcon className="size-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="min-w-[200px]">
                                <DropdownMenuItem asChild>
                                  <a href={job.projectUrl} target="_blank" rel="noopener noreferrer">
                                    <ExternalLinkIcon />
                                    Open posting in new tab
                                  </a>
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={async () => {
                                    await navigator.clipboard.writeText(job.projectUrl);
                                    toast.success("Job URL copied");
                                  }}
                                >
                                  <CopyIcon />
                                  Copy job URL
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem onClick={() => setDetail(job)}>
                                  <InfoIcon />
                                  View details…
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </TableCell>
                      </motion.tr>
                    );
                  })}
                  {!data?.length ? (
                    <TableRow>
                      <TableCell colSpan={7}>
                        <div className="flex flex-col items-center gap-2 py-14 text-center">
                          <p className="font-medium text-zinc-900 dark:text-zinc-100">No jobs in this view</p>
                          <p className="max-w-md text-sm text-zinc-500">
                            Adjust filters or wait for{" "}
                            <code className="rounded-md bg-zinc-100 px-1.5 py-0.5 font-mono text-xs dark:bg-zinc-800">monitor.py</code>{" "}
                            to ingest new postings. Rows only appear once a job has been detected at least once.
                          </p>
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-h-[90vh] max-w-xl overflow-hidden p-0 sm:rounded-2xl">
          <DialogHeader className="border-b border-zinc-200 px-6 py-5 dark:border-zinc-800">
            <DialogTitle className="leading-tight">{detail?.title}</DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[70vh] px-6 py-5">
            {detail ? (
              <div className="space-y-4 text-sm text-zinc-600 dark:text-zinc-400">
                <DetailRow label="Platform" value={detail.platform} />
                <DetailRow label="Budget" value={detail.budget} />
                <DetailRow label="Client" value={detail.clientName || "—"} />
                <DetailRow
                  label="Posted"
                  value={detail.postedAt ? new Date(detail.postedAt).toLocaleString() : "—"}
                />
                <DetailRow label="Detected" value={new Date(detail.detectedAt).toLocaleString()} />
                <DetailRow label="Notification" value={detail.notificationStatus} />
                <DetailRow
                  label="AI relevance"
                  value={
                    detail.aiAnalysis && isListingParseOnly(detail.aiAnalysis)
                      ? "— (not LLM-ranked; placeholder from listing parse)"
                      : String(detail.aiAnalysis?.relevanceScore ?? detail.aiScore ?? "—")
                  }
                />
                <div>
                  <Label className="text-xs uppercase text-zinc-500">Description</Label>
                  <p className="mt-2 whitespace-pre-wrap">{detail.description || "(empty)"}</p>
                </div>
                <div>
                  <Label className="text-xs uppercase text-zinc-500">AI JSON</Label>
                  <pre className="mt-2 max-h-[200px] overflow-auto rounded-xl bg-zinc-950 p-3 text-[11px] text-zinc-100">
                    {JSON.stringify(detail.aiAnalysis?.analysisJson ?? {}, null, 2)}
                  </pre>
                </div>
              </div>
            ) : null}
          </ScrollArea>
          <DialogFooter className="gap-3 border-t border-zinc-200 px-6 py-4 dark:border-zinc-800 sm:justify-between">
            <Button variant="outline" asChild>
              <a href={detail?.projectUrl ?? "#"} target="_blank" rel="noopener noreferrer">
                Open posting
              </a>
            </Button>
            <Button
              onClick={() => detail && navigator.clipboard.writeText(detail.projectUrl).then(() => toast.success("Copied"))}
            >
              Copy link
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function DetailRow(props: { label: string; value: string }) {
  return (
    <div className="flex gap-6">
      <p className="w-36 shrink-0 text-xs uppercase tracking-wide text-zinc-400">{props.label}</p>
      <p className="font-medium text-zinc-900 dark:text-zinc-100">{props.value}</p>
    </div>
  );
}
