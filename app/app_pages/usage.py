import altair as alt
import pandas as pd
import streamlit as st

from lib.i18n import _

from lib import data, ui

ui.page_header(_('การใช้งาน'), ":material/stacked_line_chart:",
               _('ปริมาณ tokens บน OpenRouter ย้ายไปมาระหว่างผู้ให้บริการอย่างไร และถูกใช้ทำงานประเภทไหน'))
as_of = ui.freshness()
ui.provenance("third-party", "derived",
              note=_('นับเฉพาะ traffic ที่ผ่าน OpenRouter ไม่ใช่จำนวนผู้ใช้ และไม่ใช่ส่วนแบ่งตลาด'))

usage = data.usage_vendor_daily()
if usage.empty:
    ui.empty_state(
        _('ต้องใส่ OpenRouter key ก่อนจึงจะมีข้อมูลการใช้งาน'),
        _('อันดับ tokens รายวันและสัดส่วนประเภทงานมาจาก Data API ของ OpenRouter ซึ่งต้องใช้ API key (สมัครฟรี) ใส่ใน `.env` ชื่อ `OPENROUTER_API_KEY` หรือ `ROUTER_PASSWORD` แล้วสั่ง `docker compose up -d --force-recreate worker` รอบถัดไปจะดึงข้อมูลย้อนหลัง 3 วันมาให้ทันที'),
        ":material/key:",
    )
    st.stop()

keep = data.selected_vendors() + ["Other models"]
usage = usage[usage["vendor_name"].isin(keep)].assign(
    vendor=lambda d: d["vendor_name"].replace({"Other models": _('โมเดลอื่น')}))

# Period switch (single choice, like TH / EN): full history from the one-off backfill, or only what the
# daily pipeline has collected since it started.
RANGES = {"all": _('ทั้งหมด (ตั้งแต่ ม.ค. 2025)'),
          "pipeline": _('ตั้งแต่เริ่มรัน pipeline')}
rng = st.segmented_control(_('ช่วงเวลา'), list(RANGES), format_func=RANGES.get, default="all",
                           required=True, key="usage_range")
start_daily = usage.loc[~usage["backfilled"], "usage_date"].min()
if rng == "pipeline":
    usage = usage[~usage["backfilled"]]
    st.caption(_('เฉพาะข้อมูลที่ pipeline เก็บเองทุกวัน ตั้งแต่ {0}').format(
        ui.th_date(start_daily)))
else:
    st.caption(_('ย้อนหลังถึง {0} (ดึงครั้งเดียวจาก OpenRouter) ต่อด้วยข้อมูลรายวันของ pipeline ตั้งแต่ {1} · รวมเป็นรายสัปดาห์ให้อ่านง่าย').format(
        ui.th_date(usage["usage_date"].min()), ui.th_date(start_daily)))
    # weekly totals → share, so ~2 years read cleanly
    wk = usage.assign(usage_date=usage["usage_date"].dt.to_period("W").dt.start_time)
    wk = wk.groupby(["usage_date", "vendor_name", "vendor"], as_index=False)["total_tokens"].sum()
    wk["token_share"] = wk["total_tokens"] / wk.groupby("usage_date")["total_tokens"].transform("sum")
    usage = wk
long_range = usage["usage_date"].nunique() > 20

with st.container(border=True):
    st.subheader(_('สัดส่วน tokens บน OpenRouter แยกตามผู้ให้บริการ'))
    st.altair_chart(
        alt.Chart(usage).mark_area(opacity=0.9).encode(
            x=alt.X("yearmonthdate(usage_date):T", title=None,
                    axis=alt.Axis(format="%b %Y", tickCount="month") if long_range
                    else alt.Axis(format="%d %b", tickCount="day")),
            y=alt.Y("token_share:Q", title=_('สัดส่วน tokens'), stack="normalize", axis=alt.Axis(format="%")),
            color=alt.Color("vendor:N", title=None, legend=alt.Legend(orient="top"),
                            scale=alt.Scale(domain=data.VENDORS + [_('โมเดลอื่น')])),
            tooltip=[alt.Tooltip("usage_date:T", title=_('วันที่')), alt.Tooltip("vendor:N", title=_('ผู้ให้บริการ')),
                     alt.Tooltip("token_share:Q", title=_('สัดส่วน'), format=".1%"),
                     alt.Tooltip("total_tokens:Q", title="Tokens", format=",.0f")],
        ).properties(height=340),
        alt=_('สัดส่วน tokens บน OpenRouter ของแต่ละผู้ให้บริการตามเวลา'),
    )
    ui.openrouter_attribution(as_of.get("usage_as_of"))
    st.caption(_('ข้อมูลเป็นรายวันและช้ากว่าวันจริง 1 วัน: OpenRouter เผยแพร่ตัวเลขเฉพาะวันที่จบแล้ว (เวลา UTC) รอบ 10:00 น. ของวันนี้จึงได้ข้อมูลถึงเมื่อวาน'))

# ------------------------------------------------------------------ plain-language summary of the full history
if rng == "all":
    qd = data.usage_vendor_quarterly()
    if not qd.empty:
        names = dict(zip(data.vendor_table()["vendor_id"], data.vendor_table()["vendor_name"]))
        qd = qd.assign(vendor=qd["vendor_id"].map(names).fillna(_('โมเดลอื่น')))
        five = qd[qd["vendor_id"] != "other"]
        quarters = sorted(qd["quarter"].unique())
        first_q, last_q = quarters[0], quarters[-1]
        pct = lambda v: f"{v * 100:.0f}%"  # noqa: E731
        with st.container(border=True):
            st.subheader(_('สรุปภาพรวม'), icon=":material/summarize:")
            st.caption(_('สรุปจากส่วนแบ่ง tokens รายไตรมาส · ไตรมาสล่าสุด ({0}) ยังไม่ครบไตรมาส').format(last_q))
            lines = []
            # 1) leader per year among the five
            for year in sorted({q[:4] for q in quarters}):
                yr = five[five["quarter"].str.startswith(year)].groupby("vendor")["total_tokens"].sum()
                tot = qd[qd["quarter"].str.startswith(year)]["total_tokens"].sum()
                if len(yr):
                    lines.append(_('ปี {0}: **{1}** ได้ส่วนแบ่งมากที่สุดใน 5 เจ้า ({2} ของ tokens ทั้งหมด)').format(
                        year, yr.idxmax(), pct(yr.max() / tot)))
            # 2) biggest gainer / loser, first vs latest quarter
            a = five[five["quarter"] == first_q].set_index("vendor")["token_share"]
            b = five[five["quarter"] == last_q].set_index("vendor")["token_share"]
            diff = (b.reindex(a.index.union(b.index)).fillna(0) - a.reindex(a.index.union(b.index)).fillna(0))
            if len(diff):
                up, down = diff.idxmax(), diff.idxmin()
                lines.append(_('โตมากที่สุด: **{0}** จาก {1} เป็น {2} ({3} → {4})').format(
                    up, pct(a.get(up, 0)), pct(b.get(up, 0)), first_q, last_q))
                lines.append(_('ลดลงมากที่สุด: **{0}** จาก {1} เหลือ {2}').format(
                    down, pct(a.get(down, 0)), pct(b.get(down, 0))))
            # 3) peak quarter per vendor
            peaks = five.loc[five.groupby("vendor")["token_share"].idxmax()].sort_values("token_share", ascending=False)
            lines.append(_('จุดสูงสุดของแต่ละเจ้า: ') + " · ".join(
                f"{r.vendor} {pct(r.token_share)} ({r.quarter})" for r in peaks.itertuples()))
            # 4) the rest of the market
            oth = qd[qd["vendor_id"] == "other"].set_index("quarter")["token_share"]
            if len(oth):
                lines.append(_('โมเดลจากบริษัทอื่นรวมกัน: {0} ใน {1} → {2} ใน {3} (ตลาดกระจายตัวมากขึ้น)').format(
                    pct(oth.get(first_q, 0)), first_q, pct(oth.get(last_q, 0)), last_q)
                    if oth.get(last_q, 0) > oth.get(first_q, 0) else
                    _('โมเดลจากบริษัทอื่นรวมกัน: {0} ใน {1} → {2} ใน {3}').format(
                    pct(oth.get(first_q, 0)), first_q, pct(oth.get(last_q, 0)), last_q))
            st.markdown("\n".join(f"- {l}" for l in lines))

            piv = qd.pivot_table(index="quarter", columns="vendor", values="token_share", aggfunc="sum").fillna(0)
            cols = [v for v in data.VENDORS if v in piv.columns] + [c for c in piv.columns if c not in data.VENDORS]
            piv = piv[cols].sort_index(ascending=False)
            leader = piv[[c for c in cols if c in data.VENDORS]].idxmax(axis=1)
            tbl = piv.reset_index().rename(columns={"quarter": _('ไตรมาส')})
            tbl.insert(1, _('ผู้นำใน 5 เจ้า'), leader.values)
            tbl[_('ผู้นำใน 5 เจ้า')] = tbl[_('ผู้นำใน 5 เจ้า')].map(lambda v: [v])
            st.dataframe(
                tbl, hide_index=True, height=min(400, 38 + 35 * len(tbl)),
                alt=_('ส่วนแบ่ง tokens รายไตรมาสของแต่ละผู้ให้บริการ'),
                column_config={
                    _('ผู้นำใน 5 เจ้า'): ui.vendor_column(_('ผู้นำใน 5 เจ้า')),
                    **{c: st.column_config.ProgressColumn(c, min_value=0, max_value=1, format="percent") for c in cols},
                },
            )

others = data.usage_other_models()
if not others.empty:
    with st.expander(_('โมเดลอื่น มีอะไรบ้าง?'), icon=":material/help:"):
        st.caption(_('“โมเดลอื่น” = โมเดลจากบริษัทที่เราไม่ได้ติดตาม (นอกเหนือจาก 5 เจ้า) ที่ติด 50 อันดับแรกของวัน รวมกับแถว “other” ที่ OpenRouter รวมทุกโมเดลนอก 50 อันดับไว้ด้วยกัน · 7 วันล่าสุด'))
        by_author = (others.assign(author=others["author"].replace({"other": _('นอก 50 อันดับ (รวม)')}))
                     .groupby("author", as_index=False)
                     .agg(share=("token_share_7d", "sum"), models=("model_permaslug", "count"),
                          top=("model_permaslug", "first"))
                     .sort_values("share", ascending=False))
        st.dataframe(
            pd.DataFrame({
                _('บริษัท'): by_author["author"],
                _('สัดส่วน tokens 7 วัน'): by_author["share"],
                _('จำนวนโมเดล'): by_author["models"],
                _('โมเดลที่ใช้มากสุด'): by_author["top"].where(by_author["author"] != _('นอก 50 อันดับ (รวม)'), "–"),
            }),
            hide_index=True, height=min(420, 38 + 35 * len(by_author)),
            alt=_('สัดส่วน tokens ของบริษัทอื่นนอก 5 เจ้าที่ติดตาม'),
            column_config={
                _('สัดส่วน tokens 7 วัน'): st.column_config.ProgressColumn(
                    min_value=0, max_value=float(max(by_author["share"].max(), 0.01)), format="percent"),
            },
        )

tasks = data.task_share()
with st.container(border=True):
    st.subheader(_('tokens ถูกใช้ทำงานอะไร'))
    if tasks.empty:
        st.caption(_('สัดส่วนประเภทงานจะขึ้นหลังจากรอบแรกที่มี OpenRouter key'))
    else:
        st.altair_chart(
            alt.Chart(tasks).mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("token_share:Q", title=_('สัดส่วน tokens'), axis=alt.Axis(format="%")),
                y=alt.Y("name:N", sort="-x", title=None),
                color=alt.Color("macro_category:N", title=None, legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("name:N", title=_('งาน')), alt.Tooltip("macro_category:N", title=_('หมวด')),
                         alt.Tooltip("token_share:Q", title=_('สัดส่วน tokens'), format=".1%"),
                         alt.Tooltip("usage_share:Q", title=_('สัดส่วนการเรียกใช้'), format=".1%")],
            ).properties(height=max(240, 18 * len(tasks))),
            alt=_('สัดส่วน tokens แยกตามประเภทงาน 7 วันล่าสุด'),
        )
        ui.openrouter_attribution(tasks["as_of"].max())
