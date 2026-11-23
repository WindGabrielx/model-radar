-- 006_pipeline_runs.sql — per-run tracking for the Pipeline health page (one run = one n8n execution)

CREATE OR REPLACE VIEW mart.v_pipeline_runs AS
SELECT run_id, source, started_at, finished_at,
       EXTRACT(EPOCH FROM (finished_at - started_at))::numeric(10,1) AS duration_s,
       status, rows_loaded, error
FROM ops.pipeline_run_log;

CREATE OR REPLACE VIEW mart.v_pipeline_run_summary AS
WITH steps AS (
    SELECT run_id,
           min(started_at) AS started_at, max(finished_at) AS finished_at,
           count(*) FILTER (WHERE source <> 'dq_checks') AS n_steps,
           count(*) FILTER (WHERE status = 'failed') AS n_failed,
           count(*) FILTER (WHERE status = 'partial' AND source <> 'dq_checks') AS n_partial,
           count(*) FILTER (WHERE status = 'skipped') AS n_skipped,
           count(*) FILTER (WHERE status = 'running') AS n_running,
           sum(rows_loaded) FILTER (WHERE source <> 'dq_checks') AS rows_loaded
    FROM ops.pipeline_run_log GROUP BY run_id
), dq AS (
    SELECT run_id, count(*) FILTER (WHERE result = 'fail') AS dq_fails,
           count(*) FILTER (WHERE result = 'warn') AS dq_warns
    FROM ops.dq_check_result GROUP BY run_id
)
SELECT s.*, EXTRACT(EPOCH FROM (s.finished_at - s.started_at))::numeric(10,1) AS duration_s,
       COALESCE(d.dq_fails, 0) AS dq_fails, COALESCE(d.dq_warns, 0) AS dq_warns,
       CASE WHEN s.n_running > 0 THEN 'running'
            WHEN s.n_failed > 0 OR COALESCE(d.dq_fails, 0) > 0 THEN 'failed'
            WHEN s.n_partial > 0 THEN 'partial'
            ELSE 'success' END AS run_status
FROM steps s LEFT JOIN dq d USING (run_id);

GRANT SELECT ON mart.v_pipeline_runs, mart.v_pipeline_run_summary TO streamlit_ro;
