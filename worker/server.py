"""Tiny HTTP trigger so n8n can run each pipeline step as its own node (CLAUDE.md §10).

    POST /start   -> {"run_id": "...", "skip": false}         one id per daily run, shared by every step
                  body {"if_missing": true}: skip=true when today's (Bangkok) full run already finished,
                  so n8n can fire hourly and still run exactly once a day (catch-up if the PC was off at 10:00)
    POST /run     body: {"run_id": "...", "only": "hn"}       run one or more steps of that run
                  optional "backfill_news_days": 30, "force_lifecycle": true
                  200 = step finished (success/partial/skipped), 500 = step failed, 409 = same step already running
                  response: {"run_id", "steps": {step: {status, rows, notes}}, "dq": {...} (for dq_checks)}
    GET  /health  -> ok

Only reachable inside the Docker network (no published port). Header X-Worker-Token must match WORKER_TOKEN if set.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def ran_today() -> str | None:
    """run_id of today's (Asia/Bangkok) completed full run, if any. A full run ends with its DQ step."""
    from tracker.common import load_env, pg_connect

    load_env()
    conn = pg_connect("WRITER")
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT run_id FROM ops.pipeline_run_log
                           WHERE source = 'dq_checks' AND status IN ('success', 'partial')
                             AND (started_at AT TIME ZONE 'Asia/Bangkok')::date
                                 = (now() AT TIME ZONE 'Asia/Bangkok')::date
                           ORDER BY started_at DESC LIMIT 1""")
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


RUNNING: set[str] = set()
GUARD = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "running": sorted(RUNNING)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        token = os.environ.get("WORKER_TOKEN")
        if token and self.headers.get("X-Worker-Token") != token:
            return self._send(401, {"error": "bad token"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "body must be JSON"})

        if self.path == "/start":
            run_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}-{uuid4().hex[:4]}"
            if body.get("if_missing"):
                done = ran_today()
                if done:
                    return self._send(200, {"run_id": None, "skip": True, "reason": f"already ran today: {done}"})
            return self._send(200, {"run_id": run_id, "skip": False})
        if self.path != "/run":
            return self._send(404, {"error": "not found"})

        only = str(body.get("only") or "")
        key = only or "all"
        with GUARD:
            if key in RUNNING:
                return self._send(409, {"error": f"{key} is already running"})
            RUNNING.add(key)
        try:
            cmd = [sys.executable, str(HERE / "run_daily.py")]
            if only:
                cmd += ["--only", only]
            if body.get("run_id"):
                cmd += ["--run-id", str(body["run_id"])]
            if body.get("backfill_news_days"):
                cmd += ["--backfill-news-days", str(int(body["backfill_news_days"]))]
            if body.get("force_lifecycle"):
                cmd += ["--force-lifecycle"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3 * 3600)
            lines = (proc.stdout + proc.stderr).strip().splitlines()
            sys.stdout.write("\n".join(lines[-15:]) + "\n")
            result = next((json.loads(l[7:]) for l in reversed(lines) if l.startswith("RESULT ")), {})
            result.update({"returncode": proc.returncode, "log_tail": lines[-12:]})
            self._send(200 if proc.returncode == 0 else 500, result)
        finally:
            with GUARD:
                RUNNING.discard(key)

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    port = int(os.environ.get("WORKER_PORT", "8000"))
    print(f"worker listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
