-- 005_grants.sql — read-only role for Streamlit (CLAUDE.md §6.2)
-- The role is created outside migrations (password lives in .env):
--   CREATE ROLE streamlit_ro LOGIN PASSWORD '<from .env>';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'streamlit_ro') THEN
    RAISE NOTICE 'streamlit_ro does not exist yet; create it, then re-run this file';
  ELSE
    GRANT USAGE ON SCHEMA mart, ops TO streamlit_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA mart, ops TO streamlit_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO streamlit_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA ops  GRANT SELECT ON TABLES TO streamlit_ro;
  END IF;
END $$;
