-- 011_sentiment.sql — sentiment / ABSA outputs for the News & sentiment page (CLAUDE.md §8)

-- Comment text excerpt + link, so Streamlit can show examples without reading MongoDB.
CREATE TABLE IF NOT EXISTS core.dim_comment (
    comment_id   text PRIMARY KEY,               -- HN objectID
    story_id     text,
    story_title  text,
    published_at timestamptz,
    excerpt      text,                            -- first ~400 chars, HTML stripped
    url          text,                            -- https://news.ycombinator.com/item?id=<comment_id>
    loaded_at    timestamptz NOT NULL DEFAULT now()
);

-- Evaluation results against the hand-labelled set (written by scripts/eval_sentiment.py).
CREATE TABLE IF NOT EXISTS ops.model_eval (
    model_version text NOT NULL,
    evaluated_at  timestamptz NOT NULL DEFAULT now(),
    task          text NOT NULL,                  -- polarity | intent | aspect
    label         text NOT NULL,                  -- class name, or 'macro' / 'accuracy'
    precision     numeric, recall numeric, f1 numeric, support integer,
    n_labelled    integer NOT NULL,
    PRIMARY KEY (model_version, task, label)
);

CREATE OR REPLACE VIEW mart.v_model_eval_latest AS
SELECT * FROM ops.model_eval
WHERE model_version = (SELECT model_version FROM core.fact_comment_sentiment ORDER BY loaded_at DESC LIMIT 1);

-- Vendor score: last 30 days vs the 30 days before (overall polarity, mixed = 0)
CREATE OR REPLACE VIEW mart.v_sentiment_vendor_window AS
WITH s AS (
    SELECT vendor_id, published_at,
           CASE polarity WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 ELSE 0 END AS v
    FROM core.fact_comment_sentiment WHERE aspect = 'overall'
)
SELECT v.vendor_id, v.name AS vendor_name,
       avg(s.v) FILTER (WHERE s.published_at > now() - interval '30 days')::numeric AS score_30d,
       count(*)  FILTER (WHERE s.published_at > now() - interval '30 days') AS n_30d,
       avg(s.v) FILTER (WHERE s.published_at <= now() - interval '30 days'
                          AND s.published_at > now() - interval '60 days')::numeric AS score_prev_30d,
       count(*)  FILTER (WHERE s.published_at <= now() - interval '30 days'
                          AND s.published_at > now() - interval '60 days') AS n_prev_30d,
       max(s.published_at) AS latest_comment_at
FROM core.dim_vendor v LEFT JOIN s USING (vendor_id)
GROUP BY v.vendor_id, v.name;

-- Positive / negative counts per vendor and aspect, last 30 days
CREATE OR REPLACE VIEW mart.v_sentiment_aspect_30d AS
SELECT s.vendor_id, v.name AS vendor_name, s.aspect,
       avg(CASE s.polarity WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 ELSE 0 END)::numeric AS score,
       count(*) AS n,
       count(*) FILTER (WHERE s.polarity = 'positive') AS n_positive,
       count(*) FILTER (WHERE s.polarity = 'negative') AS n_negative
FROM core.fact_comment_sentiment s JOIN core.dim_vendor v USING (vendor_id)
WHERE s.aspect <> 'overall' AND s.published_at > now() - interval '30 days'
GROUP BY 1, 2, 3;

-- Example comments: overall label + the aspects found in the same comment
CREATE OR REPLACE VIEW mart.v_sentiment_examples AS
SELECT o.comment_id, o.vendor_id, v.name AS vendor_name, o.polarity, o.intent, o.confidence, o.model_version,
       o.published_at, c.excerpt, c.url, c.story_title,
       (SELECT array_agg(a.aspect || ':' || a.polarity ORDER BY a.aspect)
          FROM core.fact_comment_sentiment a
         WHERE a.comment_id = o.comment_id AND a.vendor_id = o.vendor_id AND a.aspect <> 'overall') AS aspects
FROM core.fact_comment_sentiment o
JOIN core.dim_vendor v USING (vendor_id)
JOIN core.dim_comment c USING (comment_id)
WHERE o.aspect = 'overall' AND o.published_at > now() - interval '30 days';

-- Release markers for the trend chart
CREATE OR REPLACE VIEW mart.v_release_markers AS
SELECT e.vendor_id, v.name AS vendor_name, e.event_date, coalesce(m.name, e.model_api_name) AS label
FROM core.fact_lifecycle_event e JOIN core.dim_vendor v USING (vendor_id)
LEFT JOIN core.dim_model m ON m.model_id = e.model_id
WHERE e.event_type IN ('released', 'first_seen_openrouter') AND e.event_date > now()::date - 120
  AND (m.tier = 'top' OR e.source_type = 'official');

GRANT SELECT ON mart.v_model_eval_latest, mart.v_sentiment_vendor_window, mart.v_sentiment_aspect_30d,
               mart.v_sentiment_examples, mart.v_release_markers TO streamlit_ro;
