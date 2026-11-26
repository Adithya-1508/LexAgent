"""Determinism, DOM structure, security, and historical isolation tests for extractor (T-04)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from uae_lexagent.corpus.extractor import (
    GAZETTE_712_SOURCE_BLOCKER,
    ExtractionBundle,
    ExtractionError,
    extract_from_snapshot,
    extract_html_bytes,
    persist_extraction,
    report_gazette_712_blocker,
)
from uae_lexagent.corpus.storage import (
    SecurityPolicyViolationError,
    SnapshotIntegrityError,
    store_snapshot,
)
from uae_lexagent.models import AuthenticityTier, LegalSource, RedistributionClearance

_SAMPLE_AR_STATUTE_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><title>مرسوم بقانون اتحادي رقم 33 لسنة 2021</title></head>
<body>
  <nav class="site-nav"><a href="/">الرئيسية</a><a href="/laws">التشريعات</a></nav>
  <div class="cookie-banner">نستخدم ملفات تعريف الارتباط</div>
  <script>alert("xss should never execute");</script>
  <style>.hidden { display: none; }</style>
  <main id="legislation-content">
    <h1>مرسوم بقانون اتحادي رقم (33) لسنة 2021 بشأن تنظيم علاقات العمل</h1>
    <section class="law-article" data-article="9">
      <h2 class="article-title">المادة (9) — فترة التجربة</h2>
      <ol class="clauses">
        <li>يجوز لصاحب العمل تعيين العامل تحت التجربة لمدة لا تزيد على (6) أشهر.</li>
        <li>إذا رغب صاحب العمل في إنهاء خدمة العامل خلال فترة التجربة، وجب إخطاره خطياً قبل (14) يوماً على الأقل.
          <ol class="subclauses">
            <li>أ. يُحتسب الإخطار بالأيام.</li>
            <li>ب. لا يستحق العامل مكافأة نهاية خدمة عن فترة التجربة.</li>
          </ol>
        </li>
      </ol>
    </section>
    <section class="law-article" data-article="51">
      <h2 class="article-title">المادة (51) — مكافأة نهاية الخدمة</h2>
      <ol class="clauses">
        <li>يستحق العامل المواطن مكافأة نهاية الخدمة وفق تشريعات المعاشات.</li>
        <li>يستحق العامل الأجنبي الذي أكمل سنةً في الخدمة المستمرة مكافأةً تُحسب على الأساسي: 21 يوماً &amp; 30 يوماً.</li>
      </ol>
    </section>
  </main>
  <footer>حقوق النشر محفوظة</footer>
</body>
</html>
""".encode("utf-8")


def test_repeated_html_extraction_identical_canonical_hash() -> None:
    b1 = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="SRC_AR_HTML",
        document_id="FDL_33_2021_CURRENT",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    b2 = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="SRC_AR_HTML",
        document_id="FDL_33_2021_CURRENT",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert isinstance(b1, ExtractionBundle)
    assert b1.canonical_output_sha256 == b2.canonical_output_sha256
    assert b1.model_dump(mode="json") == b2.model_dump(mode="json")


def test_html_extraction_preserves_structure_entities_chrome_exclusion_and_dual_track() -> None:
    bundle = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="SRC_AR_HTML_2",
        document_id="FDL_33_2021_CURRENT",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert [a.article_number for a in bundle.articles] == ["9", "51"]

    art9 = bundle.articles[0]
    assert art9.article_title == "فترة التجربة"
    assert len(art9.clauses) == 4  # 2 top-level clauses + 2 nested subclauses
    assert art9.clauses[0].clause_number == "1"
    assert art9.clauses[1].clause_number == "2"
    assert art9.clauses[2].clause_number == "2"
    assert art9.clauses[2].subclause_number is not None
    # Chrome, cookie notice, script, footer MUST be excluded
    full_text = "\n".join(a.text for a in bundle.articles)
    assert "ملفات تعريف الارتباط" not in full_text
    assert "حقوق النشر" not in full_text
    assert "alert(" not in full_text

    # Entity decoding & dual-track fidelity: verbatim keeps 'سنةً' and '&', normalized simplifies
    art51 = bundle.articles[1]
    assert "21 يوماً & 30 يوماً" in art51.text
    assert "سنه" in art51.normalized_text
    assert art51.text != art51.normalized_text
    # Always unreviewed at extraction time
    assert art51.human_verification_status == "unreviewed"
    assert art51.publication_eligibility == "UNREVIEWED_STAGED"


def test_mixed_arabic_english_html_extraction() -> None:
    mixed_html = """<!DOCTYPE html>
    <html><body><main>
      <section class="law-article" data-article="60">
        <h2>المادة (60) — العقوبات</h2>
        <p>يُعاقب بغرامة لا تقل عن 100,000 AED وفقاً لأحكام FDL 9/2024.</p>
      </section>
    </main></body></html>""".encode("utf-8")
    bundle = extract_html_bytes(
        mixed_html,
        source_id="SRC_MIXED",
        document_id="DOC_MIXED",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert len(bundle.articles) == 1
    assert "100,000 AED" in bundle.articles[0].text
    assert "FDL 9/2024" in bundle.articles[0].normalized_text


def test_duplicate_and_empty_articles_flagged_never_silently_merged() -> None:
    dup_empty_html = """<!DOCTYPE html>
    <html><body><main>
      <section class="law-article" data-article="8">
        <h2>المادة (8)</h2>
        <p>النص الأول للمادة الثامنة.</p>
      </section>
      <section class="law-article" data-article="8">
        <h2>المادة (8)</h2>
        <p></p>
      </section>
    </main></body></html>""".encode("utf-8")
    bundle = extract_html_bytes(
        dup_empty_html,
        source_id="SRC_DUP",
        document_id="DOC_DUP",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert bundle.requires_human_review is True
    codes = {d.code for d in bundle.diagnostics}
    assert "DUPLICATE_ARTICLE_ID" in codes
    assert "EMPTY_ARTICLE_BODY" in codes


def test_ambiguous_missing_and_unsupported_html_layouts_flagged() -> None:
    no_articles_html = """<!DOCTYPE html>
    <html><body><main><div><table><tr><td>Raw table without article headings</td></tr></table></div></main></body></html>""".encode(
        "utf-8"
    )
    bundle = extract_html_bytes(
        no_articles_html,
        source_id="SRC_UNSUP",
        document_id="DOC_UNSUP",
        language="en",
        is_authoritative=False,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert bundle.requires_human_review is True
    assert len(bundle.articles) == 0
    assert any(
        d.code in {"MISSING_STATUTORY_STRUCTURE", "UNSUPPORTED_HTML_LAYOUT"}
        for d in bundle.diagnostics
    )


def test_malformed_deeply_nested_and_challenge_html_rejected() -> None:
    challenge = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body class='cf-chl-body'>Checking</body></html>"
    with pytest.raises(ExtractionError, match="challenge"):
        extract_html_bytes(
            challenge,
            source_id="SRC_CHALLENGE",
            document_id="DOC_CHALLENGE",
            language="ar",
            is_authoritative=True,
            authenticity_tier=AuthenticityTier.LEGACY_UNATTESTED,
            redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        )

    deep_html = (
        b"<!DOCTYPE html><html><body>"
        + b"<div>" * 160
        + b"<p>Deep</p>"
        + b"</div>" * 160
        + b"</body></html>"
    )
    with pytest.raises(ExtractionError, match="nesting depth"):
        extract_html_bytes(
            deep_html,
            source_id="SRC_DEEP",
            document_id="DOC_DEEP",
            language="en",
            is_authoritative=False,
            authenticity_tier=AuthenticityTier.LEGACY_UNATTESTED,
            redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        )


def test_snapshot_hash_mismatch_rejected_single_open_toctou_safe(tmp_path: Path) -> None:
    target, digest = store_snapshot(_SAMPLE_AR_STATUTE_HTML, tmp_path, ext="html")
    # Corrupt file on disk after staging
    target.write_bytes(b"<!DOCTYPE html><html><body>Tampered</body></html>")
    source = LegalSource(
        source_id="TAMPERED_HTML",
        document_id="DOC_TAMPERED",
        language="ar",
        publisher="MOHRE",
        source_url="https://uaelegislation.gov.ae/ar/legislations/1541",
        local_path=target.relative_to(tmp_path.resolve()).as_posix(),
        retrieved_at="2026-11-26T10:00:00Z",
        sha256=digest,
        is_authoritative=True,
        artifact_type="raw_html",
        extraction_method="manual",
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        attestation_id="att-tamper",
    )
    with pytest.raises(SnapshotIntegrityError, match="checksum mismatch"):
        extract_from_snapshot(source, tmp_path)


def test_synthetic_fixture_isolation_and_production_immutability(tmp_path: Path) -> None:
    prod_dir = Path("data/official_sources").resolve()
    bundle = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="SYNTHETIC_TEST_ONLY_HTML",
        document_id="SYNTHETIC_TEST_ONLY_DOC",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        allow_synthetic=True,
    )
    assert bundle.is_synthetic_fixture is True
    # Attempting to extract synthetic ID without allow_synthetic=True or persist to production fails closed
    target, digest = store_snapshot(_SAMPLE_AR_STATUTE_HTML, tmp_path, ext="html")
    synthetic_source = LegalSource(
        source_id="SYNTHETIC_TEST_ONLY_SRC",
        document_id="FDL_33_2021_CURRENT",
        language="ar",
        publisher="MOHRE",
        source_url="https://uaelegislation.gov.ae/ar/legislations/1541",
        local_path=target.relative_to(tmp_path.resolve()).as_posix(),
        retrieved_at="2026-11-26T10:00:00Z",
        sha256=digest,
        is_authoritative=True,
        artifact_type="raw_html",
        extraction_method="manual",
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        attestation_id="att-syn",
    )
    with pytest.raises(SecurityPolicyViolationError, match="Synthetic"):
        extract_from_snapshot(synthetic_source, tmp_path, allow_synthetic=False)

    non_syn_bundle = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="REAL_STAGED_SRC",
        document_id="FDL_33_2021_CURRENT",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    with pytest.raises(SecurityPolicyViolationError):
        persist_extraction(prod_dir, non_syn_bundle)


def test_historical_gazette_712_and_consolidated_isolation(tmp_path: Path) -> None:
    hist_html = _SAMPLE_AR_STATUTE_HTML.replace(
        b"(6)",
        b"(6) \xd8\xb9\xd9\x82\xd8\xaf \xd9\x85\xd8\xad\xd8\xaf\xd8\xaf \xd8\xa8\xd8\xab\xd9\x84\xd8\xa7\xd8\xab \xd8\xb3\xd9\x86\xd9\x88\xd8\xa7\xd8\xaa",
    )
    consolidated_bundle = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="FDL_33_2021_AR_CONSOLIDATED",
        document_id="FDL_33_2021_CURRENT",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        historical_edition="consolidated_current",
    )
    historical_bundle = extract_html_bytes(
        hist_html,
        source_id="FDL_33_2021_AR_GAZETTE_712",
        document_id="FDL_33_2021_ORIGINAL_2021",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        historical_edition="gazette_712_2021_original",
    )
    assert historical_bundle.is_historical_unamended_source is True
    assert consolidated_bundle.is_historical_unamended_source is False
    assert historical_bundle.snapshot_sha256 != consolidated_bundle.snapshot_sha256

    persist_extraction(tmp_path, consolidated_bundle)
    persist_extraction(tmp_path, historical_bundle)

    # Attempting to bind the SAME document_id or snapshot hash to both historical and consolidated must FAIL
    mixed_doc_bundle = extract_html_bytes(
        hist_html,
        source_id="FDL_33_2021_AR_GAZETTE_712_MIXED",
        document_id="FDL_33_2021_CURRENT",  # collides with consolidated document_id!
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        historical_edition="gazette_712_2021_original",
    )
    with pytest.raises(SecurityPolicyViolationError, match="Historical source isolation"):
        persist_extraction(tmp_path, mixed_doc_bundle)

    # Same snapshot hash cannot cross historical/consolidated boundary
    same_hash_hist = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,  # identical bytes to consolidated_bundle!
        source_id="FDL_33_2021_AR_SAME_HASH",
        document_id="FDL_33_2021_OTHER_HIST",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
        historical_edition="gazette_712_2021_original",
    )
    with pytest.raises(SecurityPolicyViolationError, match="Historical source isolation"):
        persist_extraction(tmp_path, same_hash_hist)

    blocker = report_gazette_712_blocker()
    assert blocker.blocker_code == GAZETTE_712_SOURCE_BLOCKER
    assert blocker.acquired is False
    assert blocker.derived_from_consolidated_forbidden is True


def test_interrupted_persist_cleanup_leaves_no_partial_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = extract_html_bytes(
        _SAMPLE_AR_STATUTE_HTML,
        source_id="SRC_FAIL_WRITE",
        document_id="DOC_FAIL_WRITE",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("Simulated disk failure during rename")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError, match="Simulated disk failure"):
        persist_extraction(tmp_path, bundle)

    extracted_dir = tmp_path / "extracted"
    if extracted_dir.exists():
        assert list(extracted_dir.iterdir()) == []


def test_heading_delimited_layout_b_and_unexpected_boundaries(tmp_path: Path) -> None:
    # Layout B: heading-delimited statute (no section.law-article wrapper)
    layout_b_html = """<!DOCTYPE html>
    <html lang="ar"><body><main>
      <h1>المرسوم بقانون</h1>
      <h2>المادة (1) — التعريفات</h2>
      <p>يُقصد بالوزارة وزارة الموارد البشرية والتوطين.</p>
      <h2>المادة (2) — الأهداف</h2>
      <ol><li>تنظيم سوق العمل.</li></ol>
    </main></body></html>""".encode("utf-8")
    b_bundle = extract_html_bytes(
        layout_b_html,
        source_id="SRC_LAYOUT_B",
        document_id="DOC_LAYOUT_B",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert [a.article_number for a in b_bundle.articles] == ["1", "2"]
    assert b_bundle.requires_human_review is False

    # Orphan text outside article containers + out-of-order numbering triggers UNEXPECTED_ARTICLE_BOUNDARY
    anomaly_html = """<!DOCTYPE html>
    <html><body><main>
      <p>نص قانوني غير مغلف خارج حاوية المادة قد يُسقط سهواً.</p>
      <section class="law-article" data-article="10"><h2>المادة (10)</h2><p>نص المادة العاشرة.</p></section>
      <section class="law-article" data-article="3"><h2>المادة (3)</h2><p>نص المادة الثالثة.</p></section>
    </main></body></html>""".encode("utf-8")
    anom = extract_html_bytes(
        anomaly_html,
        source_id="SRC_ANOM",
        document_id="DOC_ANOM",
        language="ar",
        is_authoritative=True,
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY,
        redistribution_clearance=RedistributionClearance.PENDING_REVIEW,
    )
    assert anom.requires_human_review is True
    assert any(d.code == "UNEXPECTED_ARTICLE_BOUNDARY" for d in anom.diagnostics)
    assert len(anom.review_queue) >= 1

    # End-to-end ingest_local -> extract_from_staged_source -> persist_extraction
    from datetime import datetime, timezone

    from uae_lexagent.corpus.acquisition import ingest_local
    from uae_lexagent.corpus.extractor import extract_from_staged_source
    from uae_lexagent.models import CorroborationType, ManualAttestation

    raw_html_file = tmp_path / "statute_b.html"
    raw_html_file.write_bytes(layout_b_html)
    att = ManualAttestation(
        attestation_id="att-b-1",
        source_id="STAGED_HTML_SRC",
        document_id="STAGED_HTML_DOC",
        captured_sha256=hashlib.sha256(layout_b_html).hexdigest(),
        operator_id="op-1",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://uaelegislation.gov.ae/ar/legislations/1541",
        portal_navigation_steps="Navigated to portal legislation page and captured UTF-8 DOM HTML.",
        capture_evidence_ref="evidence/dom.png",
        independent_cross_check_ref="none",
        independent_cross_check_passed=False,
        corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
    )
    staged_root = tmp_path / "staging"
    ingest_local(
        raw_html_file,
        att,
        "STAGED_HTML_DOC",
        "ar",
        "UAE Ministry of Justice",
        True,
        staged_root,
        artifact_type="raw_html",
    )
    extracted = extract_from_staged_source(staged_root, "STAGED_HTML_SRC")
    assert extracted.authenticity_tier is AuthenticityTier.OPERATOR_ATTESTED_ONLY
    assert [a.article_number for a in extracted.articles] == ["1", "2"]
    out_file = persist_extraction(staged_root, extracted)
    assert out_file.exists()
