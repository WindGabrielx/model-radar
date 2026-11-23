import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from lib.i18n import _

from lib import data, ui

ui.page_header(_('ราคาและความคุ้มค่า'), ":material/payments:",
               _('โมเดลไหนให้คุณภาพต่อเงินมากที่สุด และอันดับเปลี่ยนแค่ไหนเมื่อให้น้ำหนักกับสิ่งที่คุณสนใจต่างกัน'))
as_of = ui.freshness()
ui.glossary()

models = data.filtered_models()
ui.filter_note(len(models))
mode = st.segmented_control(_('รูปแบบราคา'), ["Standard", "Batch"], default="Standard", required=True, key="pv_mode",
                            help=_('ราคา Batch มาจากรุ่น :batch บน OpenRouter (ไม่ใช่ทุกโมเดลที่มี) ถูกกว่าแต่ได้ผลช้ากว่า'))
st.caption(_('**Standard** = ส่งคำขอแล้วได้คำตอบทันที (ราคาปกติ) · **Batch** = ส่งงานเป็นชุดแล้วรอผลภายหลัง (มักภายใน 24 ชม.) แลกกับราคาถูกลงราวครึ่งหนึ่ง เหมาะกับงานที่ไม่รีบ เช่น สรุปเอกสารจำนวนมาก · ไม่ใช่ทุกโมเดลที่มีราคา Batch'))
price_col = "blended_price_per_m" if mode == "Standard" else "batch_blended_price_per_m"
models = models.assign(price=models[price_col], value=models["quality"] / models[price_col].where(models[price_col] > 0))
scored = models.dropna(subset=["quality", "price"])
scored = scored[scored["price"] > 0]

if scored.empty:
    ui.empty_state(_('ยังไม่มีอะไรให้เปรียบเทียบ'),
                   _('ไม่มีโมเดลในตัวกรองนี้ที่มีทั้งคะแนนคุณภาพและราคา') + (_(' แบบ Batch') if mode == "Batch" else ""),
                   ":material/filter_alt_off:")
    st.stop()

# ------------------------------------------------------------------ quality vs price
left, right = st.columns([3, 2], gap="medium")
with left, st.container(border=True, height="stretch"):
    st.subheader(_('คุณภาพเทียบกับราคา'))
    ui.provenance("third-party", "derived")
    pts = scored.assign(model=scored["model_name"].map(ui.short_name))
    base = alt.Chart(pts).encode(
        x=alt.X("price:Q", title=_('ราคาเฉลี่ย USD ต่อ 1M tokens (สเกล log)'), scale=alt.Scale(type="log")),
        y=alt.Y("quality:Q", title=_('ดัชนีคุณภาพ'), scale=alt.Scale(zero=False)),
    )
    dots = base.mark_circle(size=120, opacity=0.9, stroke="white", strokeWidth=0.6).encode(
        color=ui.vendor_color(),
        tooltip=[alt.Tooltip("model:N", title=_('โมเดล')), alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                 alt.Tooltip("quality:Q", title=_('คุณภาพ'), format=".1f"),
                 alt.Tooltip("price:Q", title=_('ราคาเฉลี่ย $/1M'), format="$.2f"),
                 alt.Tooltip("value:Q", title=_('ความคุ้มค่า (คะแนน/$)'), format=".1f")],
    )
    # Every point carries its model name, in its vendor colour. Scroll to zoom into the dense cluster.
    labels = base.mark_text(align="left", dx=8, dy=-1, fontSize=11, fontWeight=500).encode(
        text="model:N", color=ui.vendor_color(legend=False))
    st.altair_chart((dots + labels).properties(height=460).interactive(),
                    alt=_('คุณภาพเทียบราคาของทุกโมเดล ยิ่งอยู่ซ้ายบนยิ่งคุ้ม'))
    missing = len(models) - len(scored)
    st.caption(_('ซ้ายบน = คุณภาพสูงแต่ราคาถูก · เลื่อนลูกกลิ้งเมาส์เพื่อซูม ลากเพื่อเลื่อน ดับเบิลคลิกเพื่อรีเซ็ต')
               + (_(' · ซ่อน {0} โมเดลที่ยังไม่มีคะแนนคุณภาพหรือราคาแบบ {1}').format(missing, mode) if missing else ""))

with right, st.container(border=True, height="stretch"):
    st.subheader(_('อันดับความคุ้มค่า'))
    ui.provenance("derived", note=_('คุณภาพ ÷ ราคาเฉลี่ย'))
    top = scored.nlargest(12, "value").assign(model=lambda d: d["model_name"].map(ui.short_name))
    st.altair_chart(
        alt.Chart(top).mark_bar(cornerRadiusEnd=4, height=16).encode(
            x=alt.X("value:Q", title=_('คะแนนคุณภาพต่อ $1 (สเกล log)'), scale=alt.Scale(type="log", domainMin=1)), x2=alt.datum(1),
            y=alt.Y("model:N", sort="-x", title=None),
            color=ui.vendor_color(legend=False),
            tooltip=[alt.Tooltip("model:N", title=_('โมเดล')), alt.Tooltip("value:Q", title=_('ความคุ้มค่า'), format=".1f"),
                     alt.Tooltip("price:Q", title=_('ราคาเฉลี่ย $/1M'), format="$.2f")],
        ).properties(height=460),
        alt=_('12 โมเดลที่คุ้มค่าที่สุด อันดับหนึ่งคือ {0}').format(top.iloc[0]['model']),
    )

# ------------------------------------------------------------------ composite score + sensitivity
st.subheader(_('ให้น้ำหนักตามที่คุณสนใจ'), icon=":material/tune:")
ui.provenance("derived", note=_('ปรับสเกล 0–1 เทียบกันภายในตัวกรองนี้ ควรดูคู่กับค่าจริงของแต่ละด้านเสมอ'))

has_speed = scored["throughput_p50"].notna().any()
c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
w_q = c1.slider(_('น้ำหนักคุณภาพ'), 0.0, 1.0, 0.5, 0.05, key="w_q")
w_p = c2.slider(_('น้ำหนักราคา'), 0.0, 1.0, 0.3, 0.05, key="w_p", help=_('ยิ่งราคาถูกยิ่งได้คะแนน'))
w_s = c3.slider(_('น้ำหนักความเร็ว'), 0.0, 1.0, 0.2, 0.05, key="w_s", disabled=not has_speed,
                help=None if has_speed else _('ข้อมูลความเร็วต้องใช้ OPENROUTER_API_KEY ระหว่างนี้จะไม่นำน้ำหนักนี้มาคิด'))
if not has_speed:
    w_s = 0.0


def norm(s: pd.Series) -> pd.Series:
    rng = s.max() - s.min()
    return (s - s.min()) / rng if rng else s * 0 + 1


def composite(df: pd.DataFrame, wq: float, wp: float, ws: float) -> pd.Series:
    total = wq + wp + ws or 1
    score = wq * norm(df["quality"]) + wp * norm(1 / df["price"])
    if ws:
        score = score + ws * norm(df["throughput_p50"]).fillna(0)
    return score / total


ranked = scored.assign(score=composite(scored, w_q, w_p, w_s)).sort_values("score", ascending=False)
ranked["rank"] = np.arange(1, len(ranked) + 1)
board, _dropped = ui.drop_empty(pd.DataFrame({
    _('อันดับ'): ranked["rank"], _('โมเดล'): ranked["model_name"].map(ui.short_name),
    _('ผู้ให้บริการ'): ui.as_list(ranked["vendor_name"]), _('คะแนนรวม'): ranked["score"], _('คุณภาพ'): ranked["quality"],
    _('ราคาเฉลี่ย $/1M'): ranked["price"], _('ความเร็ว tok/s'): ranked["throughput_p50"]}).head(15), keep=[])
st.dataframe(
    board, hide_index=True, alt=_('15 อันดับแรกตามคะแนนรวม พร้อมค่าจริงของแต่ละด้าน'),
    column_config={
        _('ผู้ให้บริการ'): ui.vendor_column(),
        _('คะแนนรวม'): st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
        _('คุณภาพ'): st.column_config.NumberColumn(format="%.1f"),
        _('ราคาเฉลี่ย $/1M'): st.column_config.NumberColumn(format="dollar"),
        _('ความเร็ว tok/s'): st.column_config.NumberColumn(format="%.0f"),
    },
)

with st.container(border=True):
    st.markdown(_('**อันดับนิ่งแค่ไหน?** อันดับของ 8 โมเดลแรกเมื่อเลื่อนน้ำหนักคุณภาพจากน้อยไปมาก (ราคาและความเร็วคงสัดส่วนเดิม) เส้นที่ราบแปลว่าอันดับไม่ค่อยขึ้นกับน้ำหนัก'))
    rest = (w_p + w_s) or 1
    rows = []
    leaders = ranked.head(8)["model_id"].tolist()
    for wq in np.linspace(0.05, 0.95, 19):
        r = scored.assign(score=composite(scored, wq, (1 - wq) * w_p / rest, (1 - wq) * w_s / rest))
        r = r.sort_values("score", ascending=False).reset_index(drop=True)
        r["rank"] = r.index + 1
        for _i, row in r[r["model_id"].isin(leaders)].iterrows():
            rows.append({"quality_weight": wq, "model": ui.short_name(row["model_name"]),
                         "vendor_name": row["vendor_name"], "rank": row["rank"]})
    stab = pd.DataFrame(rows)
    st.altair_chart(
        alt.Chart(stab).mark_line(point=True, strokeWidth=2).encode(
            x=alt.X("quality_weight:Q", title=_('น้ำหนักคุณภาพ'), axis=alt.Axis(format=".0%")),
            y=alt.Y("rank:Q", title=_('อันดับ'), scale=alt.Scale(reverse=True, domainMin=1)),
            color=alt.Color("model:N", title=None, legend=alt.Legend(orient="right")),
            detail="model:N",
            tooltip=[alt.Tooltip("model:N", title=_('โมเดล')), alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                     alt.Tooltip("rank:Q", title=_('อันดับ')),
                     alt.Tooltip("quality_weight:Q", title=_('น้ำหนักคุณภาพ'), format=".0%")],
        ).properties(height=300),
        alt=_('อันดับของ 8 โมเดลแรกเมื่อน้ำหนักคุณภาพเปลี่ยน'),
    )

# ------------------------------------------------------------------ full lineup
st.subheader(_('โมเดลทั้งหมด'), icon=":material/table_rows:")
ui.provenance("third-party", "derived", note=_('ราคาเป็น USD ต่อ 1M tokens'))
lineup = models.sort_values(["vendor_name", "blended_price_per_m"], ascending=[True, False])
st.dataframe(
    pd.DataFrame({
        _('ผู้ให้บริการ'): ui.as_list(lineup["vendor_name"]), _('โมเดล'): lineup["model_name"].map(ui.short_name),
        _('ระดับ'): lineup["tier"].map(ui.TIER_TH), "Context": lineup["context_length"],
        "Input": lineup["input_price_per_m"], "Output": lineup["output_price_per_m"],
        "Cache read": lineup["cache_read_price_per_m"], "Batch in": lineup["batch_input_price_per_m"],
        "Batch out": lineup["batch_output_price_per_m"], _('ราคาเฉลี่ย'): lineup["blended_price_per_m"],
        _('คุณภาพ'): lineup["quality"], _('ความคุ้มค่า'): lineup["value_score"], "OpenRouter id": lineup["model_id"],
    }),
    hide_index=True, height=420, alt=_('โมเดลทั้งหมดในตัวกรองนี้ พร้อมราคาและคุณภาพ'),
    column_config={
        _('ผู้ให้บริการ'): ui.vendor_column(pinned=True), _('โมเดล'): st.column_config.TextColumn(pinned=True),
        "Context": st.column_config.NumberColumn(format="compact", help=_('ความยาว context สูงสุด (tokens)')),
        **{c: st.column_config.NumberColumn(format="dollar") for c in
           ["Input", "Output", "Cache read", "Batch in", "Batch out", _('ราคาเฉลี่ย')]},
        _('คุณภาพ'): st.column_config.NumberColumn(format="%.1f"),
        _('ความคุ้มค่า'): st.column_config.NumberColumn(format="%.1f"),
    },
)
st.caption(_('ช่องว่าง = ยังไม่มีข้อมูล (เช่น โมเดลที่ Artificial Analysis ยังไม่ได้ให้คะแนน หรือไม่มีราคา Batch)'))

# ------------------------------------------------------------------ price history
st.subheader(_('ประวัติราคา'), icon=":material/timeline:")
hist = data.price_history()
hist = hist[hist["model_id"].isin(models["model_id"])]
flagships = (scored.sort_values("quality", ascending=False).groupby("vendor_name").head(1)["model_id"].tolist())
names = models.set_index("model_id")["model_name"]
choice = st.multiselect(_('เลือกโมเดล'), sorted(models["model_id"]), default=flagships[:5], key="ph_models",
                        format_func=lambda m: ui.short_name(names.get(m, m)))
h = hist[hist["model_id"].isin(choice)].assign(model=lambda d: d["model_name"].map(ui.short_name))
if hist["snapshot_date"].nunique() < 2:
    st.caption(_('เริ่มเก็บประวัติเมื่อ {0} กราฟจะยาวขึ้นวันละ 1 จุดทุกครั้งที่ระบบดึงข้อมูล').format(ui.th_date(hist['snapshot_date'].min())))
if not h.empty:
    st.altair_chart(
        alt.Chart(h).mark_line(point=True, interpolate="step-after").encode(
            x=alt.X("snapshot_date:T", title=None),
            y=alt.Y(f"{price_col}:Q", title=_('ราคาเฉลี่ย $/1M')),
            color=alt.Color("model:N", title=None, legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("model:N", title=_('โมเดล')), alt.Tooltip("snapshot_date:T", title=_('วันที่')),
                     alt.Tooltip(f"{price_col}:Q", title=_('ราคาเฉลี่ย $/1M'), format="$.2f")],
        ).properties(height=260),
        alt=_('ราคาเฉลี่ยตามเวลาของโมเดลที่เลือก'),
    )
ui.openrouter_attribution(as_of.get("models_as_of"), "models")
