"""Structured legal fact representation and statutory extraction."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LegalQuantity(BaseModel):
    """A numeric legal quantity with its unit preserved for safe comparison."""

    model_config = ConfigDict(extra="forbid")

    value: Decimal
    unit: str | None = None


class LegalFact(BaseModel):
    """Structured statutory fact extracted from provisions or draft claims."""

    model_config = ConfigDict(extra="forbid")

    claim_key: str = Field(min_length=1)
    article: str | None = None
    clause: str | None = None
    subject: str | None = None  # "employer", "worker", "contract"
    action: str | None = None  # "notice", "terminate", "probation", "non_compete", "gratuity"
    numeric_values: list[Decimal] = Field(default_factory=list)
    numeric_units: list[str] = Field(default_factory=list)  # "days", "months", "years", "aed"
    quantities: list[LegalQuantity] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    polarity: Literal["positive", "negative", "unknown"] = "positive"


_NUMERIC_MAP_ARABIC = {
    "ستة": Decimal("6"),
    "ست": Decimal("6"),
    "أربعة عشر": Decimal("14"),
    "اربعة عشر": Decimal("14"),
    "ثلاثين": Decimal("30"),
    "ثلاثون": Decimal("30"),
    "سنتين": Decimal("2"),
    "سنتان": Decimal("2"),
    "عامين": Decimal("2"),
    "عامان": Decimal("2"),
    "واحد": Decimal("1"),
    "واحدة": Decimal("1"),
    "خمس": Decimal("5"),
    "خمسة": Decimal("5"),
    "إحدى وعشرين": Decimal("21"),
    "احدى وعشرين": Decimal("21"),
    "واحد وعشرين": Decimal("21"),
}

_NUMERIC_MAP_ENGLISH = {
    "one": Decimal("1"),
    "two": Decimal("2"),
    "three": Decimal("3"),
    "five": Decimal("5"),
    "six": Decimal("6"),
    "fourteen": Decimal("14"),
    "twenty-one": Decimal("21"),
    "twenty one": Decimal("21"),
    "thirty": Decimal("30"),
}


def extract_numbers_and_units(
    text: str,
) -> tuple[list[Decimal], list[str], list[LegalQuantity]]:
    """Extract numeric quantities and associated temporal/monetary units."""
    numbers: list[Decimal] = []
    units: list[str] = []
    quantities: list[LegalQuantity] = []

    # 1. Bracketed or plain Arabic/English digits: (14) days, 6 months, etc.
    digit_matches = re.finditer(
        r"\(?(\d+(?:\.\d+)?)\)?\s*(days?|months?|years?|weeks?|hours?|aed|dirhams?|يوماً|يوم|أشهر|اشهر|شهر|سنوات|سنة|عام|أعوام|ساعات|ساعة|درهم)?",
        text,
        re.IGNORECASE,
    )
    for m in digit_matches:
        val_str = m.group(1)
        numbers.append(Decimal(val_str))
        raw_unit = m.group(2)
        normalized_unit: str | None = None
        if raw_unit:
            raw_unit_cf = raw_unit.casefold()
            if any(u in raw_unit_cf for u in ("day", "يوم")):
                normalized_unit = "days"
            elif any(u in raw_unit_cf for u in ("month", "شهر")):
                normalized_unit = "months"
            elif any(u in raw_unit_cf for u in ("year", "سن", "عام")):
                normalized_unit = "years"
            elif any(u in raw_unit_cf for u in ("aed", "dirham", "درهم")):
                normalized_unit = "aed"
        if normalized_unit:
            units.append(normalized_unit)
        quantities.append(LegalQuantity(value=Decimal(val_str), unit=normalized_unit))

    # 2. Arabic textual numbers
    for word, dec_val in _NUMERIC_MAP_ARABIC.items():
        if word in text:
            if dec_val not in numbers:
                numbers.append(dec_val)
            unit_match = re.search(
                rf"{re.escape(word)}\s*(يوماً|يوم|أشهر|اشهر|شهر|سنوات|سنة|سنتين|سنتان|عام|عامين|عامان|أعوام|درهم)",
                text,
            )
            if unit_match:
                raw = unit_match.group(1)
                normalized = (
                    "days"
                    if "يوم" in raw
                    else "months"
                    if "شهر" in raw
                    else "years"
                    if any(token in raw for token in ("سن", "عام"))
                    else "aed"
                )
                if normalized not in units:
                    units.append(normalized)
                quantities.append(LegalQuantity(value=dec_val, unit=normalized))
    text_cf = text.casefold()
    for word, dec_val in _NUMERIC_MAP_ENGLISH.items():
        if re.search(rf"\b{re.escape(word)}\b", text_cf):
            if dec_val not in numbers:
                numbers.append(dec_val)
            unit_match = re.search(
                rf"\b{re.escape(word)}\b\s*(days?|months?|years?|weeks?|aed|dirhams?)",
                text_cf,
            )
            if unit_match:
                raw = unit_match.group(1)
                normalized = (
                    "days"
                    if raw.startswith("day")
                    else "months"
                    if raw.startswith("month")
                    else "years"
                    if raw.startswith("year")
                    else "aed"
                )
                if normalized not in units:
                    units.append(normalized)
                quantities.append(LegalQuantity(value=dec_val, unit=normalized))

    # Arabic words are useful for semantic extraction, but without a nearby
    # unit they must not be treated as interchangeable with a unit-bearing
    # quantity during verification.
    return numbers, units, quantities


def extract_legal_facts(text: str, claim_key_hint: str | None = None) -> list[LegalFact]:
    """Extract structured statutory facts from legal text or draft claims."""
    facts: list[LegalFact] = []
    text_cf = text.casefold()

    # Article / Clause references
    art_match = re.search(r"(?:article|المادة|للمادة)\s*\(?(\d+)\)?", text_cf)
    clause_match = re.search(r"(?:clause|paragraph|البند|الفقرة)\s*\(?(\d+)\)?", text_cf)
    article_str = art_match.group(1) if art_match else None
    clause_str = clause_match.group(1) if clause_match else None

    numbers, units, quantities = extract_numbers_and_units(text)

    # Polarity / Negation
    polarity: Literal["positive", "negative", "unknown"] = "positive"
    if any(
        neg in text_cf
        for neg in (
            "not exceed",
            "shall not",
            "may not",
            "لا يجوز",
            "يحظر",
            "لا يتم",
            "دون",
        )
    ):
        polarity = "negative"

    # Identify primary legal subject and domain
    subject = None
    if any(s in text_cf for s in ("employer", "صاحب العمل")):
        subject = "employer"
    elif any(s in text_cf for s in ("worker", "employee", "العامل", "الموظف")):
        subject = "worker"

    # Determine claim key
    key = "statutory_provision"
    if "probation" in text_cf or "تجربة" in text_cf or "اختبار" in text_cf:
        if any(m in text_cf for m in ("max", "exceed", "duration", "مدتها", "مدة")):
            key = "probation_max_months"
        elif subject == "employer" and (
            "notice" in text_cf or "إخطار" in text_cf or "اخطار" in text_cf
        ):
            key = "probation_employer_notice_days"
        elif subject == "worker" and (
            "transfer" in text_cf or "another employer" in text_cf or "صاحب عمل آخر" in text_cf
        ):
            key = "probation_employee_transfer_notice_months"
        elif subject == "worker" and ("leave the state" in text_cf or "مغادرة الدولة" in text_cf):
            key = "probation_employee_departure_notice_days"
        elif (
            "recruitment" in text_cf
            or "compensation" in text_cf
            or "استقدام" in text_cf
            or "تعويض" in text_cf
        ):
            key = "probation_recruitment_compensation"
    elif "non-compete" in text_cf or "عدم المنافسة" in text_cf or "عدم منافسة" in text_cf:
        key = "non_compete_max_years"
    elif "gratuity" in text_cf or "end of service" in text_cf or "مكافأة نهاية الخدمة" in text_cf:
        key = "gratuity_calculation"
    elif "arabic" in text_cf or "عربي" in text_cf or "لغة" in text_cf:
        key = "arabic_precedence"

    fact = LegalFact(
        claim_key=key,
        article=article_str,
        clause=clause_str,
        subject=subject,
        numeric_values=numbers,
        numeric_units=units,
        quantities=quantities,
        polarity=polarity,
    )
    facts.append(fact)
    return facts
