-- 007_reference_price.sql — price used for value = reference price, not OpenRouter's headline price
--
-- Why: OpenRouter's /models headline price follows whichever provider it routes to, often the cheapest
-- (sometimes a quantised fp4/fp8 host). Quality is measured on the vendor's full model, so dividing it by the
-- cheapest host's price inflated value (DeepSeek V4.1 Flash 1017: $0.036 in vs first-party $0.15).
-- Reference price (CLAUDE.md §7):
--   1. the vendor's own standard endpoint on OpenRouter (provider name starts with the vendor name,
--      tag without a /variant such as flex, priority, fast); Google prefers AI Studio over Vertex;
--   2. else the median across all providers of the latest endpoint snapshot;
--   3. else OpenRouter's headline price.
-- Headline prices stay available as list_* columns.

CREATE OR REPLACE VIEW mart.v_price_reference AS
WITH latest AS (
    SELECT e.*, m.vendor_id
    FROM core.fact_endpoint_daily e
    JOIN core.dim_model m USING (model_id)
    WHERE e.snapshot_date = (SELECT max(snapshot_date) FROM core.fact_endpoint_daily x WHERE x.model_id = e.model_id)
      AND e.input_price_per_m IS NOT NULL AND e.output_price_per_m IS NOT NULL
), first_party AS (
    SELECT DISTINCT ON (l.model_id) l.model_id, l.input_price_per_m, l.output_price_per_m, l.provider_name
    FROM latest l
    JOIN core.dim_vendor v USING (vendor_id)
    WHERE l.provider_name ILIKE v.name || '%'
      AND l.provider_name !~ '\[[^]]*/'                       -- standard tier only (no /flex, /fast, …)
    ORDER BY l.model_id, (l.provider_name ILIKE '%AI Studio%') DESC, l.input_price_per_m
), med AS (
    SELECT model_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY input_price_per_m)  AS input_price_per_m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY output_price_per_m) AS output_price_per_m,
           count(*) AS n_providers
    FROM latest GROUP BY model_id
)
SELECT med.model_id,
       COALESCE(fp.input_price_per_m,  med.input_price_per_m)::numeric(14,6)  AS ref_input_price_per_m,
       COALESCE(fp.output_price_per_m, med.output_price_per_m)::numeric(14,6) AS ref_output_price_per_m,
       CASE WHEN fp.model_id IS NOT NULL THEN 'first_party' ELSE 'median_providers' END AS price_basis,
       fp.provider_name AS price_provider,
       med.n_providers
FROM med
LEFT JOIN first_party fp USING (model_id);

-- Same columns, same order as 004 (CREATE OR REPLACE VIEW rule); new columns appended.
CREATE OR REPLACE VIEW mart.v_value_score AS
WITH latest AS (
    SELECT DISTINCT ON (model_id) * FROM mart.v_price_history ORDER BY model_id, snapshot_date DESC
), priced AS (
    SELECT l.*, r.price_basis, r.price_provider,
           COALESCE((3 * r.ref_input_price_per_m + r.ref_output_price_per_m) / 4, l.blended_price_per_m) AS ref_blended
    FROM latest l LEFT JOIN mart.v_price_reference r USING (model_id)
)
SELECT p.model_id, p.model_name, p.vendor_id, p.tier, p.output_type, p.snapshot_date,
       q.intelligence_index AS quality, q.quality_source,
       p.ref_blended AS blended_price_per_m, p.batch_blended_price_per_m,
       q.intelligence_index / NULLIF(p.ref_blended, 0)                AS value_score,
       q.intelligence_index / NULLIF(p.batch_blended_price_per_m, 0) AS batch_value_score,
       p.blended_price_per_m AS list_blended_price_per_m,
       COALESCE(p.price_basis, 'list') AS price_basis
FROM priced p
LEFT JOIN mart.v_quality_current q USING (model_id);

CREATE OR REPLACE VIEW mart.v_model_current AS
WITH latest AS (
    SELECT DISTINCT ON (model_id) * FROM mart.v_price_history ORDER BY model_id, snapshot_date DESC
), spark AS (
    SELECT model_id, array_agg(blended_price_per_m ORDER BY snapshot_date) AS blended_price_30d
    FROM mart.v_price_history
    WHERE snapshot_date > (SELECT max(snapshot_date) FROM core.fact_model_daily) - 30
    GROUP BY model_id
), ref AS (
    SELECT r.*, (3 * r.ref_input_price_per_m + r.ref_output_price_per_m) / 4 AS ref_blended
    FROM mart.v_price_reference r
)
SELECT m.model_id, m.name AS model_name, m.vendor_id, v.name AS vendor_name, v.color_hex,
       m.tier, m.output_type, m.is_active, m.first_seen_date, m.last_seen_date, m.created_at_source,
       f.context_length, f.max_completion_tokens, f.expiration_date,
       l.snapshot_date,
       COALESCE(ref.ref_input_price_per_m, l.input_price_per_m)   AS input_price_per_m,
       COALESCE(ref.ref_output_price_per_m, l.output_price_per_m) AS output_price_per_m,
       l.cache_read_price_per_m,
       l.batch_input_price_per_m, l.batch_output_price_per_m,
       COALESCE(ref.ref_blended, l.blended_price_per_m) AS blended_price_per_m,
       l.batch_blended_price_per_m,
       q.intelligence_index AS quality, q.coding_index, q.agentic_index, q.quality_source, q.quality_as_of,
       q.intelligence_index / NULLIF(COALESCE(ref.ref_blended, l.blended_price_per_m), 0) AS value_score,
       s.throughput_p50, s.latency_p50, s.latency_stability, s.uptime_last_1d, s.providers,
       u.token_share_7d,
       sv.sentiment_score_30d AS vendor_sentiment_30d, sv.n_mentions_30d AS vendor_mentions_30d,
       sp.blended_price_30d,
       l.input_price_per_m  AS list_input_price_per_m,
       l.output_price_per_m AS list_output_price_per_m,
       l.blended_price_per_m AS list_blended_price_per_m,
       COALESCE(ref.price_basis, 'list') AS price_basis,
       ref.price_provider
FROM core.dim_model m
JOIN core.dim_vendor v USING (vendor_id)
LEFT JOIN latest l USING (model_id)
LEFT JOIN core.fact_model_daily f ON f.model_id = m.model_id AND f.snapshot_date = l.snapshot_date
LEFT JOIN mart.v_quality_current q ON q.model_id = m.model_id
LEFT JOIN mart.v_speed_current s ON s.model_id = m.model_id
LEFT JOIN mart.v_usage_model_7d u ON u.model_id = m.model_id
LEFT JOIN mart.v_sentiment_vendor_30d sv ON sv.vendor_id = m.vendor_id
LEFT JOIN spark sp ON sp.model_id = m.model_id
LEFT JOIN ref ON ref.model_id = m.model_id;

GRANT SELECT ON mart.v_price_reference TO streamlit_ro;
