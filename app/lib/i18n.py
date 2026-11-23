"""TH / EN switch. Thai is the source language; English comes from lib/i18n_en.json (Thai string → English).

    from lib.i18n import _
    st.title(_("ภาพรวม"))
    _("อีก {0} วัน").format(days)

A string missing from the dictionary falls back to Thai, so a missing translation never breaks a page.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import streamlit as st

LANGS = {"TH": "ไทย", "EN": "English"}


@lru_cache(maxsize=1)
def _en() -> dict[str, str]:
    path = Path(__file__).with_name("i18n_en.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def lang() -> str:
    return st.session_state.get("lang") or "TH"


def _(s: str) -> str:
    if lang() == "TH":
        return s
    return _en().get(s) or s
