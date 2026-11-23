"""News jobs (CLAUDE.md §4.2, §8.1; workflows 06–07): Hacker News stories + comments, vendor RSS, GDELT."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from tracker.common import JobResult, RawStore, http_get, sha256, upsert, utcnow
from tracker.vendors import load_rules, match_vendors

HN = "https://hn.algolia.com/api/v1/search"
COMMENTS_TOP_STORIES = 20     # per run: fetch comments for the most-discussed matched stories
COMMENTS_PER_STORY = 500


def _domain(url: str | None) -> str | None:
    return urlparse(url).netloc.removeprefix("www.") if url else None


def _save(cur, items: list[dict], bridges: list[dict]) -> int:
    n = upsert(cur, "core.fact_news_item", items, ["news_id"])
    upsert(cur, "core.bridge_news_vendor", bridges, ["news_id", "vendor_id"])
    return n


# ------------------------------------------------------------------ 06 Hacker News
def hn(conn, run_id: str, raw: RawStore, res: JobResult, hours: int = 24) -> None:
    cur = conn.cursor()
    rules = load_rules(cur)
    since = int((utcnow() - timedelta(hours=hours)).timestamp())
    stories: dict[str, dict] = {}
    bridges: dict[tuple, dict] = {}
    for rule in rules:
        for kw in rule.keywords:
            page = 0
            while True:
                params = {"query": kw, "tags": "story", "numericFilters": f"created_at_i>{since}",
                          "hitsPerPage": 100, "page": page}
                resp = http_get(HN, params=params)
                resp.raise_for_status()
                data = resp.json()
                raw.put(source="hn_story_search", request_key=f"{kw}|{since}|p{page}", request_url=resp.url,
                        run_id=run_id, http_status=200, payload=data)
                for h in data.get("hits", []):
                    title = h.get("title") or ""
                    matched = match_vendors(rules, title)
                    if not matched:
                        continue
                    nid = f"hn:{h['objectID']}"
                    stories[nid] = {"news_id": nid, "source": "hn", "title": title,
                                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                                    "published_at": h.get("created_at"), "points": h.get("points"),
                                    "num_comments": h.get("num_comments"), "domain": _domain(h.get("url"))}
                    for vid, mr in matched.items():
                        bridges[(nid, vid)] = {"news_id": nid, "vendor_id": vid, "match_rule": mr}
                page += 1
                if page >= min(data.get("nbPages", 0), 10):
                    break
    res.rows_loaded += _save(cur, list(stories.values()), list(bridges.values()))

    # comments → raw_comments (sentiment input). Most-discussed matched stories first.
    top = sorted(stories.values(), key=lambda s: s.get("num_comments") or 0, reverse=True)[:COMMENTS_TOP_STORIES]
    n_comments = 0
    for s in top:
        if not s.get("num_comments"):
            continue
        sid = s["news_id"].split(":", 1)[1]
        resp = http_get(HN, params={"tags": f"comment,story_{sid}", "hitsPerPage": COMMENTS_PER_STORY})
        if resp.status_code != 200:
            continue
        for c in resp.json().get("hits", []):
            raw.put(source="hn_comments", request_key=c["objectID"], request_url=resp.url, run_id=run_id,
                    http_status=200,
                    payload={"comment_id": c["objectID"], "story_id": sid, "story_title": s["title"],
                             "created_at": c.get("created_at"), "comment_text": c.get("comment_text"),
                             "parent_id": c.get("parent_id"), "author": None})
            n_comments += 1
    res.notes.append(f"{len(stories)} stories matched, {n_comments} comments landed raw ({raw.mode})")


# ------------------------------------------------------------------ 07 RSS + GDELT
RSS = {"rss_openai": ("https://openai.com/news/rss.xml", "openai"),
       "rss_deepmind": ("https://deepmind.google/blog/rss.xml", "google")}


def rss(conn, run_id: str, raw: RawStore, res: JobResult) -> None:
    cur = conn.cursor()
    items, bridges, failed = [], [], []
    for source, (url, vendor) in RSS.items():
        resp = http_get(url)
        if resp.status_code != 200:
            failed.append(f"{source}:{resp.status_code}")
            continue
        raw.put(source=source, request_key="feed", request_url=url, run_id=run_id, http_status=200,
                payload=resp.text, content_hash=sha256(resp.content))
        root = ET.fromstring(resp.content)
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = it.findtext("pubDate")
            if not title or not pub:
                continue
            nid = f"{source}:{sha256(link or title)[7:23]}"
            items.append({"news_id": nid, "source": source, "title": title, "url": link,
                          "published_at": parsedate_to_datetime(pub), "points": None, "num_comments": None,
                          "domain": _domain(link)})
            bridges.append({"news_id": nid, "vendor_id": vendor, "match_rule": f"source:{source}"})
    res.rows_loaded += _save(cur, items, bridges)
    if failed:
        res.status = "partial"
        res.notes.append("failed: " + ", ".join(failed))


GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
# One combined query per run. GDELT throttles per IP and five back-to-back queries got 429s even with long waits;
# vendors are matched locally with the keyword + context rule (§8.1), exactly as before.
GDELT_QUERIES = {
    "all": '(OpenAI OR ChatGPT OR Anthropic OR "Claude AI" OR "Google Gemini" OR "Gemini AI" OR DeepMind '
           'OR DeepSeek OR Grok OR xAI) sourcelang:english',
}


def gdelt(conn, run_id: str, raw: RawStore, res: JobResult, timespan: str = "1d") -> None:
    cur = conn.cursor()
    rules = load_rules(cur)
    items, bridges, failed = {}, {}, []
    for i, (vendor, q) in enumerate(GDELT_QUERIES.items()):
        if i:
            time.sleep(10)                             # GDELT: max 1 request / 5 s
        resp = http_get(GDELT, params={"query": q, "mode": "artlist", "format": "json", "maxrecords": 250,
                                       "timespan": timespan, "sort": "datedesc"}, min_backoff=60, retries=2)
        if resp.status_code != 200 or not resp.text.strip().startswith("{"):
            failed.append(f"{vendor}:{resp.status_code}")
            continue
        data = resp.json()
        raw.put(source="gdelt", request_key=f"{vendor}|{timespan}", request_url=resp.url, run_id=run_id,
                http_status=200, payload=data)
        for a in data.get("articles", []):
            title, url = a.get("title") or "", a.get("url")
            matched = match_vendors(rules, title)
            if not matched or not url:
                continue
            nid = f"gdelt:{sha256(url)[7:23]}"
            seen = a.get("seendate")                   # 20261122T063000Z
            published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if seen else utcnow()
            items[nid] = {"news_id": nid, "source": "gdelt", "title": title, "url": url, "published_at": published,
                          "points": None, "num_comments": None, "domain": a.get("domain")}
            for vid, mr in matched.items():
                bridges[(nid, vid)] = {"news_id": nid, "vendor_id": vid, "match_rule": mr}
    res.rows_loaded += _save(cur, list(items.values()), list(bridges.values()))
    if failed:
        res.status = "partial"
        res.notes.append("failed: " + ", ".join(failed) + " (rate-limited; the next run back-fills the window)")
