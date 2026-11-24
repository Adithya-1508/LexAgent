"""Comprehensive tests for source acquisition, attestation, and snapshot storage (T-03)."""

import hashlib
import http.server
import json
import os
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import certifi
import httpx
import pymupdf
import pytest

from uae_lexagent.corpus import acquisition, storage
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


# =============================================================================
# 4. Manual Local Ingestion (`ingest_local`)
# =============================================================================


def test_ingest_local_operator_attested_only(tmp_path: Path) -> None:
    file_path = tmp_path / "manual_fdl_33_2021.pdf"
    file_path.write_bytes(_pdf())

    attestation = ManualAttestation(
        attestation_id="attest-2026-001",
        source_id="MOHRE_FDL_33_2021_AR_MANUAL",
        document_id="FDL_33_2021",
        captured_sha256=_sha(file_path.read_bytes()),
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
    file_path.write_bytes(_pdf())

    attestation = ManualAttestation(
        attestation_id="attest-2026-002",
        source_id="MOHRE_FDL_33_2021_CORROBORATED",
        document_id="FDL_33_2021",
        captured_sha256=_sha(file_path.read_bytes()),
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
    )

    assert source.authenticity_tier is AuthenticityTier.OPERATOR_ATTESTED_ONLY
    assert source.redistribution_clearance is RedistributionClearance.PENDING_REVIEW


def test_ingest_local_rejects_missing_file(tmp_path: Path) -> None:
    attestation = ManualAttestation(
        attestation_id="attest-003",
        source_id="src-003",
        document_id="DOC_1",
        captured_sha256="a" * 64,
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
        document_id="DOC_1",
        captured_sha256=_sha(file_path.read_bytes()),
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page()
        return document.tobytes()


@pytest.fixture(scope="module")
def local_certificates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    directory = tmp_path_factory.mktemp("synthetic-local-tls")
    for name in ("first", "final"):
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "2",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-keyout",
                str(directory / f"{name}-key.pem"),
                "-out",
                str(directory / f"{name}-cert.pem"),
            ],
            capture_output=True,
            check=True,
        )
    first = directory / "first-cert.pem"
    final = directory / "final-cert.pem"
    trust = directory / "trust.pem"
    trust.write_bytes(first.read_bytes() + final.read_bytes())
    return first, final, trust


class LocalPortal:
    def __init__(self, certificate: Path) -> None:
        self.requests: list[str] = []
        self.response: tuple[int, dict[str, str], bytes] = (200, {}, b"")
        portal = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                portal.requests.append(self.path)
                if self.path == "/timeout":
                    time.sleep(0.3)
                    return
                status, headers, content = portal.response
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(content)
                except (ConnectionError, ssl.SSLError):
                    pass

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        key = certificate.with_name(certificate.name.replace("-cert", "-key"))
        context.load_cert_chain(certificate, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.url = f"https://localhost:{self.server.server_port}"
        self.fingerprint = "SHA256:" + _sha(ssl.PEM_cert_to_DER_cert(certificate.read_text()))
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


@pytest.fixture
def portals(
    local_certificates: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[LocalPortal, LocalPortal]]:
    first, final, trust = local_certificates
    servers = (LocalPortal(first), LocalPortal(final))
    validate = acquisition.is_approved_government_url
    monkeypatch.setattr(certifi, "where", lambda: str(trust))
    monkeypatch.setattr(
        acquisition,
        "is_approved_government_url",
        lambda url: validate(url) or any(url.startswith(server.url + "/") for server in servers),
    )
    try:
        yield servers
    finally:
        for server in servers:
            server.close()


def _acquire(root: Path, url: str, **kwargs: object) -> object:
    return acquire_from_url("loopback-test", "test-document", url, "ar", True, root, **kwargs)


def test_acquire_from_url_success_with_mock_client(tmp_path: Path) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(TypeError, match="client"):
            _acquire(tmp_path, "https://mohre.gov.ae/law.pdf", client=client)
    assert not (tmp_path / "manifest.json").exists()


def test_https_capture_persists_the_final_transport_certificate(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, final = portals
    original, destination = first.url + "/start", final.url + "/law.pdf"
    first.response = (302, {"Location": destination}, b"")
    content = _pdf()
    final.response = (
        200,
        {
            "Content-Type": "application/pdf",
            "ETag": '"test"',
            "Content-Length": str(len(content)),
            "Set-Cookie": "private=1",
        },
        content,
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "no-environment-ca.pem"))

    assert (
        main(
            [
                "acquire",
                "--source-id",
                "loopback-test",
                "--document-id",
                "test-document",
                "--url",
                original,
                "--lang",
                "ar",
                "--authoritative",
                "--snapshot-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    records = json.loads((tmp_path / "manifest.json").read_text())
    assert len(records) == 1
    record = records[0]
    assert record["source_url"] == original
    assert record["tls_cert_fingerprint"] == final.fingerprint
    assert record["tls_cert_fingerprint"] != first.fingerprint
    assert record["authenticity_tier"] == "GOV_AUTOMATED_TLS_VERIFIED"
    assert record["http_capture"]["final_url"] == destination
    assert record["http_capture"]["redirect_history"] == [original]
    assert record["http_capture"]["status_code"] == 200
    assert record["http_capture"]["content_type"] == "application/pdf"
    assert record["http_capture"]["response_headers"]["etag"] == '"test"'
    assert "set-cookie" not in record["http_capture"]["response_headers"]
    assert record["redistribution_clearance"] == "pending_review"
    assert record["sha256"] == _sha(content)
    assert (tmp_path / record["local_path"]).read_bytes() == content
    assert first.requests == ["/start"]
    assert final.requests == ["/law.pdf"]


def test_acquire_from_url_rejects_unapproved_domain(tmp_path: Path) -> None:
    with pytest.raises(SecurityPolicyViolationError, match="approved UAE government portal"):
        _acquire(tmp_path, "https://example.test/law.pdf")


def test_acquire_from_url_rejects_unauthorized_redirect(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path
) -> None:
    first, final = portals
    first.response = (302, {"Location": "https://example.test/law.pdf"}, b"")
    with pytest.raises(SecurityPolicyViolationError, match="redirect target"):
        _acquire(tmp_path, first.url + "/start")
    assert first.requests == ["/start"]
    assert not final.requests
    assert not (tmp_path / "manifest.json").exists()


def test_acquire_from_url_rejects_non_pdf_content(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path
) -> None:
    first, _ = portals
    challenge = b"<!DOCTYPE html><html><body>Portal challenge</body></html>"
    first.response = (200, {"Content-Type": "text/html"}, challenge)
    with pytest.raises(AcquisitionError, match="Content-Type"):
        _acquire(tmp_path, first.url + "/start")


def test_acquire_from_url_detects_http_403_waf_challenge(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path
) -> None:
    first, _ = portals
    first.response = (403, {}, b"Access denied")
    with pytest.raises(AcquisitionError, match=UNRETRIEVED_UPSTREAM_PDF_GATE):
        _acquire(tmp_path, first.url + "/start")


def test_https_rejects_untrusted_certificate(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = portals
    from importlib.resources import files

    monkeypatch.setattr(certifi, "where", lambda: str(files("certifi") / "cacert.pem"))
    with pytest.raises(AcquisitionError, match="CERTIFICATE_VERIFY_FAILED"):
        _acquire(tmp_path, first.url + "/start")
    assert not first.requests


def test_response_without_verified_tls_evidence_is_rejected() -> None:
    with pytest.raises(AcquisitionError, match="Missing verified TLS evidence"):
        acquisition._peer_fingerprint(httpx.Response(200))


@pytest.mark.parametrize(
    ("status", "headers", "body", "message"),
    [
        (200, {}, b"x" * 257, "exceeds maximum"),
        (200, {"Content-Length": "512"}, b"x", "exceeds maximum"),
        (200, {"Content-Length": "102"}, b"x", "HTTPS acquisition failed"),
        (200, {"Content-Length": "invalid"}, b"x", "HTTPS acquisition failed"),
        (200, {"Content-Encoding": "gzip"}, b"x", "Compressed responses"),
        (200, {"Content-Range": "bytes 0-3/20"}, b"1234", "Partial HTTP response"),
        (206, {}, b"x", "Unexpected HTTP status 206"),
        (200, {}, b"%PDF-1.4\n%%EOF", "Malformed PDF"),
        (200, {}, b"%PDF-1.4\ntruncated", "Missing PDF header or end-of-file"),
    ],
    ids=[
        "stream-limit",
        "declared-limit",
        "interrupted",
        "invalid-framing",
        "compressed",
        "content-range",
        "partial-status",
        "malformed-pdf",
        "truncated-pdf",
    ],
)
def test_bad_http_response_has_no_accepted_artifacts(
    portals: tuple[LocalPortal, LocalPortal],
    tmp_path: Path,
    status: int,
    headers: dict[str, str],
    body: bytes,
    message: str,
) -> None:
    first, _ = portals
    first.response = (status, {"Content-Type": "application/pdf", **headers}, body)
    with pytest.raises(AcquisitionError, match=message):
        _acquire(tmp_path, first.url + "/start", max_bytes=256)
    assert not list(tmp_path.iterdir())


def test_redirect_limit_and_timeout(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path
) -> None:
    first, _ = portals
    first.response = (302, {"Location": "/redirect"}, b"")
    with pytest.raises(AcquisitionError, match="Exceeded maximum redirects"):
        _acquire(tmp_path, first.url + "/redirect")
    assert first.requests == ["/redirect"] * 6
    with pytest.raises(AcquisitionError, match="HTTPS acquisition failed"):
        _acquire(tmp_path, first.url + "/timeout", timeout_seconds=0.1)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            b"<!doctype html><html><head><title>Just a moment...</title></head><body>Wait</body></html>",
            "HTML challenge",
        ),
        (b"<!-- <html> --><h1>Not an HTML snapshot</h1></html>", "Missing HTML document start"),
        (b"<html><body>Broken", "Missing HTML document end"),
    ],
    ids=["challenge", "deceptive-structure", "truncated-html"],
)
def test_invalid_html_acquisition(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path, content: bytes, message: str
) -> None:
    first, _ = portals
    first.response = (200, {"Content-Type": "text/html; charset=utf-8"}, content)
    with pytest.raises(AcquisitionError, match=message):
        _acquire(tmp_path, first.url + "/start", artifact_type="raw_html")
    assert not list(tmp_path.iterdir())


def _attestation(content: bytes, **changes: object) -> ManualAttestation:
    data: dict[str, object] = dict(
        attestation_id="operator-attestation",
        source_id="operator-source",
        document_id="test-document",
        captured_sha256=_sha(content),
        operator_id="test-operator",
        acquired_at_utc=datetime.now(timezone.utc),
        claimed_source_url="https://mohre.gov.ae/law.pdf",
        portal_navigation_steps="Opened portal and downloaded the identified instrument.",
        capture_evidence_ref="capture-record",
        independent_cross_check_ref="none",
        independent_cross_check_passed=False,
        corroboration_type=CorroborationType.NONE_OPERATOR_ONLY,
    )
    return ManualAttestation.model_validate({**data, **changes})


@pytest.mark.parametrize("change", ["bit-flip", "replacement", "wrong-document", "wrong-digest"])
def test_manual_binding_rejects_mismatch(tmp_path: Path, change: str) -> None:
    pdf = _pdf()
    file = tmp_path / "input.pdf"
    attestation = _attestation(pdf)
    requested = "test-document"
    if change == "bit-flip":
        pdf = pdf[:-1] + bytes([pdf[-1] ^ 1])
    elif change == "replacement":
        pdf = _pdf()
        assert _sha(pdf) != attestation.captured_sha256
    elif change == "wrong-document":
        requested = "different-document"
    elif change == "wrong-digest":
        attestation = _attestation(pdf, captured_sha256="0" * 64)
    file.write_bytes(pdf)
    root = tmp_path / "staging"
    with pytest.raises((SnapshotIntegrityError, SecurityPolicyViolationError)):
        ingest_local(file, attestation, requested, "ar", "Fixture", True, root)
    assert not root.exists()


def test_manual_rejects_oversized_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _pdf()
    file = tmp_path / "local.pdf"
    file.write_bytes(data)
    monkeypatch.setattr(acquisition, "MAX_ACQUISITION_BYTES", len(data) - 1)
    with pytest.raises(AcquisitionError, match="exceeds maximum"):
        ingest_local(
            file, _attestation(data), "test-document", "ar", "Fixture", True, tmp_path / "stage"
        )
    assert not (tmp_path / "stage").exists()


def test_manual_cli_persists_attestation_and_rejects_reuse(tmp_path: Path) -> None:
    original = _pdf()
    file = tmp_path / "manual.pdf"
    evidence = tmp_path / "attestation.json"
    root = tmp_path / "stage"
    attestation = _attestation(original)
    file.write_bytes(original)
    evidence.write_text(attestation.model_dump_json())
    argv = [
        "ingest-local",
        "--file",
        str(file),
        "--attestation",
        str(evidence),
        "--document-id",
        "test-document",
        "--lang",
        "ar",
        "--authoritative",
        "--publisher",
        "Fixture",
        "--snapshot-dir",
        str(root),
    ]
    assert main(argv) == main(argv) == 0
    manifest = (root / "manifest.json").read_bytes()
    attestations = (root / "attestations.json").read_bytes()
    assert json.loads(attestations) == [attestation.model_dump(mode="json")]
    source = json.loads(manifest)[0]
    assert source["attestation_id"] == attestation.attestation_id
    assert source["sha256"] == attestation.captured_sha256
    assert source["http_capture"] is None
    assert source["tls_cert_fingerprint"] is None
    assert (root / source["local_path"]).read_bytes() == original

    evidence.write_text(
        _attestation(
            original, claimed_source_url="https://mohre.gov.ae/other.pdf"
        ).model_dump_json()
    )
    assert main(argv) == 1
    replacement = _pdf()
    file.write_bytes(replacement)
    evidence.write_text(_attestation(replacement, source_id="different-source").model_dump_json())
    assert main(argv) == 1
    assert (root / "manifest.json").read_bytes() == manifest
    assert (root / "attestations.json").read_bytes() == attestations
    assert len(list((root / "snapshots").rglob("*.pdf"))) == 1


def test_private_staging_rejects_synthetic_artifacts_and_clearance(tmp_path: Path) -> None:
    pdf = _pdf()
    file = tmp_path / "manual.pdf"
    file.write_bytes(pdf)
    with pytest.raises(SecurityPolicyViolationError, match="Synthetic"):
        ingest_local(
            file,
            _attestation(pdf, source_id="SYNTHETIC_TEST_ONLY_1"),
            "test-document",
            "ar",
            "Fixture",
            True,
            tmp_path,
        )
    with pytest.raises(SecurityPolicyViolationError, match="redistribution clearance"):
        ingest_local(
            file,
            _attestation(pdf),
            "test-document",
            "ar",
            "Fixture",
            True,
            tmp_path,
            redistribution_clearance=RedistributionClearance.VERIFIED_PUBLIC_LAW,
        )
    with pytest.raises(SecurityPolicyViolationError, match="private staging"):
        ingest_local(
            file,
            _attestation(pdf),
            "test-document",
            "ar",
            "Fixture",
            True,
            Path("data/official_sources"),
        )
    assert not (tmp_path / "manifest.json").exists()


def test_concurrent_identical_snapshot_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"synthetic content-addressed fixture"
    barrier = threading.Barrier(2, timeout=5)
    publish = storage._publish_snapshot

    def simultaneous_link(first: Path, second: Path) -> None:
        barrier.wait()
        publish(first, second)

    monkeypatch.setattr(storage, "_publish_snapshot", simultaneous_link)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = (
            executor.submit(store_snapshot, content, tmp_path, "pdf") for _ in range(2)
        )
        assert first.result(timeout=10) == second.result(timeout=10)
    assert first.result()[0].read_bytes() == content
    assert not list(tmp_path.rglob("*.tmp"))


def test_snapshot_never_overwrites_conflicting_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"synthetic content-addressed fixture"
    publish = storage._publish_snapshot
    destination = compute_snapshot_path(tmp_path, _sha(content), "pdf")

    def competing_creation(first: Path, second: Path) -> None:
        with second.open("xb") as competitor:
            competitor.write(b"competing writer")
        publish(first, second)

    monkeypatch.setattr(storage, "_publish_snapshot", competing_creation)
    with pytest.raises(SnapshotIntegrityError, match="Snapshot corruption detected"):
        store_snapshot(content, tmp_path, "pdf")
    assert destination.read_bytes() == b"competing writer"
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("boundary", ["snapshot-file", "shard-directory", "snapshot-directory"])
def test_snapshot_rejects_redirected_storage(tmp_path: Path, boundary: str) -> None:
    content = b"synthetic content-addressed fixture"
    root = tmp_path / "private"
    target = compute_snapshot_path(root, _sha(content), "pdf")
    outside = tmp_path / "outside"
    outside.mkdir()
    if boundary == "snapshot-file":
        link = target
        redirect = outside / "original.pdf"
        redirect.write_bytes(content)
    else:
        link = target.parent if boundary == "shard-directory" else root / "snapshots"
        redirect = outside
    link.parent.mkdir(parents=True)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(redirect, target_is_directory=boundary != "snapshot-file")
    with pytest.raises(SecurityPolicyViolationError, match="redirected path"):
        store_snapshot(content, root, "pdf")
    assert not list(outside.glob("*.tmp"))


def test_manifest_commit_failure_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _pdf()
    file = tmp_path / "manual.pdf"
    file.write_bytes(pdf)
    root = tmp_path / "stage"
    attestation = _attestation(pdf)
    replace = os.replace

    def fail_manifest(source: Path, destination: Path) -> None:
        if destination.name == "manifest.json":
            raise OSError("simulated filesystem failure")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_manifest)
        with pytest.raises(OSError, match="simulated filesystem failure"):
            ingest_local(file, attestation, "test-document", "ar", "Fixture", True, root)
    assert not (root / "manifest.json").exists()
    assert not (root / "attestations.json").exists()
    assert not list(root.rglob("*.tmp"))
    accepted = ingest_local(file, attestation, "test-document", "ar", "Fixture", True, root)
    assert (root / accepted.local_path).read_bytes() == pdf
    assert len(json.loads((root / "manifest.json").read_bytes())) == 1


def test_concurrent_manual_ingestion_produces_consistent_manifests(tmp_path: Path) -> None:
    pdf = _pdf()
    file = tmp_path / "manual.pdf"
    file.write_bytes(pdf)
    root = tmp_path / "stage"
    attestations = [
        _attestation(pdf, source_id=f"source-{number}", attestation_id=f"att-{number}")
        for number in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        ingest = executor.map(
            lambda attestation: ingest_local(
                file, attestation, "test-document", "ar", "Fixture", True, root
            ),
            attestations,
        )
        sources = list(ingest)
    manifest = json.loads((root / "manifest.json").read_bytes())
    recorded = json.loads((root / "attestations.json").read_bytes())
    assert {source["attestation_id"] for source in manifest} == {
        record["attestation_id"] for record in recorded
    }
    assert sources[0].local_path == sources[1].local_path
    assert len(manifest) == len(recorded) == 2


def test_html_capture_preserves_restricted_redistribution(
    portals: tuple[LocalPortal, LocalPortal], tmp_path: Path
) -> None:
    first, _ = portals
    content = b"<!doctype html><html><head><title>Fixture</title></head><body>Fixture</body></html>"
    first.response = (200, {"Content-Type": "text/html; charset=utf-8"}, content)
    source = acquire_from_url(
        "loopback-html",
        "test-document",
        first.url + "/law",
        "ar",
        True,
        tmp_path,
        artifact_type="raw_html",
        redistribution_clearance=RedistributionClearance.RESTRICTED_DO_NOT_COMMIT,
    )
    assert source.artifact_type == "raw_html"
    assert source.page_count is None
    assert (tmp_path / source.local_path).read_bytes() == content
    manifest = json.loads((tmp_path / "manifest.json").read_bytes())
    assert manifest[0]["redistribution_clearance"] == "restricted_do_not_commit"


def test_stream_reader_stops_at_limit_before_the_remaining_body() -> None:
    chunks_read = []

    class Body(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            for number in range(3):
                chunks_read.append(number)
                yield b"local fixture"

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Body()))
    ) as client:
        with client.stream("GET", "https://example.test/local-fixture") as response:
            with pytest.raises(AcquisitionError, match="exceeds maximum"):
                acquisition._read_response(response, max_bytes=20)
    assert chunks_read == [0, 1]
