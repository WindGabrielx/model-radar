"""Replay data/raw_spool/*.jsonl into MongoDB once Atlas is reachable (raw-first, CLAUDE.md §3).

    python scripts/replay_raw_spool.py            # upsert every spooled document
Files are renamed *.replayed after a successful replay, never deleted.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from tracker.common import ROOT, RawStore, load_env  # noqa: E402

if __name__ == "__main__":
    load_env()
    raw = RawStore()
    if raw.mode != "mongo":
        sys.exit(f"MongoDB still unreachable: {raw.error}")
    for path in sorted((ROOT / "data" / "raw_spool").glob("*.jsonl")):
        n = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                doc = json.loads(line)
                coll = doc.pop("collection")
                doc["fetched_at"] = datetime.fromisoformat(doc["fetched_at"])
                if coll == "raw_comments":
                    key = {"source": doc["source"], "request_key": doc["request_key"]}
                elif coll == "raw_lifecycle_pages":
                    key = {"source": doc["source"], "fetched_date": doc["fetched_date"]}
                else:
                    key = {"source": doc["source"], "request_key": doc["request_key"], "fetched_date": doc["fetched_date"]}
                raw.db[coll].replace_one(key, doc, upsert=True)
                n += 1
        path.rename(path.with_suffix(".jsonl.replayed"))
        print(f"{path.name}: {n} documents")
