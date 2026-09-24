#!/usr/bin/env python3
"""
Daily AI / Data Science news digest.

Fetches the last day's articles from well-known RSS feeds (plus the Hugging Face
Daily Papers API), removes duplicates, and delivers a grouped list of clickable
titles to Telegram and to Gmail.

Usage:
    python news_bot.py             fetch, then send to every configured channel
    python news_bot.py --dry-run   fetch and print the digest; send nothing

Configuration (environment variables; GitHub Secrets when run in Actions):
    TG_TOKEN, TG_CHAT_ID             Telegram bot token and chat ID
    GMAIL_USER, GMAIL_APP_PASSWORD   Gmail address and its 16-character App Password
    MAIL_TO                          Recipients, comma-separated (default: GMAIL_USER)
    LOOKBACK_HOURS                   Only include items this recent (default 24)
    MAX_PER_SOURCE                   Items kept per source (default 5)

A channel whose secrets are missing is skipped. A source that fails is logged
and skipped. The process exits non-zero only if a configured channel failed.
"""

from __future__ import annotations

import argparse
import calendar
import html
import logging
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

# ---------------------------------------------------------------------------
# SOURCES: edit this dict to add or remove feeds.
#
#   { "Section heading": { "Source name": "feed URL", ... }, ... }
#
# Sections and sources appear in the digest in this order. When two sources
# carry the same story, the one listed first keeps it, so official and news
# sources sit above aggregators. Any RSS or Atom URL works. The one non-RSS
# source is the Hugging Face Daily Papers API, recognised by HF_PAPERS_API.
# ---------------------------------------------------------------------------
HF_PAPERS_API = "https://huggingface.co/api/daily_papers?limit=100"

SOURCES: dict[str, dict[str, str]] = {
    "Official AI Labs": {
        "OpenAI News": "https://openai.com/news/rss.xml",
        # Anthropic publishes no RSS feed; this community mirror of
        # anthropic.com/news is regenerated hourly by GitHub Actions.
        "Anthropic News": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
        "Google DeepMind": "https://deepmind.google/blog/rss.xml",
        "Google Research": "https://research.google/blog/rss/",
        # ai.meta.com/blog has no feed; Meta's newsroom AI tag is the official one.
        "Meta AI": "https://about.fb.com/news/tag/ai/feed/",
        "Hugging Face Blog": "https://huggingface.co/blog/feed.xml",
    },
    "News": {
        "MIT Technology Review (AI)": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "The Verge (AI)": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        # VentureBeat's AI category feed is stale; the main feed is mostly AI.
        "VentureBeat": "https://venturebeat.com/feed/",
    },
    "Research": {
        "Hugging Face Daily Papers": HF_PAPERS_API,
    },
    "Discussions": {
        "Hacker News (100+ points)": (
            "https://hnrss.org/newest?points=100&q="
            "AI+OR+LLM+OR+GPT+OR+OpenAI+OR+Anthropic+OR+Claude+OR+Gemini"
            "+OR+%22machine+learning%22+OR+%22deep+learning%22"
        ),
        "r/MachineLearning": "https://www.reddit.com/r/MachineLearning/top/.rss?t=day",
        "r/LocalLLaMA": "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day",
        "r/datascience": "https://www.reddit.com/r/datascience/top/.rss?t=day",
    },
    "Community & Blogs": {
        "Towards Data Science": "https://towardsdatascience.com/feed",
        "MarkTechPost": "https://www.marktechpost.com/feed/",
        "KDnuggets": "https://www.kdnuggets.com/feed",
        "Analytics Vidhya": "https://www.analyticsvidhya.com/feed/",
        "Medium #llm": "https://medium.com/feed/tag/llm",
        "Medium #generative-ai": "https://medium.com/feed/tag/generative-ai",
        "Medium #data-science": "https://medium.com/feed/tag/data-science",
        "Medium #machine-learning": "https://medium.com/feed/tag/machine-learning",
    },
}

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = (10, 30)          # (connect, read) seconds
MAX_RETRIES = 2                  # extra attempts on 429 / 5xx / connection reset
MAX_RETRY_WAIT = 30              # never sleep longer than this between attempts
SAME_HOST_GAP = 2.0              # seconds between requests to one host (Reddit, Medium)
RETRY_STATUSES = {429, 500, 502, 503, 504}
TITLE_SIMILARITY = 0.9           # titles at least this similar count as duplicates
TELEGRAM_LIMIT = 4000            # Telegram's hard limit is 4096 characters
TRACKING_PARAMS = {"source", "ref", "fbclid", "gclid", "mc_cid", "mc_eid"}

log = logging.getLogger("news_bot")


@dataclass
class Item:
    title: str
    url: str
    published: datetime


@dataclass
class SourceResult:
    section: str
    name: str
    fetched: int = 0             # items inside the lookback window
    items: list[Item] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def env_int(name: str, default: int) -> int:
    """Read a positive integer env var. Empty or invalid values fall back to default."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        log.warning("%s=%r is not a positive integer; using %d", name, raw, default)
        return default


def env_str(name: str) -> str:
    return (os.getenv(name) or "").strip()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, "
              "application/json;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})
_last_request: dict[str, float] = {}


def http_get(url: str) -> requests.Response:
    """GET with a browser User-Agent, timeouts, per-host spacing and short retries."""
    host = urlsplit(url).netloc
    for attempt in range(MAX_RETRIES + 1):
        gap = SAME_HOST_GAP - (time.monotonic() - _last_request.get(host, 0.0))
        if gap > 0:
            time.sleep(gap)
        _last_request[host] = time.monotonic()

        try:
            resp = SESSION.get(url, timeout=HTTP_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = 10 * (attempt + 1)
            reason = type(exc).__name__
        else:
            if resp.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                resp.raise_for_status()
                return resp
            wait = _retry_after(resp) or 10 * (attempt + 1)
            reason = f"HTTP {resp.status_code}"

        wait = min(wait, MAX_RETRY_WAIT)
        log.info("  %s from %s; retrying in %ds", reason, host, wait)
        time.sleep(wait)
    raise RuntimeError("unreachable")


def _retry_after(resp: requests.Response) -> int | None:
    try:
        return int(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fetching and parsing
# ---------------------------------------------------------------------------
def clean_title(raw: str | None) -> str:
    """Strip stray tags and collapse whitespace. feedparser already decodes entities."""
    text = re.sub(r"<[^>]+>", "", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_rss(url: str, cutoff: datetime) -> list[Item]:
    feed = feedparser.parse(http_get(url).content)
    if not feed.entries and feed.bozo:
        raise ValueError(f"could not parse feed: {feed.bozo_exception}")

    items = []
    for entry in feed.entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not stamp:
            continue    # no date means recency cannot be checked
        published = datetime.fromtimestamp(calendar.timegm(stamp), tz=timezone.utc)
        title, link = clean_title(entry.get("title")), strip_tracking(entry.get("link") or "")
        if published >= cutoff and title and link:
            items.append(Item(title, link, published))
    items.sort(key=lambda i: i.published, reverse=True)
    return items


def fetch_hf_papers(url: str, cutoff: datetime) -> list[Item]:
    """Hugging Face Daily Papers, most upvoted first.

    Papers are stamped with the day they were featured (midnight UTC), so a
    paper counts as recent when its day is on or after the cutoff's day.
    Otherwise an early-morning run would see only a handful of today's papers.
    """
    papers = []
    for entry in http_get(url).json():
        paper = entry.get("paper") or {}
        day = paper.get("submittedOnDailyAt") or entry.get("publishedAt")
        if not day or not paper.get("id"):
            continue
        featured = datetime.fromisoformat(day.replace("Z", "+00:00"))
        if featured.date() < cutoff.date():
            continue
        title = clean_title(entry.get("title") or paper.get("title"))
        papers.append((paper.get("upvotes", 0),
                       Item(title, f"https://huggingface.co/papers/{paper['id']}", featured)))
    papers.sort(key=lambda p: p[0], reverse=True)
    return [item for _, item in papers]


def fetch_source(url: str, cutoff: datetime) -> list[Item]:
    if url == HF_PAPERS_API:
        return fetch_hf_papers(url, cutoff)
    return fetch_rss(url, cutoff)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def strip_tracking(url: str) -> str:
    """Drop utm_* and similar tracking parameters (e.g. Medium's ?source=rss...)."""
    parts = urlsplit(url.strip())
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def normalize_url(url: str) -> str:
    """Canonical form for comparison: no scheme, www, fragment, tracking params or trailing slash."""
    parts = urlsplit(strip_tracking(url))
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit(("", host, parts.path.rstrip("/"), query, ""))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()


class Deduper:
    """Remembers every kept item and rejects repeats by URL or near-identical title."""

    def __init__(self) -> None:
        self.urls: set[str] = set()
        self.titles: list[str] = []

    def is_new(self, item: Item) -> bool:
        url, title = normalize_url(item.url), normalize_title(item.title)
        if url in self.urls:
            return False
        if title and any(
            title == seen or SequenceMatcher(None, title, seen).ratio() >= TITLE_SIMILARITY
            for seen in self.titles
        ):
            return False
        self.urls.add(url)
        if title:
            self.titles.append(title)
        return True


# ---------------------------------------------------------------------------
# Building the digest
# ---------------------------------------------------------------------------
def collect(lookback_hours: int, max_per_source: int) -> list[SourceResult]:
    """Fetch every source in config order; dedupe and cap as we go."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    dedupe = Deduper()
    results = []

    for section, feeds in SOURCES.items():
        for name, url in feeds.items():
            result = SourceResult(section, name)
            log.info("Fetching %s", name)
            try:
                recent = fetch_source(url, cutoff)
            except Exception as exc:  # one bad source must never stop the run
                result.error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("  FAILED %s: %s", name, result.error)
            else:
                result.fetched = len(recent)
                kept = []
                for item in recent:
                    if len(kept) >= max_per_source:
                        break
                    if dedupe.is_new(item):
                        kept.append(item)
                result.items = kept
                log.info("  %d recent, %d kept", result.fetched, len(kept))
            results.append(result)
    return results


def digest_title(results: list[SourceResult]) -> str:
    today = datetime.now(timezone.utc)
    count = sum(len(r.items or []) for r in results)
    return f"AI News Digest – {today.day} {today:%b %Y} ({count} articles)"


def non_empty(results: list[SourceResult]) -> list[SourceResult]:
    return [r for r in results if r.items]


# ---------------------------------------------------------------------------
# Rendering: Telegram (HTML parse mode), email HTML, plain text
# ---------------------------------------------------------------------------
def esc(text: str) -> str:
    return html.escape(text, quote=True)


def render_telegram(results: list[SourceResult]) -> list[str]:
    """Return messages under TELEGRAM_LIMIT, split only between sources where possible."""
    header = f"<b>{esc(digest_title(results))}</b>"
    filled = non_empty(results)
    if not filled:
        return [f"{header}\n\nNo new articles today."]

    blocks, last_section = [], None
    for r in filled:
        lines = []
        if r.section != last_section:
            lines.append(f"\n<b>▌{esc(r.section.upper())}</b>")
            last_section = r.section
        lines.append(f"<b>{esc(r.name)}</b>")
        lines += [f'• <a href="{esc(i.url)}">{esc(i.title)}</a>' for i in r.items]
        blocks.append("\n".join(lines))

    messages, current = [], header
    for block in blocks:
        if len(current) + 2 + len(block) <= TELEGRAM_LIMIT:
            current += "\n\n" + block
            continue
        messages.append(current)
        current = block.lstrip("\n")
        # A single source too big for one message: fall back to splitting by line.
        while len(current) > TELEGRAM_LIMIT:
            cut = current.rfind("\n", 0, TELEGRAM_LIMIT)
            cut = cut if cut > 0 else TELEGRAM_LIMIT
            messages.append(current[:cut])
            current = current[cut:].lstrip("\n")
    messages.append(current)
    return messages


def render_email_html(results: list[SourceResult]) -> str:
    title = esc(digest_title(results))
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title></head>"
        f'<body style="margin:0;padding:0;background:#f4f5f7;font-family:{font};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f5f7;"><tr><td align="center" style="padding:16px 8px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:640px;background:#ffffff;border-radius:10px;">'
        '<tr><td style="padding:24px 20px 8px 20px;">'
        f'<h1 style="margin:0;font-size:21px;line-height:1.3;color:#111827;">{title}</h1>'
        '</td></tr>'
    ]

    filled = non_empty(results)
    if not filled:
        parts.append('<tr><td style="padding:8px 20px 24px 20px;font-size:16px;color:#374151;">'
                     "No new articles today.</td></tr>")

    last_section = None
    for r in filled:
        if r.section != last_section:
            parts.append(
                '<tr><td style="padding:20px 20px 0 20px;">'
                '<div style="font-size:12px;font-weight:700;letter-spacing:.08em;'
                'text-transform:uppercase;color:#6d28d9;border-bottom:2px solid #ede9fe;'
                f'padding-bottom:6px;">{esc(r.section)}</div></td></tr>'
            )
            last_section = r.section
        links = "".join(
            '<li style="margin:0 0 10px 0;">'
            f'<a href="{esc(i.url)}" style="color:#1d4ed8;text-decoration:none;'
            f'font-size:16px;line-height:1.4;">{esc(i.title)}</a></li>'
            for i in r.items
        )
        parts.append(
            '<tr><td style="padding:12px 20px 0 20px;">'
            f'<div style="font-size:15px;font-weight:600;color:#111827;margin-bottom:6px;">{esc(r.name)}</div>'
            f'<ul style="margin:0;padding-left:20px;color:#9ca3af;">{links}</ul></td></tr>'
        )

    parts.append(
        '<tr><td style="padding:20px;font-size:12px;color:#9ca3af;">'
        "Sent automatically by news_bot on GitHub Actions.</td></tr>"
        "</table></td></tr></table></body></html>"
    )
    return "".join(parts)


def render_text(results: list[SourceResult]) -> str:
    lines = [digest_title(results)]
    filled = non_empty(results)
    if not filled:
        return lines[0] + "\n\nNo new articles today.\n"
    last_section = None
    for r in filled:
        if r.section != last_section:
            lines += ["", f"== {r.section.upper()} =="]
            last_section = r.section
        lines += ["", r.name] + [f"  - {i.title}\n    {i.url}" for i in r.items]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def send_telegram(token: str, chat_id: str, messages: list[str]) -> None:
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for n, text in enumerate(messages, 1):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "disable_web_page_preview": True,   # older clients / API versions
        }
        for attempt in range(2):
            try:
                resp = requests.post(api, json=payload, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                # Exception text contains the request URL, which embeds the token.
                raise RuntimeError(str(exc).replace(token, "***")) from None
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.ok and body.get("ok"):
                break
            retry = (body.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry and attempt == 0:
                time.sleep(min(int(retry), MAX_RETRY_WAIT))
                continue
            raise RuntimeError(f"HTTP {resp.status_code}: {body.get('description', resp.text[:200])}")
        log.info("Telegram: sent message %d/%d", n, len(messages))
        time.sleep(1)   # stay under Telegram's one-message-per-second limit per chat


def send_email(user: str, app_password: str, recipients: list[str],
               subject: str, text: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        # Google displays App Passwords in groups of four; the spaces are optional.
        smtp.login(user, app_password.replace(" ", ""))
        smtp.send_message(msg, from_addr=user, to_addrs=recipients)
    log.info("Email: sent to %s", ", ".join(recipients))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def log_report(results: list[SourceResult]) -> None:
    log.info("Source report:")
    for r in results:
        status = f"FAILED  {r.error}" if r.error else f"{r.fetched:3d} recent, {len(r.items):2d} kept"
        log.info("  %-28s %s", r.name, status)
    ok = sum(1 for r in results if not r.error)
    log.info("%d/%d sources fetched, %d articles in digest",
             ok, len(results), sum(len(r.items or []) for r in results))


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the daily AI news digest.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the digest instead of sending it")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S", stream=sys.stdout)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252

    lookback = env_int("LOOKBACK_HOURS", 24)
    per_source = env_int("MAX_PER_SOURCE", 5)
    log.info("Lookback %dh, up to %d items per source", lookback, per_source)

    results = collect(lookback, per_source)
    log_report(results)
    subject = digest_title(results)

    if args.dry_run:
        print("\n" + render_text(results))
        return 0

    failures = 0

    tg_token, tg_chat = env_str("TG_TOKEN"), env_str("TG_CHAT_ID")
    if tg_token and tg_chat:
        try:
            send_telegram(tg_token, tg_chat, render_telegram(results))
        except Exception as exc:
            failures += 1
            log.error("Telegram delivery failed: %s", exc)
    else:
        log.info("Telegram: skipped (TG_TOKEN or TG_CHAT_ID not set)")

    gmail_user, gmail_pass = env_str("GMAIL_USER"), env_str("GMAIL_APP_PASSWORD")
    recipients = [a.strip() for a in (env_str("MAIL_TO") or gmail_user).split(",") if a.strip()]
    if gmail_user and gmail_pass and recipients:
        try:
            send_email(gmail_user, gmail_pass, recipients, subject,
                       render_text(results), render_email_html(results))
        except Exception as exc:
            failures += 1
            log.error("Email delivery failed: %s", exc)
    else:
        log.info("Email: skipped (GMAIL_USER or GMAIL_APP_PASSWORD not set)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
