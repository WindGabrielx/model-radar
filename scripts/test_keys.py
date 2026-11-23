"""Validate every data source and connection (CLAUDE.md §4, §15). Read-only; prints one line per check.

    python scripts/test_keys.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from jobs.lifecycle import PAGES  # noqa: E402
from tracker.common import RawStore, env, http_get, load_env, openrouter_headers, pg_connect  # noqa: E402

OR = "https://openrouter.ai/api/v1"


def check(name: str, fn) -> bool:
    try:
        detail = fn()
        print(f"OK    {name:28} {detail or ''}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  {name:28} {type(exc).__name__}: {str(exc)[:140]}")
        return False


def status(url: str, params: dict | None = None, headers: dict | None = None, expect_json: bool = True) -> str:
    r = http_get(url, params=params, headers=headers, retries=1)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    if expect_json:
        r.json()
    return f"HTTP 200, {len(r.content):,} bytes"


if __name__ == "__main__":
    load_env()
    ok = []
    ok.append(check("supabase writer", lambda: pg_connect("WRITER").close()))
    ok.append(check("supabase reader", lambda: pg_connect("READER").close()))
    ok.append(check("mongodb", lambda: (lambda r: r.mode if r.mode == "mongo" else (_ for _ in ()).throw(
        RuntimeError(r.error)))(RawStore())))
    ok.append(check("openrouter /models", lambda: status(f"{OR}/models")))
    if env("OPENROUTER_API_KEY"):
        h = openrouter_headers()
        ok.append(check("openrouter /key", lambda: status(f"{OR}/key", headers=h)))
        ok.append(check("openrouter rankings-daily", lambda: status(f"{OR}/datasets/rankings-daily", headers=h)))
        ok.append(check("openrouter /benchmarks", lambda: status(f"{OR}/benchmarks", headers=h)))
        ok.append(check("openrouter tasks", lambda: status(f"{OR}/classifications/task", {"window": "7d"}, h)))
    else:
        print("SKIP  openrouter data API          OPENROUTER_API_KEY not set")
    ok.append(check("hn algolia", lambda: status("https://hn.algolia.com/api/v1/search", {"query": "Claude", "tags": "story"})))
    ok.append(check("rss openai", lambda: status("https://openai.com/news/rss.xml", expect_json=False)))
    ok.append(check("rss deepmind", lambda: status("https://deepmind.google/blog/rss.xml", expect_json=False)))
    for source, (_, url) in PAGES.items():
        ok.append(check(source, lambda u=url: status(u, expect_json=False)))
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)
