-- 009_usage_other_models.sql — what is inside "Other models" on the Usage page.
-- Rankings-daily lists the top 50 models per day plus one "other" row (everything outside the top 50).
-- Rows with no dim_model match are models from companies we do not track (Qwen, Meta, Tencent, …).

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
WHERE u.model_id IS NULL
GROUP BY u.model_permaslug, u.is_other_row;

GRANT SELECT ON mart.v_usage_other_models TO streamlit_ro;
