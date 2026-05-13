#!/usr/bin/env python3
"""
Poll Lancers.jp job listing pages and post Discord webhook notifications when new jobs appear.

Configure with environment variables:

  DISCORD_WEBHOOK_URL    Single webhook used for every listing URL (unless DISCORD_WEBHOOK_URLS is set)
  DISCORD_WEBHOOK_URLS   Space-separated webhooks, **one per URL** in `LANCERS_URLS` / defaults (same order).
                           Example: system webhook then web webhook → posts each category to its own Discord channel.

  COPY_TO_CLIPBOARD          If enabled (default 1), writes plain-text 「依頼詳細」をまとめた内容 to the OS clipboard whenever new jobs are notified
  POLL_INTERVAL_SECONDS Polling interval when running in daemon mode (default 180)
  STATE_PATH            JSON file storing seen job IDs **per category** under ``known_ids_by_category``
                           (keys such as ``system``, ``web``, derived from ``LANCERS_URLS`` paths).
  HTTP_TIMEOUT_SECONDS  Request timeout (default 30)
  NOTIFY_ON_FIRST_RUN   If "1"/"true"/"yes", notify for all jobs on the first poll instead of only seeding state
  DISCORD_POST_DELAY_SECONDS   Pause between webhook requests when sending batches (default 1.25)
  DISCORD_MAX_EMBEDS_PER_MESSAGE  Up to 10 embeds per POST (default 10; lower if payloads are huge)
  DISCORD_WEBHOOK_MAX_RETRIES     Max retries on HTTP 429 (default 24)
  DISCORD_RATE_LIMIT_MARGIN_SECONDS Extra seconds added to Discord's retry_after (default 0.15)
  COPY_TO_CLIPBOARD          If enabled (default 1), writes plain-text 「依頼詳細」をまとめた内容 to the OS clipboard whenever new jobs are notified
  CLIPBOARD_FETCH_WORK_DETAIL Fetch each job detail page to copy full「依頼概要」(default 1). If off, uses only the teaser from the listing page.
  DISCORD_NOTIFY_ON_START     Post a Discord message when the monitor process starts (default 1).
  PREVIEW_HTTP_BIND           Bind address for ``--preview`` (default 127.0.0.1 — use SSH port forward from a VPS).
  PREVIEW_HTTP_PORT           Port for ``--preview`` (default 8790).

Compliance: Requests use a descriptive User-Agent. Poll at considerate intervals.

Usage:
  DISCORD_WEBHOOK_URL=... python monitor.py               # loops forever (or rely on `.env`)
  DISCORD_WEBHOOK_URL=... python monitor.py --once       # single poll (cron)
  python monitor.py --reset-state                        # clear seen-job state and exit
  python monitor.py --preview                            # fetch once, serve structured data at http://127.0.0.1:8790/ and /json
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import html
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag


def load_env_file_if_available() -> None:
    """Populate os.environ from a local `.env` when python-dotenv is installed.

    Uses override=True so values in `.env` replace stale/partial variables already
    exported in the shell (a common cause of truncated DISCORD_WEBHOOK_URL).
    To force the shell value instead, run: `env -u DISCORD_WEBHOOK_URL python monitor.py`
    after unsetting, or edit `.env` to match.
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
    except ImportError:
        return
    load_dotenv(override=True)


LOG = logging.getLogger("lancers_monitor")

DEFAULT_URLS = (
    "https://www.lancers.jp/work/search/system?open=1&ref=header_menu",
    "https://www.lancers.jp/work/search/web?open=1&ref=header_menu",
)

DEFAULT_UA = "JobHunterLancersMonitor/1.0 (+https://example.invalid/bot)"

WORK_ID_RE = re.compile(r"/work/detail/(\d+)")
REQUEST_DETAIL_TERM_MARKERS = ("依頼概要", "募集内容", "仕事の内容", "依頼内容", "仕事詳細")
DISCORD_WEBHOOK_EXECUTE_PATH = re.compile(r"^/api(?:/v\d+)?/webhooks/(\d+)/([^/]+)$")
DISCORD_WEBHOOK_HOSTS = frozenset(
    {
        "discord.com",
        "canary.discord.com",
        "ptb.discord.com",
        "discordapp.com",
    }
)

STATE_SCHEMA_VERSION = 2


@dataclass
class JobListing:
    work_id: str
    title: str
    budget_text: str
    detail_url: str
    client_name: str
    client_profile_url: str | None
    client_extras: str
    source_listing_url: str
    listing_summary: str
    client_avatar_url: str | None = None
    client_orders: str | None = None
    client_rating: float | None = None

    def client_summary(self) -> str:
        parts = []
        name = self.client_name.strip()
        if name:
            parts.append(name)
        if self.client_profile_url:
            parts.append(urljoin("https://www.lancers.jp", self.client_profile_url))
        extras = self.client_extras.strip()
        if extras:
            parts.append(extras)
        return "\n".join(parts) if parts else "(発注者情報なし)"


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    s = v.strip().lower()
    if s in ("0", "false", "no", "off", ""):
        return False
    return s in ("1", "true", "yes", "on")


def normalize_discord_webhook_url(raw: str) -> str:
    u = raw.strip().strip("\ufeff").replace("\r", "").strip()
    if len(u) >= 2 and u[0] == u[-1] and u[0] in "\"'":
        u = u[1:-1].strip()
    return u.rstrip()


def webhook_url_diagnostic(url: str) -> str:
    """Non-secret hints for troubleshooting invalid webhook URLs."""
    if not url:
        return "(empty)"
    p = urlparse(url)
    segs = [s for s in p.path.strip("/").split("/") if s]
    prefix = (p.scheme + "://" + (p.netloc or ""))[:44]
    return f"len={len(url)} scheme={p.scheme!r} host={p.hostname!r} path_segments={len(segs)} prefix={prefix!r}"


def validate_discord_webhook_execute_url(url: str) -> None:
    """Raise ValueError unless URL looks like a standard incoming-webhook POST target."""
    if not url:
        raise ValueError("Webhook URL is empty.")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must start with https://")
    host = (parsed.hostname or "").lower()
    if host not in DISCORD_WEBHOOK_HOSTS:
        raise ValueError(
            f"Webhook URL has unexpected host {parsed.hostname!r}. "
            "Use the URL from Discord: Server Settings → Integrations → Webhooks → Copy Webhook URL."
        )
    path = parsed.path.rstrip("/") or "/"
    if not DISCORD_WEBHOOK_EXECUTE_PATH.match(path):
        raise ValueError(
            "Webhook URL must look like "
            "https://discord.com/api/webhooks/<webhook_id>/<token> — without extra path segments "
            "such as /slack. If the token is missing or cut off, Discord may return HTTP 405."
        )


def load_urls_env() -> list[str]:
    raw = os.environ.get("LANCERS_URLS", "").strip()
    if raw:
        return [u for u in (x.strip() for x in raw.split()) if u]
    return list(DEFAULT_URLS)


def listing_category_label(listing_url: str) -> str:
    """Short label for Discord / clipboard headers (e.g. ``system``, ``web``)."""
    try:
        parts = urlparse(listing_url).path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "work" and parts[1] == "search":
            tail = "/".join(parts[2:])
            return tail if tail else "search"
        return parts[-1] if parts else listing_url[:60]
    except Exception:
        return listing_url[:60]


def monitor_category_buckets(urls: list[str]) -> list[str]:
    """Ordered unique category keys for ``urls`` (system vs web, etc.)."""
    keys: list[str] = []
    seen: set[str] = set()
    for u in urls:
        k = listing_category_label(u)
        if k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def load_listing_webhook_assignments(urls: list[str]) -> dict[str, str]:
    """Map each monitored listing URL → Discord webhook URL."""
    multi_raw = os.environ.get("DISCORD_WEBHOOK_URLS", "").strip()
    single_raw = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    if multi_raw:
        hooks = [normalize_discord_webhook_url(h) for h in multi_raw.split()]
        hooks = [h for h in hooks if h]
        if len(hooks) != len(urls):
            raise ValueError(
                "DISCORD_WEBHOOK_URLS must contain exactly one webhook URL per monitored listing URL "
                f'({len(urls)} URLs configured); received {len(hooks)} webhook(s). Same order as LANCERS_URLS — '
                'first webhook → first URL, etc.'
            )
        return {urls[i]: hooks[i] for i in range(len(urls))}

    single = normalize_discord_webhook_url(single_raw)
    if not single:
        raise ValueError(
            "Set DISCORD_WEBHOOK_URL for one Discord channel for all categories, "
            "or DISCORD_WEBHOOK_URLS with one webhook per listing URL (same order as LANCERS_URLS)."
        )
    return {u: single for u in urls}


def fetch_html(session: requests.Session, url: str, timeout: float) -> str:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_budget(card: BeautifulSoup) -> str:
    node = card.select_one("span.p-search-job-media__price")
    if not node:
        return "(未取得)"
    return " ".join(node.get_text(separator=" ", strip=True).split())


def absolutize_lancers_asset(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin("https://www.lancers.jp/", u.lstrip("/"))


def parse_client(card: BeautifulSoup) -> tuple[str, str | None, str, str | None, str | None, float | None]:
    """Returns name, profile href, extras summary text, avatar URL, orders count text, rating float."""
    note = card.select_one("p.p-search-job-media__avatar-note a")
    name = ""
    href: str | None = None
    if note:
        name = note.get_text(strip=True)
        href = note.get("href")

    img = card.select_one("div.p-search-job-media__avatar img.c-avatar__image")
    avatar_src = img.get("src") if img else None
    avatar_url = absolutize_lancers_asset(avatar_src)

    extras: list[str] = []
    orders: str | None = None
    rating: float | None = None
    for sub in card.select("span.p-search-job-media__avatar-subnote"):
        t = sub.get_text(separator=" ", strip=True)
        if t:
            extras.append(t)
        strong = sub.select_one("strong")
        num_txt = strong.get_text(strip=True) if strong else ""
        if not num_txt:
            continue
        if "発注" in t:
            orders = num_txt
        elif "評価" in t:
            try:
                rating = float(num_txt.replace(",", "."))
            except ValueError:
                rating = None

    return name, href, " / ".join(extras) if extras else "", avatar_url, orders, rating


def parse_listing_summary_text(card: BeautifulSoup) -> str:
    """Snippet shown on the search result card (一覧の説明文)."""
    for desc in card.select("div.c-media__description"):
        if desc.select_one("ul.p-search-job-media__tag-lists"):
            continue
        t = desc.get_text(separator="\n", strip=True)
        if t:
            return t
    return ""


def extract_request_details_from_work_detail_html(html: str) -> str:
    """Full「依頼概要」(or similar) section from https://www.lancers.jp/work/detail/{id}."""
    soup = BeautifulSoup(html, "html.parser")
    for dl in soup.select("dl.c-definition-list"):
        dt = dl.select_one("dt.c-definition-list__term")
        dd = dl.select_one("dd.c-definition-list__description")
        if not dt or not dd:
            continue
        term = " ".join(dt.get_text(separator=" ", strip=True).split())
        if any(marker in term for marker in REQUEST_DETAIL_TERM_MARKERS):
            body = dd.get_text(separator="\n", strip=True)
            if body:
                return body
    return ""


def _listing_card_title_anchor(card: Tag) -> Tag | None:
    """Title link inside a listing card — main grid vs 「新着」carousel markup differ."""
    title_a = card.select_one("a.p-search-job-media__title")
    if title_a:
        return title_a
    title_a = card.select_one("a.p-search-job__latest-media-title")
    if title_a:
        return title_a
    return card.select_one("a[href^='/work/detail/']")


def parse_listings(html: str, listing_url: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[JobListing] = []
    carousel_cards = soup.select("div.p-search-job__latest-carousel-item.c-media")
    main_cards = soup.select("div.p-search-job-media.c-media")
    cards = main_cards + carousel_cards
    seen_on_page: set[str] = set()
    for card in cards:
        title_a = _listing_card_title_anchor(card)
        if not title_a:
            continue
        tags_ul = title_a.select_one("ul.p-search-job-media__tags")
        if tags_ul:
            tags_ul.extract()
        href = title_a.get("href") or ""
        m = WORK_ID_RE.search(href)
        if not m:
            continue
        wid = m.group(1)
        if wid in seen_on_page:
            continue
        seen_on_page.add(wid)
        title_text = " ".join(title_a.get_text(separator=" ", strip=True).split())
        budget = parse_budget(card)
        cname, chref, extras, cavatar, corders, crating = parse_client(card)
        detail = urljoin("https://www.lancers.jp/", href.lstrip("/"))
        listing_summary = parse_listing_summary_text(card)
        out.append(
            JobListing(
                work_id=wid,
                title=title_text,
                budget_text=budget,
                detail_url=detail,
                client_name=cname,
                client_profile_url=chref,
                client_extras=extras,
                source_listing_url=listing_url,
                listing_summary=listing_summary,
                client_avatar_url=cavatar,
                client_orders=corders,
                client_rating=crating,
            )
        )
    return out


def fetch_live_listings_snapshot(session: requests.Session, urls: list[str], timeout: float) -> list[JobListing]:
    """Same parsing path as polling, flattened for browser preview."""
    merged: list[JobListing] = []
    keyed: set[tuple[str, str]] = set()
    for listing_url in urls:
        blob = fetch_html(session, listing_url, timeout)
        for job in parse_listings(blob, listing_url):
            k = (job.work_id, job.source_listing_url)
            if k in keyed:
                continue
            keyed.add(k)
            merged.append(job)
    return merged


def job_public_dict(job: JobListing) -> dict:
    """Serializable snapshot of extracted listing fields."""
    return asdict(job)


def plaintext_block_for_job(job: JobListing, request_body: str) -> str:
    """Single job as plain UTF-8 text for the clipboard (依頼詳細相当)."""
    client = job.client_summary()
    lines = [
        job.title,
        "",
        job.detail_url,
        "",
        f"予算（一覧）: {job.budget_text}",
        f"クライアント情報: {client.replace(chr(10), ' / ')}",
        f"案件ID: {job.work_id}",
        "",
        "【依頼詳細・依頼概要】",
        request_body.strip() if request_body.strip() else "(本文を取得できませんでした。「一覧」の要約のみ下記参照)",
        "",
        "【一覧に表示された要約】",
        job.listing_summary.strip() if job.listing_summary.strip() else "(なし)",
    ]
    return "\n".join(lines)


def _clipboard_plain_inner(session: requests.Session, jobs: list[JobListing], timeout: float, *, fetch_detail: bool) -> str:
    parts: list[str] = []
    sep = os.environ.get("CLIPBOARD_JOB_SEPARATOR", "\n\n" + "─" * 48 + "\n\n")

    for job in jobs:
        body = ""
        if fetch_detail:
            try:
                detail_html_text = fetch_html(session, job.detail_url, timeout)
                body = extract_request_details_from_work_detail_html(detail_html_text)
            except requests.RequestException as e:
                LOG.warning("Clipboard: could not fetch work detail %s (%s)", job.work_id, e)
        if not body.strip():
            body = job.listing_summary
        parts.append(plaintext_block_for_job(job, body))

    return sep.join(parts)


def clipboard_text_from_jobs(
    session: requests.Session,
    jobs: list[JobListing],
    timeout: float,
    *,
    fetch_detail: bool,
    urls_priority: list[str] | None = None,
) -> str:
    """Clipboard payload; grouped by listing URL when multiple categories appear."""
    if not jobs:
        return ""
    by_src: defaultdict[str, list[JobListing]] = defaultdict(list)
    for j in jobs:
        by_src[j.source_listing_url].append(j)

    order = urls_priority if urls_priority else sorted(by_src.keys())
    sep_outer = os.environ.get("CLIPBOARD_SECTION_SEPARATOR", "\n\n" + "=" * 52 + "\n\n")

    sections: list[str] = []
    seen_sources: set[str] = set()
    for src in order:
        bucket = by_src.get(src)
        if not bucket:
            continue
        seen_sources.add(src)
        label = listing_category_label(src)
        inner = _clipboard_plain_inner(session, bucket, timeout, fetch_detail=fetch_detail)
        sections.append(f"【カテゴリ: {label}】\n{src}\n\n{inner}")

    for src in sorted(by_src.keys()):
        if src in seen_sources:
            continue
        bucket = by_src[src]
        label = listing_category_label(src)
        inner = _clipboard_plain_inner(session, bucket, timeout, fetch_detail=fetch_detail)
        sections.append(f"【カテゴリ: {label}】\n{src}\n\n{inner}")

    return sep_outer.join(sections)


def copy_plain_text_best_effort(text: str) -> bool:
    if not text.strip():
        return False

    try:
        import pyperclip  # type: ignore[import-untyped]

        pyperclip.copy(text)
        return True
    except Exception:
        pass

    data = text.encode("utf-8")
    exe_and_args: list[tuple[list[str], bytes]] = []
    if sys.platform.startswith("linux"):
        wl = shutil.which("wl-copy")
        if wl:
            exe_and_args.append(([wl], data))
        xc = shutil.which("xclip")
        if xc:
            exe_and_args.append([[xc, "-selection", "clipboard"], data])
        xs = shutil.which("xsel")
        if xs:
            exe_and_args.append([[xs, "--clipboard", "--input"], data])
    elif sys.platform == "darwin":
        pb = shutil.which("pbcopy")
        if pb:
            exe_and_args.append([[pb], data])

    for argv, stdin in exe_and_args:
        try:
            r = subprocess.run(argv, input=stdin, capture_output=True, timeout=15, check=False)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue

    return False


def split_discord_chunks(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + limit])
        start += limit
    return chunks


def format_rating_with_stars(rating: float, *, scale: float = 5.0) -> str:
    """Five-slot star row (★ / ☆) plus numeric score for Discord."""
    r = max(0.0, min(float(rating), scale))
    slots = 5
    filled = int(round((r / scale) * slots)) if scale > 0 else 0
    filled = max(0, min(slots, filled))
    stars = "★" * filled + "☆" * (slots - filled)
    return f"{stars} **`{r:g}`** / `{scale:g}`"


def format_rating_plain_text(rating: float, *, scale: float = 5.0) -> str:
    """Stars + numeric score without Discord markdown (for HTML previews)."""
    r = max(0.0, min(float(rating), scale))
    slots = 5
    filled = int(round((r / scale) * slots)) if scale > 0 else 0
    filled = max(0, min(slots, filled))
    stars = "★" * filled + "☆" * (slots - filled)
    return f"{stars} {r:g} / {scale:g}"


def job_to_discord_embed(job: JobListing) -> dict:
    """One Discord embed for a listing (combined up to 10 per webhook POST)."""
    title = job.title[:250] + ("…" if len(job.title) > 250 else "")
    client_display = job.client_name.strip() or "クライアント"

    desc_lines = [
        "Lancers求人ボードで新しく検出された案件です。",
        "",
        f"**👤 {client_display}**",
    ]
    if job.client_profile_url:
        pf = urljoin("https://www.lancers.jp", job.client_profile_url)
        desc_lines.append(f"[プロフィールを開く]({pf})")

    if job.client_orders:
        desc_lines.append(f"📦 **発注数:** `{job.client_orders}`")
    else:
        desc_lines.append("📦 **発注数:** —")

    if job.client_rating is not None:
        desc_lines.append(f"⭐ **評価:** {format_rating_with_stars(job.client_rating)}")
    else:
        desc_lines.append("⭐ **評価:** —")

    description = "\n".join(desc_lines)
    if len(description) > 4096:
        description = description[:4093] + "…"

    embed: dict = {
        "title": title,
        "url": job.detail_url,
        "description": description,
        "color": 0x58B5D5,
        "fields": [
            {"name": "💰 予算（一覧表示）", "value": job.budget_text[:1024]},
            {"name": "📎 求人URL", "value": job.detail_url[:1024]},
            {"name": "📂 監視ソース", "value": job.source_listing_url[:1024]},
            {"name": "案件 ID", "value": job.work_id[:1024]},
        ],
    }

    au = job.client_avatar_url
    if au:
        embed["thumbnail"] = {"url": au[:2048]}

    return embed


def build_live_preview_html(jobs: list[JobListing]) -> bytes:
    """Single HTML page summarizing extracted JobListing rows."""
    rows: list[str] = []
    for j in jobs:
        if j.client_avatar_url:
            au = html.escape(j.client_avatar_url, quote=True)
            avatar = (
                f'<img src="{au}" width="44" height="44" alt="" loading="lazy" '
                'referrerpolicy="no-referrer" style="object-fit:cover;border-radius:6px;display:block;">'
            )
        else:
            avatar = "—"

        rat_line = (
            format_rating_plain_text(j.client_rating) if j.client_rating is not None else "評価: —"
        )
        sub_client = html.escape(j.client_orders or "—", quote=False) + " · " + html.escape(rat_line, quote=False)

        snippet = j.listing_summary.strip()
        if len(snippet) > 220:
            snippet = snippet[:220] + "…"
        snippet_esc = html.escape(snippet or "—", quote=False)

        du = html.escape(j.detail_url, quote=True)

        rows.append(
            "<tr>"
            f"<td>{avatar}</td>"
            f"<td><span class='cat'>{html.escape(listing_category_label(j.source_listing_url), quote=False)}</span><br>"
            f"<span class='mono'><a href='{du}'>{html.escape(j.work_id, quote=False)}</a></span></td>"
            f"<td>{html.escape(j.title, quote=False)}</td>"
            f"<td class='mono'>{html.escape(j.budget_text, quote=False)}</td>"
            f"<td>{html.escape(j.client_name or '—', quote=False)}<br><span class='sub'>{sub_client}</span></td>"
            f"<td class='snip'>{snippet_esc}</td>"
            f"<td class='mono'><a href='{du}'>detail</a></td>"
            "</tr>"
        )

    body = "\n".join(rows)
    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Lancers structured preview</title>
<style>
body{{font-family:system-ui,sans-serif;margin:16px;background:#f1f5f9;color:#0f172a;}}
.note{{opacity:.85;font-size:.9rem;max-width:900px;line-height:1.5;margin-bottom:14px;}}
table{{width:100%;border-collapse:collapse;background:#fff;box-shadow:0 1px 4px rgba(15,23,42,.06);border-radius:6px;overflow:hidden;}}
th,td{{border:1px solid #e2e8f0;padding:10px;vertical-align:top;font-size:.86rem;line-height:1.35;}}
th{{background:#e8f1ff;text-align:left;font-weight:600;}}
.mono{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.82rem;}}
.cat{{color:#2563eb;font-weight:700;font-size:.8rem;}}
.sub{{font-size:.8rem;opacity:.88;display:block;margin-top:4px;line-height:1.4;}}
.snip{{max-width:360px;color:#475569;font-size:.8rem;}}</style></head><body>
<h1 style="margin:0 0 8px;font-size:1.2rem;">Lancers · structured listing snapshot</h1>
<p class="note">Fields match what <code>monitor.py</code> parses from category search HTML ({len(jobs)} row(s)).
<a href="/json">Raw JSON array</a> · stop the process with Ctrl+C.</p>
<table><thead><tr>
<th scope="col" style="width:56px;"></th><th scope="col" style="width:120px;">カテゴリ / ID</th>
<th scope="col">タイトル</th><th scope="col" style="width:140px;">予算</th>
<th scope="col">クライアント<br><small>発注・評価</small></th><th scope="col">一覧要約（抜粋）</th>
<th scope="col" style="width:72px;">リンク</th></tr></thead><tbody>{body}</tbody></table></body></html>
"""
    return page.encode("utf-8")


def serve_structured_preview(bind: str, port: int, jobs: list[JobListing]) -> None:
    json_bytes = json.dumps([job_public_dict(j) for j in jobs], ensure_ascii=False, indent=2).encode("utf-8")
    html_bytes = build_live_preview_html(jobs)

    class StructuredPreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args_: object) -> None:
            LOG.info("preview %s", fmt % args_)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(json_bytes)))
                self.end_headers()
                self.wfile.write(json_bytes)
                return
            if path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
                return
            self.send_error(404, "Use / or /json")

    httpd = HTTPServer((bind, port), StructuredPreviewHandler)
    httpd.serve_forever()


def discord_retry_after_seconds(response: requests.Response) -> float:
    hdr = response.headers.get("Retry-After")
    if hdr is not None and str(hdr).strip() != "":
        try:
            return max(0.0, float(str(hdr).strip()))
        except ValueError:
            pass
    try:
        rf = response.json().get("retry_after")
        if isinstance(rf, (int, float)):
            return max(0.0, float(rf))
        if isinstance(rf, str) and rf.strip():
            return max(0.0, float(rf.strip()))
    except Exception:
        pass
    return 2.0


def discord_execute_webhook(webhook_url: str, payload: dict) -> None:
    """POST to Discord webhook; backoff and retry on HTTP 429."""
    margin = float(os.environ.get("DISCORD_RATE_LIMIT_MARGIN_SECONDS", "0.15"))
    retries_max = int(os.environ.get("DISCORD_WEBHOOK_MAX_RETRIES", "24"))
    n_embeds = len(payload.get("embeds") or ())
    if n_embeds:
        phase = "%d embed(s)" % n_embeds
    elif isinstance(payload.get("content"), str) and payload["content"]:
        phase = "text notice"
    else:
        phase = "webhook"

    consecutive_429 = 0
    while True:
        r = requests.post(
            webhook_url,
            json=payload,
            timeout=30,
            headers={"User-Agent": "JobHunter-Webhook/1.0"},
        )

        if r.status_code == 429:
            if consecutive_429 >= retries_max:
                raise RuntimeError(
                    "Discord webhook HTTP 429 after %d backoff retries — %s"
                    % (
                        retries_max,
                        r.text[:400] if r.text else "",
                    )
                )
            consecutive_429 += 1
            delay = discord_retry_after_seconds(r) + margin
            LOG.warning(
                "Discord HTTP 429 (%s); sleeping %.2fs then retry %d/%d",
                phase,
                delay,
                consecutive_429,
                retries_max,
            )
            time.sleep(delay)
            continue

        if r.status_code == 405:
            raise RuntimeError(
                "Discord returned HTTP 405 — the webhook URL is usually wrong or incomplete "
                "(missing token after the last /). Regenerate the webhook in Discord and paste the full "
                "'Copy Webhook URL' into DISCORD_WEBHOOK_URL."
            )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook failed: HTTP {r.status_code} {r.text[:500]}")
        return


def discord_notify_monitor_started(
    webhook_url: str,
    *,
    once_mode: bool,
    interval_seconds: int,
    listing_urls_for_this_channel: list[str],
) -> None:
    """One short webhook message confirming the watcher has started."""
    try:
        host = socket.gethostname()
    except OSError:
        host = "(不明)"
    mode = "**--once** (1回だけポーリングして終了)" if once_mode else f"連続運転（約 **{interval_seconds}** 秒ごと）"
    cat_lines = "\n".join(
        f"- **{listing_category_label(u)}**: `{u}`" for u in sorted(listing_urls_for_this_channel)
    )
    content = (
        "**Lancers Monitor 起動しました**\n"
        "求人ボード監視プロセスが立ち上がりました。\n\n"
        f"- ホスト: `{host}`\n"
        f"- PID: `{os.getpid()}`\n"
        f"- モード: {mode}\n"
        f"- このチャンネルへのカテゴリ:\n{cat_lines}"
    ).strip()

    discord_execute_webhook(webhook_url, {"content": content[:2000]})


def post_discord_new_jobs(webhook_url: str, jobs: list[JobListing]) -> None:
    """Send Discord notifications batched as multiple embeds per request (fewer POSTs → fewer 429s)."""
    if not jobs:
        return

    raw_lim = os.environ.get("DISCORD_MAX_EMBEDS_PER_MESSAGE", "10").strip()
    chunk_size = 10
    if raw_lim:
        try:
            chunk_size = max(1, min(10, int(raw_lim)))
        except ValueError:
            chunk_size = 10

    pause = float(os.environ.get("DISCORD_POST_DELAY_SECONDS", "1.25"))

    total = len(jobs)
    for offset in range(0, total, chunk_size):
        chunk = jobs[offset : offset + chunk_size]
        payload = {"embeds": [job_to_discord_embed(j) for j in chunk]}
        discord_execute_webhook(webhook_url, payload)
        end = offset + len(chunk)
        if end < total:
            LOG.debug("Discord batch pause %.2fs before next webhook (%d/%d jobs)", pause, end, total)
            time.sleep(pause)


def load_state(path: Path, urls: list[str]) -> dict[str, set[str]]:
    buckets = monitor_category_buckets(urls)
    empty: dict[str, set[str]] = {k: set() for k in buckets}

    if not path.is_file():
        return empty
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        LOG.warning("Could not read state %s (%s); starting fresh", path, e)
        return empty
    if not raw:
        LOG.info("State file %s is empty — will seed on first poll if needed", path)
        return empty
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        LOG.warning("Invalid JSON in state %s (%s); starting fresh", path, e)
        return empty

    known: dict[str, set[str]] = {k: set() for k in buckets}

    by_cat = data.get("known_ids_by_category")
    if isinstance(by_cat, dict):
        for key, ids in by_cat.items():
            if isinstance(key, str) and isinstance(ids, list):
                known.setdefault(key, set()).update(str(x) for x in ids)
        for k in buckets:
            known.setdefault(k, set())
        return known

    legacy_ids = data.get("known_ids")
    if isinstance(legacy_ids, list):
        merged = {str(x) for x in legacy_ids}
        for k in buckets:
            known[k].update(merged)
        LOG.info(
            "Migrated legacy flat known_ids (%d ids) into each category bucket: %s.",
            len(merged),
            ", ".join(buckets),
        )
        return known

    LOG.warning("State file missing known_ids_by_category / known_ids; starting fresh.")
    return empty


def save_state(path: Path, known: dict[str, set[str]], urls: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bucket_keys = monitor_category_buckets(urls)
    keys_to_save = sorted(set(bucket_keys) | set(known.keys()))
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "known_ids_by_category": {
            k: sorted(known.get(k, set()), key=lambda x: int(x), reverse=True) for k in keys_to_save
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmppath = tempfile.mkstemp(prefix=".lancers_seen.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmppath, path)
    except Exception:
        try:
            os.unlink(tmppath)
        except OSError:
            pass
        raise


def run_poll(
    session: requests.Session,
    listing_webhooks: dict[str, str],
    urls: list[str],
    state_path: Path,
    timeout: float,
    *,
    notify_on_first_poll: bool,
    already_known: dict[str, set[str]],
) -> dict[str, set[str]]:
    """
    Fetch all URLs, return updated known job IDs grouped by listing category (system / web / …).

    When notify_on_first_poll is False and every category bucket is empty, only seeds state.
    """
    all_from_round: dict[str, JobListing] = {}
    ordered_ids: list[str] = []

    for url in urls:
        html = fetch_html(session, url, timeout)
        for job in parse_listings(html, url):
            if job.work_id not in all_from_round:
                ordered_ids.append(job.work_id)
                all_from_round[job.work_id] = job

    new_entries = []
    buckets = monitor_category_buckets(urls)
    known: dict[str, set[str]] = defaultdict(set)
    for k in buckets:
        known[k].update(already_known.get(k, set()))

    empty_state = sum(len(v) for v in known.values()) == 0
    suppress_all = empty_state and not notify_on_first_poll

    if suppress_all:
        for wid in ordered_ids:
            job = all_from_round[wid]
            cat = listing_category_label(job.source_listing_url)
            known.setdefault(cat, set()).add(wid)
        save_state(state_path, dict(known), urls)
        LOG.info(
            "First run / empty state — seeded category buckets (%s) without notifications",
            ", ".join(f"{k}:{len(known[k])}" for k in sorted(known.keys())),
        )
        return dict(known)

    for wid in ordered_ids:
        job = all_from_round[wid]
        cat = listing_category_label(job.source_listing_url)
        if wid not in known.setdefault(cat, set()):
            new_entries.append(job)

    # Oldest-unknown first within this batch so chronological order resembles listing order top-to-bottom
    for job in new_entries:
        LOG.info("New job detected: %s — %s", job.work_id, job.title[:80])
    if not new_entries:
        LOG.info(
            "Poll finished: no jobs to notify — every ID on this round's listing pages is already "
            "in `%s` (%d job card(s) scanned). Startup Discord messages are separate from job alerts.",
            state_path.name,
            len(all_from_round),
        )
    if new_entries:
        if env_bool("COPY_TO_CLIPBOARD", True):
            fetch_detail_cb = env_bool("CLIPBOARD_FETCH_WORK_DETAIL", True)
            blob = clipboard_text_from_jobs(
                session,
                new_entries,
                timeout,
                fetch_detail=fetch_detail_cb,
                urls_priority=urls,
            )
            if copy_plain_text_best_effort(blob):
                LOG.info("Copied request-detail text for %d new job(s) to the clipboard.", len(new_entries))
            else:
                LOG.warning(
                    "COPY_TO_CLIPBOARD is on but clipboard write failed "
                    "(headless SSH / installs: pip install pyperclip plus xclip, wl-copy, or desktop session)."
                )

        hooks_seen: list[str] = []
        hooks_added = set()
        for listing_u in urls:
            hook_u = listing_webhooks.get(listing_u)
            if hook_u is None:
                continue
            if hook_u not in hooks_added:
                hooks_added.add(hook_u)
                hooks_seen.append(hook_u)

        for hook_url in hooks_seen:
            bucket = [j for j in new_entries if listing_webhooks[j.source_listing_url] == hook_url]
            if bucket:
                post_discord_new_jobs(hook_url, bucket)

    for wid in ordered_ids:
        job = all_from_round[wid]
        cat = listing_category_label(job.source_listing_url)
        known.setdefault(cat, set()).add(wid)

    save_state(state_path, dict(known), urls)
    LOG.debug("Poll done; known counts by category: %s", {k: len(v) for k, v in sorted(known.items())})
    return dict(known)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Monitor Lancers.jp listings and notify Discord.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Perform a single poll and exit.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Override POLL_INTERVAL_SECONDS for looping mode.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete STATE_PATH and exit.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Fetch listing pages once and serve parsed data in the browser (HTTP); no Discord/state updates.",
    )
    parser.add_argument(
        "--preview-port",
        type=int,
        default=None,
        help="Port for --preview (defaults to PREVIEW_HTTP_PORT or 8790).",
    )
    parser.add_argument(
        "--preview-bind",
        type=str,
        default=None,
        help="Bind host for --preview (defaults to PREVIEW_HTTP_BIND or 127.0.0.1).",
    )
    args = parser.parse_args(argv)

    load_env_file_if_available()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )

    state_path_str = os.environ.get("STATE_PATH", "lancers_seen.json").strip()
    state_path = Path(state_path_str).expanduser().resolve()

    if args.reset_state:
        try:
            if state_path.is_file():
                state_path.unlink()
                LOG.info("Removed state file %s", state_path)
            else:
                LOG.info("No state file at %s", state_path)
        except OSError as e:
            LOG.error("Could not reset state: %s", e)
            return 1
        return 0

    timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))
    urls = load_urls_env()

    if args.preview:
        bind = (args.preview_bind or os.environ.get("PREVIEW_HTTP_BIND", "127.0.0.1")).strip()
        port = (
            args.preview_port
            if args.preview_port is not None
            else int(os.environ.get("PREVIEW_HTTP_PORT", "8790"))
        )
        session_pv = requests.Session()
        session_pv.headers.update(
            {
                "User-Agent": os.environ.get("HTTP_USER_AGENT", DEFAULT_UA).strip() or DEFAULT_UA,
                "Accept-Language": "ja,en;q=0.9",
            }
        )
        try:
            jobs = fetch_live_listings_snapshot(session_pv, urls, timeout)
        except requests.RequestException as e:
            LOG.error("Preview fetch failed: %s", e)
            return 1
        LOG.info(
            "Structured preview: %d parsed row(s). Open http://%s:%s/  (JSON: http://%s:%s/json) — Ctrl+C to stop.",
            len(jobs),
            bind,
            port,
            bind,
            port,
        )
        if bind in ("0.0.0.0", "::"):
            LOG.warning(
                "PREVIEW is bound to all interfaces; add a firewall rule or use SSH -L %s:127.0.0.1:%s ...",
                port,
                port,
            )
        try:
            serve_structured_preview(bind, port, jobs)
        except OSError as e:
            LOG.error("Could not bind preview server %s:%s — %s", bind, port, e)
            return 1
        return 0

    try:
        listing_webhooks = load_listing_webhook_assignments(urls)
    except ValueError as e:
        LOG.error("%s", e)
        return 1

    unique_hooks = sorted(set(listing_webhooks.values()))
    for hook in unique_hooks:
        try:
            validate_discord_webhook_execute_url(hook)
        except ValueError as e:
            LOG.error("%s (%s)", e, webhook_url_diagnostic(hook))
            return 1

    interval = args.interval if args.interval is not None else int(os.environ.get("POLL_INTERVAL_SECONDS", "180"))
    notify_first = env_bool("NOTIFY_ON_FIRST_RUN", False)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": os.environ.get("HTTP_USER_AGENT", DEFAULT_UA).strip() or DEFAULT_UA,
            "Accept-Language": "ja,en;q=0.9",
        }
    )

    known = load_state(state_path, urls)

    hooks_to_listings: defaultdict[str, list[str]] = defaultdict(list)
    for listing_u, hook in listing_webhooks.items():
        hooks_to_listings[hook].append(listing_u)

    if env_bool("DISCORD_NOTIFY_ON_START", True):
        startup_errors = 0
        for hook, lu_list in sorted(hooks_to_listings.items(), key=lambda kv: kv[0]):
            try:
                discord_notify_monitor_started(
                    hook,
                    once_mode=args.once,
                    interval_seconds=interval,
                    listing_urls_for_this_channel=sorted(lu_list),
                )
            except Exception as e:
                startup_errors += 1
                LOG.warning("Startup Discord notification failed for one channel: %s", e)
        if startup_errors == 0:
            LOG.info("Posted startup notification(s) to %d Discord channel(s).", len(hooks_to_listings))

    try:
        while True:
            try:
                known = run_poll(
                    session,
                    listing_webhooks,
                    urls,
                    state_path,
                    timeout,
                    notify_on_first_poll=notify_first,
                    already_known=known,
                )
            except requests.RequestException as e:
                LOG.error("Network error during poll: %s", e)
            except RuntimeError as e:
                LOG.error("%s", e)

            if args.once:
                break
            LOG.debug("Sleeping %s seconds...", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        LOG.info("Interrupted.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
