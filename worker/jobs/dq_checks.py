"""09 data quality checks (CLAUDE.md §11) → ops.dq_check_result."""
from __future__ import annotations

from tracker.common import env, upsert, utcnow


def save_dq(conn, run_id: str, results: list[dict]) -> None:
    """Persist DQ results produced inside a step (e.g. lifecycle parse checks) under the shared run_id."""
    now = utcnow()
    upsert(conn.cursor(), "ops.dq_check_result",
           [{"run_id": run_id, "check_name": r["check"], "target": r.get("target"), "result": r["result"],
             "detail": r.get("detail"), "checked_at": now} for r in results], ["run_id", "check_name"])
    conn.commit()


def run_dq(conn, run_id: str, extra: list[dict] | None = None, raw_mode: str = "mongo",
           job_status: dict[str, str] | None = None) -> list[dict]:
    cur = conn.cursor()
    out: list[dict] = list(extra or [])
    if job_status is None:   # steps ran as separate n8n nodes: read their outcome from the run log
        cur.execute("SELECT source, status || coalesce('; ' || error, '') FROM ops.pipeline_run_log WHERE run_id = %s",
                    (run_id,))
        job_status = dict(cur.fetchall())
    # include checks already saved by steps of this run (lifecycle parse checks)
    cur.execute("SELECT check_name, result, detail, target FROM ops.dq_check_result WHERE run_id = %s", (run_id,))
    saved = {r[0]: {"check": r[0], "result": r[1], "detail": r[2], "target": r[3]} for r in cur.fetchall()}
    out = list(saved.values()) + [e for e in out if e["check"] not in saved]

    def add(check, result, detail, target=None):
        out.append({"check": check, "result": result, "detail": detail, "target": target})

    # OpenRouter key
    if not env("OPENROUTER_API_KEY"):
        add("openrouter_key", "warn", "OPENROUTER_API_KEY not set: usage, benchmarks API, tasks and speed are skipped")
    elif any("key rejected" in (s or "") for s in job_status.values()):
        add("openrouter_key", "fail", "OpenRouter key rejected (401/403) — renew the key")
    else:
        add("openrouter_key", "pass", "key accepted")

    # /models completeness vs yesterday
    cur.execute("""SELECT snapshot_date, count(*) FROM core.fact_model_daily
                   GROUP BY 1 ORDER BY 1 DESC LIMIT 2""")
    days = cur.fetchall()
    if len(days) == 2:
        (d1, n1), (_, n0) = days
        add("models_count_vs_yesterday", "fail" if n1 < 0.8 * n0 else "pass", f"{n1} today vs {n0} previous",
            "core.fact_model_daily")
    else:
        add("models_count_vs_yesterday", "pass", f"first snapshot ({days[0][1] if days else 0} models)",
            "core.fact_model_daily")

    # price moves > 50 %
    cur.execute("""
        WITH t AS (SELECT * FROM core.fact_model_daily WHERE snapshot_date = (SELECT max(snapshot_date) FROM core.fact_model_daily)),
             p AS (SELECT DISTINCT ON (model_id) * FROM core.fact_model_daily
                   WHERE snapshot_date < (SELECT max(snapshot_date) FROM core.fact_model_daily)
                   ORDER BY model_id, snapshot_date DESC)
        SELECT t.model_id, p.input_price_per_m, t.input_price_per_m, p.output_price_per_m, t.output_price_per_m
        FROM t JOIN p USING (model_id)
        WHERE abs(t.input_price_per_m - p.input_price_per_m) > 0.5 * NULLIF(p.input_price_per_m, 0)
           OR abs(t.output_price_per_m - p.output_price_per_m) > 0.5 * NULLIF(p.output_price_per_m, 0)""")
    big = [f"{m}: in {a}→{b}, out {c}→{d}" for m, a, b, c, d in cur.fetchall()]
    add("price_change_over_50pct", "warn" if big else "pass",
        ("verify before showing: " + "; ".join(big))[:1500] if big else "no price move above 50%")

    # duplicates (PK prevents them; check anyway)
    cur.execute("""SELECT count(*) FROM (SELECT model_id, snapshot_date FROM core.fact_model_daily
                   GROUP BY 1, 2 HAVING count(*) > 1) d""")
    dups = cur.fetchone()[0]
    add("no_duplicate_model_day", "fail" if dups else "pass", f"{dups} duplicates", "core.fact_model_daily")

    # usage rows for the latest day
    cur.execute("SELECT max(usage_date), count(*) FILTER (WHERE usage_date = (SELECT max(usage_date) FROM core.fact_usage_daily)) FROM core.fact_usage_daily")
    ud, un = cur.fetchone()
    add("usage_rows_latest_day", "warn" if (un or 0) < 40 else "pass", f"{un or 0} rows for {ud}", "core.fact_usage_daily")

    # benchmark rows with no dim_model mapping
    cur.execute("""SELECT count(*), string_agg(model_permaslug, ', ' ORDER BY model_permaslug)
                   FROM core.fact_benchmark_snapshot
                   WHERE model_id IS NULL AND snapshot_date = (SELECT max(snapshot_date) FROM core.fact_benchmark_snapshot)
                     AND EXISTS (SELECT 1 FROM core.dim_vendor v, unnest(v.openrouter_prefixes) p
                                 WHERE model_permaslug LIKE p || '%')   -- only the five tracked vendors
                  """)
    nb, names = cur.fetchone()
    add("benchmark_unmapped", "warn" if nb else "pass", f"{nb} unmapped" + (f": {names[:1200]}" if nb else ""),
        "core.fact_benchmark_snapshot")

    # model availability (mart.v_model_status): vendor shut a model down but OpenRouter still lists it → hidden in
    # the dashboard; also surface shutdowns in the next 14 days so they can be checked before they happen
    cur.execute("""SELECT s.model_id, s.status, s.retire_date FROM mart.v_model_status s
                   JOIN core.dim_model m USING (model_id)
                   WHERE m.is_active AND (s.status = 'retired' OR (s.status = 'retiring' AND s.days_left <= 14))
                   ORDER BY s.retire_date""")
    st_rows = cur.fetchall()
    gone = [f"{m} ({d})" for m, s, d in st_rows if s == "retired"]
    soon = [f"{m} ({d})" for m, s, d in st_rows if s == "retiring"]
    detail = []
    if gone:
        detail.append(f"{len(gone)} shut down by the vendor but still listed on OpenRouter (hidden): {', '.join(gone)}")
    if soon:
        detail.append(f"{len(soon)} shut down within 14 days: {', '.join(soon)}")
    add("model_status", "warn" if st_rows else "pass", "; ".join(detail) or "no shut-down model still listed",
        "mart.v_model_status")

    # every step of this run finished (a failed source never blocks others, but must be visible)
    failed = sorted(s for s, v in job_status.items() if (v or "").startswith("failed") and s != "dq_checks")
    add("steps_failed", "fail" if failed else "pass", ", ".join(failed) if failed else "all steps finished")

    # raw landing zone
    add("raw_landing_zone", "pass" if raw_mode == "mongo" else "warn",
        "MongoDB" if raw_mode == "mongo" else "MongoDB unreachable — raw spooled to data/raw_spool/; replay later")

    # sentiment model changed without eval report
    # §11: the sentiment model in use must have an evaluation report (scripts/eval_sentiment.py → ops.model_eval)
    cur.execute("""SELECT s.model_version, (SELECT max(f1) FROM ops.model_eval e
                                             WHERE e.model_version = s.model_version
                                               AND e.task = 'polarity' AND e.label = 'macro')
                   FROM (SELECT model_version FROM core.fact_comment_sentiment
                         ORDER BY loaded_at DESC LIMIT 1) s""")
    row = cur.fetchone()
    if not row:
        add("sentiment_model_eval", "pass", "no sentiment predictions yet")
    elif row[1] is None:
        add("sentiment_model_eval", "warn", f"{row[0]} has no evaluation yet: label comments in labeler/ "
                                            "and run scripts/eval_sentiment.py")
    else:
        add("sentiment_model_eval", "pass", f"{row[0]} polarity macro-F1 {float(row[1]):.2f}")

    now = utcnow()
    upsert(cur, "ops.dq_check_result",
           [{"run_id": run_id, "check_name": r["check"], "target": r.get("target"), "result": r["result"],
             "detail": r.get("detail"), "checked_at": now} for r in out], ["run_id", "check_name"])
    conn.commit()
    return out


# ------------------------------------------------------------------ data inventory (Pipeline health page)
# (table, date column, source label, optional source column whose values split the table by source)
SUPABASE_TABLES = [
    ("core.dim_vendor", None, "Seed (dim_vendor.csv)", None),
    ("core.dim_model", "last_seen_date", "OpenRouter /models", None),
    ("core.map_model_alias", None, "Derived (alias matching)", None),
    ("core.fact_model_daily", "snapshot_date", "OpenRouter /models", None),
    ("core.fact_endpoint_daily", "snapshot_date", "OpenRouter /endpoints", None),
    ("core.fact_usage_daily", "usage_date", "OpenRouter rankings-daily", None),
    ("core.fact_benchmark_snapshot", "snapshot_date", None, "source"),
    ("core.fact_task_share", "as_of", "OpenRouter classifications/task", None),
    ("core.fact_lifecycle_event", "event_date", None, "source_type"),
    ("core.fact_news_item", "published_at", None, "source"),
    ("core.bridge_news_vendor", None, "Derived (vendor matching §8.1)", None),
    ("core.fact_comment_sentiment", "published_at", "Derived (sentiment model)", None),
    ("ops.pipeline_run_log", "started_at", "System (run log)", None),
    ("ops.dq_check_result", "checked_at", "System (DQ checks)", None),
    ("ops.source_page_hash", "fetched_date", "Vendor deprecation pages", None),
]
SOURCE_LABEL = {
    "openrouter_benchmarks": "OpenRouter /benchmarks (Artificial Analysis)",
    "openrouter_models_aa": "OpenRouter /models (Artificial Analysis)",
    "official": "Vendor deprecation pages", "derived": "Derived (OpenRouter changes)",
    "hn": "Hacker News", "rss_openai": "OpenAI news RSS", "rss_deepmind": "Google DeepMind RSS", "gdelt": "GDELT",
    "openrouter_models": "OpenRouter /models", "openrouter_endpoints": "OpenRouter /endpoints",
    "openrouter_rankings": "OpenRouter rankings-daily", "openrouter_tasks": "OpenRouter classifications/task",
    "hn_story_search": "Hacker News", "hn_comments": "Hacker News",
    "lifecycle_anthropic": "Vendor deprecation pages", "lifecycle_openai": "Vendor deprecation pages",
    "lifecycle_google": "Vendor deprecation pages", "lifecycle_deepseek": "Vendor deprecation pages",
    "lifecycle_xai": "Vendor deprecation pages",
}


def capture_inventory(conn, run_id: str, raw=None) -> int:
    """Row counts, sizes and date ranges per store / object / source → ops.data_inventory."""
    cur = conn.cursor()
    rows, now = [], utcnow()
    for table, dcol, label, split in SUPABASE_TABLES:
        cur.execute("SELECT pg_total_relation_size(%s::regclass)", (table,))
        size = cur.fetchone()[0]
        dsel = f"min({dcol})::date, max({dcol})::date" if dcol else "NULL::date, NULL::date"
        if split:
            cur.execute(f"SELECT {split}, count(*), {dsel} FROM {table} GROUP BY 1")
            parts = cur.fetchall()
            total = sum(p[1] for p in parts) or 1
            for key, n, dmin, dmax in parts:
                rows.append({"store": "supabase", "object": table, "source": SOURCE_LABEL.get(key, key),
                             "row_count": n, "size_bytes": int(size * n / total), "min_date": dmin, "max_date": dmax})
        else:
            cur.execute(f"SELECT count(*), {dsel} FROM {table}")
            n, dmin, dmax = cur.fetchone()
            rows.append({"store": "supabase", "object": table, "source": label, "row_count": n,
                         "size_bytes": size, "min_date": dmin, "max_date": dmax})
    if raw is not None and getattr(raw, "mode", None) == "mongo":
        for coll in sorted(raw.db.list_collection_names()):
            for g in raw.db[coll].aggregate([{"$group": {
                    "_id": "$source", "n": {"$sum": 1}, "bytes": {"$sum": {"$bsonSize": "$$ROOT"}},
                    "mn": {"$min": "$fetched_date"}, "mx": {"$max": "$fetched_date"}}}]):
                rows.append({"store": "mongodb", "object": coll,
                             "source": SOURCE_LABEL.get(g["_id"], g["_id"] or "unknown"),
                             "row_count": g["n"], "size_bytes": g["bytes"], "min_date": g["mn"], "max_date": g["mx"]})
    # several raw sources map to one label (e.g. 5 lifecycle pages) → merge them under the PK
    merged: dict[tuple, dict] = {}
    for r in rows:
        k = (r["store"], r["object"], r["source"])
        if k in merged:
            m = merged[k]
            m["row_count"] += r["row_count"]
            m["size_bytes"] = (m["size_bytes"] or 0) + (r["size_bytes"] or 0)
            m["min_date"] = min(filter(None, [m["min_date"], r["min_date"]]), default=None)
            m["max_date"] = max(filter(None, [m["max_date"], r["max_date"]]), default=None)
        else:
            merged[k] = dict(r)
    upsert(cur, "ops.data_inventory", [{"run_id": run_id, "captured_at": now, **r} for r in merged.values()],
           ["run_id", "store", "object", "source"])
    conn.commit()
    return len(merged)
