-- 014_usage_quarterly.sql — quarterly vendor share of OpenRouter tokens, for the Usage page summary.
CREATE OR REPLACE VIEW mart.v_usage_vendor_quarterly AS
WITH q AS (
    SELECT date_trunc('quarter', usage_date)::date AS quarter_start, vendor_id,
           sum(total_tokens) AS total_tokens, count(DISTINCT usage_date) AS days
    FROM mart.v_usage_vendor_daily
    GROUP BY 1, 2
)
SELECT quarter_start,
       to_char(quarter_start, 'YYYY') || ' Q' || to_char(quarter_start, 'Q') AS quarter,
       vendor_id, total_tokens,
       total_tokens::numeric / NULLIF(sum(total_tokens) OVER (PARTITION BY quarter_start), 0) AS token_share,
       max(days) OVER (PARTITION BY quarter_start) AS days_in_quarter
FROM q;

GRANT SELECT ON mart.v_usage_vendor_quarterly TO streamlit_ro;
