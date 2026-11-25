# CLAUDE.md — AI Model Tracker

Read this file fully before changing anything in this repository. It is the source of truth for scope, data sources, data model, metric definitions and conventions.

---

## 1. Project overview

**AI Model Tracker** collects data about the AI models of five vendors **every day**, stores it with full history, and compares them in a Streamlit dashboard.

**Vendors tracked:** OpenAI (GPT) · Anthropic (Claude) · Google (Gemini) · DeepSeek · xAI (Grok)

**Dimensions tracked:** capability · price · value for money · speed · quality · usage · lifecycle · news & sentiment

### Why
- The AI model market changes weekly (new models, price cuts, retirements). Nobody keeps a clean, queryable history of it.
- Questions the dashboard must answer:
  1. Which model gives the best quality per dollar right now, and how has that changed?
  2. How are prices and usage shifting between vendors over time?
  3. Which models are new, deprecated or about to be retired?
  4. What are developers saying about each vendor, and which aspects (price, speed, coding, …) are positive or negative?
  5. Did sentiment or usage change after a model release?

### Who benefits
- Developers and tech teams choosing a model for a task and budget, or planning migrations before a retirement date.
- Managers comparing cost and value.
- The author: a solo portfolio project showing data engineering (multi-source pipeline), analytics and applied ML end to end.

### Out of scope (do not add without asking)
- Running our own benchmark by calling models (costs money). Quality/speed come from OpenRouter's free data.
- Per-vendor API keys. Optional only (see §4.4).
- Paid market data (Similarweb, Sensor Tower), X/Twitter API, total "user counts" of a vendor.
- Vendors other than the five above.

---

## 2. Tech stack

| Layer | Technology |
|---|---|
| Orchestration / ingestion | **n8n**, self-hosted with **Docker** |
| Raw landing zone | **MongoDB Atlas** (free M0) |
| Modeled storage | **Supabase** (PostgreSQL) |
| ML / NLP jobs | **Python 3.11+** in a `worker` container (scheduled) |
| Dashboard | **Streamlit** (read-only access to Supabase) |
| Labelling tool | **Streamlit**, separate app in `labeler/` (writes a local CSV only, no database access) |

---

## 3. Architecture

```
10:00 Asia/Bangkok — n8n (Docker) → worker (Python, Docker)
  OpenRouter (models, endpoints, usage, benchmarks, tasks)
  Hacker News (Algolia) · Vendor RSS · Vendor deprecation pages · GDELT
        │
        ├─► MongoDB  raw_* collections     (every response, unchanged)
        └─► Supabase core.* tables         (typed, upserted)
                │
10:45 — worker (Python): sentiment/ABSA, matching, value scores ─► Supabase core.*
                │
        Supabase mart.* views ─► Streamlit (read-only role)
```

Principles:
- **Raw first.** Store every API response in MongoDB before transforming, so any day can be reprocessed.
- **Idempotent.** Every Supabase write is `INSERT … ON CONFLICT DO UPDATE` on natural keys. Re-running a day never duplicates.
- **Snapshots are history.** APIs return only the current state. A missed day is a permanent gap for snapshot data — log it in `ops.pipeline_run_log`.
- **Business logic lives in SQL views and the worker**, not in Streamlit or n8n Code nodes.
- **Store UTC, display Asia/Bangkok.**
- **Label provenance.** Every number shown is one of: official (vendor), third-party (OpenRouter / Artificial Analysis), or platform-derived (our calculation). Streamlit must show which.

---

## 4. Data sources (validated 2026-11-22)

All sources below were tested on 2026-11-22. ✅ = pulled successfully.

### 4.1 OpenRouter — core source (one free key)

Free-tier account key (`is_free_tier = true`) works for every endpoint below. Key: `OPENROUTER_API_KEY`, sent as `Authorization: Bearer …`.

| # | Endpoint | Key | Gives | Status |
|---|---|---|---|---|
| A | `GET https://openrouter.ai/api/v1/models` | no | id, canonical_slug, name, created, context_length, architecture (input/output modalities), pricing (USD **per token** as decimal strings: prompt, completion, input_cache_read), top_provider, supported_parameters, knowledge_cutoff, expiration_date | ✅ |
| B | `GET /api/v1/models?category=<cat>` | no | models filtered by category (e.g. `programming`) | ✅ |
| C | `GET /api/v1/models/{author}/{slug}/endpoints` | **yes** for latency/throughput | per provider: pricing, uptime_last_1d, latency_last_30m {p50,p75,p90,p99} (ms), throughput_last_30m {p50,p75,p90,p99} (tokens/s) | ✅ |
| D | `GET /api/v1/datasets/rankings-daily?start_date=&end_date=` | yes | top 50 public models per day by `total_tokens` (string) + one `other` row; data from 2025-01-01 | ✅ |
| E | `GET /api/v1/benchmarks` | yes | per `model_permaslug`: intelligence_index, coding_index, agentic_index, domain indices (legal, finance, multilingual, …); source tagged | ✅ |
| F | `GET /api/v1/classifications/task?window=7d` | yes | 29 task classes with usage/token shares (0–1) and top models | ✅ |
| G | `GET /api/v1/key` | yes | key status, limits, usage — used for health checks | ✅ |

Limits & rules:
- Data API endpoints (D, E, F) share **30 requests/min per key and 500 requests/day per account**.
- **Attribution is mandatory** wherever data is shown: `Source: OpenRouter (openrouter.ai/rankings), as of <meta.as_of>`. For benchmarks use the response's `meta.citation` string.
- Usage (D) reflects **public traffic through OpenRouter only** — never label it "users" or "market share". Label: "Share of tokens on OpenRouter".
- `/models` response is ~1 MB. Store raw daily for 30 days; after that keep only diffs (see §6.1).
- Key has a $1 credit limit and an expiry date. A 401 must raise a DQ failure and an alert.

### 4.2 News & sentiment

| Source | Endpoint | Key | Status | Notes |
|---|---|---|---|---|
| **Hacker News (Algolia)** — stories | `GET https://hn.algolia.com/api/v1/search?query=<q>&tags=story` | no | ✅ | title, url, points, num_comments, created_at, objectID |
| **Hacker News (Algolia)** — comments | `GET https://hn.algolia.com/api/v1/search?tags=comment,story_<objectID>` | no | ✅ | comment_text for sentiment (a big launch thread had 2,155 comments) |
| **OpenAI news RSS** | `https://openai.com/news/rss.xml` | no | ✅ | items not strictly chronological — sort by pubDate |
| **Google DeepMind blog RSS** (Gemini) | `https://deepmind.google/blog/rss.xml` | no | ✅ | descriptions often empty — use title |
| **GDELT DOC 2.0** | `GET https://api.gdeltproject.org/api/v2/doc/doc?query=<q>&mode=artlist&format=json&maxrecords=250&timespan=1d` | no | ✅ reachable (HTTP 429 when too fast) | **max 1 request every 5 s**; rolling 3-month window |

Anthropic, DeepSeek and xAI have no news RSS; their news comes from HN, GDELT and their release-notes pages (§4.3).

**Additional discussion sources (optional, NOT validated):** e.g. Lobsters, DEV.to, Reddit, YouTube comments. Before adding any: test it with `scripts/test_keys.py`-style checks, read its terms of service, and ask the author. Reddit and X are excluded unless their terms allow this use.

### 4.3 Lifecycle — official vendor pages (no key)

These are documentation pages, not APIs. Fetch daily, store the raw text in MongoDB, compute a content hash, and parse tables **only when the hash changes**.

| Vendor | URL | Format | Status | Parsed table columns |
|---|---|---|---|---|
| Anthropic | `https://platform.claude.com/docs/en/about-claude/model-deprecations.md` | Markdown | ✅ | API model name · Current state · Deprecated · Tentative retirement date |
| OpenAI | `https://developers.openai.com/api/docs/deprecations.md` | Markdown | ✅ | Shutdown date · Affected model · Status · Recommended replacement · Notice date |
| Google | `https://ai.google.dev/gemini-api/docs/deprecations?hl=en` | HTML | ✅ | Model · Release date · Shutdown date · Recommended replacement |
| DeepSeek | `https://api-docs.deepseek.com/updates.md` | Markdown (dated change log) | ✅ | date headings + release/update headlines |
| xAI | `https://docs.x.ai/release-notes.md` | Markdown (month headings) | ✅ | entries incl. "retired" / "end of life" |

Rules:
- Official pages are the **primary** source for lifecycle. OpenRouter `created` and "disappeared from `/models`" are **secondary** (derived) signals — store them with `source_type = 'derived'`.
- Page layouts change. If parsing returns 0 rows or the header row differs from the table above, mark the DQ check `fail` and keep yesterday's parsed data. Never delete lifecycle events because a parse failed.

### 4.4 Optional: per-vendor API keys

Not required. Creating keys is free, listing models costs no tokens, but some vendors may require billing set up before a key works (not verified). Only worthwhile extra: OpenAI `GET /v1/models` exposes an optional `shutdown_date` per model. Add only if the author asks.

---

## 5. Model identity rules

Applied in the worker when populating `core.dim_model`:

- **Vendor by OpenRouter prefix:** `openai/` → OpenAI, `anthropic/` → Anthropic, `google/gemini` → Google, `deepseek/` → DeepSeek, `x-ai/` → xAI. Other prefixes are ignored (e.g. `google/gemma-*` is not Gemini).
- **Batch variants** (`…:batch`) are **not** separate models: store their prices as `batch_input_price` / `batch_output_price` on the base model.
- **Aliases** whose id starts with `~` (e.g. `~x-ai/grok-latest`) are skipped.
- **Modality:** use `architecture.output_modalities` to set `output_type` = `text` or `image` (image models are kept but filtered out by default in the UI).
- **Tier** (`top` / `mid` / `small`) is curated by hand in `seed/model_tiers.csv`; unknown new models default to `unclassified` and are listed for review.
- **Join keys:** usage and benchmarks use `model_permaslug`; map to `dim_model` through `core.map_model_alias` (exact match on canonical_slug first, then a reviewed fuzzy match). Lifecycle pages use vendor API names (e.g. `claude-opus-5-5`, OpenRouter `anthropic/claude-opus-5.5`) — map through the same alias table. A dated snapshot name (`gpt-5-2025-08-07`, `claude-sonnet-4-5-20250929`) maps only when its words and **date stamp** equal the model's OpenRouter `canonical_slug` (`openai/gpt-5-2025-08-07`, `anthropic/claude-4.5-sonnet-20250929`), so a retired old snapshot never retires an alias that now points to a newer one. Stored events still unmapped are re-mapped every run.
- **Availability status** (`mart.v_model_status`): `active` / `retiring` (firm shutdown within 30 days) / `retired` (date passed, or gone from OpenRouter). Retired models are hidden in the dashboard by default even if OpenRouter still lists them. Cases the rules get wrong are fixed by hand in `seed/model_overrides.csv` (model_id, status, retire_date, replacement, note — the file is the full list).

---

## 6. Data model

### 6.1 MongoDB Atlas — raw landing zone

Database `ai_model_tracker`. One document per response:

```js
{
  source: "openrouter_models" | "openrouter_endpoints" | "openrouter_rankings" | "openrouter_benchmarks"
        | "openrouter_tasks" | "hn_story_search" | "hn_comments" | "rss_openai" | "rss_deepmind"
        | "gdelt" | "lifecycle_anthropic" | "lifecycle_openai" | "lifecycle_google"
        | "lifecycle_deepseek" | "lifecycle_xai",
  request_key: "anthropic/claude-opus-5.5",   // model id, query, story id, page name
  request_url: "https://...",
  run_id: "2026-11-23T00:00:03Z-a1b2",         // = ops.pipeline_run_log.run_id
  fetched_at: ISODate(...),
  fetched_date: "2026-11-23",                  // UTC, idempotency key
  http_status: 200,
  content_hash: "sha256:...",                  // lifecycle pages and /models
  payload: { ... } | "<raw text>"
}
```

Collections: `raw_openrouter`, `raw_news`, `raw_comments`, `raw_lifecycle_pages`, `workflow_logs`.

Indexes (`mongodb/init_indexes.js`):
```js
db.raw_openrouter.createIndex({source:1, request_key:1, fetched_date:1}, {unique:true});
db.raw_news.createIndex({source:1, request_key:1, fetched_date:1}, {unique:true});
db.raw_comments.createIndex({source:1, request_key:1}, {unique:true});        // comment id
db.raw_lifecycle_pages.createIndex({source:1, fetched_date:1}, {unique:true});
db.raw_openrouter.createIndex({run_id:1});
```
Writes are upserts. Retention: `/models` full payload 30 days, then keep only documents whose `content_hash` changed. Keep everything else while the cluster is below 400 MB (M0 limit 512 MB).

Atlas setup: M0 cluster in a nearby region (e.g. Singapore). Add the IP of the machine running n8n to the IP Access List; avoid `0.0.0.0/0`. DB user with `readWrite` on `ai_model_tracker` only.

### 6.2 Supabase — modeled storage

Connection: **Session pooler** string from *Project → Connect* (IPv4, port 5432, user `postgres.<project-ref>`), `sslmode=require`. The direct host `db.<ref>.supabase.co` may be IPv6-only and fail from Docker.

Schemas — never use `public` (exposed by Supabase's REST API):

| Schema | Contents | Written by |
|---|---|---|
| `core` | dimensions, facts, ML outputs | n8n, worker |
| `ops` | run log, DQ results, page hashes | n8n, worker |
| `mart` | views for Streamlit | migrations only |

Read-only role for Streamlit:
```sql
CREATE ROLE streamlit_ro LOGIN PASSWORD '<from .env>';
GRANT USAGE ON SCHEMA mart, ops TO streamlit_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA mart, ops TO streamlit_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO streamlit_ro;
```

Core tables (minimum; migrations in `supabase/migrations/`):

| Table | Grain / key | Main columns |
|---|---|---|
| `core.dim_vendor` | vendor_id | name, openrouter_prefixes text[], color_hex, hn_keywords text[], hn_context_terms text[] |
| `core.dim_model` | model_id (OpenRouter id) | vendor_id, canonical_slug, name, tier, output_type, first_seen_date, last_seen_date, is_active |
| `core.map_model_alias` | (alias, alias_source) | model_id, match_method (`exact`/`fuzzy_reviewed`/`manual`) |
| `core.fact_model_daily` | (model_id, snapshot_date) | context_length, max_completion_tokens, input_price_per_m, output_price_per_m, cache_read_price_per_m, batch_input_price_per_m, batch_output_price_per_m, input_modalities, supported_parameters jsonb, expiration_date |
| `core.fact_endpoint_daily` | (model_id, provider_name, snapshot_date) | input/output price, uptime_last_1d, latency_p50…p99 (ms), throughput_p50…p99 (tok/s) |
| `core.fact_usage_daily` | (usage_date, model_permaslug) | model_id (nullable), total_tokens bigint, is_other_row |
| `core.fact_benchmark_snapshot` | (snapshot_date, source, model_permaslug) | model_id, intelligence_index, coding_index, agentic_index, extra jsonb |
| `core.fact_task_share` | (as_of, task_tag) | name, macro_category, usage_share, token_share, top_models jsonb |
| `core.fact_lifecycle_event` | (vendor_id, model_api_name, event_type, event_date) | model_id, announced_date, replacement, source_url, source_type (`official`/`derived`) |
| `core.model_override` | model_id | status, retire_date, replacement, note (loaded from `seed/model_overrides.csv`) |
| `core.fact_news_item` | news_id | source (`hn`/`rss_openai`/`rss_deepmind`/`gdelt`), title, url, published_at, points, num_comments |
| `core.bridge_news_vendor` | (news_id, vendor_id) | match_rule |
| `core.fact_comment_sentiment` | (comment_id, vendor_id, aspect) | story_id, published_at, polarity, intent, confidence, model_version |
| `ops.pipeline_run_log` | run_id, source | started_at, finished_at, status (`running`/`success`/`partial`/`failed`), rows_loaded, error |
| `ops.dq_check_result` | (run_id, check_name) | target, result (`pass`/`warn`/`fail`), detail |
| `ops.source_page_hash` | (source, fetched_date) | content_hash, changed bool, rows_parsed |

Event types for `fact_lifecycle_event`: `released`, `deprecated`, `retired`, `price_change`, `first_seen_openrouter`, `removed_from_openrouter`.

Mart views (Streamlit reads only these):
`mart.v_model_current`, `mart.v_model_status`, `mart.v_price_history`, `mart.v_value_score`, `mart.v_speed_current`, `mart.v_usage_vendor_daily`, `mart.v_task_share_latest`, `mart.v_news_latest`, `mart.v_sentiment_weekly`, `mart.v_lifecycle_timeline`, `mart.v_pipeline_health`.

---

## 7. Metric definitions

| Metric | Definition | Provenance |
|---|---|---|
| Input / output price | OpenRouter pricing × 1,000,000 → USD per 1M tokens | third-party |
| **Reference price** | Vendor's own standard endpoint on OpenRouter (`/endpoints`, provider name = vendor, no `/flex`-style variant; Google prefers AI Studio) → else median across providers → else the `/models` headline price. Never the cheapest host (often quantised). Headline price kept as `list_*` | third-party, derived |
| **Blended price** | `(3 × input + 1 × output) / 4` per 1M tokens, on the reference price | derived |
| Batch price | prices of the `:batch` variant | third-party |
| **Quality** | `intelligence_index` from OpenRouter `/benchmarks` (Artificial Analysis source) | third-party |
| **Speed** | throughput p50 (tok/s) and latency p50 (ms) from `/endpoints`; a model's speed = **median across its providers** of p50 | third-party |
| Speed stability | p99 ÷ p50 latency | derived |
| **Value score** | `quality / blended_price` (points per USD), reference price. Heavy-tailed: charts use a log scale; compare within a tier | derived |
| Composite score (UI only) | `w_q·norm(quality) + w_p·norm(1/blended) + w_s·norm(throughput)`, min-max normalised within the current filter; default weights 0.5 / 0.3 / 0.2, user-adjustable | derived |
| **Usage share** | model (or vendor) `total_tokens` ÷ sum of all rows that day incl. `other` | third-party, derived share |
| Price change | any day where input or output price differs from the previous snapshot → `price_change` event | derived |
| **Model status** | retire date = earliest **firm** official shutdown (tentative "not sooner than" dates excluded) or OpenRouter `expiration_date`, whichever is first (a manual override wins). `retired` if that date has passed or the model left OpenRouter; `retiring` if within 30 days; else `active`. Retired models are hidden by default; retiring ones carry "closes <date>" after the name | official, derived |
| Sentiment score | mean of polarity (+1 / 0 / −1, `mixed` = 0) over comments in the window, per vendor (and per aspect) | derived (ML) |

Always show composite scores **together with** the per-dimension values. Never show the composite alone.

---

## 8. News & sentiment pipeline

### 8.1 Matching news/comments to vendors
Keywords alone are ambiguous (Gemini = crypto exchange/zodiac, Claude = person's name, GPT = partition table). A match requires **a vendor keyword AND a context term** in the title/text:

| Vendor | Keywords | Context terms (at least one) |
|---|---|---|
| OpenAI | GPT, ChatGPT, OpenAI, Codex | OpenAI, model, AI, LLM, API |
| Anthropic | Claude, Anthropic | Anthropic, model, AI, LLM, API, Opus, Sonnet, Haiku |
| Google | Gemini | Google, DeepMind, model, AI, LLM |
| DeepSeek | DeepSeek | (keyword alone is enough) |
| xAI | Grok, xAI | xAI, Musk, model, AI, LLM |

Stored in `core.dim_vendor` so they can be changed without code.

### 8.2 Sentiment labels (two separate dimensions)
- **Polarity:** `positive` / `negative` / `neutral` / `mixed`
- **Intent:** `comparison` / `recommendation` / `complaint` / `question` / `informational`

Do not merge them into one label set.

### 8.3 Aspects (ABSA)
`coding`, `reasoning`, `writing`, `accuracy`, `speed`, `pricing`, `reliability`, `context_window`, `user_experience`, `multimodal`. A comment may produce several (vendor, aspect, polarity) rows.

### 8.4 Models and evaluation
- Start with pretrained open models run locally in the worker (sentence-level sentiment + zero-shot/aspect classification). **No paid LLM calls by default.**
- Build a hand-labelled evaluation set of ~300 HN comments (`data/labels/comments_labelled.csv`) and report precision / recall / F1 per class and per aspect in `reports/sentiment_eval.md`. Re-run after any model change; store `model_version` with every prediction.
- **Current model `v0.1`** (`worker/jobs/sentiment.py`): polarity = `cardiffnlp/twitter-roberta-base-sentiment-latest` on the whole comment and on each clause that mentions an aspect (clauses split at *but / however / although …*); overall = `mixed` when confident positive and negative clauses coexist; aspects = keyword lexicon; intent = rules; vendor = keywords in the comment, else the story's single vendor. Weights cached in `data/hf_cache/` (git-ignored). Pipeline step `sentiment` (n8n node `06b Sentiment`) scores only comments not yet in `core.dim_comment`; `--rescore` after a model change.

### 8.5 Labelling app (separate from the dashboard)
The dashboard is read-only; labelling needs writes, so it is a **separate Streamlit app** in `labeler/`:
- `python scripts/export_label_sample.py` draws a fixed, seeded sample of 300 comments → `data/labels/comments_to_label.csv` (60 per vendor where available; small vendors keep all, the rest fills to 300).
- `cd labeler && streamlit run labeler_app.py --server.port 8502` — one comment at a time: relevant to the vendor?, polarity, intent, aspects, note. Saves to `data/labels/comments_labelled.csv` (atomic write; re-labelling replaces the row). It never touches Supabase/MongoDB and never shows model predictions (no anchoring).
- `python scripts/eval_sentiment.py` → `reports/sentiment_eval.md` + `ops.model_eval`. The dashboard shows "Not evaluated yet" until a result exists for the model version in use; DQ `sentiment_model_eval` warns likewise.

---

## 9. Analyses (the "ML / analytics" layer)

1. **ABSA + sentiment trend** — per vendor and aspect over time (§8).
2. **Value score + sensitivity** — interactive weights; show how rankings change as weights move (rank stability chart).
3. **Event study** — sentiment and usage share N days before vs after a `released` / `price_change` event (interrupted time series, with confidence intervals).
4. **Recommender (optional)** — user enters task type, monthly budget and speed need → top 3 models with the numbers that justify each.

---

## 10. n8n workflows (Docker)

`docker-compose.yml` (summary):
```yaml
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n:<pinned-version>
    restart: unless-stopped
    ports: ["5678:5678"]
    environment:
      - GENERIC_TIMEZONE=Asia/Bangkok
      - TZ=Asia/Bangkok
      - N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}
    volumes: [n8n_data:/home/node/.n8n]
  worker:
    build: ./worker            # Python 3.11, runs ML/matching jobs on a schedule
    env_file: .env
    restart: unless-stopped
volumes:
  n8n_data:
```

| Workflow | Schedule | Does |
|---|---|---|
| `00_master` | daily 10:00 | Schedule Trigger → `POST /start` (one run_id) → one HTTP node per step (`POST http://worker:8000/run {run_id, only}`), branches as below → Merge → 09 DQ → Stop-and-Error alert if any DQ fail. Logic stays in Python (`worker/run_daily.py`); n8n only schedules, tracks and retries |
| `01_or_models` | via master | `/models` → raw → `fact_model_daily`, new/disappeared models, price changes |
| `02_or_endpoints` | via master | `/endpoints` for active models of the 5 vendors → `fact_endpoint_daily` |
| `03_or_usage` | via master | rankings-daily for the last 3 days (self-heals gaps) → `fact_usage_daily` |
| `04_or_benchmarks` | via master | `/benchmarks` → `fact_benchmark_snapshot` |
| `05_or_tasks` | via master | classifications/task → `fact_task_share` |
| `06_hn` | via master | story search per vendor keyword (last 24 h) → stories; comments for matched stories → `raw_comments` |
| `06b_sentiment` | via master, after 06 | score new comments (§8.4) → `core.dim_comment`, `core.fact_comment_sentiment` |
| `07_rss_gdelt` | via master | OpenAI + DeepMind RSS; GDELT per vendor with **≥5 s wait** between calls |
| `08_lifecycle_pages` | via master | fetch 5 pages → hash → parse only if changed → `fact_lifecycle_event` |
| `09_dq_checks` | via master | §11 checks → `ops.dq_check_result` |
| worker job | daily 10:45 | vendor matching, sentiment/ABSA on new comments, alias mapping, value scores |

Every external call: retry 3× with exponential backoff, honour `Retry-After`. A failed source marks the run `partial` and never blocks the others. Export workflows to `n8n/workflows/*.json` after each change and check exports for secrets before committing.

---

## 11. Data quality checks

| Check | Fail / Warn |
|---|---|
| OpenRouter key returns 401/403 (expired or revoked) | fail + alert |
| `/models` returned < 80% of yesterday's model count for the 5 vendors | fail (do not mark models as removed) |
| Price changed by > 50% in one day | warn (verify before showing as `price_change`) |
| No duplicate `(model_id, snapshot_date)` | fail |
| Usage rows for today < 40 | warn |
| Lifecycle page parsed 0 rows or header changed | fail (keep previous data) |
| Benchmark rows with no `dim_model` mapping | warn (list for review) |
| Raw document count = rows loaded for the run | warn |
| Sentiment model version changed without a new eval report | warn |
| Model shut down by its vendor but still listed on OpenRouter, or shutting down within 14 days (`model_status`) | warn (listed; already hidden in the UI) |

---

## 12. Streamlit app

Built with the official Streamlit agent skill (`streamlit skills` installs it into `.claude/skills/`; add those symlinks to `.gitignore`). Follow it: native widgets, theme in `.streamlit/config.toml`, no custom CSS, Material icons, `alt=` on charts and tables. One agreed exception: the animated radar logo on Overview (`ui.radar_header`, scoped `amt-*` classes, respects reduced motion).

Pages / tabs (prototype in `app/`):
1. **Overview** — KPI cards, flagship comparison table (price, quality, speed, value, usage share, sentiment, 30-day price sparkline).
2. **Price & value** — full lineup per vendor (normal / batch toggle), quality vs price scatter (log price), value ranking, weight sliders + rank-stability chart.
3. **Usage** — vendor token share over time, task-type shares.
4. **News & sentiment** — sentiment by vendor and aspect, trend, news feed with source links.
5. **Lifecycle** — release timeline, upcoming retirements (official), models removed from OpenRouter.
6. **Pipeline health** — freshness per source, rows per day, DQ results.

Rules: read only `mart.*` via `st.connection(..., type="sql")` with the `streamlit_ro` user; cache with `ttl=3600`; show "last updated" and the OpenRouter attribution line; mark every value as official / third-party / derived; display times in Asia/Bangkok; never call external APIs from Streamlit.

---

## 13. Configuration

`.env` (never committed; `.env.example` has empty values):
```dotenv
OPENROUTER_API_KEY=          # alias accepted: ROUTER_PASSWORD
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority&appName=ai-model-tracker
MONGODB_DB=ai_model_tracker
SUPABASE_DB_HOST=aws-0-<region>.pooler.supabase.com
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER_WRITER=postgres.<project-ref>
SUPABASE_DB_PASSWORD_WRITER=
SUPABASE_DB_USER_READER=streamlit_ro.<project-ref>
SUPABASE_DB_PASSWORD_READER=
SUPABASE_SSLMODE=require
N8N_ENCRYPTION_KEY=          # generate once, never change
# optional
OPENAI_API_KEY=
YOUTUBE_API_KEY=
```
Streamlit reads `app/.streamlit/secrets.toml` (git-ignored).

### Secrets hygiene
- Never paste keys into code files, chat, screenshots or terminal output that will be shared.
- If a key is exposed anywhere, revoke it in the provider dashboard and create a new one immediately.
- OpenRouter key: credit limit $1, expiry set; record the expiry date in `ops` and warn 14 days before.

---

## 14. Repository layout

```
ai-model-tracker/
├── CLAUDE.md
├── README.md
├── .env.example
├── docker-compose.yml
├── n8n/workflows/            # exported JSON, no credentials
├── mongodb/init_indexes.js
├── supabase/migrations/      # 001_schemas.sql, 002_core.sql, ...
├── seed/                     # dim_vendor.csv, model_tiers.csv, model_overrides.csv
├── worker/                   # Dockerfile, jobs/ (matching, sentiment, scores)
├── data/labels/              # hand-labelled evaluation set
├── reports/                  # sentiment_eval.md, analyses
├── app/                      # Streamlit dashboard (streamlit_app.py, app_pages/, lib/) — read-only
│   └── .streamlit/config.toml
├── labeler/                  # Streamlit labelling tool (labeler_app.py) — writes data/labels/ only
├── scripts/test_keys.py      # source validation (re-run when a source changes)
└── tests/
```

---

## 15. Conventions for Claude

- Solo project: keep changes small and reviewable; explain any schema or metric change in the commit message.
- Schema changes only via a **new numbered migration**; never edit an applied one.
- All loads idempotent (upsert on natural keys).
- Metric definitions live in §7 and the SQL views — change both together, and the README.
- Respect API limits (§4); set `User-Agent: ai-model-tracker/<version>` on every request.
- Never call paid model endpoints. Never add a new data source, vendor or paid service without asking.
- After touching ingestion: run `python scripts/test_keys.py`. After touching transformations: run the DQ checks. After touching Streamlit: verify with the Streamlit skill's agent API before reporting done.
- Label sample/mock data clearly in the UI until real data replaces it.

---

## 16. Commands

```bash
docker compose up -d                       # n8n at http://localhost:5678 + worker
docker compose logs -f n8n
mongosh "$MONGODB_URI" mongodb/init_indexes.js
psql "host=$SUPABASE_DB_HOST port=5432 dbname=postgres user=$SUPABASE_DB_USER_WRITER sslmode=require" \
     -f supabase/migrations/001_schemas.sql
python scripts/test_keys.py                # validate all sources
cd app && streamlit run streamlit_app.py   # dashboard at http://localhost:8501
python scripts/export_label_sample.py     # draw the 300-comment labelling sample (once)
cd labeler && streamlit run labeler_app.py --server.port 8502   # labelling tool
python scripts/eval_sentiment.py          # evaluate the sentiment model → reports/sentiment_eval.md
```
