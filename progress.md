# Progress — AI Model Tracker

Status against [CLAUDE.md](CLAUDE.md). Updated 2026-11-22.

Legend: ✅ done and verified · 🟡 partly done / works with limits · ⏳ not started · ⛔ blocked (needs the author)

## Blockers (need you)

| # | What | Why it matters | How to fix |
|---|---|---|---|
| B1 | ✅ **MongoDB connected** (2026-11-22) | IP 183.89.11.152 added to Atlas IP Access List; spool replayed (7,138 comments + news/openrouter/lifecycle), indexes created | If a run shows DQ `raw_landing_zone = warn`, the home IP changed: Atlas → IP Access List → Add Current IP Address, then `python scripts/replay_raw_spool.py` |
| B2 | ⛔ **`OPENROUTER_API_KEY` is empty** | Usage (D), benchmarks API (E), tasks (F), latency/throughput (C) are skipped. The Usage page shows an empty state; Speed is blank | Create a free key (credit limit $1, set an expiry), put it in `.env`, run `python worker/run_daily.py` |
| B3 | ✅ `.env` now `MONGODB_DB=ai_model_tracker`; old `tech_radar` database no longer exists | — | — |
| B4 | ⚠ `seed/model_tiers.csv` is an **auto draft** | Tiers are meant to be hand-curated (§5). Draft = price bands + name hints (48 top / 17 mid / 60 small) | Review the `tier` column, then `python scripts/migrate.py` |

## Build status

### 1. Storage
- ✅ Supabase schemas `core`, `ops`, `mart` — `supabase/migrations/001–005`, applied, tracked in `ops.schema_migrations`
- ✅ All core tables from §6.2, plus `dim_model.created_at_source`, `fact_lifecycle_event.detail/is_tentative`, `fact_news_item.domain`, `fact_benchmark_snapshot.citation`
- ✅ Mart views: all 10 from §6.2 + helpers (`v_vendor`, `v_quality_current`, `v_usage_model_7d`, `v_sentiment_vendor_30d`, `v_pipeline_rows_daily`, `v_dq_latest`)
- ✅ `streamlit_ro` grants on `mart`, `ops` (role already existed)
- ✅ Seeds: `seed/dim_vendor.csv` (keywords/context terms from §8.1)
- ✅ MongoDB `ai_model_tracker`: indexes from `mongodb/init_indexes.js` created (via pymongo)

### 2. Ingestion — `worker/run_daily.py` (Python, same steps as n8n `00_master`)
| Step | Status | Last run |
|---|---|---|
| 01 `or_models` | ✅ | 125 vendor models, 78 batch variants folded in, 56 quality scores, 125 first-seen events |
| 02 `or_endpoints` | 🟡 | 559 provider rows; prices + uptime only, latency/throughput need key (B2) |
| 03 `or_usage` | ⛔ B2 | written, untested against the live API (response field names guessed defensively) |
| 04 `or_benchmarks` | ⛔ B2 | written, untested. Meanwhile quality comes from the AA scores embedded in `/models` (`source = openrouter_models_aa`) |
| 05 `or_tasks` | ⛔ B2 | written, untested |
| 06 `hn` | ✅ | 776 stories matched (30-day backfill), 7,138 comments landed raw (spool) |
| 07 `rss` + `gdelt` | 🟡 | RSS ✅ (1,519 items). GDELT works but hits 429s: some vendors fail per run → `partial`. Backoff now ≥10 s |
| 08 `lifecycle` | ✅ | 295 official events from 5 pages; hash-gated parsing; header checks feed DQ |
| 09 `dq_checks` | ✅ | all §11 checks except "raw doc count = rows loaded" (needs Mongo) |
| Raw landing (Mongo) | ✅ | writes to MongoDB; falls back to `data/raw_spool/*.jsonl` if Atlas is unreachable (replay script) |
| `scripts/test_keys.py` | ✅ | all pass except the OpenRouter data API (no key, B2) |

Scheduling (decided 2026-11-22: n8n only triggers the Python steps):
- ✅ Worker container `worker` (`worker/Dockerfile`, image `ai-model-tracker-worker:local`) serving `POST /run` on the internal Docker network (`worker/server.py`); `.env` read at runtime, spool on `./data`
- ✅ Verified end to end: n8n network → `POST /run` → worker → MongoDB + Supabase (`raw=mongo`)
- ✅ n8n `00_master` (15 nodes, `n8n/workflows/00_master.json`): Daily 10:00 / Run now → Start run (one run_id) → 5 branches: 01→02 · 03→04→05 · 06 · 07 · 08 → All steps done → 09 DQ → DQ failed? → Alert (Stop and Error). Each step node continues on error so one source never blocks the others
- ✅ Pipeline health page tracks each run step by step (same names as the n8n nodes) + run history grid (migration 006: `mart.v_pipeline_runs`, `mart.v_pipeline_run_summary`)
- ⚠ **Activate it once in the n8n UI** (http://localhost:5678 → 00_master → Publish/Active). Imported workflows start inactive
- Rebuild the worker after code changes: `docker build -f worker/Dockerfile -t ai-model-tracker-worker:local . && docker compose up -d worker` (compose `build:` is blocked by a Docker Desktop entitlement prompt on this machine)

### 3. Worker ML / analytics (§8–9)
- ✅ Vendor matching (keyword AND context rule, §8.1) — used by HN and GDELT
- ✅ Lifecycle → model alias mapping (exact after hyphen→dot normalisation); unmatched names stay NULL
- ✅ Value score, blended price, composite score (UI), rank stability
- ⏳ Sentiment + ABSA model on HN comments (`fact_comment_sentiment`) — next build step; needs a local model (e.g. transformers) and Mongo or the spool as input
- ⏳ Hand-labelled eval set (`data/labels/comments_labelled.csv`, ~300) + `reports/sentiment_eval.md`
- ⏳ Event study (§9.3), recommender (§9.4, optional)

### 4. Streamlit app — `app/` ✅
- 2026-11-22: UI translated to **Thai** (font IBM Plex Sans Thai); vendor badges share the chart colours; filter state shown on every page with a reset button; empty columns hidden with a note instead of "None"; Overview gained top-10 quality and value charts; Pipeline health uses a **Databricks-style run matrix** (click a run to see its tasks).
Built with the official Streamlit skill (`developing-with-streamlit`, installed globally because Windows blocks symlinks), `streamlit-design`, and Impeccable (Operate mode + craft floor).

- Theme `app/.streamlit/config.toml`: ink sidebar rail, warm paper ground, Geist / Geist Mono, light + dark, vendor colours pinned in chart order. No custom CSS.
- Provenance badges (Official / Third-party / Derived) on every section; OpenRouter attribution line; Bangkok times; read-only `mart.*` via `st.connection` (ttl 3600).
- Pages: Overview ✅ · Price & value ✅ · Usage 🟡 (empty state until B2) · News & sentiment 🟡 (feed + coverage ✅, sentiment empty until worker ML) · Lifecycle ✅ · Pipeline health ✅
- Verified: all 6 pages `ready` via the agent API with zero console warnings; Overview, Price & value and Lifecycle checked by screenshot.
- Secrets: `python scripts/make_app_secrets.py` writes `app/.streamlit/secrets.toml` (git-ignored) from `.env`.

### 5. Repo hygiene
- ✅ `.env.example`, `.gitignore` (+ `data/raw_spool/`), `worker/requirements.txt`, `app/requirements.txt`
- ⏳ `README.md`, `tests/` (AppTest for pages, unit tests for parsers/matching)
- ⏳ Not a git repo yet (`git init` when you want)

### 6. Reliability when a day is missed (2026-11-23)
- ✅ n8n fires hourly 10:00–22:00; `POST /start {"if_missing": true}` skips if today's (Bangkok) full run already finished, so a PC that was off at 10:00 still gets exactly one run that day
- ✅ Catch-up windows: HN and GDELT reach back to their last successful load (max 30 days); usage rankings re-fetch from the last loaded day (3–30 days)
- ✅ Price changes found after a gap are flagged `is_tentative` with "changed between <prev> and <today>"
- ✅ Lifecycle dates are re-read from official pages every run and "days left" is computed at view time, so missed days do not shift them
- ⚠ Permanent gaps for missed days: daily price/model snapshots and provider stats (APIs only return "now"). Shown as "Missed days" on the Pipeline health page
- ✅ GDELT: one combined query per run (was 5) after repeated per-IP 429s
- ✅ App DB connection: pre-ping, recycle, keepalives and timeouts, so an overnight idle connection no longer hangs a page

### 7. UI language
- ✅ TH / EN switch at the top of the sidebar (single choice, `?lang=EN` in the URL). Thai is the source; English in `app/lib/i18n_en.json` (291 strings). New UI text must be wrapped in `_()` and added to that file
- ✅ Metric glossary ("Metric definitions") on Overview and Price & value; every model labelled on the quality-vs-price chart (scroll to zoom)

### 8. Price basis fix (2026-11-23)
- ✅ Value now uses a **reference price** (migration 007, `mart.v_price_reference`): the vendor's own standard endpoint on OpenRouter → else median across providers → else the `/models` headline price. The headline price followed OpenRouter's routing to the cheapest (often quantised) host and inflated value (DeepSeek V4.1 Flash 1017: 214 → 124). Headline prices kept as `list_*` columns
- ✅ `price_change` events now come from the vendor's own endpoint (in `or_endpoints`), not the headline price, so routing changes do not create false price changes
- ✅ Value charts use a log scale (value is heavy-tailed: median 13, top 182); glossary explains it
- ✅ Google lifecycle: tables under "Deprecated models" are real shutdowns (44), not earliest-possible dates

### 9. Sentiment, labelling, usage history (2026-11-23)
- ✅ Sentiment + ABSA `v0.1` (`worker/jobs/sentiment.py`, step `sentiment`, n8n node `06b Sentiment`): local RoBERTa sentiment, clause-level aspects, rule-based intent; migration 011 (`core.dim_comment`, `ops.model_eval`, sentiment views). Worker image now includes CPU PyTorch (1.8 GB); weights cached in `data/hf_cache/`
- 🟡 Backfill of the 7,317 existing HN comments running; only comments that name a vendor (or sit in a single-vendor story) are attributed
- ✅ News & sentiment page rebuilt to the approved mockup (vendor scores with 30-day delta, aspect heatmap, weekly trend with release markers, positive-vs-negative by aspect, example comments)
- ✅ Labelling app `labeler/` (port 8502), separate from the dashboard; 300-comment sample in `data/labels/comments_to_label.csv` (Google 9 / xAI 13: few HN comments name them alone)
- ⏳ **You:** label the 300 comments, then `python scripts/eval_sentiment.py` → `reports/sentiment_eval.md`; the dashboard badge switches from "Not evaluated yet" to the F1 score
- ✅ Usage history backfilled from 2025-01-01 (34,884 rows, `scripts/backfill_usage.py`, migration 012 `backfilled` flag); Usage page has a period switch: full history (weekly) / since the pipeline started. Retired models are attributed to their vendor by OpenRouter prefix (`mart.vendor_of_slug`, migration 013)
- ✅ Data inventory on Pipeline health (migration 008); "Other models" breakdown on Usage (migration 009); latest-DQ view fixed (010)

### 10. Sentiment backfill, README, publish (2026-11-23)
- ✅ All 7,317 HN comments scored with `v0.1` (11,172 sentiment rows). Mean polarity: Google −0.08 (26) · DeepSeek −0.13 (116) · xAI −0.19 (27) · OpenAI −0.26 (807) · Anthropic −0.33 (6,345); most negative aspect Anthropic accuracy −0.64
- ✅ News & sentiment and Usage pages re-checked with the final data (Edge headless screenshots, no exceptions)
- ✅ README in English (`README.md`) and Thai (`README.th.md`) with features, screenshots, architecture, sources, metric logic and a 23 Nov 2026 snapshot
- ✅ `.gitignore` covers `.env`, secrets, `.claude/`, `config/`, HF cache, raw spool

## Known data caveats
- Only 29 of 115 active text models have a quality score (AA via `/models`); the rest show blank quality/value.
- Price history starts 2026-11-22; sparklines and history charts fill in daily.
- `fact_lifecycle_event` "retired" with `is_tentative = true` = Anthropic "not sooner than" / Google "earliest possible" dates; hidden by default in the UI.
- 2 Google events with link markup in the model name were deleted and re-parsed after a parser fix (not a parse-failure deletion).

## How to run
```bash
python scripts/migrate.py                  # migrations + seeds (idempotent)
python scripts/test_keys.py                # check every source
python worker/run_daily.py                 # daily pipeline (add --backfill-news-days 30 on first run)
python scripts/make_app_secrets.py
cd app && streamlit run streamlit_app.py   # http://localhost:8501
```

## Next steps (in order)
1. Publish / activate `00_master` in n8n so the 10:00 schedule runs on its own.
2. Label the 300 comments in the labelling app, then `python scripts/eval_sentiment.py`.
3. Wait for GDELT to lift the IP rate limit (the next run back-fills the window).
4. Tests (`tests/`), then the event-study analysis (§9.3).
