"""Property-based and adversarial tests for dual-track Arabic normalization (T-04 / REQ-02)."""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from uae_lexagent.corpus.normalizer import (
    DOCUMENTED_NORMALIZATION_RULES,
    DualTrackText,
    create_dual_track_text,
    normalize_arabic_search,
)

# Targeted Arabic + English + punctuation + digit alphabets for focused property fuzzing
_ARABIC_LETTERS = "ابتثجحخدذرزسشصضطظعغفقكلمنهويءؤئإأآٱىةك"
_PERSIAN_VARIANTS = "یکۀہ"
_DIACRITICS = "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670"
_TATWEEL = "\u0640"
_ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_BIDI_CONTROLS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u061c\ufeff"
_ARABIC_PUNCT = "،؛؟٪"
_ASCII_LEGAL = "Article 51(2) 100,000 AED 2024-08-31 - "

_LEGAL_MIXED_ALPHABET = (
    _ARABIC_LETTERS
    + _PERSIAN_VARIANTS
    + _DIACRITICS
    + _TATWEEL
    + _ARABIC_INDIC_DIGITS
    + _PERSIAN_DIGITS
    + _BIDI_CONTROLS
    + _ARABIC_PUNCT
    + _ASCII_LEGAL
    + " \t\n\u00a0"
)


def test_documented_normalization_rules_non_empty() -> None:
    assert len(DOCUMENTED_NORMALIZATION_RULES) >= 7


@settings(max_examples=250, deadline=None)
@given(st.text(alphabet=_LEGAL_MIXED_ALPHABET, min_size=0, max_size=200))
def test_hypothesis_idempotence_and_determinism_mixed_legal(raw: str) -> None:
    first = normalize_arabic_search(raw)
    second = normalize_arabic_search(first)
    third = normalize_arabic_search(raw)
    assert first == second
    assert first == third


@settings(max_examples=250, deadline=None)
@given(st.text(min_size=0, max_size=200))
def test_hypothesis_idempotence_arbitrary_unicode(raw: str) -> None:
    first = normalize_arabic_search(raw)
    second = normalize_arabic_search(first)
    assert first == second


@settings(max_examples=150, deadline=None)
@given(st.text(alphabet=_LEGAL_MIXED_ALPHABET, min_size=1, max_size=120))
def test_hypothesis_dual_track_preserves_original_text_verbatim(raw: str) -> None:
    dual = create_dual_track_text(raw)
    assert isinstance(dual, DualTrackText)
    assert dual.text == raw
    assert dual.normalized_text == normalize_arabic_search(raw)


@settings(max_examples=150, deadline=None)
@given(
    amount=st.integers(min_value=1, max_value=1_000_000),
    article_num=st.integers(min_value=1, max_value=74),
    clause_num=st.integers(min_value=1, max_value=12),
    year=st.integers(min_value=2021, max_value=2026),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
)
def test_hypothesis_numbers_dates_and_articles_preserved(
    amount: int, article_num: int, clause_num: int, year: int, month: int, day: int
) -> None:
    eastern_map = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    date_str = f"{year:04d}-{month:02d}-{day:02d}"
    raw = (
        f"المَادَّةُ ({str(article_num).translate(eastern_map)}) البَنْد ({clause_num}): "
        f"غَرَامَة لا تَقِلُّ عَنْ {str(amount).translate(eastern_map)} درهم اعتباراً من {date_str}"
    )
    norm = normalize_arabic_search(raw)
    assert str(article_num) in norm
    assert str(clause_num) in norm
    assert str(amount) in norm
    assert date_str in norm
    # Negation preserved cleanly
    assert "لا تقل" in norm


def test_diacritics_tatweel_alef_persian_and_presentation_forms() -> None:
    raw = "الـمَادَّةُ (٥١) — إِنْـهَاءُ العَمَلِ بِمُوجِبِ پ/ی/ک \ufe8e\ufedf\ufee3\ufe8e\ufea9\ufe94"
    norm = normalize_arabic_search(raw)
    assert "الماده (51)" in norm
    assert "انهاء العمل بموجب پ/ي/ك" in norm
    assert "الماده" in norm
    assert not any(c in norm for c in _DIACRITICS + _TATWEEL)


def test_directional_controls_and_rtl_order_not_reversed() -> None:
    # Logical reading order starts with "المادة" and contains English token "AED"
    raw = "\u200fالمادة \u202b54\u202c غرامة \u200e100000 AED\u2069"
    norm = normalize_arabic_search(raw)
    assert norm == "الماده 54 غرامه 100000 AED"
    assert not any(c in norm for c in _BIDI_CONTROLS)
    # Verify Arabic string was NOT manually reversed
    assert norm.startswith("الماده")


def test_legally_meaningful_negations_never_stripped() -> None:
    negations = [
        "لا يجوز فصل العامل تعسفياً",
        "لم يلتزم صاحب العمل بالسداد",
        "لن يعتد بأي اتفاق مخالف",
        "غير خاضع لأحكام هذا المرسوم",
        "دون الإخلال بحقوق العامل",
        "إلا بموافقة الوزارة الخطية",
        "عدم جواز التنازل عن الحقوق",
    ]
    for phrase in negations:
        norm = normalize_arabic_search(phrase)
        for neg_word in ("لا", "لم", "لن", "غير", "دون", "الا", "عدم"):
            if neg_word in phrase or ("إلا" in phrase and neg_word == "الا"):
                assert neg_word in norm


def test_arabic_punctuation_combining_and_whitespace_edge_cases() -> None:
    assert normalize_arabic_search("") == ""
    assert normalize_arabic_search("   \t\n\u00a0\u200f  ") == ""
    assert (
        normalize_arabic_search("البند ١، البند ٢؛ هل يجوز؟ ٥٠٪")
        == "البند 1, البند 2; هل يجوز? 50%"
    )
    # Decomposed Hamza on Alef (ا + U+0654) composes via NFC before Alef unification
    decomposed = "ا\u0654حكام ا\u0655نهاء"
    assert normalize_arabic_search(decomposed) == "احكام انهاء"
    # Pre-composed vs decomposed should match identically
    composed = "أحكام إنهاء"
    assert normalize_arabic_search(decomposed) == normalize_arabic_search(composed)
    assert not re.search(r"\s{2,}", normalize_arabic_search("  كلمة   أخرى  "))
