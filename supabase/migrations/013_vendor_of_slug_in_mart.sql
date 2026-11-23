-- 013_vendor_of_slug_in_mart.sql — the prefix→vendor helper must live in mart.
-- Views call it with the caller's schema privileges; streamlit_ro has USAGE on mart/ops only (never core),
-- so core.vendor_of_slug (012) made the usage views fail for the dashboard.

CREATE OR REPLACE FUNCTION mart.vendor_of_slug(slug text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = core, pg_temp AS $$
    SELECT v.vendor_id FROM core.dim_vendor v, unnest(v.openrouter_prefixes) p
    WHERE slug LIKE p || '%' ORDER BY length(p) DESC LIMIT 1
$$;
REVOKE ALL ON FUNCTION mart.vendor_of_slug(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mart.vendor_of_slug(text) TO streamlit_ro;

CREATE OR REPLACE VIEW mart.v_usage_vendor_daily AS
WITH d AS (
    SELECT u.usage_date,
           COALESCE(m.vendor_id, CASE WHEN u.is_other_row THEN NULL ELSE mart.vendor_of_slug(u.model_permaslug) END,
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
WHERE u.model_id IS NULL AND (u.is_other_row OR mart.vendor_of_slug(u.model_permaslug) IS NULL)
GROUP BY u.model_permaslug, u.is_other_row;

DROP FUNCTION IF EXISTS core.vendor_of_slug(text);
