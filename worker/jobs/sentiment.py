"""Sentiment + ABSA on Hacker News comments (CLAUDE.md §8). Runs locally, no paid calls.

Model version v0.1:
  • polarity  — cardiffnlp/twitter-roberta-base-sentiment-latest (negative / neutral / positive), run on the whole
                comment (overall) and on each sentence that mentions an aspect (aspect polarity);
                overall = "mixed" when the comment holds confident positive AND negative aspect sentences.
  • aspects   — keyword lexicon per aspect (§8.3), sentence level.
  • intent    — rules (question / comparison / recommendation / complaint / informational).
  • vendors   — vendor keywords in the comment; else the story's single matched vendor.
Every prediction stores MODEL_VERSION. Evaluate with scripts/eval_sentiment.py against the hand-labelled set.
"""
from __future__ import annotations

import html
import logging
import os
import re
from collections import Counter
from datetime import datetime

from tracker.common import JobResult, RawStore, upsert

log = logging.getLogger("tracker.sentiment")
HF_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
MODEL_VERSION = "v0.1-roberta-sent+lexicon-aspects+rule-intent"
CONF_MIXED = 0.60

ASPECTS: dict[str, str] = {
    "coding": r"\b(code|coding|coder|programm\w*|refactor\w*|debug\w*|bugs?|ide|cursor|copilot|repo|pull request|"
              r"compil\w*|python|javascript|typescript|rust|golang|sql|unit tests?)\b",
    "reasoning": r"\b(reason\w*|logic\w*|math\w*|thinking|chain[- ]of[- ]thought|puzzles?|proofs?|planning)\b",
    "writing": r"\b(prose|essays?|writing (quality|style)|creative writing|copywriting|tone of voice|"
               r"translat\w*|fiction|articles?|blog posts?)\b",
    "accuracy": r"\b(hallucinat\w*|accura\w*|wrong|incorrect|mistakes?|made[- ]up|factual\w*|confabulat\w*)\b",
    "speed": r"\b(fast(er|est)?|slow(er|est|ly)?|latency|speed|tokens?/s(ec)?|tok/s|quick(er|ly)?|laggy|sluggish)\b",
    "pricing": r"(\b(price[sd]?|pricing|costs?|costly|cheap(er|est)?|expensive|pricey|subscription|billing|"
               r"per[- ]token|afford\w*)\b|\$\d)",
    "reliability": r"\b(outages?|downtime|down again|errors?|rate[- ]?limit\w*|uptime|overloaded|unreliable|"
                   r"reliab\w*|5\d\d|timeouts?|flaky)\b",
    "context_window": r"\b(context( window| length)?|long[- ]context|\d+k tokens|1m tokens|token limit)\b",
    "user_experience": r"\b(ui|ux|interface|desktop app|mobile app|web app|usability|user experience|workflow)\b",
    "multimodal": r"\b(images?|vision|videos?|audio|voice|speech|multimodal|pdfs?|screenshots?)\b",
}
ASPECT_RX = {a: re.compile(p, re.I) for a, p in ASPECTS.items()}
INTENT_RX = {
    "comparison": re.compile(r"\b(vs\.?|versus|better than|worse than|compared (to|with)|than (gpt|chatgpt|claude|"
                             r"gemini|grok|deepseek|o\d)|over (gpt|chatgpt|claude|gemini|grok|deepseek))\b", re.I),
    "recommendation": re.compile(r"\b(recommend\w*|you should (try|use)|switch(ed|ing)? to|give it a try|"
                                 r"highly suggest|go with|worth (it|trying))\b", re.I),
    "question": re.compile(r"^\s*(how|what|why|does|is|are|can|could|should|has|have|anyone|which)\b.*\?", re.I | re.S),
}
TAG_RX = re.compile(r"<[^>]+>")
SENT_RX = re.compile(r"(?<=[.!?])\s+|\n+")
# contrast words split a sentence into clauses, so "great code, but too expensive" scores each part on its own
CLAUSE_RX = re.compile(r"\s*(?:;|,?\s+\b(?:but|however|although|though|whereas|yet|except)\b)\s*", re.I)


def clean(text: str | None) -> str:
    t = html.unescape(TAG_RX.sub(" ", (text or "").replace("<p>", "\n")))
    return re.sub(r"[ \t]+", " ", t).strip()


def sentences(text: str) -> list[str]:
    out = []
    for s in SENT_RX.split(text):
        out += [c.strip() for c in CLAUSE_RX.split(s) if len(c.strip()) > 3]
    return out


class Scorer:
    """Thin wrapper around the HF pipeline; loaded once per process (model cached in HF_HOME)."""

    def __init__(self) -> None:
        from transformers import pipeline  # imported lazily: heavy

        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.pipe = pipeline("sentiment-analysis", model=HF_MODEL, tokenizer=HF_MODEL, device=-1,
                             truncation=True, max_length=256, top_k=None)

    def score(self, texts: list[str], batch_size: int = 32) -> list[tuple[str, float]]:
        out = []
        for i in range(0, len(texts), batch_size):
            for res in self.pipe([t[:1500] for t in texts[i:i + batch_size]], batch_size=batch_size):
                best = max(res, key=lambda r: r["score"])
                out.append((best["label"].lower(), float(best["score"])))
        return out


def vendor_terms(cur) -> dict[str, re.Pattern]:
    cur.execute("SELECT vendor_id, hn_keywords FROM core.dim_vendor")
    return {v: re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in kws) + r")(?![\w])", re.I)
            for v, kws in cur.fetchall()}


def intent_of(text: str, polarity: str, conf: float) -> str:
    if INTENT_RX["question"].search(text[:400]) or (text.count("?") >= 1 and len(text) < 220):
        return "question"
    if INTENT_RX["comparison"].search(text):
        return "comparison"
    if INTENT_RX["recommendation"].search(text):
        return "recommendation"
    if polarity == "negative" and conf >= CONF_MIXED:
        return "complaint"
    return "informational"


def analyse(texts: list[str], scorer: Scorer) -> list[dict]:
    """Pure analysis for a batch of comment texts → overall + aspect predictions (used by job and eval)."""
    jobs: list[tuple[int, str | None, str]] = []          # (comment index, aspect or None, text)
    for i, t in enumerate(texts):
        jobs.append((i, None, t))
        for s in sentences(t):
            for a, rx in ASPECT_RX.items():
                if rx.search(s):
                    jobs.append((i, a, s))
    scored = scorer.score([j[2] for j in jobs])
    res = [{"overall": None, "aspects": {}} for _ in texts]
    per_aspect: dict[tuple[int, str], list[tuple[str, float]]] = {}
    for (i, a, _), (lab, conf) in zip(jobs, scored):
        if a is None:
            res[i]["overall"] = (lab, conf)
        else:
            per_aspect.setdefault((i, a), []).append((lab, conf))
    for (i, a), labs in per_aspect.items():
        c = Counter(l for l, _ in labs)
        top = c.most_common()
        pol = "mixed" if len(top) > 1 and top[0][1] == top[1][1] and {"positive", "negative"} <= set(c) else top[0][0]
        res[i]["aspects"][a] = (pol, max(conf for l, conf in labs if l == top[0][0]))
    for i, t in enumerate(texts):
        lab, conf = res[i]["overall"]
        confident = {l for l, c in per_aspect_flat(per_aspect, i) if c >= CONF_MIXED}
        if {"positive", "negative"} <= confident:
            lab = "mixed"
        res[i]["overall"] = (lab, conf)
        res[i]["intent"] = intent_of(t, lab, conf)
    return res


def per_aspect_flat(per_aspect: dict, i: int):
    for (j, _), labs in per_aspect.items():
        if j == i:
            yield from labs


def sentiment(conn, run_id: str, raw: RawStore, res: JobResult, limit: int | None = None,
              rescore: bool = False) -> None:
    if raw.mode != "mongo":
        res.status = "skipped"
        res.notes.append("MongoDB unreachable: comments are read from raw_comments")
        return
    cur = conn.cursor()
    done = set()
    if not rescore:
        cur.execute("SELECT comment_id FROM core.dim_comment")
        done = {r[0] for r in cur.fetchall()}
    terms = vendor_terms(cur)
    cur.execute("SELECT split_part(news_id, ':', 2), array_agg(vendor_id) FROM core.bridge_news_vendor "
                "WHERE news_id LIKE 'hn:%%' GROUP BY 1")
    story_vendors = dict(cur.fetchall())

    todo = []
    for d in raw.db.raw_comments.find({"source": "hn_comments"}, {"payload": 1, "_id": 0}):
        p = d.get("payload") or {}
        if p.get("comment_id") in done:
            continue
        text = clean(p.get("comment_text"))
        if len(text) < 15:
            continue
        todo.append((p, text))
        if limit and len(todo) >= limit:
            break
    if not todo:
        res.notes.append("no new comments")
        return
    log.info("scoring %d comments with %s", len(todo), MODEL_VERSION)
    scorer = Scorer()
    rows, dims, CHUNK = [], [], 200
    for k in range(0, len(todo), CHUNK):
        chunk = todo[k:k + CHUNK]
        preds = analyse([t for _, t in chunk], scorer)
        for (p, text), pred in zip(chunk, preds):
            cid, sid = p["comment_id"], p.get("story_id")
            published = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")) if p.get("created_at") else None
            dims.append({"comment_id": cid, "story_id": sid, "story_title": p.get("story_title"),
                         "published_at": published, "excerpt": text[:400],
                         "url": f"https://news.ycombinator.com/item?id={cid}"})
            vendors = [v for v, rx in terms.items() if rx.search(text)]
            if not vendors and len(story_vendors.get(sid) or []) == 1:
                vendors = list(story_vendors[sid])
            lab, conf = pred["overall"]
            for v in vendors:
                rows.append({"comment_id": cid, "vendor_id": v, "aspect": "overall", "story_id": sid,
                             "published_at": published, "polarity": lab, "intent": pred["intent"],
                             "confidence": round(conf, 4), "model_version": MODEL_VERSION})
                for a, (alab, aconf) in pred["aspects"].items():
                    rows.append({"comment_id": cid, "vendor_id": v, "aspect": a, "story_id": sid,
                                 "published_at": published, "polarity": alab, "intent": None,
                                 "confidence": round(aconf, 4), "model_version": MODEL_VERSION})
        upsert(cur, "core.dim_comment", dims, ["comment_id"])
        res.rows_loaded += upsert(cur, "core.fact_comment_sentiment", rows, ["comment_id", "vendor_id", "aspect"])
        conn.commit()
        log.info("  %d/%d comments", min(k + CHUNK, len(todo)), len(todo))
        rows, dims = [], []
    res.notes.append(f"{len(todo)} comments scored ({MODEL_VERSION})")
