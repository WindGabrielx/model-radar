"""Vendor identity (CLAUDE.md §5) and news matching rules (§8.1). Matching rules are read from core.dim_vendor."""
from __future__ import annotations

import re
from dataclasses import dataclass

VENDOR_PREFIXES = {
    "openai/": "openai",
    "anthropic/": "anthropic",
    "google/gemini": "google",
    "deepseek/": "deepseek",
    "x-ai/": "xai",
}


def vendor_for_model_id(model_id: str) -> str | None:
    """Map an OpenRouter id to a vendor. Aliases (~…) and other prefixes (e.g. google/gemma) → None."""
    if model_id.startswith("~"):
        return None
    for prefix, vendor in VENDOR_PREFIXES.items():
        if model_id.startswith(prefix):
            return vendor
    return None


@dataclass
class VendorRule:
    vendor_id: str
    keywords: list[str]
    context_terms: list[str]

    def _rx(self, words: list[str]) -> re.Pattern:
        return re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in words) + r")(?![\w])", re.IGNORECASE)

    def match(self, text: str) -> str | None:
        """Return the match rule if text mentions a vendor keyword AND a context term (or no context needed)."""
        if not text:
            return None
        kw = self._rx(self.keywords).search(text)
        if not kw:
            return None
        if not self.context_terms:
            return f"keyword:{kw.group(1)}"
        # a keyword that is also a context term (OpenAI, Anthropic, xAI) is unambiguous on its own
        ctx_lower = {c.lower() for c in self.context_terms}
        for m in self._rx(self.keywords).finditer(text):
            if m.group(1).lower() in ctx_lower:
                return f"keyword+context:{m.group(1)}"
        # context term must be a different word occurrence than the keyword itself
        for m in self._rx(self.context_terms).finditer(text):
            if m.span() != kw.span():
                return f"keyword:{kw.group(1)}+context:{m.group(1)}"
        return None


def load_rules(cur) -> list[VendorRule]:
    cur.execute("SELECT vendor_id, hn_keywords, hn_context_terms FROM core.dim_vendor ORDER BY vendor_id")
    return [VendorRule(v, list(k), list(c or [])) for v, k, c in cur.fetchall()]


def match_vendors(rules: list[VendorRule], text: str) -> dict[str, str]:
    out = {}
    for r in rules:
        rule = r.match(text)
        if rule:
            out[r.vendor_id] = rule
    return out
