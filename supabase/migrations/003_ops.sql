-- 003_ops.sql — run log, DQ results, page hashes

CREATE TABLE IF NOT EXISTS ops.pipeline_run_log (
    run_id      text NOT NULL,
    source      text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL CHECK (status IN ('running','success','partial','failed','skipped')),
    rows_loaded integer NOT NULL DEFAULT 0,
    error       text,
    PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS ops.dq_check_result (
    run_id     text NOT NULL,
    check_name text NOT NULL,
    target     text,
    result     text NOT NULL CHECK (result IN ('pass','warn','fail')),
    detail     text,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, check_name)
);

CREATE TABLE IF NOT EXISTS ops.source_page_hash (
    source       text NOT NULL,
    fetched_date date NOT NULL,
    content_hash text NOT NULL,
    changed      boolean NOT NULL,
    rows_parsed  integer,
    PRIMARY KEY (source, fetched_date)
);
