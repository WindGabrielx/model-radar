"""Shared helpers for ingestion and worker jobs: config, Supabase, MongoDB raw store, HTTP, run log."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
import requests

log = logging.getLogger("tracker")
ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- config
def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (existing env wins)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


# Alternative names accepted for a variable (first non-empty wins). Works for .env and Docker env_file alike.
ENV_ALIASES = {"OPENROUTER_API_KEY": ("ROUTER_PASSWORD",)}


def env(key: str, default: str | None = None) -> str | None:
    for name in (key, *ENV_ALIASES.get(key, ())):
        val = os.environ.get(name)
        if val:
            return val
    return default


def user_agent() -> str:
    return f"ai-model-tracker/{env('TECH_RADAR_VERSION', '0.1.0')}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_utc() -> date:
    return utcnow().date()


def sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- supabase
def pg_connect(role: str = "WRITER"):
    return psycopg2.connect(
        host=env("SUPABASE_DB_HOST"),
        port=env("SUPABASE_DB_PORT", "5432"),
        dbname=env("SUPABASE_DB_NAME", "postgres"),
        user=env(f"SUPABASE_DB_USER_{role}"),
        password=env(f"SUPABASE_DB_PASSWORD_{role}"),
        sslmode=env("SUPABASE_SSLMODE", "require"),
        connect_timeout=15,
        application_name="ai-model-tracker",
    )


def upsert(cur, table: str, rows: list[dict], key: list[str], update: list[str] | None = None) -> int:
    """INSERT ... ON CONFLICT (key) DO UPDATE — idempotent load (CLAUDE.md §3)."""
    if not rows:
        return 0
    rows = list({tuple(str(r[k]) for k in key): r for r in rows}.values())   # last duplicate wins
    cols = list(rows[0].keys())
    update = [c for c in (update if update is not None else cols) if c not in key]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update) or None
    conflict = f"ON CONFLICT ({', '.join(key)}) " + (f"DO UPDATE SET {set_clause}" if set_clause else "DO NOTHING")
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s {conflict}"
    values = [tuple(_adapt(r[c]) for c in cols) for r in rows]
    psycopg2.extras.execute_values(cur, sql, values, page_size=500)
    return len(rows)


def _adapt(v: Any) -> Any:
    if isinstance(v, dict):
        return psycopg2.extras.Json(v)
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return psycopg2.extras.Json(v)
    return v


# ---------------------------------------------------------------- raw store (MongoDB)
RAW_COLLECTION = {
    "openrouter": "raw_openrouter",
    "hn_story_search": "raw_news", "rss_openai": "raw_news", "rss_deepmind": "raw_news", "gdelt": "raw_news",
    "hn_comments": "raw_comments",
    "lifecycle": "raw_lifecycle_pages",
}


class RawStore:
    """Raw-first landing zone. If MongoDB is unreachable, falls back to data/raw_spool/ (JSONL)
    so a run is never lost; replay the spool with scripts/replay_raw_spool.py once Atlas is reachable."""

    def __init__(self) -> None:
        self.db = None
        self.mode = "spool"
        self.error: str | None = None
        uri = env("MONGODB_URI")
        if uri:
            try:
                from pymongo import MongoClient

                client = MongoClient(uri, serverSelectionTimeoutMS=8000, appname="ai-model-tracker")
                client.admin.command("ping")
                self.db = client[env("MONGODB_DB", "ai_model_tracker")]
                self.mode = "mongo"
            except Exception as exc:  # noqa: BLE001 — any connection error → spool
                self.error = f"{type(exc).__name__}: {str(exc)[:160]}"
                log.warning("MongoDB unreachable, spooling raw locally: %s", self.error)
        self.spool_dir = ROOT / "data" / "raw_spool"

    @staticmethod
    def collection_for(source: str) -> str:
        for prefix, coll in RAW_COLLECTION.items():
            if source.startswith(prefix):
                return coll
        return "raw_misc"

    def put(self, *, source: str, request_key: str, request_url: str, run_id: str, http_status: int,
            payload: Any, content_hash: str | None = None) -> None:
        now = utcnow()
        doc = {
            "source": source, "request_key": request_key, "request_url": request_url, "run_id": run_id,
            "fetched_at": now, "fetched_date": now.date().isoformat(), "http_status": http_status,
            "content_hash": content_hash, "payload": payload,
        }
        coll = self.collection_for(source)
        if coll == "raw_comments":
            key = {"source": source, "request_key": request_key}
        elif coll == "raw_lifecycle_pages":
            key = {"source": source, "fetched_date": doc["fetched_date"]}
        else:
            key = {"source": source, "request_key": request_key, "fetched_date": doc["fetched_date"]}
        if self.mode == "mongo":
            self.db[coll].replace_one(key, doc, upsert=True)
            return
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        with open(self.spool_dir / f"{doc['fetched_date']}_{coll}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"collection": coll, **doc}, default=str) + "\n")


# ---------------------------------------------------------------- HTTP
_session = requests.Session()


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = 60, retries: int = 3, min_backoff: float = 0) -> requests.Response:
    """GET with User-Agent, 3 retries, exponential backoff, honours Retry-After (CLAUDE.md §10)."""
    hdrs = {"User-Agent": user_agent(), **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = max(float(resp.headers.get("Retry-After") or 0) or 2 ** (attempt + 1), min_backoff * (attempt + 1))
                log.info("HTTP %s from %s, retry in %.0fs", resp.status_code, url, wait)
                time.sleep(min(wait, 60))
                continue
            return resp
        except requests.RequestException as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last}")


def openrouter_headers() -> dict:
    key = env("OPENROUTER_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---------------------------------------------------------------- run log
def new_run_id() -> str:
    return f"{utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}-{uuid.uuid4().hex[:4]}"


@dataclass
class JobResult:
    rows_loaded: int = 0
    status: str = "success"           # success | partial | failed | skipped
    notes: list[str] = field(default_factory=list)


@contextmanager
def run_logged(conn, run_id: str, source: str) -> Iterator[JobResult]:
    """Write ops.pipeline_run_log around a job. Failures are recorded, never raised (one source never blocks others)."""
    with conn.cursor() as cur:
        upsert(cur, "ops.pipeline_run_log",
               [{"run_id": run_id, "source": source, "started_at": utcnow(), "status": "running",
                 "rows_loaded": 0, "error": None, "finished_at": None}], ["run_id", "source"])
    conn.commit()
    result = JobResult()
    try:
        yield result
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.exception("job %s failed", source)
        result.status = "failed"
        result.notes.append(f"{type(exc).__name__}: {exc}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ops.pipeline_run_log SET finished_at = now(), status = %s, rows_loaded = %s, error = %s "
            "WHERE run_id = %s AND source = %s",
            (result.status, result.rows_loaded, "; ".join(result.notes)[:2000] or None, run_id, source),
        )
    conn.commit()
    log.info("%-22s %-8s rows=%-6d %s", source, result.status, result.rows_loaded, "; ".join(result.notes)[:200])
