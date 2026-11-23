import altair as alt
import pandas as pd
import streamlit as st

from lib.i18n import _, lang

from lib import data, ui

ui.page_header(_('วงจรชีวิตโมเดล'), ":material/event_upcoming:",
               _('การเปิดตัว ประกาศเลิกใช้ และวันปิดให้บริการ เพื่อวางแผนย้ายโมเดลก่อนถูกปิด'))
ui.freshness()

life = data.lifecycle()
life = life[life["vendor_name"].isin(data.selected_vendors())]
today = pd.Timestamp.now(tz=data.TZ).normalize().tz_localize(None)
official = life[life["source_type"] == "official"]

retire = official[(official["event_type"] == "retired") & (official["event_date"] >= today)]
firm = retire[~retire["is_tentative"]]
derived = life[life["source_type"] == "derived"]
recent_new = derived[(derived["event_type"] == "first_seen_openrouter")
                     & (derived["event_date"] >= today - pd.Timedelta(days=30))]
removed = derived[(derived["event_type"] == "removed_from_openrouter")
                  & (derived["event_date"] >= today - pd.Timedelta(days=30))]

with st.container(horizontal=True, gap="medium"):
    st.metric(_('จะปิดใน 90 วัน'), int((firm["event_date"] <= today + pd.Timedelta(days=90)).sum()),
              border=True, help=_("นับตามชื่อ API จากประกาศทางการ ไม่นับวันที่แบบ 'ไม่เร็วกว่า' รุ่นที่มีวันที่ต่อท้าย (เช่น o3-2025-04-16) นับแยก"))
    st.metric(_('ประกาศปิดแล้วทั้งหมด'), len(firm), border=True, help=_('วันปิดให้บริการในอนาคตจากประกาศทางการ'))
    st.metric(_('ขึ้น OpenRouter ใหม่ 30 วัน'), len(recent_new), border=True, help=_('คำนวณจากวันที่สร้างบน OpenRouter'))
    st.metric(_('ถูกถอดจาก OpenRouter 30 วัน'), len(removed), border=True, help=_('คำนวณจากโมเดลที่หายไปจาก /models'))

# ------------------------------------------------------------------ upcoming
st.subheader(_('กำหนดปิดให้บริการที่ใกล้เข้ามา'), icon=":material/schedule:")
ui.provenance("official", note=_('จากหน้าประกาศเลิกใช้ของผู้ให้บริการแต่ละเจ้า'))
tentative = st.toggle(_("รวมวันที่แบบ 'เร็วที่สุดที่เป็นไปได้'"), value=False, key="lc_tentative",
                      help=_("Anthropic และ Google ระบุวันที่แบบ 'ไม่เร็วกว่า' ไว้กับโมเดลที่ยังใช้งานได้ ซึ่งไม่ใช่ประกาศปิดจริง"))
up = (retire if tentative else firm).sort_values("event_date")
if up.empty:
    st.caption(_('ยังไม่มีประกาศปิดให้บริการสำหรับผู้ให้บริการที่เลือก'))
else:
    st.dataframe(
        pd.DataFrame({
            _('วันที่จะปิดโมเดล'): up["event_date"].dt.date,
            _('อีกกี่วันจะปิดโมเดล'): (up["event_date"] - today).dt.days,
            _('ผู้ให้บริการ'): ui.as_list(up["vendor_name"]), _('โมเดล (ชื่อ API)'): up["model_api_name"],
            _('รุ่นที่แนะนำให้ใช้แทน'): up["replacement"].fillna(""),
            _('ประเภท'): up["is_tentative"].map(lambda t: [_('เร็วที่สุดที่เป็นไปได้')] if t else [_('ประกาศแล้ว')]),
            _('แหล่ง'): up["source_url"],
        }),
        hide_index=True, height=min(460, 38 + 35 * len(up)), alt=_('กำหนดปิดให้บริการโมเดลจากประกาศทางการ'),
        column_config={
            _('วันที่จะปิดโมเดล'): st.column_config.DateColumn(format="DD MMM YYYY", help=_('วันที่ผู้ให้บริการจะปิดโมเดลนี้บน API ของตัวเอง หลังวันนี้ request ที่ใช้ชื่อนี้จะ error')),
            _('อีกกี่วันจะปิดโมเดล'): st.column_config.NumberColumn(format="%d วัน" if lang() == "TH" else "%d days",
                                                                    help=_('นับจากวันนี้ถึงวันปิด (คำนวณใหม่ทุกครั้งที่เปิดหน้า)')),
            _('ผู้ให้บริการ'): ui.vendor_column(),
            _('ประเภท'): st.column_config.MultiselectColumn(options=[_('ประกาศแล้ว'), _('เร็วที่สุดที่เป็นไปได้')],
                                                         color=["red", "gray"]),
            _('แหล่ง'): st.column_config.LinkColumn(display_text=_('หน้าประกาศ')),
        },
    )

# ------------------------------------------------------------------ timeline
st.subheader(_('ไทม์ไลน์'), icon=":material/timeline:")
ui.provenance("official", note=_('ย้อนหลัง 1 ปี และล่วงหน้า 1 ปี · เส้นประ = วันนี้'))
window = official[(official["event_date"] >= today - pd.Timedelta(days=365))
                  & (official["event_date"] <= today + pd.Timedelta(days=365))
                  & official["event_type"].isin(["released", "deprecated", "retired"])]
if not tentative:
    window = window[~window["is_tentative"]]
if window.empty:
    st.caption(_('ไม่มีเหตุการณ์ทางการในช่วงนี้'))
else:
    pts = window.assign(event=window["event_type"].map(ui.EVENT_TH))
    lo, hi = today - pd.Timedelta(days=365), today + pd.Timedelta(days=365)
    # Rows are placed on a numeric axis (one unit per vendor, event types at -/0/+ offsets) so the
    # alternating lane bands can span the full row height and line up with the vendor labels.
    vidx = {v: i for i, v in enumerate(data.VENDORS)}
    off = {_('เปิดตัว'): 0.27, _('ประกาศเลิกใช้'): 0.0, _('ปิดให้บริการ'): -0.27}
    pts = pts.assign(row=pts["vendor_name"].map(vidx) - pts["event"].map(off))
    names = "[" + ",".join(f"'{v}'" for v in data.VENDORS) + "]"
    y_scale = alt.Scale(domain=[-0.5, len(data.VENDORS) - 0.5], nice=False, zero=False, reverse=True)
    y_axis = alt.Axis(values=list(range(len(data.VENDORS))), labelExpr=f"{names}[datum.value]", grid=False,
                      domain=False, ticks=False, labelFontSize=12, labelFontWeight=600, labelPadding=8)
    x = alt.X("event_date:T", title=None, scale=alt.Scale(domain=[lo, hi]),
              axis=alt.Axis(format="%b %Y", tickCount={"interval": "month", "step": 2}, labelAngle=0,
                            grid=True, gridOpacity=0.4, labelFontSize=11, labelOverlap=True))
    lanes = pd.DataFrame({"y0": [i - 0.5 for i in range(len(data.VENDORS))],
                          "y1": [i + 0.5 for i in range(len(data.VENDORS))],
                          "x0": lo, "x1": hi, "band": [i % 2 for i in range(len(data.VENDORS))]})
    bands = alt.Chart(lanes[lanes["band"] == 0]).mark_rect(opacity=0.09, color="#8A8F99").encode(
        x=alt.X("x0:T", scale=alt.Scale(domain=[lo, hi])), x2="x1:T",
        y=alt.Y("y0:Q", scale=y_scale, axis=y_axis, title=None), y2="y1:Q")
    seps = alt.Chart(lanes).mark_rule(opacity=0.25, color="#8A8F99").encode(y=alt.Y("y0:Q", scale=y_scale))
    dots = alt.Chart(pts).mark_point(filled=True, size=110, opacity=0.9, strokeWidth=0).encode(
        x=x, y=alt.Y("row:Q", scale=y_scale, axis=y_axis, title=None),
        color=ui.vendor_color(legend=False),
        shape=alt.Shape("event:N", title=None,
                        legend=alt.Legend(orient="top", symbolFillColor="#8A8F99", symbolStrokeColor="#8A8F99",
                                          symbolSize=110),
                        scale=alt.Scale(domain=[_('เปิดตัว'), _('ประกาศเลิกใช้'), _('ปิดให้บริการ')],
                                        range=["circle", "triangle-down", "cross"])),
        tooltip=[alt.Tooltip("event_date:T", title=_('วันที่'), format="%d %b %Y"),
                 alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                 alt.Tooltip("event:N", title=_('เหตุการณ์')), alt.Tooltip("model_api_name:N", title=_('โมเดล')),
                 alt.Tooltip("replacement:N", title=_('รุ่นที่ใช้แทน'))],
    )
    now = pd.DataFrame({"d": [today], "label": [_('วันนี้')]})
    rule = alt.Chart(now).mark_rule(strokeDash=[4, 4], strokeWidth=1.5, color="#C2352B", opacity=0.8).encode(x="d:T")
    rule_label = alt.Chart(now).mark_text(align="left", dx=4, dy=-6, fontSize=11, fontWeight=600,
                                          color="#C2352B").encode(x="d:T", y=alt.value(0), text="label:N")
    st.altair_chart((bands + seps + dots + rule + rule_label).properties(height=360),
                    alt=_('การเปิดตัว ประกาศเลิกใช้ และปิดให้บริการ แยกตามผู้ให้บริการ เส้นประคือวันนี้'))
    st.caption(_('แต่ละแถว = 1 ผู้ให้บริการ · เส้นตั้งจาง = ต้นเดือน · เส้นประสีแดง = วันนี้ · ชี้ที่จุดเพื่อดูชื่อโมเดล'))

# ------------------------------------------------------------------ OpenRouter movements
st.subheader(_('ความเคลื่อนไหวบน OpenRouter'), icon=":material/swap_vert:")
ui.provenance("derived", note=_('สัญญาณรอง: โมเดลที่ปรากฏหรือหายไปจาก /models ของ OpenRouter'))
moves = derived[derived["event_type"].isin(["first_seen_openrouter", "removed_from_openrouter"])
                & (derived["event_date"] >= today - pd.Timedelta(days=60))].sort_values("event_date", ascending=False)
if moves.empty:
    st.caption(_('ไม่มีโมเดลเพิ่มหรือถูกถอดในช่วง 60 วันที่ผ่านมา'))
else:
    st.dataframe(
        pd.DataFrame({
            _('วันที่'): moves["event_date"].dt.date, _('ผู้ให้บริการ'): ui.as_list(moves["vendor_name"]),
            _('โมเดล'): moves["model_name"].map(ui.short_name).where(moves["model_name"].notna(), moves["model_api_name"]),
            _('การเปลี่ยนแปลง'): moves["event_type"].map(lambda e: [_('เพิ่มใหม่')] if e == "first_seen_openrouter" else [_('ถูกถอด')]),
        }),
        hide_index=True, alt=_('โมเดลที่เพิ่มหรือถูกถอดจาก OpenRouter ใน 60 วันที่ผ่านมา'),
        column_config={_('วันที่'): st.column_config.DateColumn(format="DD MMM YYYY"),
                       _('ผู้ให้บริการ'): ui.vendor_column(),
                       _('การเปลี่ยนแปลง'): st.column_config.MultiselectColumn(options=[_('เพิ่มใหม่'), _('ถูกถอด')],
                                                                             color=["green", "red"])},
    )
