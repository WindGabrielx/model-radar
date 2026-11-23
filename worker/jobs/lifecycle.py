"""08 lifecycle pages (CLAUDE.md §4.3): fetch → hash → parse only if changed → fact_lifecycle_event.

A parse that returns 0 rows or sees a changed header is a DQ fail; previous events are kept (never deleted).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from tracker.common import JobResult, RawStore, http_get, sha256, today_utc, upsert

PAGES = {
    "lifecycle_anthropic": ("anthropic", "https://platform.claude.com/docs/en/about-claude/model-deprecations.md"),
    "lifecycle_openai": ("openai", "https://developers.openai.com/api/docs/deprecations.md"),
    "lifecycle_google": ("google", "https://ai.google.dev/gemini-api/docs/deprecations?hl=en"),
    "lifecycle_deepseek": ("deepseek", "https://api-docs.deepseek.com/updates.md"),
    "lifecycle_xai": ("xai", "https://docs.x.ai/release-notes.md"),
}
EXPECTED_HEADERS = {
    "lifecycle_anthropic": ["api model name", "current state", "deprecated", "tentative retirement date"],
    "lifecycle_openai": ["shutdown date", "affected model", "status", "recommended replacement", "notice date"],
    "lifecycle_google": ["model", "release date", "shutdown date", "recommended replacement"],
}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
DATE_RX = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", re.I)


class HeaderChanged(Exception):
    pass


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    m = DATE_RX.search(text)
    if m:
        return datetime.strptime(f"{m.group(1)[:3]} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _clean(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)      # [text](link) → text
    return re.sub(r"[*`\\]", "", s).strip()


def _md_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables, rows, header = [], [], None
    for line in text.splitlines() + [""]:
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not set("".join(cells)) <= set(":- "):
                rows.append(cells)
        elif header is not None:
            tables.append((header, rows))
            header, rows = None, []
    return tables


def _ev(vendor, name, etype, d, url, **kw) -> dict:
    return {"vendor_id": vendor, "model_api_name": name, "event_type": etype, "event_date": d, "model_id": None,
            "announced_date": kw.get("announced"), "replacement": kw.get("replacement"), "detail": kw.get("detail"),
            "is_tentative": kw.get("tentative", False), "source_url": url, "source_type": "official"}


def parse_anthropic(text: str, url: str) -> list[dict]:
    out = []
    for header, rows in _md_tables(text):
        if [h.lower() for h in header] != EXPECTED_HEADERS["lifecycle_anthropic"]:
            continue
        for r in rows:
            if len(r) < 4:
                continue
            name, state, dep, ret = (_clean(x) for x in r[:4])
            if (d := parse_date(dep)):
                out.append(_ev("anthropic", name, "deprecated", d, url, detail=state))
            if (d := parse_date(ret)):
                out.append(_ev("anthropic", name, "retired", d, url, detail=state,
                               tentative="not sooner" in ret.lower() or state.lower() == "active"))
    if not out and "API model name" not in text:
        raise HeaderChanged("anthropic table header not found")
    return out


def parse_openai(text: str, url: str) -> list[dict]:
    out, seen_header = [], False
    for header, rows in _md_tables(text):
        if [h.lower() for h in header] != EXPECTED_HEADERS["lifecycle_openai"]:
            continue
        seen_header = True
        for r in rows:
            if len(r) < 5:
                continue
            shutdown, models, status, repl, notice = r[:5]
            sd, nd = parse_date(shutdown), parse_date(notice)
            for name in re.findall(r"`([^`]+)`", models) or [_clean(models)]:
                if nd:
                    out.append(_ev("openai", name, "deprecated", nd, url, replacement=_clean(repl), detail=_clean(status)))
                if sd:
                    out.append(_ev("openai", name, "retired", sd, url, announced=nd, replacement=_clean(repl),
                                   detail=_clean(status)))
    if not seen_header:
        raise HeaderChanged("openai deprecations table header not found")
    return out


def parse_google(text: str, url: str) -> list[dict]:
    """Markdoc {% table %} blocks: header bullets, then rows separated by ---."""
    out, seen_header = [], False
    for m in re.finditer(r"\{% table %\}(.*?)\{% /table %\}", text, re.S):
        block = m.group(1)
        # Tables under a "### Deprecated models/agents" heading list models that are already shut down
        # (firm dates). Tables in the main sections give the *earliest possible* shutdown date (tentative).
        headings = re.findall(r"^(#{2,3}) (.+)$", text[:m.start()], re.M)
        shut_down = bool(headings) and headings[-1][0] == "###" and "deprecated" in headings[-1][1].lower()
        parts = [p for p in re.split(r"\n\s*---\s*\n", block)]
        cells = [[_clean(c) for c in re.findall(r"^\s*\*\s?(.*)$", p, re.M)] for p in parts]
        if not cells or [c.lower() for c in cells[0]] != EXPECTED_HEADERS["lifecycle_google"]:
            continue
        seen_header = True
        for r in cells[1:]:
            if len(r) < 3 or not r[0]:
                continue
            name, rel, shut = r[0], r[1], r[2]
            repl = r[3] if len(r) > 3 else None
            for n in [x.strip() for x in name.split(",") if x.strip()]:
                if (d := parse_date(rel)):
                    out.append(_ev("google", n, "released", d, url))
                if (d := parse_date(shut)):
                    out.append(_ev("google", n, "retired", d, url, replacement=repl or None,
                                   tentative=not shut_down,
                                   detail="shut down" if shut_down else "earliest possible shutdown date"))
    if not seen_header:
        raise HeaderChanged("google deprecations table header not found")
    return out


def parse_deepseek(text: str, url: str) -> list[dict]:
    out = []
    sections = re.split(r"^## Date:\s*", text, flags=re.M)[1:]
    for sec in sections:
        d = parse_date(sec[:10])
        if not d:
            continue
        for title, body in re.findall(r"^### (.+?)\n(.*?)(?=^### |\Z)", sec, re.M | re.S):
            t = _clean(title)
            if re.search(r"\brelease\b", t, re.I):
                out.append(_ev("deepseek", re.sub(r"\s*release\s*$", "", t, flags=re.I), "released", d, url, detail=t))
            for sentence in re.split(r"(?<=\.)\s", body):
                if re.search(r"\bretired\b", sentence, re.I):
                    for name in re.findall(r"`([^`]+)`", sentence):
                        out.append(_ev("deepseek", name, "retired", d, url, detail=_clean(sentence)[:300]))
    if not sections:
        raise HeaderChanged("deepseek '## Date:' headings not found")
    return out


def parse_xai(text: str, url: str, today: date) -> list[dict]:
    out, year, last_month = [], today.year, None
    blocks = re.split(r"^## ", text, flags=re.M)[1:]
    for blk in blocks:
        month_name = blk.split("\n", 1)[0].strip().lower()
        if month_name not in MONTHS:
            continue
        mi = MONTHS.index(month_name) + 1
        if last_month is not None and mi > last_month:   # page runs newest → oldest
            year -= 1
        last_month = mi
        for title, body in re.findall(r"^### (.+?)\n(.*?)(?=^### |\Z)", blk, re.M | re.S):
            t = _clean(title)
            low = t.lower()
            names = re.findall(r"`([^`]+)`", title) or re.findall(r"`([^`]+)`", body)[:1] or [t]
            d = parse_date(body) or date(year, mi, 1)
            if "end of life" in low or "retire" in low:
                etype = "retired"
            elif "deprecat" in low:
                etype = "deprecated"
            elif re.search(r"\b(is available|release|launch|introduc)", low) or re.match(r"^grok[\w .-]*\d", low):
                etype = "released"
            else:
                continue
            out.append(_ev("xai", names[0], etype, d, url, detail=t))
    if not blocks:
        raise HeaderChanged("xai month headings not found")
    return out


PARSERS = {"lifecycle_anthropic": parse_anthropic, "lifecycle_openai": parse_openai,
           "lifecycle_google": parse_google, "lifecycle_deepseek": parse_deepseek}


def _map_models(cur, events: list[dict]) -> None:
    """Exact match after documented normalisation: vendor prefix + hyphenated version → dotted
    (claude-opus-5-5 → anthropic/claude-opus-5.5). Unmatched names stay NULL for review."""
    cur.execute("SELECT model_id, vendor_id FROM core.dim_model")
    known = {mid: v for mid, v in cur.fetchall()}
    prefix = {"openai": "openai/", "anthropic": "anthropic/", "google": "google/", "deepseek": "deepseek/", "xai": "x-ai/"}
    aliases = []
    for e in events:
        n = e["model_api_name"].lower()
        cands = [prefix[e["vendor_id"]] + n, prefix[e["vendor_id"]] + re.sub(r"(?<=\d)-(?=\d)", ".", n)]
        for c in cands:
            if c in known:
                e["model_id"] = c
                aliases.append({"alias": e["model_api_name"], "alias_source": f"api_name_{e['vendor_id']}",
                                "model_id": c, "match_method": "exact"})
                break
    upsert(cur, "core.map_model_alias", list({a["alias"] + a["alias_source"]: a for a in aliases}.values()),
           ["alias", "alias_source"])


def lifecycle_pages(conn, run_id: str, raw: RawStore, res: JobResult, force: bool = False) -> list[dict]:
    """Returns DQ results for each page (consumed by dq_checks)."""
    cur = conn.cursor()
    today = today_utc()
    dq = []
    for source, (vendor, url) in PAGES.items():
        resp = http_get(url)
        if resp.status_code != 200:
            res.status = "partial"
            res.notes.append(f"{source}: HTTP {resp.status_code}")
            dq.append({"check": f"lifecycle_parse_{vendor}", "result": "fail", "detail": f"HTTP {resp.status_code}"})
            continue
        text = resp.text
        h = sha256(text)
        raw.put(source=source, request_key=source, request_url=url, run_id=run_id, http_status=200, payload=text,
                content_hash=h)
        cur.execute("SELECT content_hash FROM ops.source_page_hash WHERE source = %s AND fetched_date < %s "
                    "AND rows_parsed > 0 ORDER BY fetched_date DESC LIMIT 1", (source, today))
        prev = cur.fetchone()
        changed = prev is None or prev[0] != h
        rows = None
        if changed or force:
            try:
                events = parse_xai(text, url, today) if source == "lifecycle_xai" else PARSERS[source](text, url)
                events = list({(e["vendor_id"], e["model_api_name"], e["event_type"], e["event_date"]): e
                               for e in events}.values())
                if not events:
                    raise HeaderChanged("parsed 0 rows")
                _map_models(cur, events)
                res.rows_loaded += upsert(cur, "core.fact_lifecycle_event", events,
                                          ["vendor_id", "model_api_name", "event_type", "event_date"],
                                          update=["model_id", "announced_date", "replacement", "detail", "is_tentative",
                                                  "source_url"])
                rows = len(events)
                dq.append({"check": f"lifecycle_parse_{vendor}", "result": "pass", "detail": f"{rows} events"})
            except HeaderChanged as exc:
                rows = 0
                res.status = "partial"
                res.notes.append(f"{source}: {exc}")
                dq.append({"check": f"lifecycle_parse_{vendor}", "result": "fail",
                           "detail": f"{exc}; previous data kept"})
        else:
            dq.append({"check": f"lifecycle_parse_{vendor}", "result": "pass", "detail": "page unchanged"})
        upsert(cur, "ops.source_page_hash", [{"source": source, "fetched_date": today, "content_hash": h,
                                              "changed": changed, "rows_parsed": rows if rows is not None else -1}],
               ["source", "fetched_date"])
    return dq
