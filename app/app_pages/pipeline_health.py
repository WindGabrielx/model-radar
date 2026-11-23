import altair as alt
import pandas as pd
import streamlit as st

from lib.i18n import _

from lib import data, ui

ui.page_header(_('สถานะระบบดึงข้อมูล'), ":material/monitor_heart:",
               _('ข้อมูลสดและเชื่อถือได้ไหม ดูทุกรอบการรันทีละขั้นตอน ตามที่ n8n สั่งทำงานทุกวัน 10:00 น.'))
ui.freshness()

# Same order and names as the n8n 00_master workflow.
STEPS = {
    "or_models": "01 OpenRouter models", "or_endpoints": "02 Endpoints", "or_usage": "03 Usage",
    "or_benchmarks": "04 Benchmarks", "or_tasks": "05 Tasks", "hn": "06 Hacker News", "rss": "07 RSS",
    "gdelt": "07 GDELT", "lifecycle": "08 Lifecycle pages", "dq_checks": "09 DQ checks",
}
STATUS = ["success", "partial", "skipped", "failed", "running"]
STATUS_TH = {"success": _('สำเร็จ'), "partial": _('สำเร็จบางส่วน'), "skipped": _('ข้าม'), "failed": _('ล้มเหลว'),
             "running": _('กำลังรัน')}
STATUS_COLORS = ["green", "orange", "gray", "red", "blue"]
DQ_TH = {"pass": _('ผ่าน'), "warn": _('เตือน'), "fail": _('ไม่ผ่าน')}

runs = data.pipeline_run_summary()
if runs.empty:
    ui.empty_state(_('ยังไม่มีการรัน'), _('เปิด workflow `00_master` ใน n8n หรือรัน `python worker/run_daily.py`'),
                   ":material/play_circle:")
    st.stop()
steps = data.pipeline_runs()
dq = data.dq_latest()

# ------------------------------------------------------------------ latest run
# KPIs describe the latest *full* run (a one-step test run would otherwise show as the latest)
full_runs = runs[runs["n_steps"] >= 8]
latest = (full_runs if len(full_runs) else runs).iloc[0]
now = pd.Timestamp.now(tz=data.TZ)
age_h = (now - latest["started_at"]).total_seconds() / 3600
with st.grid(6, gap="medium", min_column_width=150):
    st.metric(_('รอบล่าสุด'), STATUS_TH.get(latest["run_status"], latest["run_status"]), border=True, height="stretch",
              help=f"run_id {latest['run_id']}")
    st.metric(_('เริ่มเมื่อ'), ui.th_date(latest["started_at"], with_time=True), border=True, height="stretch", help=_('เวลาไทย'))
    st.metric(_('ใช้เวลา'), _('{0:.1f} นาที').format((latest['duration_s'] or 0) / 60), border=True, height="stretch")
    st.metric(_('แถวที่โหลด'), f"{int(latest['rows_loaded'] or 0):,}", border=True, height="stretch")
    st.metric(_('DQ ไม่ผ่าน / เตือน'), f"{int(latest['dq_fails'])} / {int(latest['dq_warns'])}", border=True, height="stretch")
    full_days = set(runs.loc[runs["n_steps"] >= 8, "started_at"].dt.date)
    first_day = min(full_days) if full_days else now.date()
    last_due = now.date() - pd.Timedelta(days=0 if now.hour >= 11 else 1)
    missed = [d for d in pd.date_range(first_day, last_due, freq="D").date if d not in full_days][-30:]
    st.metric(_('วันที่ไม่ได้รัน'), len(missed), border=True, height="stretch",
              help=_('วันที่ไม่มีรอบเต็มเลย (นับตั้งแต่รอบแรก) · ราคาและสถานะของวันนั้นจะหายไปถาวร ส่วนข่าวและการใช้งานระบบจะดึงย้อนหลังให้เองในรอบถัดไป')
                   + (" · " + ", ".join(ui.th_date(d) for d in missed[-5:]) if missed else ""))
if age_h > 26:
    st.warning(_('รอบล่าสุดเก่ากว่า 26 ชั่วโมง ตรวจว่า Docker Desktop และ n8n เปิดอยู่ตอน 10:00 น.'),
               icon=":material/schedule:", title=_('อาจพลาดรอบประจำวัน'))

# ------------------------------------------------------------------ runs (Databricks Jobs "Runs" layout)
def fmt_dur(sec) -> str:
    if sec is None or pd.isna(sec):
        return "–"
    m, s_ = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s_:02d}s" if h else f"{m}m {s_:02d}s"


with st.container(horizontal=True, vertical_alignment="center"):
    st.subheader(_('ประวัติการรัน'), icon=":material/view_module:", width="stretch")
    full_only = st.toggle(_('เฉพาะรอบเต็ม (รันครบทุก task)'), value=True, key="ph_full_only",
                          help=_('ซ่อนรอบที่รันทดสอบแค่บาง task'), width="content")
rs = runs[runs["n_steps"] >= 8] if full_only else runs
if rs.empty:
    rs = runs
rs = rs.head(30).sort_values("started_at").copy()
rs["label"] = rs["started_at"].dt.strftime("%d/%m %H:%M")
rs["status_th"] = rs["run_status"].map(STATUS_TH)
rs["minutes"] = rs["duration_s"].fillna(0) / 60
order = rs["label"].tolist()
run_ids = rs["run_id"].tolist()

# Selected run: the latest by default; a click on the matrix, a row in the runs table, or
# "latest successful run" changes it (callbacks write ph_sel, last action wins).
if st.session_state.get("ph_sel") not in run_ids:
    st.session_state["ph_sel"] = run_ids[-1]


def _on_matrix() -> None:
    picked = (st.session_state["run_matrix"].selection.get("pick") or [{}])[0].get("run_id")
    if picked:
        st.session_state["ph_sel"] = picked


def _on_table() -> None:
    rows = st.session_state["runs_table"].selection.rows
    if rows:
        st.session_state["ph_sel"] = st.session_state["_ph_table_ids"][rows[0]]


def _latest_success() -> None:
    ok = rs[rs["run_status"] == "success"]
    st.session_state["ph_sel"] = (ok if len(ok) else rs)["run_id"].iloc[-1]


sel_id = st.session_state["ph_sel"]

grid = pd.MultiIndex.from_product([run_ids, list(STEPS)], names=["run_id", "source"]).to_frame(index=False)
grid = grid.merge(steps[["run_id", "source", "status", "rows_loaded", "duration_s", "error"]],
                  on=["run_id", "source"], how="left")
grid["status_th"] = grid["status"].map(STATUS_TH).fillna(_('ไม่ได้รัน'))
grid["step"] = grid["source"].map(STEPS)
grid = grid.merge(rs[["run_id", "label"]], on="run_id")

DOMAIN = [STATUS_TH[s] for s in STATUS] + [_('ไม่ได้รัน')]
RANGE = ["#0F8F63", "#D9822B", "#A8A69E", "#C2352B", "#2F6FE0", "#D4D1C8"]
color = alt.Color("status_th:N", title=None, scale=alt.Scale(domain=DOMAIN, range=RANGE),
                  legend=alt.Legend(orient="top", direction="horizontal"))
focus = alt.condition(alt.datum.run_id == sel_id, alt.value(1), alt.value(0.4))
pick = alt.selection_point(fields=["run_id"], name="pick", on="click", clear=False)
STEP = 34
x_top = alt.X("label:O", sort=order, title=None,
              axis=alt.Axis(orient="top", labelAngle=0, labelFontSize=10, ticks=False, domain=False,
                            labelExpr="split(datum.label, ' ')[0]"))
bars = alt.Chart(rs).mark_bar(width=22, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
    x=x_top, y=alt.Y("minutes:Q", title=_('เวลาทั้งหมด (นาที)'), axis=alt.Axis(tickCount=3, grid=True)),
    color=color, opacity=focus,
    tooltip=[alt.Tooltip("label:N", title=_('รอบ')), alt.Tooltip("status_th:N", title=_('สถานะ')),
             alt.Tooltip("minutes:Q", title=_('นาที'), format=".1f"),
             alt.Tooltip("rows_loaded:Q", title=_('แถว'), format=","), alt.Tooltip("run_id:N")],
).properties(width=alt.Step(STEP), height=110)
cells = alt.Chart(grid).mark_rect(width=22, height=18, cornerRadius=4).encode(
    x=alt.X("label:O", sort=order, title=None, axis=None),
    y=alt.Y("step:O", sort=list(STEPS.values()), title="Tasks",
            axis=alt.Axis(labelLimit=180, ticks=False, domain=False)),
    color=color, opacity=focus,
    tooltip=[alt.Tooltip("label:N", title=_('รอบ')), alt.Tooltip("step:N", title="Task"),
             alt.Tooltip("status_th:N", title=_('สถานะ')), alt.Tooltip("rows_loaded:Q", title=_('แถว'), format=","),
             alt.Tooltip("duration_s:Q", title=_('วินาที'), format=".0f"), alt.Tooltip("error:N", title=_('หมายเหตุ'))],
).add_params(pick).properties(width=alt.Step(STEP), height=alt.Step(24))
st.altair_chart(alt.vconcat(bars, cells, spacing=4).resolve_scale(color="shared").configure_view(stroke=None),
                on_select=_on_matrix, key="run_matrix", width="content",
                alt=_('ตารางสถานะของทุก task ในแต่ละรอบ คล้าย matrix ของ Databricks Jobs'))
st.caption(_('แต่ละคอลัมน์ = 1 รอบ (เก่า → ใหม่) · แท่งด้านบน = เวลาที่ใช้ · ช่องสี่เหลี่ยม = สถานะของแต่ละ task · กดที่ช่องหรือแถวในตารางด้านล่างเพื่อดูรายละเอียด'))

# runs table
st.button(_('ไปที่รอบล่าสุดที่สำเร็จ'), type="tertiary", icon=":material/check_circle:",
          on_click=_latest_success, key="ph_latest_ok")
table = runs[runs["run_id"].isin(run_ids)].sort_values("started_at", ascending=False)
st.session_state["_ph_table_ids"] = table["run_id"].tolist()
first_issue = (steps[steps["status"].isin(["failed", "partial"]) & (steps["source"] != "dq_checks")]
               .sort_values("started_at").groupby("run_id").first())


def _issue(r: str) -> str:
    if r not in first_issue.index:
        return ""
    src = first_issue.at[r, "source"]
    return f"{STEPS.get(src, src)}: {(first_issue.at[r, 'error'] or '')[:80]}"


st.dataframe(
    pd.DataFrame({
        _('เวลาเริ่ม'): table["started_at"].dt.tz_localize(None),
        "Run ID": table["run_id"],
        _('เวลาที่ใช้'): table["duration_s"].map(fmt_dur),
        _('สถานะ'): table["run_status"].map(lambda r: [STATUS_TH.get(r, r)]),
        "Tasks": table.apply(lambda r: f"{int(r['n_steps'] - r['n_failed'] - r['n_skipped'])}/{int(r['n_steps'])}",
                             axis=1),
        _('ปัญหาแรกที่พบ'): table["run_id"].map(_issue),
    }),
    hide_index=True, key="runs_table", on_select=_on_table, selection_mode="single-row",
    alt=_('รายการรอบการรัน กดเลือกแถวเพื่อดูรายละเอียด'),
    column_config={
        _('เวลาเริ่ม'): st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm", help=_('เวลาไทย')),
        _('สถานะ'): st.column_config.MultiselectColumn(options=[STATUS_TH[s] for s in STATUS], color=STATUS_COLORS),
        "Tasks": st.column_config.TextColumn(help=_('task ที่สำเร็จหรือสำเร็จบางส่วน / task ทั้งหมด')),
        _('ปัญหาแรกที่พบ'): st.column_config.TextColumn(width="large"),
    },
)

# ------------------------------------------------------------------ selected run detail
sel = runs[runs["run_id"] == sel_id].iloc[0]
st.subheader(_('รายละเอียดรอบ {0}').format(ui.th_date(sel['started_at'], with_time=True)),
             icon=":material/account_tree:")
failed = steps[(steps["run_id"] == sel_id) & (steps["status"] == "failed")]
if len(failed):
    st.error(_('Task ที่ล้มเหลว: ') + ", ".join(failed["source"].map(STEPS).fillna(failed["source"])),
             icon=":material/error:", title=_('รอบนี้มี task ล้มเหลว'))
cur = steps[steps["run_id"] == sel_id].copy()
cur["order"] = cur["source"].map(lambda s: list(STEPS).index(s) if s in STEPS else 99)
cur = cur.sort_values("order")
st.dataframe(
    pd.DataFrame({
        "Task": cur["source"].map(STEPS).fillna(cur["source"]),
        _('สถานะ'): cur["status"].map(lambda s: [STATUS_TH.get(s, s)]),
        _('เริ่ม'): cur["started_at"].dt.tz_localize(None),
        _('ใช้เวลา (วินาที)'): cur["duration_s"],
        _('จำนวนแถว'): cur["rows_loaded"],
        _('หมายเหตุ / error'): cur["error"].fillna(""),
    }),
    hide_index=True, alt=_('สถานะ เวลา และจำนวนแถวของแต่ละ task ในรอบที่เลือก'),
    column_config={
        _('สถานะ'): st.column_config.MultiselectColumn(options=[STATUS_TH[s] for s in STATUS], color=STATUS_COLORS),
        _('เริ่ม'): st.column_config.DatetimeColumn(format="HH:mm:ss", help=_('เวลาไทย')),
        _('ใช้เวลา (วินาที)'): st.column_config.ProgressColumn(
            min_value=0, max_value=float(max(cur["duration_s"].max() or 1, 1)), format="%.0f"),
        _('จำนวนแถว'): st.column_config.NumberColumn(format="localized"),
        _('หมายเหตุ / error'): st.column_config.TextColumn(width="large"),
    },
)
st.caption(f"run_id `{sel_id}`")

left, right = st.columns([3, 2], gap="medium")
with left, st.container(border=True, height="stretch"):
    st.markdown(_('**ตรวจคุณภาพข้อมูล (DQ) รอบล่าสุด**'))
    st.dataframe(
        pd.DataFrame({_('การตรวจ'): dq["check_name"], _('ผล'): dq["result"].map(lambda r: [DQ_TH.get(r, r)]),
                      _('รายละเอียด'): dq["detail"]}),
        hide_index=True, height=360, alt=_('ผลตรวจคุณภาพข้อมูลรอบล่าสุด'),
        column_config={_('ผล'): st.column_config.MultiselectColumn(options=list(DQ_TH.values()),
                                                                color=["green", "orange", "red"]),
                       _('รายละเอียด'): st.column_config.TextColumn(width="large")},
    )
with right, st.container(border=True, height="stretch"):
    st.markdown(_('**ความสดของข้อมูลแต่ละแหล่ง**'))
    health = data.pipeline_health()
    fresh = pd.DataFrame({
        _('แหล่ง'): health["source"].map(STEPS).fillna(health["source"]),
        _('สำเร็จล่าสุด'): health["last_success_at"].dt.tz_localize(None),
        _('ผ่านมา (ชม.)'): ((now - health["last_success_at"]).dt.total_seconds() / 3600).round(1),
    }).sort_values(_('แหล่ง'))
    st.dataframe(fresh, hide_index=True, height=360, alt=_('กี่ชั่วโมงแล้วตั้งแต่แต่ละแหล่งโหลดสำเร็จครั้งล่าสุด'),
                 column_config={_('สำเร็จล่าสุด'): st.column_config.DatetimeColumn(format="DD MMM, HH:mm"),
                                _('ผ่านมา (ชม.)'): st.column_config.NumberColumn(format="%.1f")})
    keyed = health[health["source"].isin(["or_usage", "or_benchmarks", "or_tasks"])]
    if keyed.empty or keyed["last_success_at"].isna().all():
        st.caption(_('03–05 ว่างเพราะยังไม่มี OpenRouter key'))

# ------------------------------------------------------------------ all data held by the system
st.subheader(_('ข้อมูลทั้งหมดในระบบ'), icon=":material/database:")
inv = data.data_inventory()
if inv.empty:
    st.caption(_('ยังไม่มีสถิติ จะขึ้นหลังรอบถัดไป (เก็บในขั้นตอน 09 DQ)'))
else:
    def _mb(b: float) -> str:
        return f"{b / 1_048_576:,.1f} MB" if b >= 1_048_576 else f"{b / 1024:,.0f} KB"

    sb, mg = inv[inv["store"] == "supabase"], inv[inv["store"] == "mongodb"]
    days = (pd.to_datetime(inv["max_date"]).max() - pd.to_datetime(inv["min_date"]).min()).days + 1
    with st.grid(4, gap="medium", min_column_width=180):
        st.metric(_('รวมทั้งหมด'), f"{int(inv['row_count'].sum()):,}", border=True, height="stretch",
                  help=_('แถวใน Supabase + เอกสาร raw ใน MongoDB'))
        st.metric(_('แถวใน Supabase'), f"{int(sb['row_count'].sum()):,}", border=True,
                  height="stretch", help=_mb(sb["size_bytes"].sum()))
        st.metric(_('เอกสาร raw ใน MongoDB'), f"{int(mg['row_count'].sum()):,}",
                  border=True, height="stretch", help=_mb(mg["size_bytes"].sum()) + " / 512 MB (M0)")
        st.metric(_('ขนาดรวม'), _mb(inv["size_bytes"].sum()), border=True, height="stretch",
                  help=_('ข้อมูลครอบคลุม {0} วัน').format(days))
    st.caption(_('นับ ณ {0} · แยกตามแหล่งที่มาด้านล่าง').format(
        ui.th_date(inv["captured_at"].max(), with_time=True)))

    by_src = (inv.groupby(["source", "store"], as_index=False)["row_count"].sum())
    by_src["store"] = by_src["store"].map({"supabase": "Supabase", "mongodb": "MongoDB (raw)"})
    order = by_src.groupby("source")["row_count"].sum().sort_values(ascending=False).index.tolist()
    st.altair_chart(
        alt.Chart(by_src).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("row_count:Q", title=_('จำนวนแถว / เอกสาร'), stack=True),
            y=alt.Y("source:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
            color=alt.Color("store:N", title=None, legend=alt.Legend(orient="top"),
                            scale=alt.Scale(range=["#2F6FE0", "#A8A69E"])),
            tooltip=[alt.Tooltip("source:N", title=_('แหล่งที่มา')), alt.Tooltip("store:N", title=_('ที่เก็บ')),
                     alt.Tooltip("row_count:Q", title=_('จำนวน'), format=",")],
        ).properties(height=max(220, 26 * len(order))),
        alt=_('จำนวนข้อมูลแยกตามแหล่งที่มาและที่เก็บ'),
    )
    tbl = inv.sort_values(["store", "row_count"], ascending=[False, False])
    st.dataframe(
        pd.DataFrame({
            _('แหล่งที่มา'): tbl["source"],
            _('ที่เก็บ'): tbl["store"].map({"supabase": "Supabase", "mongodb": "MongoDB"}),
            _('ตาราง / collection'): tbl["object"],
            _('จำนวน'): tbl["row_count"],
            _('ขนาด'): tbl["size_bytes"].map(_mb),
            _('ข้อมูลตั้งแต่'): pd.to_datetime(tbl["min_date"]).dt.date,
            _('ถึง'): pd.to_datetime(tbl["max_date"]).dt.date,
        }),
        hide_index=True, height=420, alt=_('รายละเอียดข้อมูลทุกตารางและ collection'),
        column_config={_('จำนวน'): st.column_config.NumberColumn(format="localized")},
    )

