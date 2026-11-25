"""AI Model Tracker — Streamlit entrypoint (TH/EN UI). Run: cd app && streamlit run streamlit_app.py"""
import streamlit as st

from lib.i18n import LANGS, _

from lib import data, ui

st.set_page_config(page_title="AI model tracker", page_icon=":material/radar:", layout="wide")

# Language first: page titles below are translated, so the switch must be read before st.navigation.
# One language at a time (single-select); ?lang=EN in the URL opens the English version.
with st.sidebar:
    st.segmented_control("ภาษา / Language", list(LANGS), default="TH", required=True, key="lang",
                         width="stretch", bind="query-params")

pages = {
    _('เปรียบเทียบ'): [
        st.Page("app_pages/overview.py", title=_('ภาพรวม'), icon=":material/space_dashboard:", default=True),
        st.Page("app_pages/price_value.py", title=_('ราคาและความคุ้มค่า'), icon=":material/payments:"),
        st.Page("app_pages/usage.py", title=_('การใช้งาน'), icon=":material/stacked_line_chart:"),
    ],
    _('ติดตาม'): [
        st.Page("app_pages/news_sentiment.py", title=_('ข่าวและความเห็น'), icon=":material/forum:"),
        st.Page("app_pages/lifecycle.py", title=_('วงจรชีวิตโมเดล'), icon=":material/event_upcoming:"),
        st.Page("app_pages/pipeline_health.py", title=_('สถานะระบบดึงข้อมูล'), icon=":material/monitor_heart:"),
    ],
}
pg = st.navigation(pages, position="top")
st.session_state["_page"] = pg.url_path or "overview"

# Global filters live in the entrypoint so their state carries across pages.
with st.sidebar:
    st.subheader(_('ตัวกรอง'), icon=":material/tune:")
    st.pills(_('ผู้ให้บริการ'), data.VENDORS, selection_mode="multi", default=data.VENDORS, key="f_vendors",
             help=_('กดเพื่อซ่อน/แสดงผู้ให้บริการ ปุ่มที่สว่างคือที่กำลังแสดงอยู่'))
    st.pills(_('ระดับรุ่น'), data.TIERS, selection_mode="multi", default=data.DEFAULT_TIERS, key="f_tiers",
             format_func=ui.TIER_TH.get,
             help=_("จัดกลุ่มด้วยมือใน seed/model_tiers.csv โมเดลใหม่จะเป็น 'ยังไม่จัดกลุ่ม' ก่อน"))
    st.toggle(_('รวมโมเดลสร้างภาพ'), key="f_images", value=False,
              help=_('โมเดลสร้างภาพไม่มีคะแนนคุณภาพ จึงไม่อยู่ในการ์ด KPI และกราฟคุณภาพ/ความคุ้มค่า · เห็นได้ในตาราง “โมเดลทั้งหมด” หน้าราคาและความคุ้มค่า'))
    st.toggle(_('รวมโมเดลที่ปิดแล้ว'), key="f_retired", value=False,
              help=_('โมเดลที่ผู้ให้บริการปิดไปแล้ว หรือหายจาก OpenRouter ซ่อนไว้เป็นค่าเริ่มต้น เพราะใช้งานผ่าน API ของผู้ให้บริการไม่ได้แล้ว · เปิดแล้วจะเห็นในตาราง “โมเดลทั้งหมด” หน้าราคาและความคุ้มค่า (ป้าย “ปิดแล้ว”)'))
    st.button(_('แสดงทั้งหมด'), icon=":material/filter_alt_off:", on_click=data.reset_filters, width="stretch",
              key="reset_sidebar")
    st.space("medium")
    st.caption(_('ข้อมูลจาก OpenRouter, Artificial Analysis (ผ่าน OpenRouter), หน้าประกาศของผู้ให้บริการ, Hacker News, RSS ของผู้ให้บริการ และ GDELT · อัปเดตทุกวัน 10:00 น.'))

pg.run()
