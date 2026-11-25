-- 015_model_status.sql — one availability status per model, computed daily from official shutdown notices,
-- OpenRouter's own expiration_date and whether the model is still listed on OpenRouter.
-- Why: a model the vendor has already shut down can stay listed on OpenRouter for months; the dashboard must not
-- present it as usable. Business logic lives here (CLAUDE.md §3); the app only filters and labels.
--
--   active   — no firm shutdown date within the next 30 days
--   retiring — firm shutdown (official) or OpenRouter expiration within 30 days
--   retired  — shutdown date has passed, or the model disappeared from OpenRouter
-- Tentative dates ("not sooner than", "earliest possible") never retire a model; they stay on the Lifecycle page.

-- Hand overrides for cases the rules get wrong (seed/model_overrides.csv, loaded by scripts/migrate.py).
CREATE TABLE IF NOT EXISTS core.model_override (
    model_id     text PRIMARY KEY REFERENCES core.dim_model,
    status       text CHECK (status IN ('active', 'retiring', 'retired')),   -- forces the status when set
    retire_date  date,                                                     -- replaces the computed date when set
    replacement  text,
    note         text NOT NULL,                                            -- why the override exists
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW mart.v_model_status AS
WITH official AS (           -- earliest firm official shutdown per model
    SELECT DISTINCT ON (model_id) model_id, event_date, replacement, source_url
    FROM core.fact_lifecycle_event
    WHERE event_type = 'retired' AND source_type = 'official' AND NOT is_tentative AND model_id IS NOT NULL
    ORDER BY model_id, event_date
),
expiry AS (                  -- OpenRouter's own expiration_date on the latest snapshot
    SELECT DISTINCT ON (model_id) model_id, expiration_date
    FROM core.fact_model_daily
    ORDER BY model_id, snapshot_date DESC
),
base AS (
    SELECT m.model_id, m.is_active, m.last_seen_date,
           o.event_date AS official_date, o.replacement, o.source_url,
           e.expiration_date AS openrouter_date,
           ov.status AS override_status, ov.retire_date AS override_date, ov.replacement AS override_replacement,
           ov.note AS override_note
    FROM core.dim_model m
    LEFT JOIN official o USING (model_id)
    LEFT JOIN expiry e USING (model_id)
    LEFT JOIN core.model_override ov USING (model_id)
),
dated AS (
    SELECT b.*,
           coalesce(b.override_date, least(b.official_date, b.openrouter_date)) AS retire_date,
           CASE WHEN b.override_date IS NOT NULL THEN 'override'
                WHEN b.official_date IS NOT NULL
                     AND (b.openrouter_date IS NULL OR b.official_date <= b.openrouter_date) THEN 'official'
                WHEN b.openrouter_date IS NOT NULL THEN 'openrouter'
           END AS retire_source
    FROM base b
)
SELECT model_id,
       coalesce(override_status,
                CASE WHEN NOT is_active THEN 'retired'
                     WHEN retire_date <= current_date THEN 'retired'
                     WHEN retire_date <= current_date + 30 THEN 'retiring'
                     ELSE 'active' END) AS status,
       CASE WHEN override_status IS NULL AND NOT is_active AND (retire_date IS NULL OR retire_date > current_date)
            THEN 'removed_from_openrouter' ELSE retire_source END AS status_reason,
       CASE WHEN NOT is_active AND (retire_date IS NULL OR retire_date > current_date) THEN last_seen_date
            ELSE retire_date END AS retire_date,
       retire_date - current_date AS days_left,
       coalesce(override_replacement, replacement) AS replacement,
       source_url,
       override_note
FROM dated;

GRANT SELECT ON mart.v_model_status TO streamlit_ro;
