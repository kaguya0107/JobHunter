"use client";

import type { ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** よくある Lancers 「/work/search/{slug}」の略称（URLスラグ → 一覧カテゴリ表示）。 */
const LANCERS_SLUG_LABEL: Record<string, string> = {
  system: "システム",
  web: "Web",
  design: "デザイン",
  illustration: "イラスト",
  writing: "ライティング",
  translation: "翻訳",
  business: "事務・秘書",
  sales: "営業",
  multimedia: "映像・音響",
  data_entry: "データ入力",
  marketing: "マーケティング",
  seo: "SEO",
  sns: "SNS運用",
  customer_support: "カスタマー",
  ecommerce: "EC",
  cad: "CAD",
  semiconductor: "半導体",
};

/** CrowdWorks 検索 ``category_id``（monitor のデフォルト 226/230 など）。未知は #ID で示す。 */
const CROWDWORKS_CATEGORY_LABEL: Record<string, string> = {
  "226": "システム",
  "230": "Web",
};

function platformToneClass(platformKey: string): string {
  const p = platformKey.toLowerCase();
  if (p.includes("lancers")) {
    return "border-sky-600/35 bg-sky-100 dark:border-sky-400/40 dark:bg-sky-950/50 text-sky-950 dark:text-sky-100";
  }
  if (p.includes("crowd") || p.includes("cloudwork")) {
    return "border-violet-600/35 bg-violet-100 dark:border-violet-400/40 dark:bg-violet-950/45 text-violet-950 dark:text-violet-100";
  }
  return "border-zinc-400 bg-zinc-100 dark:border-zinc-600 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100";
}

function abbreviatePostingPlatform(platformRaw: string): { abbr: string; tonesKey: string; fullJa: string } {
  const s = platformRaw.trim();
  const low = s.toLowerCase();
  if (low.includes("lancers")) {
    return { abbr: "LW", tonesKey: "lancers", fullJa: "ランサーズ" };
  }
  if (low.includes("crowd") || low.includes("cloudwork")) {
    return { abbr: "CW", tonesKey: "crowdworks", fullJa: "クラウドワークス" };
  }
  const abbr = (s.slice(0, 2) || "?").toUpperCase();
  return { abbr, tonesKey: s || "unknown", fullJa: s || "不明" };
}

function safeListingUrl(raw: string | undefined | null): URL | null {
  const t = (raw ?? "").trim();
  if (!t) return null;
  try {
    return new URL(t);
  } catch {
    try {
      return new URL(/^https?:/i.test(t) ? t : `https://${t}`);
    } catch {
      return null;
    }
  }
}

/** 監視ソースURLから「この投稿がどの一覧カテゴリ由来か」を短く返す（Tooltip 用の説明文も冗長気味に付ける）。 */
function postingBoardCategory(platformRaw: string, listingUrlRaw: string | undefined | null): {
  short: string;
  detailLines: string[];
} {
  const pf = abbreviatePostingPlatform(platformRaw);
  const u = safeListingUrl(listingUrlRaw);
  const host = u?.hostname.toLowerCase() ?? "";
  const urlLine = listingUrlRaw?.trim() || "(監視URLなし)";

  const lines = (extra: string) => [`${pf.fullJa}（${pf.abbr}）`, extra, "", `一覧URL: ${urlLine}`];

  if (!u) {
    const guess = /\blancers\b/i.test(listingUrlRaw ?? "") ? "LW" : /\bcrowd\b/i.test(listingUrlRaw ?? "") ? "CW" : "—";
    return {
      short: guess !== "—" ? "要確認" : "—",
      detailLines: [
        `${pf.fullJa}`,
        listingUrlRaw ? "URL を解釈できませんでした。パスまたはクエリを確認してください。" : "一覧URL が渡されていません。",
        "",
        `値: ${urlLine}`,
      ],
    };
  }

  if (host.endsWith("lancers.jp") || host.endsWith("www.lancers.jp")) {
    const segs = u.pathname.replace(/^\//u, "").split("/").filter(Boolean);
    let slug = "";
    if (segs[0] === "work" && segs[1] === "search" && segs[2]) {
      slug = segs[2].replace(/^\s+|\s+$/gu, "");
    }
    const label = slug ? (LANCERS_SLUG_LABEL[slug] ?? slug.replace(/-/g, " ").toUpperCase()) : "検索";
    return {
      short: label,
      detailLines: lines(`ランサーワーク求人の一覧区分: 「${label}」（URLスラグ: ${slug || "―"}）`),
    };
  }

  if (host.endsWith("crowdworks.jp")) {
    const id = u.searchParams.get("category_id") ?? u.searchParams.get("categoryId");
    const label =
      id != null ? (CROWDWORKS_CATEGORY_LABEL[id] ?? `#${id}`) : pathSuffixHint(u.pathname);
    const idPart =
      id != null
        ? `category_id=${id}（標準では 226=システム開発、230=Web制作）`
        : "クエリ category_id が見つからない場合のパス推定";

    return {
      short: label,
      detailLines: lines(`クラウドワークス募集一覧のカテゴリ区分: 「${label}」 — ${idPart}`),
    };
  }

  return {
    short: "その他",
    detailLines: lines(`ホスト「${host}」の求人カテゴリはマッピングしていません`),
  };
}

function pathSuffixHint(pathname: string): string {
  const parts = pathname.split("/").filter(Boolean);
  const tail = parts[parts.length - 1];
  return tail ? tail.slice(0, 12).toUpperCase() : "募集";
}

function SourcePill(props: { className?: string; title?: string; children: ReactNode }) {
  return (
    <span
      role="presentation"
      title={props.title}
      className={cn(
        "inline-flex items-center justify-center rounded-lg border px-2 py-1 text-xs shadow-sm [&_*]:text-inherit",
        props.className,
      )}
    >
      {props.children}
    </span>
  );
}

/**
 * 「どのプラットフォーム由来か」（LW/CW）と「どの求人ボード一覧カテゴリか」（監視URL由来）を1つの短いバッジにする。
 *
 * （旧 ScrapingType の HTML/API/HYBRID ではなく **投稿側の一覧カテゴリ** を示す）
 */
export function PostingSourceBadges(props: {
  platform: string;
  listingUrl?: string | null;
  orientation?: "row" | "col";
  className?: string;
  dense?: boolean;
}) {
  const { platform, listingUrl, orientation = "col", className, dense } = props;
  const pf = abbreviatePostingPlatform(platform);
  const cat = postingBoardCategory(platform, listingUrl ?? null);
  const tooltip = cat.detailLines.join("\n");

  const textSz = dense ? "text-[11px]" : "text-xs";
  const abbrSz = dense ? "text-[11px]" : "text-sm";

  return (
    <div
      className={cn(
        "inline-flex flex-wrap gap-1.5 align-top",
        orientation === "col" ? "flex-col items-start" : "flex-row items-start",
        className,
      )}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "inline-flex cursor-help rounded-lg border-0 bg-transparent p-0 text-left outline-none hover:opacity-[0.93]",
              "focus-visible:ring-2 focus-visible:ring-sky-500/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            )}
            aria-label={`プラットフォーム ${pf.abbr}、求人ボード一覧カテゴリ ${cat.short}`}
          >
            <SourcePill className={platformToneClass(pf.tonesKey)} title={listingUrl ?? undefined}>
              <span className={cn("flex flex-wrap items-baseline gap-1 tracking-tight", textSz)}>
                <span className={cn("font-bold tabular-nums tracking-tighter", abbrSz)}>{pf.abbr}</span>
                <span className="select-none opacity-50" aria-hidden>
                  ·
                </span>
                <span className={cn("min-w-0 max-w-[7.5rem] truncate font-semibold", textSz)}>{cat.short}</span>
              </span>
            </SourcePill>
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[min(28rem,calc(100vw-2rem))] whitespace-pre-wrap text-left text-xs leading-relaxed">
          {tooltip}
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
