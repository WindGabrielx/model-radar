"""Read-only data access: mart.* and ops.* through the streamlit_ro user (CLAUDE.md §12)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

TZ = ZoneInfo("Asia/Bangkok")
TTL = 3600
STAMP_TTL = 60   # how often to check whether the pipeline wrote new data
VENDORS = ["Anthropic", "DeepSeek", "Google", "OpenAI", "xAI"]   # chart colour order (theme categorical)
TIERS = ["top", "mid", "small", "unclassified"]
DEFAULT_TIERS = ["top", "mid", "small"]


def _conn():
    # Supabase's pooler drops idle connections; pre-ping + recycle replace dead ones instead of hanging,
    # and TCP keepalives / timeouts make a broken socket fail fast rather than block the page.
    return st.connection("tracker", type="sql", pool_pre_ping=True, pool_recycle=300,
                         connect_args={"connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30,
                                       "keepalives_interval": 10, "keepalives_count": 3,
                                       "options": "-c statement_timeout=30000"})


def data_stamp() -> str:
    """Changes whenever a pipeline step finishes or a sentiment evaluation is saved (checked every minute)."""
    try:
        df = _conn().query("""SELECT (SELECT max(finished_at) FROM ops.pipeline_run_log)::text
                                     || '|' || coalesce((SELECT max(evaluated_at) FROM ops.model_eval)::text, '')
                              AS stamp""", ttl=STAMP_TTL)
        return str(df.iloc[0, 0])
    except Exception:
        return ""


def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    # The stamp is part of the cached SQL text, so new pipeline data replaces the 1-hour cache within a minute
    # (both locally and on Community Cloud) instead of waiting for the TTL.
    return _conn().query(f"/* data:{data_stamp()} */\n{sql}", params=params, ttl=TTL)


def to_bkk(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True).dt.tz_convert(TZ)


# ------------------------------------------------------------------ loaders (cached by conn.query)
def models() -> pd.DataFrame:
    # Status (active / retiring / retired) comes from mart.v_model_status: official shutdown notices,
    # OpenRouter's expiration_date and whether the model is still listed (migration 015).
    df = query("""SELECT m.*, s.status, s.status_reason, s.retire_date, s.days_left, s.replacement AS status_replacement
                  FROM mart.v_model_current m LEFT JOIN mart.v_model_status s USING (model_id)""")
    df["status"] = df["status"].fillna("active")
    num = ["input_price_per_m", "output_price_per_m", "cache_read_price_per_m", "batch_input_price_per_m",
           "batch_output_price_per_m", "blended_price_per_m", "batch_blended_price_per_m", "quality",
           "coding_index", "agentic_index", "value_score", "throughput_p50", "latency_p50", "latency_stability",
           "uptime_last_1d", "token_share_7d", "vendor_sentiment_30d"]
    for c in num:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["blended_price_30d"] = df["blended_price_30d"].apply(
        lambda a: [float(x) for x in a if x is not None] if isinstance(a, list) else [])
    return df


def price_history() -> pd.DataFrame:
    df = query("""SELECT p.*, v.vendor_name FROM mart.v_price_history p JOIN mart.v_vendor v USING (vendor_id)""")
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    for c in ["blended_price_per_m", "batch_blended_price_per_m", "input_price_per_m", "output_price_per_m"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def usage_vendor_daily() -> pd.DataFrame:
    df = query("""SELECT u.usage_date, COALESCE(v.vendor_name, 'Other models') AS vendor_name, u.total_tokens,
                         u.token_share, u.backfilled
                  FROM mart.v_usage_vendor_daily u LEFT JOIN mart.v_vendor v USING (vendor_id)""")
    df["usage_date"] = pd.to_datetime(df["usage_date"])
    df["token_share"] = pd.to_numeric(df["token_share"], errors="coerce").astype("float64")
    df["total_tokens"] = pd.to_numeric(df["total_tokens"], errors="coerce").astype("float64")
    return df


def task_share() -> pd.DataFrame:
    return query("SELECT * FROM mart.v_task_share_latest")


def news() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_news_latest ORDER BY published_at DESC")
    df["published_at"] = to_bkk(df["published_at"])
    vmap = dict(zip(vendor_table()["vendor_id"], vendor_table()["vendor_name"]))
    df["vendors"] = df["vendor_ids"].apply(lambda ids: [vmap.get(i, i) for i in (ids or [])])
    return df


def sentiment_weekly() -> pd.DataFrame:
    df = query("""SELECT s.*, v.vendor_name FROM mart.v_sentiment_weekly s JOIN mart.v_vendor v USING (vendor_id)""")
    df["week_start"] = pd.to_datetime(df["week_start"])
    df["sentiment_score"] = pd.to_numeric(df["sentiment_score"], errors="coerce")
    return df


def lifecycle() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_lifecycle_timeline")
    df["event_date"] = pd.to_datetime(df["event_date"])
    return df


def pipeline_health() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_pipeline_health ORDER BY source")
    for c in ["last_started_at", "last_finished_at", "last_success_at"]:
        df[c] = to_bkk(df[c])
    return df


def pipeline_rows_daily() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_pipeline_rows_daily")
    df["run_date"] = pd.to_datetime(df["run_date"])
    return df


def dq_latest() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_dq_latest ORDER BY result DESC, check_name")
    df["checked_at"] = to_bkk(df["checked_at"])
    return df


def vendor_table() -> pd.DataFrame:
    return query("SELECT * FROM mart.v_vendor ORDER BY vendor_name")


def as_of() -> dict:
    """Freshness for the header and the OpenRouter attribution line."""
    df = query("""SELECT (SELECT max(snapshot_date) FROM mart.v_price_history) AS models_as_of,
                         (SELECT max(last_success_at) FROM mart.v_pipeline_health) AS last_success_at,
                         (SELECT max(usage_date) FROM mart.v_usage_vendor_daily) AS usage_as_of""")
    row = df.iloc[0].to_dict()
    if pd.notna(row.get("last_success_at")):
        row["last_success_at"] = pd.to_datetime(pd.Series([row["last_success_at"]]), utc=True).dt.tz_convert(TZ)[0]
    return row


# ------------------------------------------------------------------ global filters (set in streamlit_app.py)
def _in_scope(df: pd.DataFrame) -> pd.DataFrame:
    vendors = st.session_state.get("f_vendors") or VENDORS
    tiers = st.session_state.get("f_tiers") or TIERS
    df = df[df["vendor_name"].isin(vendors) & df["tier"].isin(tiers)]
    if not st.session_state.get("f_images", False):
        df = df[df["output_type"] == "text"]
    return df


def filtered_models() -> pd.DataFrame:
    """Models for the comparison pages. Retired models (shut down by the vendor, or gone from OpenRouter) are hidden
    unless the sidebar toggle includes them; retiring and retired models carry a label after their name, so every
    table, chart and picker that shows the name also shows the warning."""
    from lib import ui   # local import: ui imports data
    from lib.i18n import _

    df = _in_scope(models())
    if not st.session_state.get("f_retired", False):
        df = df[df["status"] != "retired"]

    def label(r) -> str:
        if r["status"] == "retiring":
            return f"{r['model_name']} · " + _('ปิด {0}').format(ui.short_date(r["retire_date"]))
        if r["status"] == "retired":
            return f"{r['model_name']} · " + _('ปิดแล้ว')
        return r["model_name"]

    return df.assign(model_name=df.apply(label, axis=1)) if len(df) else df


def status_counts() -> dict:
    """Retiring / hidden-retired counts within the current vendor, tier and image filters."""
    df = _in_scope(models())
    return {"retiring": int((df["status"] == "retiring").sum()), "retired": int((df["status"] == "retired").sum())}


def selected_vendors() -> list[str]:
    return st.session_state.get("f_vendors") or VENDORS


def reset_filters() -> None:
    st.session_state["f_vendors"] = list(VENDORS)
    st.session_state["f_tiers"] = list(DEFAULT_TIERS)
    st.session_state["f_images"] = False
    st.session_state["f_retired"] = False


def pipeline_runs() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_pipeline_runs")
    for c in ["started_at", "finished_at"]:
        df[c] = to_bkk(df[c])
    df["duration_s"] = pd.to_numeric(df["duration_s"], errors="coerce")
    return df


def pipeline_run_summary() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_pipeline_run_summary ORDER BY started_at DESC LIMIT 60")
    for c in ["started_at", "finished_at"]:
        df[c] = to_bkk(df[c])
    df["duration_s"] = pd.to_numeric(df["duration_s"], errors="coerce")
    return df


def usage_other_models() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_usage_other_models ORDER BY tokens_7d DESC")
    for c in ["tokens_7d", "token_share_7d"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def data_inventory() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_data_inventory")
    for c in ["row_count", "size_bytes"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["captured_at"] = to_bkk(df["captured_at"])
    return df


def data_inventory_history() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_data_inventory_history ORDER BY captured_at")
    for c in ["row_count", "size_bytes"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["captured_at"] = to_bkk(df["captured_at"])
    return df


def sentiment_vendor_window() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_sentiment_vendor_window ORDER BY vendor_name")
    for c in ["score_30d", "score_prev_30d"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def sentiment_aspect_30d() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_sentiment_aspect_30d")
    df["score"] = pd.to_numeric(df["score"], errors="coerce").astype("float64")
    return df


def sentiment_examples(vendor_id: str) -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_sentiment_examples WHERE vendor_id = :v "
               "ORDER BY confidence DESC, published_at DESC LIMIT 400", params={"v": vendor_id})
    df["published_at"] = to_bkk(df["published_at"])
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").astype("float64")
    return df


def model_eval_latest() -> pd.DataFrame:
    return query("SELECT * FROM mart.v_model_eval_latest")


def release_markers() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_release_markers ORDER BY event_date")
    df["event_date"] = pd.to_datetime(df["event_date"])
    return df


def usage_vendor_quarterly() -> pd.DataFrame:
    df = query("SELECT * FROM mart.v_usage_vendor_quarterly ORDER BY quarter_start")
    for c in ["total_tokens", "token_share"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df
