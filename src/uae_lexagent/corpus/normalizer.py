"""Dual-track Arabic text normalization for UAE statutory search (T-04 / REQ-02).

Maintains strict separation between:
1. `text`: faithfully extracted statutory text (never overwritten or stripped of legal markers).
2. `normalized_text`: deterministic, idempotent search-normalized text derived from `text`.

Never alters legal negations, article numbers, monetary amounts, dates, or manually
reverses Arabic strings for display.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict

ARABIC_NORMALIZER_VERSION = "1.0.0"

DOCUMENTED_NORMALIZATION_RULES: tuple[tuple[str, str], ...] = (
    (
        "unicode_nfc_pre",
        "Apply Unicode NFC so decomposed Alef+Hamza sequences compose before letter canonicalization.",
    ),
    (
        "arabic_presentation_forms",
        "Decompose Arabic Presentation Forms-A/B (U+FB50-U+FDFF, U+FE70-U+FEFF) via NFKC into logical Arabic letters and map ornate parentheses to ASCII parentheses.",
    ),
    (
        "strip_bidi_and_invisible_controls",
        "Remove Bidi formatting marks (LRM/RLM/LRE/RLE/PDF/LRI/RLI/FSI/PDI/ALM), BOM, and soft hyphens without reversing logical word order.",
    ),
    (
        "strip_diacritics_and_tatweel",
        "Strip Arabic vowel diacritics (U+0610-U+061A, U+064B-U+065F, U+0670, U+06D6-U+06ED) and Tatweel elongation (U+0640).",
    ),
    (
        "canonicalize_arabic_letters",
        "Unify Alef variants (إأآٱ -> ا), Alef Maksura & Persian Yeh (ىی -> ي), Persian Kaf (ک -> ك), and Teh Marbuta / Heh variants (ةۀہ -> ه).",
    ),
    (
        "canonicalize_digits_and_punctuation",
        "Convert Eastern Arabic-Indic (٠-٩) and Extended Arabic-Indic (۰-۹) digits to Western ASCII (0-9); map ٪ -> %, ، -> ,, ؛ -> ;, ؟ -> ?, ٫ -> ., ٬ -> ,, Unicode dashes -> -.",
    ),
    (
        "collapse_whitespace",
        "Collapse runs of Unicode whitespace (including NBSP U+00A0 and thin spaces) to single ASCII space and strip ends.",
    ),
)

# Bidi controls and zero-width invisible format characters
_BIDI_AND_INVISIBLE_RE = re.compile(r"[\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")

# Arabic tashkeel / harakat / honorific & Quranic combining marks + Tatweel
_DIACRITICS_AND_TATWEEL_RE = re.compile(r"[\u0610-\u061a\u0640\u064b-\u065f\u0670\u06d6-\u06ed]")

# Whitespace including NBSP, thin/hair/ideographic spaces, newlines, tabs
_WHITESPACE_RE = re.compile(r"[\s\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")

_CHAR_TRANSLATION = str.maketrans(
    {
        # Alef variants
        "\u0622": "\u0627",  # آ -> ا
        "\u0623": "\u0627",  # أ -> ا
        "\u0625": "\u0627",  # إ -> ا
        "\u0671": "\u0627",  # ٱ -> ا
        # Alef Maksura & Persian Yeh -> Arabic Yeh
        "\u0649": "\u064a",  # ى -> ي
        "\u06cc": "\u064a",  # ی -> ي
        # Persian Kaf -> Arabic Kaf
        "\u06a9": "\u0643",  # ک -> ك
        # Teh Marbuta & Heh variants -> Heh
        "\u0629": "\u0647",  # ة -> ه
        "\u06c0": "\u0647",  # ۀ -> ه
        "\u06c1": "\u0647",  # ہ -> ه
        # Eastern Arabic-Indic digits ٠-٩ -> 0-9
        "\u0660": "0",
        "\u0661": "1",
        "\u0662": "2",
        "\u0663": "3",
        "\u0664": "4",
        "\u0665": "5",
        "\u0666": "6",
        "\u0667": "7",
        "\u0668": "8",
        "\u0669": "9",
        # Extended Arabic-Indic / Persian digits ۰-۹ -> 0-9
        "\u06f0": "0",
        "\u06f1": "1",
        "\u06f2": "2",
        "\u06f3": "3",
        "\u06f4": "4",
        "\u06f5": "5",
        "\u06f6": "6",
        "\u06f7": "7",
        "\u06f8": "8",
        "\u06f9": "9",
        # Arabic punctuation & numeric signs
        "\u060c": ",",  # ،
        "\u061b": ";",  # ؛
        "\u061f": "?",  # ؟
        "\u066a": "%",  # ٪
        "\u066b": ".",  # ٫ decimal separator
        "\u066c": ",",  # ٬ thousands separator
        "\ufd3e": ")",  # ﴾ ornate right parenthesis
        "\ufd3f": "(",  # ﴿ ornate left parenthesis
        # Unicode hyphens & dashes -> ASCII hyphen-minus
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)


class DualTrackText(BaseModel):
    """Dual-track representation separating verbatim statutory text from search normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    normalized_text: str
    normalizer_version: str = ARABIC_NORMALIZER_VERSION
    transformations: tuple[str, ...] = tuple(name for name, _ in DOCUMENTED_NORMALIZATION_RULES)


def _normalize_presentation_forms(text: str) -> str:
    """Convert Arabic Presentation Forms-A/B into canonical logical Arabic letters cleanly."""
    if not any("\ufb50" <= ch <= "\ufdff" or "\ufe70" <= ch <= "\ufeff" for ch in text):
        return text
    out: list[str] = []
    for ch in text:
        if ch in ("\ufd3e", "\ufd3f"):
            out.append(ch)
        elif "\ufb50" <= ch <= "\ufdff" or "\ufe70" <= ch <= "\ufeff":
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def normalize_arabic_search(text: str) -> str:
    """Apply deterministic, idempotent search normalization without altering legal semantics.

    Preserves numbers, dates, monetary values, English tokens, and Arabic negations.
    Does not reverse character order.
    """
    if not text:
        return ""

    # 1. Pre-normalize to NFC so decomposed Alef+Hamza composes prior to letter unification
    result = unicodedata.normalize("NFC", text)
    # 2. Convert Arabic Presentation Forms into logical characters, then re-compose
    result = _normalize_presentation_forms(result)
    result = unicodedata.normalize("NFC", result)
    # 3. Strip Bidi directional & invisible format control characters
    result = _BIDI_AND_INVISIBLE_RE.sub("", result)
    # 4. Strip Arabic diacritics and Tatweel; stabilize NFC
    result = _DIACRITICS_AND_TATWEEL_RE.sub("", result)
    result = unicodedata.normalize("NFC", result)
    # 5. Canonicalize Arabic letters, digits, and punctuation
    result = result.translate(_CHAR_TRANSLATION)
    # 6. Normalize whitespace
    result = _WHITESPACE_RE.sub(" ", result).strip()
    return result


def create_dual_track_text(raw_text: str) -> DualTrackText:
    """Construct a DualTrackText pair preserving verbatim `text` alongside `normalized_text`."""
    return DualTrackText(
        text=raw_text,
        normalized_text=normalize_arabic_search(raw_text),
    )
