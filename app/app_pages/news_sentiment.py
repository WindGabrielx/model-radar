import altair as alt
import pandas as pd
import streamlit as st

from lib.i18n import _

from lib import data, ui

ui.page_header(_('ข่าวและความเห็น'), ":material/forum:",
               _('นักพัฒนาและสื่อพูดถึงแต่ละผู้ให้บริการว่าอย่างไร และชมหรือติเรื่องไหน'))
ui.freshness()

vendors = data.selected_vendors()
ASPECT_TH = {"coding": _('เขียนโค้ด'), "reasoning": _('การให้เหตุผล'), "writing": _('การเขียน'), "accuracy": _('ความถูกต้อง'),
             "speed": _('ความเร็ว'), "pricing": _('ราคา'), "reliability": _('ความเสถียร'), "context_window": "Context",
             "user_experience": _('ประสบการณ์ใช้งาน'), "multimodal": "Multimodal"}

# ------------------------------------------------------------------ sentiment
POL = {"positive": _('บวก'), "negative": _('ลบ'), "neutral": _('กลางๆ'), "mixed": _('ปนกัน')}
INTENT = {"comparison": _('เปรียบเทียบ'), "recommendation": _('แนะนำ'), "complaint": _('บ่น'), "question": _('ถาม'), "informational": _('ให้ข้อมูล')}
ASPECT_ORDER = list(ASPECT_TH)

st.subheader(_('ความเห็นของนักพัฒนา 30 วันล่าสุด'), icon=":material/sentiment_satisfied:")
evals = data.model_eval_latest()
macro = evals[(evals["task"] == "polarity") & (evals["label"] == "macro")] if not evals.empty else evals
if macro.empty:
    eval_tag = ":orange-badge[:material/pending: " + _('ยังไม่ผ่านการวัดผล') + "]"
else:
    eval_tag = (":green-badge[:material/fact_check: " + _('วัดผลแล้ว') + f" F1 {float(macro['f1'].iloc[0]):.2f}]")
ui.provenance("derived", note=eval_tag + " " + _('จากคอมเมนต์ Hacker News ให้คะแนนด้วยโมเดลในเครื่อง · −1 = ลบทั้งหมด, +1 = บวกทั้งหมด'))

win = data.sentiment_vendor_window()
win = win[win["vendor_name"].isin(vendors)]
if win["n_30d"].fillna(0).sum() == 0:
    ui.empty_state(_('ยังไม่มีคะแนนความเห็น'),
                   _('โมเดลจะให้คะแนนคอมเมนต์ใหม่ทุกวันในขั้นตอน 06b Sentiment ของ pipeline'), ":material/model_training:")
else:
    with st.grid(len(win), gap="medium", min_column_width=150):
        for _i, r in win.iterrows():
            with st.container(border=True, height="stretch"):
                has = pd.notna(r["score_30d"])
                delta = (r["score_30d"] - r["score_prev_30d"]) if has and pd.notna(r["score_prev_30d"]) else None
                st.metric(r["vendor_name"], f"{r['score_30d']:+.2f}" if has else "–",
                          f"{delta:+.2f}" if delta is not None else None,
                          delta_description=_('เทียบ 30 วันก่อนหน้า') if delta is not None else None)
                st.markdown(f"{ui.vendor_badge(r['vendor_name'])} · " +
                            _('{0:,} คอมเมนต์').format(int(r["n_30d"] or 0)))

    asp = data.sentiment_aspect_30d()
    asp = asp[asp["vendor_name"].isin(vendors)].assign(aspect_th=lambda df: df["aspect"].map(ASPECT_TH))
    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True, height="stretch"):
        st.markdown(_('**ชมหรือติเรื่องไหน** (แดง = ลบ, น้ำเงิน = บวก, ตัวเลข = คะแนน, ชี้เพื่อดูจำนวน)'))
        if asp.empty:
            st.caption(_('ยังไม่มีคอมเมนต์ที่พูดถึงประเด็นเฉพาะ'))
        else:
            order = [ASPECT_TH[a] for a in ASPECT_ORDER]
            base = alt.Chart(asp).encode(x=alt.X("aspect_th:N", title=None, sort=order,
                                                 axis=alt.Axis(labelAngle=-35, labelOverlap=False, labelLimit=120)),
                                         y=alt.Y("vendor_name:N", title=None, sort=data.VENDORS))
            rect = base.mark_rect(cornerRadius=3).encode(
                color=alt.Color("score:Q", title=_('คะแนน'), scale=alt.Scale(scheme="redblue", domain=[-1, 1])),
                tooltip=[alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                         alt.Tooltip("aspect_th:N", title=_('ประเด็น')),
                         alt.Tooltip("score:Q", title=_('คะแนน'), format="+.2f"),
                         alt.Tooltip("n:Q", title=_('จำนวนครั้ง')),
                         alt.Tooltip("n_positive:Q", title=POL["positive"]), alt.Tooltip("n_negative:Q", title=POL["negative"])])
            txt = base.mark_text(fontSize=11).encode(
                text=alt.Text("score:Q", format="+.1f"),
                color=alt.condition("abs(datum.score) > 0.5", alt.value("white"), alt.value("#16181D")))
            st.altair_chart((rect + txt).properties(height=60 + 36 * asp["vendor_name"].nunique()),
                            alt=_('คะแนนความเห็นแยกตามผู้ให้บริการและประเด็น 30 วันล่าสุด'))
    with right, st.container(border=True, height="stretch"):
        st.markdown(_('**แนวโน้มรายสัปดาห์**'))
        weekly = data.sentiment_weekly()
        weekly = weekly[(weekly["aspect"] == "overall") & weekly["vendor_name"].isin(vendors)]
        if weekly["week_start"].nunique() < 2:
            st.caption(_('ต้องมีข้อมูลอย่างน้อย 2 สัปดาห์จึงจะเห็นแนวโน้ม'))
        line = alt.Chart(weekly).mark_line(point=True).encode(
            x=alt.X("week_start:T", title=None, axis=alt.Axis(format="%d %b")),
            y=alt.Y("sentiment_score:Q", title=_('คะแนนเฉลี่ย'), scale=alt.Scale(domain=[-1, 1])),
            color=ui.vendor_color(),
            tooltip=[alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                     alt.Tooltip("week_start:T", title=_('สัปดาห์ของ'), format="%d %b"),
                     alt.Tooltip("sentiment_score:Q", title=_('คะแนน'), format="+.2f"),
                     alt.Tooltip("n_mentions:Q", title=_('จำนวนครั้ง'))])
        marks = data.release_markers()
        marks = marks[marks["vendor_name"].isin(vendors) & (marks["event_date"] >= weekly["week_start"].min())] \
            if not weekly.empty else marks.iloc[0:0]
        rules = alt.Chart(marks).mark_rule(strokeDash=[4, 4], opacity=0.5).encode(
            x="event_date:T", color=ui.vendor_color(legend=False),
            tooltip=[alt.Tooltip("label:N", title=_('เปิดตัว')), alt.Tooltip("event_date:T", format="%d %b %Y")])
        st.altair_chart((line + rules).properties(height=60 + 36 * max(asp["vendor_name"].nunique(), 4)),
                        alt=_('คะแนนความเห็นรายสัปดาห์ เส้นประคือวันเปิดตัวโมเดล'))

    st.markdown(_('**เลือกผู้ให้บริการเพื่อดูรายละเอียด**'))
    pick = st.segmented_control(_('ผู้ให้บริการ'), [v for v in data.VENDORS if v in vendors],
                                default=next((v for v in data.VENDORS if v in vendors), None),
                                key="sent_vendor", label_visibility="collapsed")
    if pick:
        vid = dict(zip(win["vendor_name"], win["vendor_id"]))[pick]
        c1, c2 = st.columns([2, 3], gap="medium")
        with c1, st.container(border=True, height="stretch"):
            st.markdown(_('**{0}: บวก vs ลบ แยกตามประเด็น**').format(pick))
            mine = asp[asp["vendor_name"] == pick]
            if mine.empty:
                st.caption(_('ยังไม่มีคอมเมนต์ที่พูดถึงประเด็นเฉพาะ'))
            else:
                pn = pd.concat([mine.assign(kind=POL["positive"], count=mine["n_positive"]),
                                mine.assign(kind=POL["negative"], count=-mine["n_negative"])])
                st.altair_chart(alt.Chart(pn).mark_bar(cornerRadius=2).encode(
                    x=alt.X("count:Q", title=_('← ลบ   จำนวนคอมเมนต์   บวก →')),
                    y=alt.Y("aspect_th:N", title=None, sort=[ASPECT_TH[a] for a in ASPECT_ORDER]),
                    color=alt.Color("kind:N", title=None, legend=alt.Legend(orient="top"),
                                    scale=alt.Scale(domain=[POL["positive"], POL["negative"]], range=["#2F6FE0", "#C2352B"])),
                    tooltip=[alt.Tooltip("aspect_th:N", title=_('ประเด็น')), "kind:N",
                             alt.Tooltip("count:Q", title=_('จำนวน'))],
                ).properties(height=40 + 28 * len(mine)),
                    alt=_('จำนวนคอมเมนต์บวกและลบต่อประเด็น'))
        with c2, st.container(border=True, height="stretch"):
            st.markdown(_('**ตัวอย่างคอมเมนต์ของ {0}**').format(pick))
            f1, f2 = st.columns(2)
            pol_f = f1.pills(_('ความเห็น'), list(POL), format_func=POL.get, selection_mode="multi",
                             default=["positive", "negative"], key="sent_pol")
            asp_f = f2.selectbox(_('ประเด็น'), ["*"] + ASPECT_ORDER,
                                 format_func=lambda a: _('ทุกประเด็น') if a == "*" else ASPECT_TH[a],
                                 key="sent_asp")
            ex = data.sentiment_examples(vid)
            if pol_f:
                ex = ex[ex["polarity"].isin(pol_f)]
            if asp_f != "*":
                ex = ex[ex["aspects"].apply(lambda xs: any(str(x).startswith(asp_f + ":") for x in (xs or [])))]
            if ex.empty:
                st.caption(_('ไม่มีคอมเมนต์ที่ตรงกับตัวกรอง'))
            else:
                st.dataframe(pd.DataFrame({
                    _('ความเห็น'): ex["polarity"].map(lambda p: [POL.get(p, p)]),
                    _('ประเด็น'): ex["aspects"].apply(
                        lambda xs: [ASPECT_TH.get(str(x).split(":")[0], x) for x in (xs or [])]),
                    _('เจตนา'): ex["intent"].map(INTENT),
                    _('คอมเมนต์'): ex["excerpt"],
                    _('ความมั่นใจ'): ex["confidence"],
                    _('กระทู้'): ex["url"],
                }).head(60), hide_index=True, height=360,
                    alt=_('ตัวอย่างคอมเมนต์พร้อมผลวิเคราะห์'),
                    column_config={
                        _('ความเห็น'): st.column_config.MultiselectColumn(
                            options=list(POL.values()), color=["blue", "red", "gray", "orange"]),
                        _('ประเด็น'): st.column_config.MultiselectColumn(
                            options=list(ASPECT_TH.values()), color="auto"),
                        _('คอมเมนต์'): st.column_config.TextColumn(width="large"),
                        _('ความมั่นใจ'): st.column_config.ProgressColumn(
                            min_value=0, max_value=1, format="percent",
                            help=_('ความมั่นใจของโมเดลต่อคำตอบความเห็น')),
                        _('กระทู้'): st.column_config.LinkColumn(display_text=_('เปิด')),
                    })

# ------------------------------------------------------------------ coverage
news = data.news()
news = news[news["vendors"].apply(lambda vs: any(v in vendors for v in vs))]
st.subheader(_('ปริมาณการถูกพูดถึง'), icon=":material/bar_chart:")
ui.provenance("derived", note=_('จำนวนข่าว/กระทู้ที่จับคู่ได้ต่อสัปดาห์ (ต้องมีชื่อผู้ให้บริการ + คำบริบท ตาม §8.1)'))
if news.empty:
    st.caption(_('ยังไม่มีข่าวที่จับคู่ได้'))
else:
    weekly = news.explode("vendors")
    weekly = weekly[weekly["vendors"].isin(vendors)]
    weekly = weekly.assign(week=weekly["published_at"].dt.tz_localize(None).dt.to_period("W").dt.start_time)
    weekly = weekly[weekly["week"] >= weekly["week"].max() - pd.Timedelta(weeks=12)]
    counts = weekly.groupby(["week", "vendors"], as_index=False).size().rename(columns={"vendors": "vendor_name"})
    st.altair_chart(
        alt.Chart(counts).mark_bar().encode(
            x=alt.X("week:T", title=None), y=alt.Y("size:Q", title=_('จำนวนครั้ง')),
            color=ui.vendor_color(), xOffset="vendor_name:N",
            tooltip=[alt.Tooltip("week:T", title=_('สัปดาห์ของ')), alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                     alt.Tooltip("size:Q", title=_('จำนวนครั้ง'))],
        ).properties(height=260),
        alt=_('จำนวนครั้งที่ถูกพูดถึงรายสัปดาห์ 12 สัปดาห์ล่าสุด'),
    )

# ------------------------------------------------------------------ feed
st.subheader(_('ข่าวล่าสุด'), icon=":material/newspaper:")
source_labels = {"hn": "Hacker News", "rss_openai": _('ข่าว OpenAI'), "rss_deepmind": _('บล็อก Google DeepMind'),
                 "gdelt": _('สื่อทั่วไป (GDELT)')}
f1, f2 = st.columns([2, 3], vertical_alignment="bottom")
sources = f1.pills(_('แหล่ง'), list(source_labels), selection_mode="multi", default=list(source_labels),
                   format_func=source_labels.get, key="news_sources")
search = f2.text_input(_('ค้นหาในหัวข้อ'), placeholder=_('เช่น pricing, coding, outage'), key="news_search")
feed = news[news["source"].isin(sources or list(source_labels))]
if search:
    feed = feed[feed["title"].str.contains(search, case=False, regex=False)]
if feed.empty:
    st.caption(_('ไม่พบรายการ ลองล้างคำค้นหรือเพิ่มแหล่ง'))
else:
    discussion = feed["news_id"].where(feed["source"] == "hn").map(
        lambda nid: f"https://news.ycombinator.com/item?id={nid.split(':', 1)[1]}" if isinstance(nid, str) else None)
    st.dataframe(
        pd.DataFrame({
            _('เวลา'): feed["published_at"].dt.tz_localize(None),
            _('ผู้ให้บริการ'): feed["vendors"], _('หัวข้อ'): feed["title"],
            _('แหล่ง'): feed["source"].map(source_labels), _('คะแนน HN'): feed["points"],
            _('คอมเมนต์'): feed["num_comments"], _('ต้นฉบับ'): feed["url"], _('กระทู้ HN'): discussion,
        }).head(500),
        hide_index=True, height=460, alt=_('ข่าวล่าสุดที่จับคู่ได้ พร้อมลิงก์ต้นฉบับ'),
        column_config={
            _('เวลา'): st.column_config.DatetimeColumn(format="DD MMM, HH:mm", help=_('เวลาไทย')),
            _('ผู้ให้บริการ'): st.column_config.MultiselectColumn(options=list(ui.VENDOR_COLOR),
                                                                color=list(ui.VENDOR_COLOR.values())),
            _('หัวข้อ'): st.column_config.TextColumn(width="large"),
            _('ต้นฉบับ'): st.column_config.LinkColumn(display_text=_('เปิด'),
                                                   help=_('บางเว็บต้องสมัครสมาชิกก่อนอ่าน (paywall)')),
            _('กระทู้ HN'): st.column_config.LinkColumn(display_text=_('อ่านคอมเมนต์'),
                                                     help=_('หน้าคอมเมนต์บน Hacker News อ่านได้ฟรีเสมอ')),
        },
    )
    st.caption(_('{0:,} รายการใน 90 วันล่าสุด').format(len(feed)) + (_(' (แสดง 500 รายการล่าสุด)') if len(feed) > 500 else "")
               + _(' · ลิงก์ต้นฉบับบางเว็บติด paywall แต่ไม่กระทบคะแนน เพราะระบบนับจากหัวข้อและคอมเมนต์ใน HN'))
