-- 010_dq_latest_full_run.sql — "latest DQ" = the latest run whose DQ step ran.
-- Steps such as lifecycle save their own checks under their run_id; a one-step run (e.g. a manual
-- lifecycle re-parse) used to become the "latest" and hide the full check list.

CREATE OR REPLACE VIEW mart.v_dq_latest AS
SELECT d.* FROM ops.dq_check_result d
WHERE d.run_id = (SELECT run_id FROM ops.dq_check_result
                  WHERE check_name = 'openrouter_key'          -- written only by the 09 DQ step
                  ORDER BY checked_at DESC LIMIT 1);
