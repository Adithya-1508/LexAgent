"""Comprehensive tests for source acquisition, attestation, and snapshot storage (T-03)."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from uae_lexagent.corpus.acquisition import (
    UNRETRIEVED_UPSTREAM_PDF_GATE,
    AcquisitionError,
    SecurityPolicyViolationError,
    SnapshotIntegrityError,
    acquire_from_url,
    compute_snapshot_path,
    ingest_local,
    is_approved_government_url,
    main,
    store_snapshot,
)
from uae_lexagent.models import (
    AuthenticityTier,
    CorroborationType,
    ManualAttestation,
    RedistributionClearance,
)

# =============================================================================
# 1. URL and Security Policy Validation
# =============================================================================


def test_is_approved_government_url_valid() -> None:
    assert is_approved_government_url("https://mohre.gov.ae/en/rules.aspx")
    assert is_approved_government_url("https://www.mohre.gov.ae/assets/download/law.pdf")
    assert is_approved_government_url("https://uaelegislation.gov.ae/ar/legislations/1541/download")
    assert is_approved_government_url("https://www.uaelegislation.gov.ae/en")


def test_is_approved_government_url_rejected_insecure_scheme() -> None:
    assert not is_approved_government_url("http://mohre.gov.ae/law.pdf")
    assert not is_approved_government_url("ftp://mohre.gov.ae/law.pdf")
    assert not is_approved_government_url("file:///etc/passwd")


def test_is_approved_government_url_rejected_deceptive_domains() -> None:
    # Deceptive subdomain suffixes
    assert not is_approved_government_url("https://mohre.gov.ae.attacker.com/download.pdf")
    assert not is_approved_government_url("https://uaelegislation.gov.ae.evil.org/law.pdf")
    # Deceptive domain prefixes
    assert not is_approved_government_url("https://fake-mohre.gov.ae/law.pdf")
    assert not is_approved_government_url("https://evil-uaelegislation.gov.ae/law.pdf")
    # Unrelated domains
    assert not is_approved_government_url("https://google.com")
    assert not is_approved_government_url("https://localhost:8000/law.pdf")


def test_is_approved_government_url_rejected_ports_and_userinfo() -> None:
    # Non-standard port
    assert not is_approved_government_url("https://mohre.gov.ae:8443/download.pdf")
    # Userinfo embedded
    assert not is_approved_government_url("https://user:password@mohre.gov.ae/download.pdf")
    # Malformed
    assert not is_approved_government_url("")
    assert not is_approved_government_url("not a url")


# =============================================================================
# 2. Content-Addressed Snapshot Storage and Integrity
# =============================================================================


def test_compute_snapshot_path_layout(tmp_path: Path) -> None:
    sha = "e" * 64
    target = compute_snapshot_path(tmp_path, sha, "pdf")
    expected = tmp_path.resolve() / "snapshots" / "ee" / f"{sha}.pdf"
    assert target == expected


def test_compute_snapshot_path_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(SnapshotIntegrityError):
        compute_snapshot_path(tmp_path, "../../../etc/passwd", "pdf")

    with pytest.raises(SnapshotIntegrityError):
        compute_snapshot_path(tmp_path, "not-a-hash", "pdf")


def test_store_snapshot_atomic_and_idempotent(tmp_path: Path) -> None:
    content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    target_path, sha256_hash = store_snapshot(content, tmp_path, ext="pdf")

    assert target_path.exists()
    assert target_path.read_bytes() == content

    # Second call is idempotent and returns identical path
    second_path, second_hash = store_snapshot(content, tmp_path, ext="pdf")
    assert second_path == target_path
    assert second_hash == sha256_hash


def test_store_snapshot_detects_disk_corruption(tmp_path: Path) -> None:
    content = b"%PDF-1.4 original content"
    target_path, _ = store_snapshot(content, tmp_path, ext="pdf")

    # Corrupt the file on disk
    target_path.write_bytes(b"corrupted bytes on disk")

    # Attempting to store or access with expected checksum must raise SnapshotIntegrityError
    with pytest.raises(SnapshotIntegrityError, match="Snapshot corruption detected"):
        store_snapshot(content, tmp_path, ext="pdf")


def test_store_snapshot_bit_flip_mutation(tmp_path: Path) -> None:
    original = b"%PDF-1.4 pure content"
    target, orig_sha = store_snapshot(original, tmp_path, ext="pdf")

    # Mutate a single bit in expected SHA-256
    mutated_sha = orig_sha[:-1] + ("0" if orig_sha[-1] != "0" else "1")

    with pytest.raises(SnapshotIntegrityError, match="Checksum mismatch"):
        store_snapshot(original, tmp_path, ext="pdf", expected_sha256=mutated_sha)


# =============================================================================
# 3. Automated URL Acquisition (`acquire_from_url`)
# =============================================================================


def test_acquire_from_url_success_with_mock_client(tmp_path: Path) -> None:
    pdf_bytes = b"%PDF-1.4 mock pdf document"
    cert_fp = "SHA256:4cb50467dcfef9341aca93f51360575e83c7b81ead9f837491dee3a75fd81eb0"

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf_bytes, headers={"content-type": "application/pdf"})

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    source = acquire_from_url(
        source_id="MOHRE_FDL_33_2021_AR_PDF",
        document_id="FDL_33_2021",
        url="https://uaelegislation.gov.ae/ar/legislations/1541/download",
        language="ar",
        is_authoritative=True,
        snapshot_dir=tmp_path,
        artifact_type="raw_pdf",
        client=client,
        cert_fingerprint_fn=lambda host, port, timeout: cert_fp,
    )

    assert source.source_id == "MOHRE_FDL_33_2021_AR_PDF"
    assert source.authenticity_tier is AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED
    assert source.tls_cert_fingerprint == cert_fp
    assert source.is_authoritative is True
    assert source.language == "ar"
    assert source.extraction_method == "pdf_text"
    assert Path(source.local_path).exists() or (tmp_path / Path(source.local_path).name).exists()


def test_acquire_from_url_rejects_unapproved_domain(tmp_path: Path) -> None:
    with pytest.raises(SecurityPolicyViolationError, match="approved UAE government portal"):
        acquire_from_url(
            source_id="BAD_URL",
            document_id="DOC_1",
            url="https://evil.com/law.pdf",
            language="en",
            is_authoritative=False,
            snapshot_dir=tmp_path,
        )


def test_acquire_from_url_rejects_unauthorized_redirect(tmp_path: Path) -> None:
    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://attacker.com/malware.pdf"})

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    with pytest.raises(SecurityPolicyViolationError, match="Redirect target.*violates approved"):
        acquire_from_url(
            source_id="MOHRE_REDIRECT_BAD",
            document_id="DOC_1",
            url="https://mohre.gov.ae/law.pdf",
            language="en",
            is_authoritative=False,
            snapshot_dir=tmp_path,
            client=client,
            cert_fingerprint_fn=lambda h, p, t: "SHA256:" + "0" * 64,
        )


def test_acquire_from_url_rejects_non_pdf_content(tmp_path: Path) -> None:
    html_challenge = b"<!DOCTYPE html><html><body>Cloudflare Challenge</body></html>"

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html_challenge, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    with pytest.raises(AcquisitionError, match="valid PDF magic bytes"):
        acquire_from_url(
            source_id="MOHRE_HTML_NOT_PDF",
            document_id="DOC_1",
            url="https://uaelegislation.gov.ae/ar/legislations/1541/download",
            language="ar",
            is_authoritative=True,
            snapshot_dir=tmp_path,
            artifact_type="raw_pdf",
            client=client,
            cert_fingerprint_fn=lambda h, p, t: "SHA256:" + "0" * 64,
        )


def test_acquire_from_url_detects_http_403_waf_challenge(tmp_path: Path) -> None:
    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"Access Denied")

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    with pytest.raises(AcquisitionError) as exc_info:
        acquire_from_url(
            source_id="MOHRE_WAF_GATED",
            document_id="DOC_1",
            url="https://uaelegislation.gov.ae/ar/legislations/1541/download",
            language="ar",
            is_authoritative=True,
            snapshot_dir=tmp_path,
            client=client,
            cert_fingerprint_fn=lambda h, p, t: "SHA256:" + "0" * 64,
        )
    assert UNRETRIEVED_UPSTREAM_PDF_GATE in str(exc_info.value)


# =============================================================================
# 4. Manual Local Ingestion (`ingest_local`)
# =============================================================================


def test_ingest_local_operator_attested_only(tmp_path: Path) -> None:
    file_path = tmp_path / "manual_fdl_33_2021.pdf"
    file_path.write_bytes(b"%PDF-1.4 manual operator capture")

    attestation = ManualAttestation(
        attestation_id="attest-2026-001",
        source_id="MOHRE_FDL_33_2021_AR_MANUAL",
        operator_id="operator-dxb",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://uaelegislation.gov.ae/ar/legislations/1541/download",
        portal_navigation_steps="Opened browser, navigated to portal, downloaded PDF to local disk.",
        capture_evidence_ref="evidence/screen.png",
        independent_cross_check_ref="none",
        independent_cross_check_passed=False,
        corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
    )

    source = ingest_local(
        file_path=file_path,
        attestation=attestation,
        document_id="FDL_33_2021",
        language="ar",
        publisher="UAE Ministry of Justice",
        is_authoritative=True,
        snapshot_dir=tmp_path,
        artifact_type="raw_pdf",
    )

    assert source.source_id == "MOHRE_FDL_33_2021_AR_MANUAL"
    assert source.authenticity_tier is AuthenticityTier.OPERATOR_ATTESTED_ONLY
    assert source.attestation_id == "attest-2026-001"
    assert source.extraction_method == "manual"
    assert source.redistribution_clearance is RedistributionClearance.PENDING_REVIEW


def test_ingest_local_independently_corroborated(tmp_path: Path) -> None:
    file_path = tmp_path / "corroborated.pdf"
    file_path.write_bytes(b"%PDF-1.4 corroborated government pdf")

    attestation = ManualAttestation(
        attestation_id="attest-2026-002",
        source_id="MOHRE_FDL_33_2021_CORROBORATED",
        operator_id="operator-dxb",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://uaelegislation.gov.ae/ar/legislations/1541/download",
        portal_navigation_steps="Acquired from portal and corroborated against official mirror digest.",
        capture_evidence_ref="evidence/screen.png",
        independent_cross_check_ref="mirror_digest_match:sha256",
        independent_cross_check_passed=True,
        corroboration_type=CorroborationType.AUTOMATED_MIRROR_MATCH,
    )

    source = ingest_local(
        file_path=file_path,
        attestation=attestation,
        document_id="FDL_33_2021",
        language="ar",
        publisher="UAE Ministry of Justice",
        is_authoritative=True,
        snapshot_dir=tmp_path,
        artifact_type="raw_pdf",
        redistribution_clearance=RedistributionClearance.VERIFIED_PUBLIC_LAW,
    )

    assert source.authenticity_tier is AuthenticityTier.GOV_INDEPENDENTLY_CORROBORATED
    assert source.redistribution_clearance is RedistributionClearance.VERIFIED_PUBLIC_LAW


def test_ingest_local_rejects_missing_file(tmp_path: Path) -> None:
    attestation = ManualAttestation(
        attestation_id="attest-003",
        source_id="src-003",
        operator_id="op",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://mohre.gov.ae/law.pdf",
        portal_navigation_steps="Step 1, step 2, step 3, step 4.",
        capture_evidence_ref="ref",
        independent_cross_check_ref="none",
        independent_cross_check_passed=False,
        corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
    )

    with pytest.raises(AcquisitionError, match="Local file does not exist"):
        ingest_local(
            file_path=tmp_path / "non_existent.pdf",
            attestation=attestation,
            document_id="DOC_1",
            language="ar",
            publisher="MOHRE",
            is_authoritative=True,
            snapshot_dir=tmp_path,
        )


def test_ingest_local_rejects_unapproved_claimed_source_url(tmp_path: Path) -> None:
    file_path = tmp_path / "untrusted.pdf"
    file_path.write_bytes(b"%PDF-1.4 test")

    attestation = ManualAttestation(
        attestation_id="attest-004",
        source_id="src-004",
        operator_id="op",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://untrusted-blog.com/law.pdf",
        portal_navigation_steps="Step 1, step 2, step 3, step 4.",
        capture_evidence_ref="ref",
        independent_cross_check_ref="none",
        independent_cross_check_passed=False,
        corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
    )

    with pytest.raises(SecurityPolicyViolationError, match="approved UAE government portal"):
        ingest_local(
            file_path=file_path,
            attestation=attestation,
            document_id="DOC_1",
            language="ar",
            publisher="MOHRE",
            is_authoritative=True,
            snapshot_dir=tmp_path,
        )


# =============================================================================
# 5. CLI Execution
# =============================================================================


def test_cli_acquire_invalid_url_fails() -> None:
    ret = main(
        [
            "acquire",
            "--source-id",
            "SRC_1",
            "--document-id",
            "DOC_1",
            "--url",
            "https://unapproved.com/law.pdf",
            "--lang",
            "en",
        ]
    )
    assert ret == 1
