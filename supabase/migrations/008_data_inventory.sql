-- 008_data_inventory.sql — how much data the system holds, per store / object / source, captured every run.
-- Written by the worker's DQ step (it can reach MongoDB; Streamlit cannot and must not).

CREATE TABLE IF NOT EXISTS ops.data_inventory (
    run_id      text NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    store       text NOT NULL CHECK (store IN ('supabase', 'mongodb')),
    object      text NOT NULL,                 -- table or collection
    source      text NOT NULL,                 -- where the data comes from (OpenRouter /models, Hacker News, …)
    row_count   bigint NOT NULL,
    size_bytes  bigint,
    min_date    date,
    max_date    date,
    PRIMARY KEY (run_id, store, object, source)
);

CREATE OR REPLACE VIEW mart.v_data_inventory AS
SELECT i.*
FROM ops.data_inventory i
WHERE i.run_id = (SELECT run_id FROM ops.data_inventory ORDER BY captured_at DESC LIMIT 1);

-- totals per capture, for growth over time
CREATE OR REPLACE VIEW mart.v_data_inventory_history AS
SELECT run_id, min(captured_at) AS captured_at, store,
       sum(row_count) AS row_count, sum(size_bytes) AS size_bytes
FROM ops.data_inventory
GROUP BY run_id, store;

GRANT SELECT ON ops.data_inventory, mart.v_data_inventory, mart.v_data_inventory_history TO streamlit_ro;
