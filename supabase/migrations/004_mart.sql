-- 004_mart.sql — read models for Streamlit (business logic lives here, CLAUDE.md §3, §7)
-- Metric definitions: §7. Change this file, §7 and README together.

CREATE OR REPLACE VIEW mart.v_vendor AS
SELECT vendor_id, name AS vendor_name, color_hex FROM core.dim_vendor;

-- Latest price snapshot per model (+ blended price, §7)
CREATE OR REPLACE VIEW mart.v_price_history AS
SELECT f.model_id, m.name AS model_name, m.vendor_id, m.tier, m.output_type, f.snapshot_date,
       f.input_price_per_m, f.output_price_per_m, f.cache_read_price_per_m,
       f.batch_input_price_per_m, f.batch_output_price_per_m,
       (3 * f.input_price_per_m + f.output_price_per_m) / 4 AS blended_price_per_m,
       (3 * f.batch_input_price_per_m + f.batch_output_price_per_m) / 4 AS batch_blended_price_per_m
FROM core.fact_model_daily f
JOIN core.dim_model m USING (model_id);

-- Speed = median across providers of p50, latest endpoint snapshot per model (§7)
CREATE OR REPLACE VIEW mart.v_speed_current AS
WITH latest AS (
    SELECT model_id, max(snapshot_date) AS snapshot_date FROM core.fact_endpoint_daily GROUP BY model_id
)
SELECT e.model_id, e.snapshot_date,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.throughput_p50) AS throughput_p50,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.latency_p50)    AS latency_p50,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.latency_p99)    AS latency_p99,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.latency_p99)
         / NULLIF(percentile_cont(0.5) WITHIN GROUP (ORDER BY e.latency_p50), 0) AS latency_stability,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.uptime_last_1d) AS uptime_last_1d,
       count(*) AS providers,
       count(e.throughput_p50) AS providers_with_speed
FROM core.fact_endpoint_daily e
JOIN latest l USING (model_id, snapshot_date)
GROUP BY e.model_id, e.snapshot_date;

-- Latest quality per model. Prefer the /benchmarks API source over the copy embedded in /models.
CREATE OR REPLACE VIEW mart.v_quality_current AS
SELECT DISTINCT ON (model_id)
       model_id, snapshot_date AS quality_as_of, source AS quality_source,
       intelligence_index, coding_index, agentic_index, citation
FROM core.fact_benchmark_snapshot
WHERE model_id IS NOT NULL AND intelligence_index IS NOT NULL
ORDER BY model_id, snapshot_date DESC, (source = 'openrouter_benchmarks') DESC;

-- Value score = quality / blended price (points per USD per 1M tokens), §7
CREATE OR REPLACE VIEW mart.v_value_score AS
WITH latest AS (
    SELECT DISTINCT ON (model_id) * FROM mart.v_price_history ORDER BY model_id, snapshot_date DESC
)
SELECT l.model_id, l.model_name, l.vendor_id, l.tier, l.output_type, l.snapshot_date,
       q.intelligence_index AS quality, q.quality_source,
       l.blended_price_per_m, l.batch_blended_price_per_m,
       q.intelligence_index / NULLIF(l.blended_price_per_m, 0)       AS value_score,
       q.intelligence_index / NULLIF(l.batch_blended_price_per_m, 0) AS batch_value_score
FROM latest l
LEFT JOIN mart.v_quality_current q USING (model_id);

-- Usage share = tokens / all tokens that day incl. the "other" row (OpenRouter traffic only), §7
CREATE OR REPLACE VIEW mart.v_usage_vendor_daily AS
WITH d AS (
    SELECT u.usage_date, COALESCE(m.vendor_id, 'other') AS vendor_id, sum(u.total_tokens) AS total_tokens
    FROM core.fact_usage_daily u
    LEFT JOIN core.dim_model m USING (model_id)
    GROUP BY 1, 2
)
SELECT usage_date, vendor_id, total_tokens,
       total_tokens::numeric / NULLIF(sum(total_tokens) OVER (PARTITION BY usage_date), 0) AS token_share
FROM d;

CREATE OR REPLACE VIEW mart.v_usage_model_7d AS
WITH win AS (
    SELECT * FROM core.fact_usage_daily
    WHERE usage_date > (SELECT max(usage_date) FROM core.fact_usage_daily) - 7
)
SELECT model_id, sum(total_tokens) AS tokens_7d,
       sum(total_tokens)::numeric / NULLIF((SELECT sum(total_tokens) FROM win), 0) AS token_share_7d
FROM win WHERE model_id IS NOT NULL
GROUP BY model_id;

-- Sentiment score = mean polarity (+1/0/-1, mixed = 0) per vendor/aspect/week, §7
CREATE OR REPLACE VIEW mart.v_sentiment_weekly AS
SELECT date_trunc('week', (s.published_at AT TIME ZONE 'Asia/Bangkok'))::date AS week_start,
       s.vendor_id, s.aspect,
       avg(CASE s.polarity WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 ELSE 0 END)::numeric AS sentiment_score,
       count(*) AS n_mentions,
       count(*) FILTER (WHERE s.polarity = 'positive') AS n_positive,
       count(*) FILTER (WHERE s.polarity = 'negative') AS n_negative,
       max(s.model_version) AS model_version
FROM core.fact_comment_sentiment s
WHERE s.published_at IS NOT NULL
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW mart.v_sentiment_vendor_30d AS
SELECT vendor_id,
       avg(CASE polarity WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 ELSE 0 END)::numeric AS sentiment_score_30d,
       count(*) AS n_mentions_30d
FROM core.fact_comment_sentiment
WHERE aspect = 'overall' AND published_at > now() - interval '30 days'
GROUP BY vendor_id;

-- One row per model: the Overview table
CREATE OR REPLACE VIEW mart.v_model_current AS
WITH latest AS (
    SELECT DISTINCT ON (model_id) * FROM mart.v_price_history ORDER BY model_id, snapshot_date DESC
), spark AS (
    SELECT model_id, array_agg(blended_price_per_m ORDER BY snapshot_date) AS blended_price_30d
    FROM mart.v_price_history
    WHERE snapshot_date > (SELECT max(snapshot_date) FROM core.fact_model_daily) - 30
    GROUP BY model_id
)
SELECT m.model_id, m.name AS model_name, m.vendor_id, v.name AS vendor_name, v.color_hex,
       m.tier, m.output_type, m.is_active, m.first_seen_date, m.last_seen_date, m.created_at_source,
       f.context_length, f.max_completion_tokens, f.expiration_date,
       l.snapshot_date, l.input_price_per_m, l.output_price_per_m, l.cache_read_price_per_m,
       l.batch_input_price_per_m, l.batch_output_price_per_m,
       l.blended_price_per_m, l.batch_blended_price_per_m,
       q.intelligence_index AS quality, q.coding_index, q.agentic_index, q.quality_source, q.quality_as_of,
       q.intelligence_index / NULLIF(l.blended_price_per_m, 0) AS value_score,
       s.throughput_p50, s.latency_p50, s.latency_stability, s.uptime_last_1d, s.providers,
       u.token_share_7d,
       sv.sentiment_score_30d AS vendor_sentiment_30d, sv.n_mentions_30d AS vendor_mentions_30d,
       sp.blended_price_30d
FROM core.dim_model m
JOIN core.dim_vendor v USING (vendor_id)
LEFT JOIN latest l USING (model_id)
LEFT JOIN core.fact_model_daily f ON f.model_id = m.model_id AND f.snapshot_date = l.snapshot_date
LEFT JOIN mart.v_quality_current q ON q.model_id = m.model_id
LEFT JOIN mart.v_speed_current s ON s.model_id = m.model_id
LEFT JOIN mart.v_usage_model_7d u ON u.model_id = m.model_id
LEFT JOIN mart.v_sentiment_vendor_30d sv ON sv.vendor_id = m.vendor_id
LEFT JOIN spark sp ON sp.model_id = m.model_id;

CREATE OR REPLACE VIEW mart.v_task_share_latest AS
SELECT * FROM core.fact_task_share
WHERE as_of = (SELECT max(as_of) FROM core.fact_task_share);

CREATE OR REPLACE VIEW mart.v_news_latest AS
SELECT n.news_id, n.source, n.title, n.url, n.domain, n.published_at, n.points, n.num_comments,
       array_agg(b.vendor_id ORDER BY b.vendor_id) AS vendor_ids
FROM core.fact_news_item n
JOIN core.bridge_news_vendor b USING (news_id)
WHERE n.published_at > now() - interval '90 days'
GROUP BY n.news_id;

CREATE OR REPLACE VIEW mart.v_lifecycle_timeline AS
SELECT e.vendor_id, v.name AS vendor_name, v.color_hex, e.model_api_name, e.model_id, m.name AS model_name,
       e.event_type, e.event_date, e.announced_date, e.replacement, e.detail, e.is_tentative,
       e.source_url, e.source_type
FROM core.fact_lifecycle_event e
JOIN core.dim_vendor v USING (vendor_id)
LEFT JOIN core.dim_model m ON m.model_id = e.model_id;

CREATE OR REPLACE VIEW mart.v_pipeline_health AS
SELECT DISTINCT ON (source)
       source, run_id AS last_run_id, started_at AS last_started_at, finished_at AS last_finished_at,
       status AS last_status, rows_loaded AS last_rows_loaded, error AS last_error,
       (SELECT max(finished_at) FROM ops.pipeline_run_log p2
         WHERE p2.source = p.source AND p2.status IN ('success','partial')) AS last_success_at
FROM ops.pipeline_run_log p
ORDER BY source, started_at DESC;

CREATE OR REPLACE VIEW mart.v_pipeline_rows_daily AS
SELECT (started_at AT TIME ZONE 'Asia/Bangkok')::date AS run_date, source,
       sum(rows_loaded) AS rows_loaded,
       bool_or(status = 'failed') AS any_failed
FROM ops.pipeline_run_log
GROUP BY 1, 2;

CREATE OR REPLACE VIEW mart.v_dq_latest AS
SELECT d.* FROM ops.dq_check_result d
WHERE d.run_id = (SELECT run_id FROM ops.dq_check_result ORDER BY checked_at DESC LIMIT 1);
