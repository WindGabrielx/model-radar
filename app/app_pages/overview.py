import altair as alt
import pandas as pd
import streamlit as st

from lib.i18n import _

from lib import data, ui

ui.radar_header("AI model tracker",
                _('เปรียบเทียบโมเดล AI ของ 5 ผู้ให้บริการทุกวัน ทั้งคุณภาพ ราคา ความคุ้มค่า ความเร็ว การใช้งาน และวงจรชีวิต'))
as_of = ui.freshness()
ui.glossary()

models = data.filtered_models()
ui.filter_note(len(models))
if models.empty:
    ui.empty_state(_('ไม่มีโมเดลตรงกับตัวกรอง'), _('ลองเลือกผู้ให้บริการหรือระดับรุ่นเพิ่มในแถบด้านซ้าย'),
                   ":material/filter_alt_off:")
    st.stop()

scored = models.dropna(subset=["quality"])
priced = models[models["blended_price_per_m"] > 0]

# ------------------------------------------------------------------ KPI row
life = data.lifecycle()
today = pd.Timestamp.now(tz=data.TZ).normalize().tz_localize(None)
upcoming = life[(life["event_type"] == "retired") & (life["source_type"] == "official") & ~life["is_tentative"]
                & (life["event_date"] >= today) & life["vendor_name"].isin(data.selected_vendors())
                ].sort_values("event_date")

with st.grid(4, gap="medium", min_column_width=220):
    with st.container(border=True, height="stretch"):
        if not scored.empty:
            best = scored.loc[scored["quality"].idxmax()]
            st.metric(_('คุณภาพสูงสุด'), ui.num(best["quality"]),
                      help=_('ดัชนี Intelligence ของ Artificial Analysis (ผ่าน OpenRouter) · แหล่งภายนอก'))
            st.caption(_('ทำข้อสอบมาตรฐานได้คะแนนสูงสุด (ยิ่งสูงยิ่งเก่ง)'))
            st.markdown(f"{ui.short_name(best['model_name'])} {ui.vendor_badge(best['vendor_name'])}")
        else:
            st.metric(_('คุณภาพสูงสุด'), "–")
    with st.container(border=True, height="stretch"):
        val = scored[scored["value_score"].notna()]
        if not val.empty:
            v = val.loc[val["value_score"].idxmax()]
            st.metric(_('คุ้มค่าที่สุด'), _('{0:,.0f} คะแนน/$').format(v['value_score']),
                      help=_('คุณภาพ ÷ ราคาเฉลี่ยต่อ 1M tokens · คำนวณโดยระบบ'))
            st.caption(_('ได้คะแนนคุณภาพมากที่สุดต่อเงิน $1'))
            st.markdown(f"{ui.short_name(v['model_name'])} {ui.vendor_badge(v['vendor_name'])}")
            st.caption(_('ราคา {0} ต่อ 1M tokens').format(ui.money(v['blended_price_per_m'])))
        else:
            st.metric(_('คุ้มค่าที่สุด'), "–")
    with st.container(border=True, height="stretch"):
        top = priced[priced["tier"] == "top"]
        if not top.empty:
            c = top.loc[top["blended_price_per_m"].idxmin()]
            st.metric(_('รุ่นท็อปที่ถูกที่สุด'), ui.money(c["blended_price_per_m"]),
                      help=_('ราคาเฉลี่ย = (3 × input + output) ÷ 4 ต่อ 1M tokens · คำนวณจากราคา OpenRouter'))
            st.caption(_('รุ่นระดับท็อปที่ค่าใช้งานถูกที่สุด ต่อ 1M tokens'))
            st.markdown(f"{ui.short_name(c['model_name'])} {ui.vendor_badge(c['vendor_name'])}")
        else:
            st.metric(_('รุ่นท็อปที่ถูกที่สุด'), "–")
    with st.container(border=True, height="stretch"):
        if not upcoming.empty:
            n = upcoming.iloc[0]
            days = (n["event_date"] - today).days
            st.metric(_('ปิดให้บริการครั้งถัดไป'), _('อีก {0} วัน').format(days),
                      help=_("จากหน้าประกาศของผู้ให้บริการ ไม่นับวันที่แบบ 'ไม่เร็วกว่า'"))
            st.caption(_('โมเดลที่จะถูกปิดเร็วที่สุดตามประกาศทางการ'))
            st.markdown(f"`{n['model_api_name']}` {ui.vendor_badge(n['vendor_name'])}")
            st.caption(ui.th_date(n["event_date"]))
        else:
            st.metric(_('ปิดให้บริการครั้งถัดไป'), _('ยังไม่มีประกาศ'))

# ------------------------------------------------------------------ leaderboards
left, right = st.columns(2, gap="medium")
with left, st.container(border=True, height="stretch"):
    st.subheader(_('คุณภาพสูงสุด 10 อันดับ'))
    ui.provenance("third-party")
    q = scored.nlargest(10, "quality").assign(model=lambda d: d["model_name"].map(ui.short_name))
    st.altair_chart(
        alt.Chart(q).mark_bar(cornerRadiusEnd=4, height=18).encode(
            x=alt.X("quality:Q", title=_('ดัชนีคุณภาพ')), y=alt.Y("model:N", sort="-x", title=None),
            color=ui.vendor_color(legend=False),
            tooltip=[alt.Tooltip("model:N", title=_('โมเดล')), alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                     alt.Tooltip("quality:Q", title=_('คุณภาพ'), format=".1f"),
                     alt.Tooltip("blended_price_per_m:Q", title="$/1M", format="$.2f")],
        ).properties(height=320),
        alt=_('10 โมเดลที่คุณภาพสูงสุด อันดับหนึ่งคือ {0}').format(q.iloc[0]['model'] if len(q) else '-'),
    )
with right, st.container(border=True, height="stretch"):
    st.subheader(_('คุ้มค่าที่สุด 10 อันดับ'))
    ui.provenance("derived", note=_('คุณภาพ ÷ ราคา'))
    vv = scored.dropna(subset=["value_score"]).nlargest(10, "value_score") \
        .assign(model=lambda d: d["model_name"].map(ui.short_name))
    st.altair_chart(
        alt.Chart(vv).mark_bar(cornerRadiusEnd=4, height=18).encode(
            x=alt.X("value_score:Q", title=_('คะแนนคุณภาพต่อ $1 (สเกล log)'), scale=alt.Scale(type="log", domainMin=1)), x2=alt.datum(1), y=alt.Y("model:N", sort="-x", title=None),
            color=ui.vendor_color(legend=False),
            tooltip=[alt.Tooltip("model:N", title=_('โมเดล')), alt.Tooltip("vendor_name:N", title=_('ผู้ให้บริการ')),
                     alt.Tooltip("value_score:Q", title=_('ความคุ้มค่า'), format=".1f"),
                     alt.Tooltip("blended_price_per_m:Q", title="$/1M", format="$.2f")],
        ).properties(height=320),
        alt=_('10 โมเดลที่คุ้มค่าที่สุด อันดับหนึ่งคือ {0}').format(vv.iloc[0]['model'] if len(vv) else '-'),
    )
st.caption(_('มีคะแนนคุณภาพ {0} จาก {1} โมเดล · โมเดลที่ไม่มีคะแนนจะไม่อยู่ในกราฟ').format(len(scored), len(models)))

# ------------------------------------------------------------------ flagship comparison
st.subheader(_('รุ่นเรือธงของแต่ละเจ้า'), icon=":material/compare_arrows:")
ui.provenance("third-party", "derived", note=_('รุ่นเรือธง = รุ่นที่คุณภาพสูงสุดของแต่ละผู้ให้บริการ ภายใต้ตัวกรองที่เลือก'))

flag = (scored.sort_values("quality", ascending=False).groupby("vendor_name", as_index=False).head(1)
        .sort_values("quality", ascending=False))
table = pd.DataFrame({
    _('ผู้ให้บริการ'): ui.as_list(flag["vendor_name"]),
    _('โมเดล'): flag["model_name"].map(ui.short_name),
    _('คุณภาพ'): flag["quality"],
    _('ราคาเฉลี่ย $/1M'): flag["blended_price_per_m"],
    _('ความคุ้มค่า'): flag["value_score"],
    _('ความเร็ว tok/s'): flag["throughput_p50"],
    _('สัดส่วนการใช้ 7 วัน'): flag["token_share_7d"],
    _('ความเห็น 30 วัน'): flag["vendor_sentiment_30d"],
    _('ราคา 30 วัน'): flag["blended_price_30d"],
})
table, missing = ui.drop_empty(table, keep=[_('ผู้ให้บริการ'), _('โมเดล'), _('ราคา 30 วัน')])
st.dataframe(
    table, hide_index=True, alt=_('รุ่นเรือธงของแต่ละผู้ให้บริการ พร้อมคุณภาพ ราคา และความคุ้มค่า'),
    column_config={
        _('ผู้ให้บริการ'): ui.vendor_column(pinned=True),
        _('โมเดล'): st.column_config.TextColumn(pinned=True),
        _('คุณภาพ'): st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f",
                                                  help=_('ดัชนี Intelligence ของ Artificial Analysis (แหล่งภายนอก)')),
        _('ราคาเฉลี่ย $/1M'): st.column_config.NumberColumn(format="dollar", help=_('(3 × input + output) ÷ 4 (คำนวณโดยระบบ)')),
        _('ความคุ้มค่า'): st.column_config.NumberColumn(format="%.1f", help=_('คะแนนคุณภาพต่อราคา $1 (คำนวณโดยระบบ)')),
        _('ความเร็ว tok/s'): st.column_config.NumberColumn(format="%.0f", help=_('ค่ากลางของ throughput p50 ข้าม provider')),
        _('สัดส่วนการใช้ 7 วัน'): st.column_config.NumberColumn(format="percent", help=_('สัดส่วน tokens บน OpenRouter เท่านั้น')),
        _('ความเห็น 30 วัน'): st.column_config.NumberColumn(format="%.2f", help=_('ค่าเฉลี่ยความเห็น −1 ถึง +1')),
        _('ราคา 30 วัน'): st.column_config.LineChartColumn(help=_('ราคาเฉลี่ยย้อนหลัง (คำนวณโดยระบบ)')),
    },
)
pending = {_('ความเร็ว tok/s'): _('ความเร็ว'), _('สัดส่วนการใช้ 7 วัน'): _('สัดส่วนการใช้งาน'), _('ความเห็น 30 วัน'): _('ความเห็นนักพัฒนา')}
notes = []
if missing:
    notes.append(_('ยังไม่มีข้อมูล ') + ", ".join(pending.get(m, m) for m in missing))
if _('ความเห็น 30 วัน') in missing:
    notes.append(_('ความเห็นนักพัฒนาจะขึ้นเมื่อเปิดใช้โมเดลวิเคราะห์ sentiment'))
hist_days = data.price_history()["snapshot_date"].nunique()
if hist_days < 2:
    notes.append(_('ราคา 30 วัน: เพิ่งเริ่มเก็บ ({0} วัน) เส้นจึงยังแบน จะเริ่มขยับเมื่อมีข้อมูลตั้งแต่ 2 วันขึ้นไป').format(hist_days))
notes.append(_('สัดส่วนการใช้ว่าง = โมเดลนั้นไม่ติด 50 อันดับแรกของ OpenRouter ในสัปดาห์นี้'))
st.caption(" · ".join(notes))

# ------------------------------------------------------------------ what changed
st.subheader(_('มีอะไรเปลี่ยนไปบ้าง'), icon=":material/update:")
ui.provenance("official", "derived", note=_('30 วันที่ผ่านมา'))
recent = life[(life["event_date"] >= today - pd.Timedelta(days=30)) & (life["event_date"] <= today)
              & life["vendor_name"].isin(data.selected_vendors())].sort_values("event_date", ascending=False)
if recent.empty:
    st.caption(_('ไม่มีการเปิดตัว เลิกใช้ หรือเปลี่ยนราคาในช่วง 30 วันที่ผ่านมา'))
else:
    ev = recent["event_type"].map(ui.EVENT_TH)
    st.dataframe(
        pd.DataFrame({
            _('วันที่'): recent["event_date"].dt.date,
            _('ผู้ให้บริการ'): ui.as_list(recent["vendor_name"]),
            _('เหตุการณ์'): ui.as_list(ev),
            _('โมเดล'): recent["model_name"].map(ui.short_name).where(recent["model_name"].notna(),
                                                                   recent["model_api_name"]),
            _('รายละเอียด'): recent["detail"].fillna(recent["replacement"].map(
                lambda r: _('รุ่นที่ใช้แทน: {0}').format(r) if isinstance(r, str) and r else None)).fillna(""),
            _('แหล่ง'): recent["source_type"].map(ui.SOURCE_TH),
        }),
        hide_index=True, height=min(420, 38 + 35 * len(recent)),
        alt=_('เหตุการณ์ในวงจรชีวิตและราคาใน 30 วันที่ผ่านมา'),
        column_config={
            _('วันที่'): st.column_config.DateColumn(format="DD MMM YYYY"),
            _('ผู้ให้บริการ'): ui.vendor_column(),
            _('เหตุการณ์'): st.column_config.MultiselectColumn(options=list(ui.EVENT_COLOR),
                                                             color=list(ui.EVENT_COLOR.values())),
        },
    )

ui.openrouter_attribution(as_of.get("models_as_of"), "models")
