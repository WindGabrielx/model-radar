-- 001_schemas.sql — schemas for AI Model Tracker (never use public)
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS mart;

-- Keep our schemas away from Supabase's REST roles.
REVOKE ALL ON SCHEMA core, ops, mart FROM anon, authenticated;

CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
