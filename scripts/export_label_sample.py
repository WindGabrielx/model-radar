"""Draw the hand-labelling sample (CLAUDE.md §8.4): ~300 HN comments, balanced across the five vendors.

    python scripts/export_label_sample.py            # writes data/labels/comments_to_label.csv (once)
    python scripts/export_label_sample.py --force    # redraw (labels already given are kept in the labelled file)

The sample is fixed (seeded) so the evaluation set does not drift. Labels are written by labeler/labeler_app.py
to data/labels/comments_labelled.csv. No model predictions are shown to the labeller (avoids anchoring).
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from jobs.sentiment import clean  # noqa: E402
from tracker.common import ROOT, RawStore, load_env, pg_connect  # noqa: E402
from jobs.sentiment import vendor_terms  # noqa: E402

OUT = ROOT / "data" / "labels" / "comments_to_label.csv"
PER_VENDOR, TOTAL, SEED = 60, 300, 20261123

if __name__ == "__main__":
    load_env()
    if OUT.exists() and "--force" not in sys.argv:
        sys.exit(f"{OUT.relative_to(ROOT)} already exists (use --force to redraw)")
    raw = RawStore()
    if raw.mode != "mongo":
        sys.exit("MongoDB unreachable")
    conn = pg_connect()
    terms = vendor_terms(conn.cursor())
    pools: dict[str, list[dict]] = {v: [] for v in terms}
    for d in raw.db.raw_comments.find({"source": "hn_comments"}, {"payload": 1, "_id": 0}):
        p = d["payload"]
        text = clean(p.get("comment_text"))
        if not 40 <= len(text) <= 1200:                       # long enough to judge, short enough to read
            continue
        hits = [v for v, rx in terms.items() if rx.search(text)]
        if len(hits) == 1:                                    # unambiguous target vendor for the label
            pools[hits[0]].append({"comment_id": p["comment_id"], "story_id": p.get("story_id"),
                                   "story_title": p.get("story_title"), "vendor_id": hits[0], "text": text,
                                   "url": f"https://news.ycombinator.com/item?id={p['comment_id']}"})
    rnd = random.Random(SEED)
    sample = []
    for v, pool in pools.items():
        rnd.shuffle(pool)
        sample += pool[:PER_VENDOR]
        print(f"{v:10} pool {len(pool):5}  sampled {min(len(pool), PER_VENDOR)}")
    # small vendors (few HN comments name them alone) keep all they have; fill to TOTAL from the rest, round-robin
    rest = {v: pool[PER_VENDOR:] for v, pool in pools.items()}
    while len(sample) < TOTAL and any(rest.values()):
        for v in list(rest):
            if rest[v] and len(sample) < TOTAL:
                sample.append(rest[v].pop(0))
    rnd.shuffle(sample)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["comment_id", "story_id", "story_title", "vendor_id", "text", "url"])
        w.writeheader()
        w.writerows(sample)
    print(f"wrote {len(sample)} comments to {OUT.relative_to(ROOT)}")
