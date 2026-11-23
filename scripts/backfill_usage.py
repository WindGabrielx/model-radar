"""One-off backfill of OpenRouter rankings-daily (CLAUDE.md §4.1 D) from 2025-01-01 to the day before the
pipeline's first usage row. Rows are marked backfilled = true (migration 012). Idempotent: re-running upserts.

    python scripts/backfill_usage.py                 # 2025-01-01 → first daily-run day − 1
    python scripts/backfill_usage.py --start 2025-06-01

Respects the Data API limits (30 req/min, 500 req/day): 60-day chunks, 3 s apart (~12 requests in total).
Raw responses land in MongoDB like any other run (source openrouter_rankings, request_key = date range).
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from tracker.common import (RawStore, env, http_get, load_env, new_run_id, openrouter_headers,  # noqa: E402
                            pg_connect, run_logged, upsert)

URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
CHUNK_DAYS = 60

if __name__ == "__main__":
    load_env()
    if not env("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY (or ROUTER_PASSWORD) not set")
    start = date.fromisoformat(sys.argv[sys.argv.index("--start") + 1]) if "--start" in sys.argv else date(2025, 1, 1)
    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT min(usage_date) FROM core.fact_usage_daily WHERE NOT backfilled")
    first_daily = cur.fetchone()[0]
    end = (first_daily - timedelta(days=1)) if first_daily else date.today() - timedelta(days=1)
    cur.execute("SELECT alias, model_id FROM core.map_model_alias WHERE alias_source IN ('permaslug','openrouter_id')")
    amap = dict(cur.fetchall())
    raw, run_id = RawStore(), new_run_id()
    print(f"backfill {start} -> {end} (run {run_id}, raw={raw.mode})")

    with run_logged(conn, run_id, "usage_backfill") as res:
        d = start
        while d <= end:
            e = min(d + timedelta(days=CHUNK_DAYS - 1), end)
            resp = http_get(URL, params={"start_date": d.isoformat(), "end_date": e.isoformat()},
                            headers=openrouter_headers())
            if resp.status_code in (401, 403):
                raise RuntimeError(f"OpenRouter key rejected (HTTP {resp.status_code})")
            resp.raise_for_status()
            payload = resp.json()
            raw.put(source="openrouter_rankings", request_key=f"{d}..{e}", request_url=resp.url, run_id=run_id,
                    http_status=200, payload=payload)
            rows = {}
            for r in payload.get("data") or []:
                slug = r.get("model_permaslug") or "other"
                other = slug == "other"
                rows[(r["date"], slug)] = {"usage_date": r["date"], "model_permaslug": slug,
                                           "model_id": None if other else amap.get(slug),
                                           "total_tokens": int(r.get("total_tokens") or 0),
                                           "is_other_row": other, "backfilled": True}
            n = upsert(cur, "core.fact_usage_daily", list(rows.values()), ["usage_date", "model_permaslug"])
            conn.commit()
            res.rows_loaded += n
            print(f"  {d} -> {e}: {n} rows, {len({k[0] for k in rows})} days")
            d = e + timedelta(days=1)
            time.sleep(3)
        res.notes.append(f"{start} -> {end}")
    print(f"done: {res.rows_loaded} rows ({res.status})")
