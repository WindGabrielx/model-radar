"""Shared UI pieces (TH/EN UI): provenance tags, vendor badges, freshness line, attribution, chart helpers."""
from __future__ import annotations

import base64
import re
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from lib import data
from lib.i18n import _, lang

# Vendor → theme colour name. Same hue as the vendor's chart colour (config.toml), so a badge in a
# table, a dot in a chart and a pill in the filter all read as the same vendor.
VENDOR_COLOR = {"Anthropic": "orange", "DeepSeek": "violet", "Google": "blue", "OpenAI": "green", "xAI": "gray"}

# Thai labels below are wrapped in _() and rebuilt on every access (module __getattr__), so the TH/EN switch
# applies to them too. Provenance is the app's signature: every number says where it came from (CLAUDE.md §3).
def _consts() -> dict:
    return {
        "PROVENANCE": {
            "official": ":green-badge[:material/verified: " + _("ข้อมูลทางการ") + "]",
            "third-party": ":blue-badge[:material/hub: " + _("แหล่งภายนอก") + "]",
            "derived": ":violet-badge[:material/function: " + _("คำนวณโดยระบบ") + "]",
        },
        "PROVENANCE_HELP": _("ข้อมูลทางการ = จากหน้าเว็บของผู้ให้บริการเอง · แหล่งภายนอก = OpenRouter / Artificial Analysis · "
                             "คำนวณโดยระบบ = ตัวเลขที่ระบบนี้คำนวณต่อ"),
        "TIER_TH": {"top": _("รุ่นท็อป"), "mid": _("รุ่นกลาง"), "small": _("รุ่นเล็ก"), "unclassified": _("ยังไม่จัดกลุ่ม")},
        "EVENT_TH": {"released": _("เปิดตัว"), "deprecated": _("ประกาศเลิกใช้"), "retired": _("ปิดให้บริการ"),
                     "price_change": _("ราคาเปลี่ยน"), "first_seen_openrouter": _("ขึ้น OpenRouter ใหม่"),
                     "removed_from_openrouter": _("ถูกถอดจาก OpenRouter")},
        "EVENT_COLOR": {_("เปิดตัว"): "green", _("ประกาศเลิกใช้"): "orange", _("ปิดให้บริการ"): "red",
                        _("ราคาเปลี่ยน"): "blue", _("ขึ้น OpenRouter ใหม่"): "violet", _("ถูกถอดจาก OpenRouter"): "gray"},
        "SOURCE_TH": {"official": _("ทางการ"), "derived": _("คำนวณโดยระบบ")},
    }


_LAZY = {"PROVENANCE", "PROVENANCE_HELP", "TIER_TH", "EVENT_TH", "EVENT_COLOR", "SOURCE_TH"}


def __getattr__(name: str):
    # Only our translated constants; other probes (module reloads, introspection) must not touch session state.
    if name in _LAZY:
        return _consts()[name]
    raise AttributeError(name)


TH_MONTHS = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.']


def provenance(*kinds: str, note: str | None = None) -> None:
    c = _consts()
    tags = " ".join(c["PROVENANCE"][k] for k in kinds)
    st.markdown(f"{tags} {note or ''}".strip(), help=c["PROVENANCE_HELP"])


def page_header(title: str, icon: str, lede: str) -> None:
    st.title(title, icon=icon)
    st.caption(lede)


# The app's one authored motion moment: a radar sweep on the logo, with five blips in the vendor colours
# lighting up as the beam passes. The animation lives inside a self-contained SVG rendered by st.image
# (st.html strips SVG), so no CSS touches the app itself. Mid-tone colours read on light and dark.
# Stops for users who prefer reduced motion. Agreed exception to CLAUDE.md §12, Overview header only.
_RADAR = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48">
<style>
.sweep { transform-origin: 24px 24px; animation: spin 3.2s linear infinite; }
.blip { opacity: .3; animation: blip 3.2s ease-out infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes blip { 0% { opacity: 1; } 55% { opacity: .3; } 100% { opacity: .3; } }
@media (prefers-reduced-motion: reduce) { .sweep, .blip { animation: none; } .blip { opacity: 1; } }
</style>
<defs><linearGradient id="beam" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#7FA6F5" stop-opacity="0"/><stop offset="1" stop-color="#7FA6F5" stop-opacity=".55"/>
</linearGradient></defs>
<circle cx="24" cy="24" r="21" fill="none" stroke="#7F8AA0" stroke-width="2.4"/>
<circle cx="24" cy="24" r="14" fill="none" stroke="#7F8AA0" stroke-opacity=".45" stroke-width="1.2"/>
<circle cx="24" cy="24" r="7" fill="none" stroke="#7F8AA0" stroke-opacity=".45" stroke-width="1.2"/>
<path d="M24 3v42M3 24h42" stroke="#7F8AA0" stroke-opacity=".25" stroke-width="1"/>
<g class="sweep"><path d="M24 24 L24 3 A21 21 0 0 1 42.2 13.5 Z" fill="url(#beam)"/>
<path d="M24 24 L42.2 13.5" stroke="#7FA6F5" stroke-width="2" stroke-linecap="round"/></g>
<circle class="blip" style="animation-delay:.35s" cx="33" cy="12" r="2.4" fill="#E07A52"/>
<circle class="blip" style="animation-delay:1.0s" cx="38" cy="30" r="2.4" fill="#8B6CF0"/>
<circle class="blip" style="animation-delay:1.65s" cx="22" cy="36" r="2.4" fill="#4E8BF5"/>
<circle class="blip" style="animation-delay:2.2s" cx="11" cy="27" r="2.4" fill="#22B47D"/>
<circle class="blip" style="animation-delay:2.75s" cx="15" cy="13" r="2.4" fill="#A3AEC2"/>
<circle cx="24" cy="24" r="2.6" fill="#7FA6F5"/>
</svg>"""


BRANDS_DIR = Path(__file__).resolve().parents[1] / "static" / "brands"
BRAND_FILE = {"Anthropic": "anthropic", "DeepSeek": "deepseek", "Google": "google", "OpenAI": "openai", "xAI": "xai"}
BRAND_HEX = {"Anthropic": "#E07A52", "DeepSeek": "#8B6CF0", "Google": "#4E8BF5", "OpenAI": "#22B47D", "xAI": "#A3AEC2"}


def _brand_icon(vendor: str, x: float, y: float, size: float, opacity: float) -> str | None:
    """Brand icon from app/static/brands/<name>.svg (or .png), drawn faintly in the vendor's colour.
    Monochrome SVG icons are inlined so they can take the vendor colour; PNGs are embedded as images."""
    svg = BRANDS_DIR / f"{BRAND_FILE[vendor]}.svg"
    if svg.exists():
        src = svg.read_text(encoding="utf-8")
        box = re.search(r'viewBox="([^"]+)"', src)
        inner = re.sub(r"<title>.*?</title>", "", src[src.index(">") + 1: src.rindex("</svg>")], flags=re.S)
        inner = re.sub(r"<script.*?</script>", "", inner, flags=re.S | re.I)   # images never run scripts; belt and braces
        return (f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="{box.group(1) if box else "0 0 24 24"}" '
                f'fill="{BRAND_HEX[vendor]}" fill-rule="evenodd" opacity="{opacity}">{inner}</svg>')
    png = BRANDS_DIR / f"{BRAND_FILE[vendor]}.png"
    if png.exists():
        b64 = base64.b64encode(png.read_bytes()).decode()
        return (f'<image href="data:image/png;base64,{b64}" x="{x}" y="{y}" width="{size}" height="{size}" '
                f'preserveAspectRatio="xMidYMid meet" opacity="{opacity}"/>')
    return None


def _brand_strip() -> str:
    """Faint decorative row of the five tracked brand icons (colour wordmark fallback if a file is missing)."""
    w, h, n, size = 640, 120, len(BRAND_FILE), 56
    cells = []
    for i, v in enumerate(BRAND_FILE):
        cx = w / n * (i + 0.5)
        y_off = 22 if i % 2 else 0                      # gentle stagger so it reads as decoration, not a toolbar
        icon = _brand_icon(v, cx - size / 2, 20 + y_off, size, 0.30)
        cells.append(icon or (f'<text x="{cx}" y="{60 + y_off}" text-anchor="middle" font-family="sans-serif" '
                              f'font-size="26" font-weight="700" fill="{BRAND_HEX[v]}" opacity=".30">{v}</text>'))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            + "".join(cells) + "</svg>")


def radar_header(title: str, lede: str) -> None:
    """Overview header: animated radar logo + title, with a faint brand strip on the right."""
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.image(_RADAR, width=46, alt="")
            st.title(title, width="content")
        st.caption(lede)
    with right:
        st.image(_brand_strip(), width="stretch", alt="")


def th_date(d, with_time: bool = False) -> str:
    if d is None or pd.isna(d):
        return "–"
    d = pd.Timestamp(d)
    if lang() == "EN":
        return f"{d.day} {d:%b %Y}" + (f", {d:%H:%M}" if with_time else "")
    s = f"{d.day} {TH_MONTHS[d.month - 1]} {d.year}"
    return s + (_(' {0:%H:%M} น.').format(d) if with_time else "")


def freshness() -> dict:
    a = data.as_of()
    parts = []
    if pd.notna(a.get("last_success_at")):
        parts.append(_('อัปเดตล่าสุด {0} (เวลาไทย)').format(th_date(a['last_success_at'], with_time=True)))
    if a.get("models_as_of"):
        parts.append(_('ราคา ณ {0}').format(th_date(a['models_as_of'])))
    st.caption(" · ".join(parts) or _('ยังไม่มีการดึงข้อมูล'))
    return a


def filter_note(n_models: int) -> None:
    """Make the sidebar filter state visible on the page, so a narrowed view is never mistaken for missing data."""
    vendors = data.selected_vendors()
    if len(vendors) == len(data.VENDORS):
        who = _('ทุกผู้ให้บริการ')
    else:
        who = _('เฉพาะ ') + " ".join(vendor_badge(v) for v in vendors)
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown(_(':material/filter_alt: กำลังแสดง {0} · {1} โมเดล').format(who, n_models), width="content")
        if len(vendors) < len(data.VENDORS) or set(st.session_state.get("f_tiers") or []) != set(data.DEFAULT_TIERS):
            st.button(_('ล้างตัวกรอง'), icon=":material/filter_alt_off:", type="tertiary", on_click=data.reset_filters,
                      key=f"reset_{st.session_state.get('_page', 'x')}")


def glossary() -> None:
    """Metric definitions (CLAUDE.md §7), one click away on every comparison page."""
    with st.expander(_('นิยามตัวชี้วัด: คุณภาพ ราคา ความคุ้มค่า วัดจากอะไร'), icon=":material/menu_book:"):
        for title, tag, body in [
            (_('คุณภาพ'), "third-party",
             _('Artificial Analysis Intelligence Index ที่ OpenRouter เผยแพร่ เป็นคะแนนรวมจากชุดทดสอบมาตรฐานหลายชุด (ความรู้ทั่วไป การให้เหตุผล คณิตศาสตร์ การเขียนโค้ด และการทำตามคำสั่ง) ยิ่งสูงยิ่งเก่ง วัดความสามารถอย่างเดียว ไม่รวมความเร็วหรือราคา มีเฉพาะโมเดลที่ Artificial Analysis ทดสอบแล้ว และควรเทียบกันภายในดัชนีเวอร์ชันเดียวกันเท่านั้น')),
            (_('ราคาเฉลี่ย (blended)'), "derived",
             _('(3 × ราคา input + 1 × ราคา output) ÷ 4 ต่อ 1M tokens สมมติว่างานทั่วไปส่ง input มากกว่า output ราว 3 เท่า ใช้ราคาอ้างอิง: ราคาจากผู้พัฒนาเองบน OpenRouter ถ้ามี ไม่อย่างนั้นใช้ค่ากลางของทุก provider (ไม่ใช้ราคาเจ้าที่ถูกที่สุด เพราะมักเป็นรุ่นที่ถูกบีบขนาด ทำให้ความคุ้มค่าสูงเกินจริง)')),
            (_('ความคุ้มค่า'), "derived",
             _('คุณภาพ ÷ ราคาเฉลี่ย = ได้คะแนนคุณภาพกี่คะแนนต่อเงิน $1 (ต่อ 1M tokens) โมเดลราคาถูกมากจะได้ค่านี้สูงแม้คุณภาพปานกลาง ค่านี้จึงโดดมากสำหรับรุ่นเล็กราคาถูก กราฟจึงใช้สเกล log และควรเทียบภายในระดับรุ่นเดียวกัน')),
            (_('ความเร็ว'), "third-party",
             _('ค่ากลาง (median) ข้าม provider ของ throughput p50 (tokens/วินาที) และ latency p50 (มิลลิวินาที) จาก 30 นาทีล่าสุดบน OpenRouter')),
            (_('สัดส่วนการใช้งาน'), "third-party",
             _('tokens ของโมเดล ÷ tokens ทั้งหมดในวันนั้นบน OpenRouter ไม่ใช่จำนวนผู้ใช้ และไม่ใช่ส่วนแบ่งตลาด')),
            (_('ความเห็นนักพัฒนา'), "derived",
             _('ค่าเฉลี่ยของความเห็นในคอมเมนต์ Hacker News (+1 บวก, 0 กลางๆ หรือปนกัน, −1 ลบ) ให้คะแนนด้วยโมเดลในเครื่อง')),
            (_('รุ่นท็อป / รุ่นกลาง / รุ่นเล็ก'), "derived",
             _('ระดับรุ่นของแต่ละโมเดล ใช้เทียบของระดับเดียวกัน เช่น รุ่นท็อป = รุ่นที่เก่งที่สุดและแพงที่สุดของแต่ละเจ้า (Opus, GPT Astra, Gemini Neon) จัดกลุ่มไว้ใน seed/model_tiers.csv')),
            (_('1M tokens'), "derived",
             _('หน่วยคิดราคาของ AI: 1 ล้าน tokens ≈ ข้อความภาษาอังกฤษราว 750,000 คำ (ภาษาไทยใช้ tokens มากกว่าต่อคำ)')),
            (_('คะแนนรวม'), "derived",
             _('ถ่วงน้ำหนัก คุณภาพ / ราคาถูก / ความเร็ว ที่ปรับสเกล 0–1 ภายในตัวกรองปัจจุบัน ตามน้ำหนักที่คุณเลือก')),
        ]:
            st.markdown(f"**{_(title)}** {_consts()['PROVENANCE'][tag]}  \n{_(body)}")


def openrouter_attribution(as_of: object, what: str = "rankings") -> None:
    st.caption(f"Source: OpenRouter (openrouter.ai/{what}), as of {as_of}")


def empty_state(title: str, body: str, icon: str = ":material/hourglass_empty:") -> None:
    st.info(body, title=title, icon=icon)


def vendor_badge(v: str) -> str:
    return f":{VENDOR_COLOR.get(v, 'gray')}-badge[{v}]"


def vendor_column(label: str | None = None, pinned: bool = False) -> st.column_config.Column:
    return st.column_config.MultiselectColumn(label or _("ผู้ให้บริการ"), options=list(VENDOR_COLOR),
                                              color=list(VENDOR_COLOR.values()), pinned=pinned)


def as_list(s: pd.Series) -> pd.Series:
    return s.map(lambda v: [v] if isinstance(v, str) else [])


def drop_empty(df: pd.DataFrame, keep: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Drop columns with no values at all (data not collected yet) and return their names for a note."""
    empty = [c for c in df.columns if c not in keep and df[c].isna().all()]
    return df.drop(columns=empty), empty


# ------------------------------------------------------------------ charts
def vendor_color(field: str = "vendor_name", legend: bool = True) -> alt.Color:
    """Pinned domain → each vendor keeps its theme colour on every page, in light and dark."""
    return alt.Color(f"{field}:N", title=_("ผู้ให้บริการ"), scale=alt.Scale(domain=data.VENDORS),
                     legend=alt.Legend(orient="top", title=None) if legend else None)


def money(v: float | None, digits: int = 2) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"${v:,.{digits}f}"


def num(v: float | None, digits: int = 1) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"{v:,.{digits}f}"


def short_name(name: str | None) -> str:
    """'Anthropic: Claude Opus 5.5' → 'Claude Opus 5.5'."""
    if not isinstance(name, str):
        return ""
    return name.split(": ", 1)[-1]
