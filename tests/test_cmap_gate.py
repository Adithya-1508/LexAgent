"""Adversarial and positive tests for PDF CMap corruption detection and review quarantine (T-04)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
import pytest

from uae_lexagent.corpus.extractor import (
    EXTRACTION_FIDELITY_THRESHOLD,
    ExtractionBundle,
    ExtractionError,
    extract_from_snapshot,
    extract_pdf_bytes,
    persist_extraction,
)
from uae_lexagent.corpus.storage import store_snapshot
from uae_lexagent.models import (
    AuthenticityTier,
    LegalSource,
    RedistributionClearance,
)


def _make_pdf_bytes(
    pages_text: list[str],
    *,
    add_image_page: bool = False,
    two_column_overlapping: bool = False,
) -> bytes:
    """Create a deterministic genuine PDF binary in memory via PyMuPDF."""
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        if two_column_overlapping:
            # Deliberately insert interleaved columns at identical Y coordinates out of order
            page.insert_text((320, 72), "Column B fragment", fontsize=11)
            page.insert_text((50, 72), "Column A fragment", fontsize=11)
            page.insert_text((320, 90), "Column B line 2", fontsize=11)
            page.insert_text((50, 90), "Column A line 2", fontsize=11)
        elif text:
            # Use built-in font for ASCII or Unicode text insertion
            page.insert_text((50, 72), text, fontsize=11)
    if add_image_page:
        page = doc.new_page()
        # Draw vector rectangle + raster pixmap with ZERO text layer (OCR-required page)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        pix.set_rect(pix.irect, (200, 200, 200))
        page.insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pix)
    return doc.tobytes()


def _make_corrupted_arabic_cmap_pdf() -> bytes:
    """Construct a real PDF with a broken ToUnicode CMap mapping glyphs to U+FFFD and PUA."""
    raw_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type0 /BaseFont /CorruptArabic "
        b"/Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >> endobj\n"
        b"6 0 obj << /Type /Font /Subtype /CIDFontType2 /BaseFont /CorruptArabic "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/FontDescriptor 8 0 R >> endobj\n"
        b"8 0 obj << /Type /FontDescriptor /FontName /CorruptArabic /Flags 4 "
        b"/FontBBox [0 0 1000 1000] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >> endobj\n"
    )
    tounicode = (
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\n"
        b"begincmap\n"
        b"/CMapType 2 def\n"
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        b"3 beginbfchar\n"
        b"<0001> <FFFD>\n"
        b"<0002> <E001>\n"
        b"<0003> <FE8E>\n"
        b"endbfchar\n"
        b"endcmap\n"
        b"CMapName currentdict /CMap defineresource pop\n"
        b"end end\n"
    )
    stream_7 = (
        f"7 0 obj << /Length {len(tounicode)} >> stream\n".encode("ascii")
        + tounicode
        + b"endstream endobj\n"
    )
    content = b"BT /F1 12 Tf 72 720 Td <000100020003> Tj ET\n"
    stream_5 = (
        f"5 0 obj << /Length {len(content)} >> stream\n".encode("ascii")
        + content
        + b"endstream endobj\n"
    )
    body = raw_pdf + stream_5 + stream_7
    xref = (
        b"xref\n0 9\n0000000000 65535 f \n"
        + b"0000000010 00000 n \n" * 8
        + b"trailer << /Size 9 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )
    return body + xref


def test_clean_english_and_bilingual_pdf_no_false_positive_cmap() -> None:
    en_pdf = _make_pdf_bytes(
        [
            "Article 1: Definitions. In the application of this Decree-Law, Worker means natural person.",
            "Article 51(2): Expatriate worker completing 1 year receives end-of-service gratuity.",
        ]
    )
    bundle = extract_pdf_bytes(
        en_pdf,
        source_id="TEST_CLEAN_EN_PDF",
        document_id="TEST_DOC_EN",
        language="en",
        is_authoritative=False,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        allow_synthetic=True,
    )
    assert bundle.cmap_corruption_detected is False
    assert bundle.cmap_suspicion_detected is False
    assert bundle.ocr_required is False
    # Clean PDF still preserves review_status='pending_human_review' or UNREVIEWED_STAGED until RV-03 review,
    # NEVER auto-VERIFIED_PUBLISHABLE, but must NOT trigger false CMap corruption!
    assert not any(d.code.startswith("CMAP_") for d in bundle.diagnostics)


def test_corrupted_cmap_pdf_triggers_direct_corruption_and_quarantine() -> None:
    corrupt_pdf = _make_corrupted_arabic_cmap_pdf()
    bundle = extract_pdf_bytes(
        corrupt_pdf,
        source_id="TEST_CORRUPT_AR_PDF",
        document_id="TEST_DOC_CORRUPT",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        allow_synthetic=True,
    )
    assert bundle.cmap_corruption_detected is True
    assert bundle.requires_human_review is True
    assert len(bundle.review_queue) >= 1
    assert any(
        d.code in {"CMAP_REPLACEMENT_CHAR", "CMAP_PUA_GLYPH"}
        and d.detection_basis == "direct_observation"
        for d in bundle.diagnostics
    )
    for item in bundle.review_queue:
        assert item.review_status == "pending_human_review"
        assert item.quarantined_publication_state == "BLOCKED_LEGAL_TOPIC"
        assert item.snapshot_sha256 == hashlib.sha256(corrupt_pdf).hexdigest()


def test_clean_arabic_page_analysis_false_positive_control() -> None:
    from uae_lexagent.corpus.extractor import analyze_pdf_page_cmap

    clean_arabic = (
        "المادة (51): يستحق العامل الأجنبي الذي يعمل وفق نمط العمل بالدوام الكامل، "
        "والذي أكمل سنة أو أكثر في الخدمة المستمرة مكافأة نهاية الخدمة عند انتهاء خدمته."
    )
    corrupt, suspect, ocr_req, diags = analyze_pdf_page_cmap(1, clean_arabic, language="ar")
    assert corrupt is False
    assert suspect is False
    assert ocr_req is False
    assert diags == []


def test_bilingual_page_analysis_false_positive_control() -> None:
    from uae_lexagent.corpus.extractor import analyze_pdf_page_cmap

    bilingual = (
        "Article (51) المادة (51): يستحق العامل الأجنبي مكافأة نهاية الخدمة "
        "Expatriate worker severance gratuity under UAE Labour Law FDL 33/2021."
    )
    corrupt, suspect, ocr_req, diags = analyze_pdf_page_cmap(1, bilingual, language="ar")
    assert corrupt is False
    assert suspect is False
    assert ocr_req is False
    assert diags == []


def test_scanned_image_pdf_triggers_ocr_required_and_never_pretends_ocr_ran() -> None:
    scanned_pdf = _make_pdf_bytes([], add_image_page=True)
    bundle = extract_pdf_bytes(
        scanned_pdf,
        source_id="TEST_SCANNED_PDF",
        document_id="TEST_DOC_SCAN",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        allow_synthetic=True,
    )
    assert bundle.ocr_required is True
    assert bundle.requires_human_review is True
    assert len(bundle.review_queue) >= 1
    assert any(d.code == "OCR_REQUIRED" for d in bundle.diagnostics)
    assert bundle.extraction_fidelity_score_summary is None


def test_ocr_used_output_is_always_quarantined_and_never_publishable() -> None:
    scanned_pdf = _make_pdf_bytes([], add_image_page=True)
    bundle = extract_pdf_bytes(
        scanned_pdf,
        source_id="TEST_OCR_OUTPUT_PDF",
        document_id="TEST_DOC_OCR",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.GOV_INDEPENDENTLY_CORROBORATED,
        redistribution_clearance=RedistributionClearance.VERIFIED_PUBLIC_LAW,
        ocr_text_override={1: "المادة 1: نص مستخرج عبر التعرف الضوئي"},
        allow_synthetic=True,
    )
    assert bundle.extraction_method == "ocr"
    assert bundle.requires_human_review is True
    assert any(d.code == "OCR_USED_REVIEW_REQUIRED" for d in bundle.diagnostics)
    assert all(
        art.publication_eligibility == "QUARANTINED_REVIEW_REQUIRED" for art in bundle.articles
    )


def test_below_threshold_fidelity_triggers_review_quarantine() -> None:
    clean_pdf = _make_pdf_bytes(["Article 1: Sample text."])
    bundle = extract_pdf_bytes(
        clean_pdf,
        source_id="TEST_LOW_FIDELITY",
        document_id="TEST_DOC_FID",
        language="en",
        is_authoritative=False,
        authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        measured_fidelity_score=EXTRACTION_FIDELITY_THRESHOLD - 0.01,
        allow_synthetic=True,
    )
    assert bundle.requires_human_review is True
    assert any(d.code == "LOW_EXTRACTION_FIDELITY" for d in bundle.diagnostics)


def test_malformed_or_truncated_pdf_rejected_safely() -> None:
    truncated = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\n"
    with pytest.raises(ExtractionError, match="PDF"):
        extract_pdf_bytes(
            truncated,
            source_id="TEST_TRUNCATED_PDF",
            document_id="TEST_DOC_TRUNC",
            language="ar",
            is_authoritative=True,
            authenticity_tier=AuthenticityTier.LEGACY_UNATTESTED,
            redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
            allow_synthetic=True,
        )


def test_staging_extraction_preserves_authenticity_and_rejects_production_dir(
    tmp_path: Path,
) -> None:
    corrupt_pdf = _make_corrupted_arabic_cmap_pdf()
    target, digest = store_snapshot(corrupt_pdf, tmp_path, ext="pdf")
    source = LegalSource(
        source_id="STAGED_CORRUPT_SRC",
        document_id="STAGED_CORRUPT_DOC",
        language="ar",
        publisher="UAE Official Portal",
        source_url="https://uaelegislation.gov.ae/ar/constitution/modifications/1297/download",
        local_path=target.relative_to(tmp_path.resolve()).as_posix(),
        retrieved_at="2026-11-26T10:00:00Z",
        sha256=digest,
        page_count=1,
        is_authoritative=True,
        artifact_type="raw_pdf",
        source_role="archival_reference",
        extraction_method="manual",
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        attestation_id="attest-corrupt-1",
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    bundle = extract_from_snapshot(source, tmp_path)
    assert isinstance(bundle, ExtractionBundle)
    # Must NEVER upgrade authenticity_tier or redistribution_clearance!
    assert bundle.authenticity_tier is AuthenticityTier.OPERATOR_ATTESTED_ONLY
    assert bundle.redistribution_clearance is RedistributionClearance.PENDING_REVIEW
    assert bundle.cmap_corruption_detected is True
    out_path = persist_extraction(tmp_path, bundle)
    assert out_path.exists()
    assert (tmp_path / "review_queue" / f"{source.source_id}.review.json").exists()


def test_two_column_pdf_reading_order_and_mojibake_suspicion() -> None:
    from uae_lexagent.corpus.extractor import analyze_pdf_page_cmap

    # 1. Multi-column overlapping PDF triggers heuristic reading order suspicion
    col_pdf = _make_pdf_bytes(["dummy"], two_column_overlapping=True)
    bundle = extract_pdf_bytes(
        col_pdf,
        source_id="TEST_TWO_COL_PDF",
        document_id="TEST_DOC_COL",
        language="en",
        is_authoritative=False,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        allow_synthetic=True,
    )
    assert bundle.cmap_suspicion_detected is True
    assert bundle.requires_human_review is True
    assert any(
        d.code == "CMAP_UNSUPPORTED_READING_ORDER" and d.detection_basis == "heuristic_suspicion"
        for d in bundle.diagnostics
    )

    # 2. Latin-1 mojibake ratio on Arabic document triggers heuristic suspicion
    corrupt, suspect, ocr_req, diags = analyze_pdf_page_cmap(
        1, "ÃÏÓãÈÑ ÃÏÓãÈÑ ÃÏÓãÈÑ", language="ar"
    )
    assert corrupt is False
    assert suspect is True
    assert ocr_req is False
    assert any(d.code == "CMAP_ABNORMAL_CHAR_DISTRIBUTION" for d in diags)

    # 3. Clean multi-page PDF preserves page boundaries and leaves fidelity score None (never invented)
    multi_pdf = _make_pdf_bytes(
        [
            "Article 1: Definitions of the Federal Decree-Law.",
            "Article 2: Objectives of the legislation.",
        ]
    )
    multi_bundle = extract_pdf_bytes(
        multi_pdf,
        source_id="TEST_MULTIPAGE_PDF",
        document_id="TEST_DOC_MULTI",
        language="en",
        is_authoritative=False,
        authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        allow_synthetic=True,
    )
    assert len(multi_bundle.pages) == 2
    assert multi_bundle.pages[0].page_number == 1
    assert multi_bundle.pages[1].page_number == 2
    assert [a.article_number for a in multi_bundle.articles] == ["1", "2"]
    assert multi_bundle.articles[0].page_start == 1
    assert multi_bundle.articles[1].page_start == 2
    assert multi_bundle.articles[0].extraction_fidelity_score is None
