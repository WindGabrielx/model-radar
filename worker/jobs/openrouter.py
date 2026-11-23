"""OpenRouter jobs (CLAUDE.md §4.1, workflows 01–05).

01 or_models     /models → dim_model, fact_model_daily, embedded AA benchmarks, lifecycle-derived events
02 or_endpoints  /models/{id}/endpoints → fact_endpoint_daily
03 or_usage      /datasets/rankings-daily (last 3 days)      — needs OPENROUTER_API_KEY
04 or_benchmarks /benchmarks                                  — needs OPENROUTER_API_KEY
05 or_tasks      /classifications/task?window=7d              — needs OPENROUTER_API_KEY
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from psycopg2.extras import Json

from tracker.common import JobResult, RawStore, env, http_get, openrouter_headers, sha256, today_utc, upsert
from tracker.vendors import vendor_for_model_id

BASE = "https://openrouter.ai/api/v1"
AA_MAIN = ("intelligence_index", "coding_index", "agentic_index")


def per_m(v) -> Decimal | None:
    """OpenRouter price (USD per token, decimal string) → USD per 1M tokens. Negative = dynamic/unknown → None."""
    if v in (None, ""):
        return None
    try:
        d = Decimal(str(v)) * 1_000_000
    except InvalidOperation:
        return None
    return None if d < 0 else d.quantize(Decimal("0.000001"))


def _date_or_none(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date() if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------ 01 models
def or_models(conn, run_id: str, raw: RawStore, res: JobResult) -> None:
    url = f"{BASE}/models"
    resp = http_get(url, headers=openrouter_headers())
    if resp.status_code in (401, 403):
        raise RuntimeError(f"OpenRouter key rejected (HTTP {resp.status_code})")
    resp.raise_for_status()
    payload = resp.json()
    raw.put(source="openrouter_models", request_key="all", request_url=url, run_id=run_id,
            http_status=resp.status_code, payload=payload, content_hash=sha256(resp.content))
    today = today_utc()

    base: dict[str, dict] = {}
    batch: dict[str, dict] = {}
    for m in payload["data"]:
        mid = m["id"]
        vendor = vendor_for_model_id(mid)
        if not vendor:
            continue
        if mid.endswith(":batch"):
            batch[mid.rsplit(":", 1)[0]] = m
        else:
            base[mid] = (m, vendor)

    cur = conn.cursor()
    cur.execute("SELECT model_id FROM core.dim_model")
    known = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT count(*) FROM core.dim_model WHERE is_active")
    active_before = cur.fetchone()[0]

    dims, facts, aliases, bench, events = [], [], [], [], []
    for mid, (m, vendor) in base.items():
        arch = m.get("architecture") or {}
        outs = arch.get("output_modalities") or ["text"]
        created = datetime.fromtimestamp(m["created"], tz=timezone.utc) if m.get("created") else None
        dims.append({
            "model_id": mid, "vendor_id": vendor, "canonical_slug": m.get("canonical_slug"), "name": m.get("name"),
            "output_type": "image" if "image" in outs else "text", "created_at_source": created,
            "first_seen_date": today, "last_seen_date": today, "is_active": True, "updated_at": datetime.now(timezone.utc),
        })
        pr = m.get("pricing") or {}
        bpr = (batch.get(mid) or {}).get("pricing") or {}
        top = m.get("top_provider") or {}
        facts.append({
            "model_id": mid, "snapshot_date": today,
            "context_length": m.get("context_length"), "max_completion_tokens": top.get("max_completion_tokens"),
            "input_price_per_m": per_m(pr.get("prompt")), "output_price_per_m": per_m(pr.get("completion")),
            "cache_read_price_per_m": per_m(pr.get("input_cache_read")),
            "batch_input_price_per_m": per_m(bpr.get("prompt")), "batch_output_price_per_m": per_m(bpr.get("completion")),
            "input_modalities": arch.get("input_modalities"), "supported_parameters": {"params": m.get("supported_parameters") or []},
            "expiration_date": _date_or_none(m.get("expiration_date")),
        })
        if m.get("canonical_slug"):
            aliases.append({"alias": m["canonical_slug"], "alias_source": "permaslug", "model_id": mid, "match_method": "exact"})
        aliases.append({"alias": mid, "alias_source": "openrouter_id", "model_id": mid, "match_method": "exact"})
        aa = ((m.get("benchmarks") or {}).get("artificial_analysis")) or {}
        if any(aa.get(k) is not None for k in aa):
            bench.append({
                "snapshot_date": today, "source": "openrouter_models_aa",
                "model_permaslug": m.get("canonical_slug") or mid, "model_id": mid,
                **{k: aa.get(k) for k in AA_MAIN},
                "extra": {k: v for k, v in aa.items() if k not in AA_MAIN and v is not None},
                "citation": "Artificial Analysis, via OpenRouter /models",
            })
        if mid not in known and created:
            events.append({"vendor_id": vendor, "model_api_name": mid, "event_type": "first_seen_openrouter",
                           "event_date": created.date(), "model_id": mid, "announced_date": None, "replacement": None,
                           "detail": None, "is_tentative": False, "source_url": "https://openrouter.ai/models",
                           "source_type": "derived"})

    upsert(cur, "core.dim_model", dims, ["model_id"],
           update=["vendor_id", "canonical_slug", "name", "output_type", "created_at_source", "last_seen_date",
                   "is_active", "updated_at"])
    res.rows_loaded += upsert(cur, "core.fact_model_daily", facts, ["model_id", "snapshot_date"])
    upsert(cur, "core.map_model_alias", aliases, ["alias", "alias_source"])
    upsert(cur, "core.fact_benchmark_snapshot", bench, ["snapshot_date", "source", "model_permaslug"])

    # price_change events are detected in or_endpoints on the vendor's own endpoint (reference price),
    # not here: the /models headline price moves with OpenRouter's routing and would create false changes.

    # removed_from_openrouter — only when today's list is complete enough (§11: < 80% → do not mark removed)
    if active_before and len(base) < 0.8 * active_before:
        res.status = "partial"
        res.notes.append(f"/models returned {len(base)} vendor models vs {active_before} active; removals skipped")
    else:
        cur.execute("""SELECT model_id, vendor_id FROM core.dim_model
                       WHERE is_active AND NOT (model_id = ANY(%s))""", (list(base),))
        gone = cur.fetchall()
        for mid, vendor in gone:
            events.append({"vendor_id": vendor, "model_api_name": mid, "event_type": "removed_from_openrouter",
                           "event_date": today, "model_id": mid, "announced_date": None, "replacement": None,
                           "detail": None, "is_tentative": False, "source_url": "https://openrouter.ai/models",
                           "source_type": "derived"})
        if gone:
            cur.execute("UPDATE core.dim_model SET is_active = false WHERE model_id = ANY(%s)", ([g[0] for g in gone],))
    upsert(cur, "core.fact_lifecycle_event", events, ["vendor_id", "model_api_name", "event_type", "event_date"])
    res.notes.append(f"{len(base)} models, {len(batch)} batch variants, {len(bench)} AA scores, {len(events)} events")


# ------------------------------------------------------------------ 02 endpoints
def or_endpoints(conn, run_id: str, raw: RawStore, res: JobResult) -> None:
    cur = conn.cursor()
    cur.execute("SELECT model_id FROM core.dim_model WHERE is_active AND output_type = 'text' ORDER BY model_id")
    models = [r[0] for r in cur.fetchall()]
    today, failures = today_utc(), []
    for mid in models:
        url = f"{BASE}/models/{mid}/endpoints"
        resp = http_get(url, headers=openrouter_headers())
        if resp.status_code != 200:
            failures.append(f"{mid}:{resp.status_code}")
            continue
        payload = resp.json()
        raw.put(source="openrouter_endpoints", request_key=mid, request_url=url, run_id=run_id,
                http_status=200, payload=payload)
        rows = {}
        for ep in (payload.get("data") or {}).get("endpoints") or []:
            pr, lat, thr = ep.get("pricing") or {}, ep.get("latency_last_30m") or {}, ep.get("throughput_last_30m") or {}
            prov = ep.get("provider_name") or ep.get("name")
            if ep.get("tag"):
                prov = f"{prov} [{ep['tag']}]"
            rows[prov] = {
                "model_id": mid, "provider_name": prov, "snapshot_date": today,
                "input_price_per_m": per_m(pr.get("prompt")), "output_price_per_m": per_m(pr.get("completion")),
                "uptime_last_1d": ep.get("uptime_last_1d"),
                **{f"latency_{p}": lat.get(p) for p in ("p50", "p75", "p90", "p99")},
                **{f"throughput_{p}": thr.get(p) for p in ("p50", "p75", "p90", "p99")},
            }
        res.rows_loaded += upsert(cur, "core.fact_endpoint_daily", list(rows.values()),
                                  ["model_id", "provider_name", "snapshot_date"])
        time.sleep(0.25)
    if failures:
        res.status = "partial"
        res.notes.append(f"{len(failures)} failed: {', '.join(failures[:8])}")
    if not env("OPENROUTER_API_KEY"):
        res.notes.append("no OPENROUTER_API_KEY: latency/throughput are null")
    _price_change_events(conn, today, res)


def _price_change_events(conn, today, res: JobResult) -> None:
    """price_change (§7) on the reference price: the vendor's own standard endpoint, today vs its previous
    snapshot. If days were missed, the event says the change happened somewhere in the gap (tentative)."""
    cur = conn.cursor()
    cur.execute("""
        WITH fp AS (
          SELECT e.model_id, e.provider_name, e.snapshot_date, e.input_price_per_m, e.output_price_per_m, m.vendor_id
          FROM core.fact_endpoint_daily e JOIN core.dim_model m USING (model_id) JOIN core.dim_vendor v USING (vendor_id)
          WHERE e.provider_name ILIKE v.name || '%%' AND e.provider_name !~ '\\[[^]]*/'),
        t AS (SELECT * FROM fp WHERE snapshot_date = %(d)s),
        p AS (SELECT DISTINCT ON (model_id, provider_name) * FROM fp WHERE snapshot_date < %(d)s
              ORDER BY model_id, provider_name, snapshot_date DESC)
        SELECT t.model_id, t.vendor_id, t.provider_name, p.input_price_per_m, t.input_price_per_m,
               p.output_price_per_m, t.output_price_per_m, p.snapshot_date
        FROM t JOIN p USING (model_id, provider_name)
        WHERE t.input_price_per_m IS DISTINCT FROM p.input_price_per_m
           OR t.output_price_per_m IS DISTINCT FROM p.output_price_per_m""", {"d": today})
    events = []
    for mid, vendor, prov, pi, ti, po, to, prev_date in cur.fetchall():
        gap = (today - prev_date).days
        when = "" if gap <= 1 else f" (changed between {prev_date} and {today}; {gap - 1} day(s) not captured)"
        events.append({"vendor_id": vendor, "model_api_name": mid, "event_type": "price_change", "event_date": today,
                       "model_id": mid, "announced_date": None, "replacement": None,
                       "detail": f"{prov}: input {pi}→{ti}, output {po}→{to} USD/1M{when}", "is_tentative": gap > 1,
                       "source_url": f"https://openrouter.ai/{mid}/providers", "source_type": "derived"})
    if events:
        upsert(cur, "core.fact_lifecycle_event", events, ["vendor_id", "model_api_name", "event_type", "event_date"])
        res.notes.append(f"{len(events)} price changes")


# ------------------------------------------------------------------ 03–05 data API (key required)
def _require_key(res: JobResult) -> bool:
    if env("OPENROUTER_API_KEY"):
        return True
    res.status = "skipped"
    res.notes.append("OPENROUTER_API_KEY not set")
    return False


def _get_data_api(url: str, params: dict | None = None):
    resp = http_get(url, params=params, headers=openrouter_headers())
    if resp.status_code in (401, 403):
        raise RuntimeError(f"OpenRouter key rejected (HTTP {resp.status_code})")
    resp.raise_for_status()
    return resp


def or_usage(conn, run_id: str, raw: RawStore, res: JobResult, days: int = 3) -> None:
    if not _require_key(res):
        return
    end = today_utc() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    url = f"{BASE}/datasets/rankings-daily"
    resp = _get_data_api(url, {"start_date": start.isoformat(), "end_date": end.isoformat()})
    payload = resp.json()
    raw.put(source="openrouter_rankings", request_key=f"{start}..{end}", request_url=resp.url, run_id=run_id,
            http_status=200, payload=payload)
    cur = conn.cursor()
    cur.execute("SELECT alias, model_id FROM core.map_model_alias WHERE alias_source IN ('permaslug','openrouter_id')")
    amap = dict(cur.fetchall())
    rows = {}
    for r in payload.get("data") or []:
        slug = r.get("model_permaslug") or r.get("permaslug") or r.get("model") or "other"
        d = r.get("date") or r.get("usage_date")
        is_other = slug == "other" or bool(r.get("is_other"))
        rows[(d, slug)] = {"usage_date": d, "model_permaslug": slug, "model_id": None if is_other else amap.get(slug),
                           "total_tokens": int(r.get("total_tokens") or 0), "is_other_row": is_other,
                           "backfilled": False}
    res.rows_loaded += upsert(cur, "core.fact_usage_daily", list(rows.values()), ["usage_date", "model_permaslug"])


def or_benchmarks(conn, run_id: str, raw: RawStore, res: JobResult) -> None:
    if not _require_key(res):
        return
    url = f"{BASE}/benchmarks"
    resp = _get_data_api(url)
    payload = resp.json()
    raw.put(source="openrouter_benchmarks", request_key="all", request_url=url, run_id=run_id, http_status=200,
            payload=payload)
    citation = (payload.get("meta") or {}).get("citation") or "Artificial Analysis, via OpenRouter /benchmarks"
    cur = conn.cursor()
    cur.execute("SELECT alias, model_id FROM core.map_model_alias WHERE alias_source IN ('permaslug','openrouter_id')")
    amap = dict(cur.fetchall())
    rows = {}
    # The feed mixes sources (artificial-analysis, design-arena, openrouter). Quality (§7) = Artificial Analysis only.
    for r in payload.get("data") or []:
        slug = r.get("model_permaslug")
        if not slug or r.get("source") != "artificial-analysis":
            continue
        rows[slug] = {"snapshot_date": today_utc(), "source": "openrouter_benchmarks", "model_permaslug": slug,
                      "model_id": amap.get(slug), **{k: r.get(k) for k in AA_MAIN},
                      "extra": {k: v for k, v in r.items() if k.endswith("_index") and k not in AA_MAIN and v is not None}
                               | {"display_name": r.get("display_name")},
                      "citation": citation}
    res.rows_loaded += upsert(cur, "core.fact_benchmark_snapshot", list(rows.values()),
                              ["snapshot_date", "source", "model_permaslug"])


def or_tasks(conn, run_id: str, raw: RawStore, res: JobResult) -> None:
    if not _require_key(res):
        return
    url = f"{BASE}/classifications/task"
    resp = _get_data_api(url, {"window": "7d"})
    payload = resp.json()
    raw.put(source="openrouter_tasks", request_key="7d", request_url=resp.url, run_id=run_id, http_status=200,
            payload=payload)
    body = payload.get("data") or {}
    as_of = _date_or_none(body.get("as_of")) or today_utc()
    rows = [{"as_of": as_of, "task_tag": t.get("tag"), "name": t.get("display_name") or t.get("tag"),
             "macro_category": t.get("macro_category"), "usage_share": t.get("usage_share"),
             "token_share": t.get("token_share"), "top_models": Json(t.get("models") or [])}
            for t in body.get("classifications") or [] if t.get("tag")]
    res.rows_loaded += upsert(conn.cursor(), "core.fact_task_share", rows, ["as_of", "task_tag"])
