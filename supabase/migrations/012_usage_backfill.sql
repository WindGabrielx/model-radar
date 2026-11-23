-- 012_usage_backfill.sql — usage history back to 2025-01-01 (scripts/backfill_usage.py)
--
-- backfilled = true marks rows loaded by the one-off backfill, so the Usage page can switch between
-- "full history" and "since the pipeline started".
-- Vendor attribution falls back to the OpenRouter prefix (§5): retired models are not in dim_model, and
-- without this their tokens would land in "other" and distort historical vendor shares.

ALTER TABLE core.fact_usage_daily ADD COLUMN IF NOT EXISTS backfilled boolean NOT NULL DEFAULT false;

CREATE OR REPLACE FUNCTION core.vendor_of_slug(slug text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT v.vendor_id FROM core.dim_vendor v, unnest(v.openrouter_prefixes) p
    WHERE slug LIKE p || '%' ORDER BY length(p) DESC LIMIT 1
$$;

-- same columns and order as 004, backfilled appended
CREATE OR REPLACE VIEW mart.v_usage_vendor_daily AS
WITH d AS (
    SELECT u.usage_date,
           COALESCE(m.vendor_id, CASE WHEN u.is_other_row THEN NULL ELSE core.vendor_of_slug(u.model_permaslug) END,
                    'other') AS vendor_id,
           sum(u.total_tokens) AS total_tokens,
           bool_or(u.backfilled) AS backfilled
    FROM core.fact_usage_daily u
    LEFT JOIN core.dim_model m USING (model_id)
    GROUP BY 1, 2
)
SELECT usage_date, vendor_id, total_tokens,
       total_tokens::numeric / NULLIF(sum(total_tokens) OVER (PARTITION BY usage_date), 0) AS token_share,
       backfilled
FROM d;

-- "Other models" = rows that belong to none of the five vendors (by model_id or by prefix)
CREATE OR REPLACE VIEW mart.v_usage_other_models AS
WITH win AS (
    SELECT * FROM core.fact_usage_daily
    WHERE usage_date > (SELECT max(usage_date) FROM core.fact_usage_daily) - 7
), tot AS (SELECT sum(total_tokens) AS t FROM win)
SELECT u.model_permaslug,
       CASE WHEN u.is_other_row THEN 'other' ELSE split_part(u.model_permaslug, '/', 1) END AS author,
       u.is_other_row,
       sum(u.total_tokens) AS tokens_7d,
       sum(u.total_tokens)::numeric / NULLIF((SELECT t FROM tot), 0) AS token_share_7d,
       count(DISTINCT u.usage_date) AS days_in_top50
FROM win u
WHERE u.model_id IS NULL AND (u.is_other_row OR core.vendor_of_slug(u.model_permaslug) IS NULL)
GROUP BY u.model_permaslug, u.is_other_row;

GRANT EXECUTE ON FUNCTION core.vendor_of_slug(text) TO streamlit_ro;
