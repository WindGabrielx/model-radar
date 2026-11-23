"""Daily pipeline in Python — the same steps as n8n `00_master` (CLAUDE.md §10).

    python worker/run_daily.py                 # full daily run
    python worker/run_daily.py --only or_models,hn
    python worker/run_daily.py --backfill-news-days 30   # first run: wider HN/GDELT window

Each source is independent: a failure marks the run partial and never blocks the others.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs import lifecycle, news, openrouter, sentiment  # noqa: E402
from jobs.dq_checks import capture_inventory, run_dq, save_dq  # noqa: E402
from tracker.common import RawStore, load_env, new_run_id, pg_connect, run_logged  # noqa: E402

STEPS = ["or_models", "or_endpoints", "or_usage", "or_benchmarks", "or_tasks", "hn", "sentiment", "rss", "gdelt",
         "lifecycle"]


def catch_up_hours(conn, source: str, cap_days: int = 30) -> int:
    """Missed days are recovered: the window reaches back to the last successful load of this source
    (+1 h overlap; loads are idempotent), at least 24 h, at most cap_days."""
    with conn.cursor() as cur:
        cur.execute("""SELECT EXTRACT(EPOCH FROM now() - max(started_at)) / 3600 FROM ops.pipeline_run_log
                       WHERE source = %s AND status IN ('success', 'partial') AND rows_loaded > 0""", (source,))
        h = cur.fetchone()[0]
    return int(min(max(24, (h or 24) + 1), cap_days * 24))


def usage_catch_up_days(conn) -> int:
    """Rankings: refetch from the last loaded day (min 3 days, max 30) so gaps self-heal."""
    with conn.cursor() as cur:
        cur.execute("SELECT (now() AT TIME ZONE 'utc')::date - max(usage_date) FROM core.fact_usage_daily")
        gap = cur.fetchone()[0]
    return int(min(max(3, (gap or 3) + 1), 30))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated steps: " + ",".join(STEPS) + ",dq_checks")
    ap.add_argument("--run-id", help="join an existing run (n8n passes one run_id to every step)")
    ap.add_argument("--no-dq", action="store_true", help="skip the DQ step (n8n runs it as its own node)")
    ap.add_argument("--backfill-news-days", type=int, default=0,
                    help="override the news window; default = catch up since the last successful load (min 1 day)")
    ap.add_argument("--rescore", action="store_true", help="re-score all comments (after a sentiment model change)")
    ap.add_argument("--force-lifecycle", action="store_true", help="re-parse pages even if the hash is unchanged")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_env()
    conn = pg_connect("WRITER")
    raw = RawStore()
    run_id = args.run_id or new_run_id()
    steps = args.only.split(",") if args.only else STEPS + ([] if args.no_dq else ["dq_checks"])
    days = args.backfill_news_days
    logging.info("run %s  raw=%s  steps=%s", run_id, raw.mode, ",".join(steps))

    status: dict[str, str] = {}
    result: dict = {"run_id": run_id, "raw_mode": raw.mode, "steps": {}}
    for step in steps:
        if step == "dq_checks":
            continue
        with run_logged(conn, run_id, step) as res:
            if step == "or_models":
                openrouter.or_models(conn, run_id, raw, res)
            elif step == "or_endpoints":
                openrouter.or_endpoints(conn, run_id, raw, res)
            elif step == "or_usage":
                openrouter.or_usage(conn, run_id, raw, res, days=usage_catch_up_days(conn))
            elif step == "or_benchmarks":
                openrouter.or_benchmarks(conn, run_id, raw, res)
            elif step == "or_tasks":
                openrouter.or_tasks(conn, run_id, raw, res)
            elif step == "hn":
                hours = 24 * days if days else catch_up_hours(conn, "hn")
                res.notes.append(f"window {hours} h")
                news.hn(conn, run_id, raw, res, hours=hours)
            elif step == "sentiment":
                sentiment.sentiment(conn, run_id, raw, res, rescore=args.rescore)
            elif step == "rss":
                news.rss(conn, run_id, raw, res)
            elif step == "gdelt":
                gd = days or -(-catch_up_hours(conn, "gdelt") // 24)
                res.notes.append(f"window {gd} d")
                news.gdelt(conn, run_id, raw, res, timespan=f"{min(gd, 90)}d")
            elif step == "lifecycle":
                save_dq(conn, run_id, lifecycle.lifecycle_pages(conn, run_id, raw, res, force=args.force_lifecycle))
            else:
                raise ValueError(f"unknown step {step}")
        status[step] = res.status + ("; " + "; ".join(res.notes) if res.notes else "")
        result["steps"][step] = {"status": res.status, "rows": res.rows_loaded, "notes": res.notes}

    if "dq_checks" in steps:
        dq = run_dq(conn, run_id, raw_mode=raw.mode)
        try:
            result["inventory_rows"] = capture_inventory(conn, run_id, raw)
        except Exception as exc:  # noqa: BLE001 — inventory is informational; never fail the run on it
            conn.rollback()
            logging.warning("inventory capture failed: %s", exc)
        with run_logged(conn, run_id, "dq_checks") as res:
            res.rows_loaded = len(dq)
            fails = [d["check"] for d in dq if d["result"] == "fail"]
            warns = [d["check"] for d in dq if d["result"] == "warn"]
            if fails:
                res.status = "partial"
                res.notes.append("fail: " + ", ".join(fails))
        result["dq"] = {"fails": fails, "warns": warns, "checks": len(dq)}
    conn.close()
    bad = [s for s, v in status.items() if v.startswith("failed")]
    logging.info("run %s done. failed steps: %s", run_id, bad or "none")
    print("RESULT " + json.dumps(result, default=str), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
