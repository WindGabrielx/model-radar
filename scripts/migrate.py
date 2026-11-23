"""Apply supabase/migrations/*.sql in order (tracked in ops.schema_migrations), then load seeds.

    python scripts/migrate.py            # apply new migrations + seeds
    python scripts/migrate.py --status   # list applied migrations
Never edit an applied migration; add a new numbered file (CLAUDE.md §15).
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from tracker.common import ROOT, load_env, pg_connect, upsert  # noqa: E402

MIGRATIONS = ROOT / "supabase" / "migrations"


def applied(cur) -> set[str]:
    cur.execute("SELECT to_regclass('ops.schema_migrations')")
    if cur.fetchone()[0] is None:
        return set()
    cur.execute("SELECT filename FROM ops.schema_migrations")
    return {r[0] for r in cur.fetchall()}


def apply_migrations(conn) -> None:
    with conn.cursor() as cur:
        done = applied(cur)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in done:
            continue
        print(f"applying {path.name}")
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO ops.schema_migrations (filename) VALUES (%s)", (path.name,))
        conn.commit()


def _arr(s: str) -> list[str]:
    return [x for x in (s or "").split("|") if x]


def load_seeds(conn) -> None:
    with open(ROOT / "seed" / "dim_vendor.csv", encoding="utf-8") as fh:
        rows = [{**r, "openrouter_prefixes": _arr(r["openrouter_prefixes"]), "hn_keywords": _arr(r["hn_keywords"]),
                 "hn_context_terms": _arr(r["hn_context_terms"])} for r in csv.DictReader(fh)]
    with conn.cursor() as cur:
        upsert(cur, "core.dim_vendor", rows, ["vendor_id"])
    tiers = ROOT / "seed" / "model_tiers.csv"
    if tiers.exists():
        with open(tiers, encoding="utf-8") as fh:
            trows = [r for r in csv.DictReader(fh) if r.get("tier")]
        with conn.cursor() as cur:
            for r in trows:
                cur.execute("UPDATE core.dim_model SET tier = %s WHERE model_id = %s", (r["tier"], r["model_id"]))
    conn.commit()
    print(f"seeded {len(rows)} vendors")


if __name__ == "__main__":
    load_env()
    conn = pg_connect("WRITER")
    if "--status" in sys.argv:
        with conn.cursor() as cur:
            print(sorted(applied(cur)))
    else:
        apply_migrations(conn)
        load_seeds(conn)
    conn.close()
