"""Evaluate the sentiment model against the hand-labelled set (CLAUDE.md §8.4).

    python scripts/eval_sentiment.py              # needs data/labels/comments_labelled.csv (from labeler/)
    python scripts/eval_sentiment.py --min 50     # allow a partial set (default needs 100 labelled comments)

Runs the current model on the labelled comments, reports precision / recall / F1 per class (polarity, intent)
and per aspect (detection), writes reports/sentiment_eval.md and ops.model_eval. Re-run after any model change.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from jobs.sentiment import ASPECTS, MODEL_VERSION, Scorer, analyse  # noqa: E402
from tracker.common import ROOT, load_env, pg_connect, upsert  # noqa: E402

LABELS = ROOT / "data" / "labels" / "comments_labelled.csv"
SAMPLE = ROOT / "data" / "labels" / "comments_to_label.csv"
REPORT = ROOT / "reports" / "sentiment_eval.md"


def prf(gold: list[str], pred: list[str], classes: list[str]) -> list[dict]:
    rows = []
    for c in classes:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        rows.append({"label": c, "precision": pr, "recall": rc, "f1": f1, "support": sum(g == c for g in gold)})
    present = [r for r in rows if r["support"]]
    rows.append({"label": "macro", "precision": sum(r["precision"] for r in present) / max(len(present), 1),
                 "recall": sum(r["recall"] for r in present) / max(len(present), 1),
                 "f1": sum(r["f1"] for r in present) / max(len(present), 1), "support": len(gold)})
    rows.append({"label": "accuracy", "precision": None, "recall": None,
                 "f1": sum(g == p for g, p in zip(gold, pred)) / max(len(gold), 1), "support": len(gold)})
    return rows


def table(rows: list[dict]) -> str:
    out = ["| class | precision | recall | F1 | support |", "|---|---|---|---|---|"]
    for r in rows:
        f = lambda v: "–" if v is None else f"{v:.2f}"  # noqa: E731
        out.append(f"| {r['label']} | {f(r['precision'])} | {f(r['recall'])} | {f(r['f1'])} | {r['support']} |")
    return "\n".join(out)


if __name__ == "__main__":
    load_env()
    need = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 100
    if not LABELS.exists():
        sys.exit("No labels yet: run the labeler app first (cd labeler; streamlit run labeler_app.py --server.port 8502)")
    texts = {r["comment_id"]: r["text"] for r in csv.DictReader(open(SAMPLE, encoding="utf-8"))}
    labelled = [r for r in csv.DictReader(open(LABELS, encoding="utf-8"))
                if r["relevant"] == "1" and r["polarity"] and r["comment_id"] in texts]
    if len(labelled) < need:
        sys.exit(f"Only {len(labelled)} relevant labelled comments (need {need}; pass --min to override)")

    preds = analyse([texts[r["comment_id"]] for r in labelled], Scorer())
    pol = prf([r["polarity"] for r in labelled], [p["overall"][0] for p in preds],
              ["positive", "negative", "neutral", "mixed"])
    inten = prf([r["intent"] for r in labelled], [p["intent"] for p in preds],
                ["comparison", "recommendation", "complaint", "question", "informational"])
    asp = []
    for a in ASPECTS:
        g = ["yes" if a in r["aspects"].split("|") else "no" for r in labelled]
        p = ["yes" if a in pr["aspects"] else "no" for pr in preds]
        row = prf(g, p, ["yes"])[0]
        row["label"] = a
        asp.append(row)

    now = datetime.now(timezone.utc)
    conn = pg_connect()
    rows = []
    for task, res in (("polarity", pol), ("intent", inten), ("aspect", asp)):
        for r in res:
            rows.append({"model_version": MODEL_VERSION, "evaluated_at": now, "task": task, "label": r["label"],
                         "precision": r["precision"], "recall": r["recall"], "f1": r["f1"],
                         "support": r["support"], "n_labelled": len(labelled)})
    upsert(conn.cursor(), "ops.model_eval", rows, ["model_version", "task", "label"])
    conn.commit()

    confusion = Counter((r["polarity"], p["overall"][0]) for r, p in zip(labelled, preds))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        f"# Sentiment evaluation\n\n"
        f"- Model version: `{MODEL_VERSION}`\n- Evaluated: {now:%Y-%m-%d %H:%M} UTC\n"
        f"- Labelled comments used: {len(labelled)} (relevant to their vendor; from `data/labels/comments_labelled.csv`)\n\n"
        f"## Polarity\n\n{table(pol)}\n\n## Intent\n\n{table(inten)}\n\n"
        f"## Aspect detection (per aspect, yes/no)\n\n{table(asp)}\n\n"
        f"## Polarity confusion (gold → predicted)\n\n"
        + "\n".join(f"- {g} → {p}: {n}" for (g, p), n in sorted(confusion.items())) + "\n",
        encoding="utf-8")
    m = next(r for r in pol if r["label"] == "macro")
    print(f"wrote {REPORT.relative_to(ROOT)} · polarity macro-F1 {m['f1']:.2f} on {len(labelled)} comments")
