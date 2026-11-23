"""Hand-labelling tool for the sentiment evaluation set (CLAUDE.md §8.4). Separate from the dashboard.

    cd labeler
    streamlit run labeler_app.py --server.port 8502

Reads  data/labels/comments_to_label.csv   (made by scripts/export_label_sample.py)
Writes data/labels/comments_labelled.csv   (one row per comment; re-labelling a comment replaces its row)
No database access and no model predictions are shown, so labels are independent of the model being evaluated.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "labels" / "comments_to_label.csv"
LABELS = ROOT / "data" / "labels" / "comments_labelled.csv"
FIELDS = ["comment_id", "vendor_id", "relevant", "polarity", "intent", "aspects", "note", "labelled_at"]

POLARITY = {"positive": "บวก", "negative": "ลบ", "neutral": "กลางๆ", "mixed": "ปนกัน (มีทั้งชมและติ)"}
INTENT = {"comparison": "เปรียบเทียบ", "recommendation": "แนะนำ", "complaint": "บ่น", "question": "ถาม",
          "informational": "ให้ข้อมูล"}
ASPECTS = {"coding": "เขียนโค้ด", "reasoning": "การให้เหตุผล", "writing": "การเขียน", "accuracy": "ความถูกต้อง",
           "speed": "ความเร็ว", "pricing": "ราคา", "reliability": "ความเสถียร", "context_window": "Context",
           "user_experience": "ประสบการณ์ใช้งาน", "multimodal": "Multimodal"}

st.set_page_config(page_title="ติดป้ายคอมเมนต์", page_icon=":material/label:", layout="centered")


def load_sample() -> list[dict]:
    if not SAMPLE.exists():
        return []
    with open(SAMPLE, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_labels() -> dict[str, dict]:
    if not LABELS.exists():
        return {}
    with open(LABELS, encoding="utf-8") as fh:
        return {r["comment_id"]: r for r in csv.DictReader(fh)}


def save_label(row: dict) -> None:
    labels = load_labels()
    labels[row["comment_id"]] = row
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LABELS.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(labels.values())
    tmp.replace(LABELS)                                    # atomic: a crash never leaves a half-written file


sample = load_sample()
if not sample:
    st.error("ยังไม่มีชุดคอมเมนต์ ให้รัน `python scripts/export_label_sample.py` ก่อน", icon=":material/error:")
    st.stop()
labels = load_labels()
ids = [r["comment_id"] for r in sample]

if "idx" not in st.session_state:                         # start at the first unlabelled comment
    st.session_state.idx = next((i for i, c in enumerate(ids) if c not in labels), 0)

st.title("ติดป้ายคอมเมนต์", icon=":material/label:")
st.caption("ชุดทดสอบสำหรับวัดความแม่นยำของโมเดลวิเคราะห์ความเห็น · อ่านแล้วเลือกคำตอบที่ถูกตามความเห็นของคุณ "
           "ไม่ต้องเดาว่าโมเดลจะตอบอะไร")
done = sum(1 for c in ids if c in labels)
st.progress(done / len(ids), text=f"ติดป้ายแล้ว {done} / {len(ids)} คอมเมนต์")

i = st.session_state.idx
c = sample[i]
prev = labels.get(c["comment_id"], {})

with st.container(border=True):
    st.markdown(f"**#{i + 1}** · แบรนด์เป้าหมาย: **{c['vendor_id']}** · กระทู้: {c['story_title']}")
    st.markdown(f"> {c['text']}")
    st.link_button("เปิดอ่านในบริบทบน Hacker News", c["url"], icon=":material/open_in_new:", type="tertiary")

with st.form(f"label_{c['comment_id']}", border=False):
    relevant = st.toggle(f"คอมเมนต์นี้พูดถึง {c['vendor_id']} จริง (ไม่ใช่แค่เอ่ยชื่อผ่านๆ)",
                         value=prev.get("relevant", "1") != "0")
    polarity = st.segmented_control("ความเห็นต่อแบรนด์นี้", list(POLARITY), format_func=POLARITY.get,
                                    default=prev.get("polarity") or None)
    intent = st.segmented_control("เจตนาของคอมเมนต์", list(INTENT), format_func=INTENT.get,
                                  default=prev.get("intent") or None)
    aspects = st.pills("พูดถึงประเด็นไหนบ้าง (เลือกได้หลายข้อ หรือไม่เลือกเลย)", list(ASPECTS),
                       format_func=ASPECTS.get, selection_mode="multi",
                       default=[a for a in (prev.get("aspects") or "").split("|") if a])
    note = st.text_input("หมายเหตุ (ไม่บังคับ)", value=prev.get("note", ""),
                         placeholder="เช่น ประชด, ไม่แน่ใจ, ภาษากำกวม")
    c1, c2, c3 = st.columns(3)
    back = c1.form_submit_button("ก่อนหน้า", icon=":material/arrow_back:", width="stretch", disabled=i == 0)
    skip = c2.form_submit_button("ข้าม", icon=":material/skip_next:", width="stretch")
    save = c3.form_submit_button("บันทึกและถัดไป", icon=":material/check:", type="primary", width="stretch")

if save:
    if relevant and (not polarity or not intent):
        st.warning("เลือกความเห็นและเจตนาก่อนบันทึก (หรือปิดสวิตช์ถ้าไม่เกี่ยวกับแบรนด์นี้)",
                   icon=":material/warning:")
    else:
        save_label({"comment_id": c["comment_id"], "vendor_id": c["vendor_id"], "relevant": "1" if relevant else "0",
                    "polarity": polarity or "", "intent": intent or "", "aspects": "|".join(aspects or []),
                    "note": note, "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        st.session_state.idx = min(i + 1, len(ids) - 1)
        st.toast("บันทึกแล้ว", icon=":material/check:")
        st.rerun()
elif skip:
    st.session_state.idx = min(i + 1, len(ids) - 1)
    st.rerun()
elif back:
    st.session_state.idx = max(i - 1, 0)
    st.rerun()

with st.expander("คู่มือการติดป้ายสั้นๆ", icon=":material/menu_book:"):
    st.markdown(
        "- **บวก** ชม แนะนำ พอใจ · **ลบ** ติ บ่น ผิดหวัง · **กลางๆ** เล่าข้อเท็จจริงไม่มีความรู้สึก · "
        "**ปนกัน** ชมเรื่องหนึ่งแต่ติอีกเรื่อง\n"
        "- ตัดสินจาก **ความเห็นต่อแบรนด์เป้าหมาย** เท่านั้น ถ้าชมคู่แข่งแต่ติแบรนด์นี้ ให้นับเป็นลบ\n"
        "- **ประเด็น** เลือกเฉพาะที่คอมเมนต์พูดถึงจริง ไม่ต้องเลือกครบ\n"
        "- ไม่แน่ใจ กด **ข้าม** ได้ หรือเขียนหมายเหตุไว้\n"
        f"- ไฟล์ที่บันทึก: `{LABELS.relative_to(ROOT)}`")
jump = st.number_input("ไปที่คอมเมนต์ #", min_value=1, max_value=len(ids), value=i + 1, step=1)
if jump - 1 != i:
    st.session_state.idx = int(jump) - 1
    st.rerun()
