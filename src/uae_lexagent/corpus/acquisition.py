"""Bounded source acquisition and attested local capture (T-03 / ADR-006)."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import ssl
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin, urlsplit

from ..ingestion import ALLOWED_OFFICIAL_HOSTNAMES
from ..models import (
    AuthenticityTier,
    HttpAcquisitionEvidence,
    LegalSource,
    ManualAttestation,
    RedistributionClearance,
)
from .storage import (
    DEFAULT_STAGING_DIR,
    AcquisitionError,
    SecurityPolicyViolationError,
    SnapshotIntegrityError,
    compute_snapshot_path,
    persist_acquisition,
    store_snapshot,
)

if TYPE_CHECKING:
    import httpx

__all__ = [
    "UNRETRIEVED_UPSTREAM_PDF_GATE",
    "AcquisitionError",
    "SecurityPolicyViolationError",
    "SnapshotIntegrityError",
    "acquire_from_url",
    "compute_snapshot_path",
    "ingest_local",
    "is_approved_government_url",
    "store_snapshot",
]

UNRETRIEVED_UPSTREAM_PDF_GATE = "UNRETRIEVED_UPSTREAM_PDF_GATE"
MAX_ACQUISITION_BYTES = 50_000_000
MAX_ATTESTATION_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_REDIRECTS = 5
_HEADERS = (
    "content-type",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "etag",
    "last-modified",
    "date",
)
ArtifactType = Literal["raw_pdf", "raw_html"]
SourceRole = Literal["extraction_primary", "archival_reference"]
_CONTENT_TYPES: dict[ArtifactType, Literal["application/pdf", "text/html"]] = {
    "raw_pdf": "application/pdf",
    "raw_html": "text/html",
}


def is_approved_government_url(url: str) -> bool:
    """Require an exact HTTPS origin and reject ambiguous URL syntax."""
    if not isinstance(url, str) or any(
        ord(char) <= 32 or ord(char) >= 127 or char in "\\#" for char in url
    ):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        return (
            parsed.scheme == "https"
            and host in ALLOWED_OFFICIAL_HOSTNAMES
            and parsed.netloc.lower() in {host, f"{host}:443"}
        )
    except ValueError:
        return False


def _require_approved_url(url: str) -> None:
    if not is_approved_government_url(url):
        raise SecurityPolicyViolationError(
            "URL or redirect target violates approved UAE government portal HTTPS policy"
        )


def _validate_staging(source_id: str, document_id: str, clearance: RedistributionClearance) -> None:
    if any(value.upper().startswith("SYNTHETIC_TEST_ONLY") for value in (source_id, document_id)):
        raise SecurityPolicyViolationError("Synthetic fixtures cannot enter acquisition staging")
    if clearance not in (
        RedistributionClearance.PENDING_REVIEW,
        RedistributionClearance.RESTRICTED_DO_NOT_COMMIT,
    ):
        raise SecurityPolicyViolationError("Acquisition cannot grant redistribution clearance")


def _peer_fingerprint(response: httpx.Response) -> str:
    stream = response.extensions.get("network_stream")
    peer = stream.get_extra_info("ssl_object") if stream is not None else None
    context = getattr(peer, "context", None)
    if (
        peer is None
        or not isinstance(context, ssl.SSLContext)
        or context.verify_mode != ssl.CERT_REQUIRED
        or not context.check_hostname
    ):
        raise AcquisitionError("Missing verified TLS evidence from the response transport")
    certificate = peer.getpeercert(True)
    if not certificate:
        raise AcquisitionError("Missing TLS peer certificate from the response transport")
    return "SHA256:" + hashlib.sha256(certificate).hexdigest()


def _read_response(response: httpx.Response, max_bytes: int) -> bytes:
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise AcquisitionError(
            "Compressed responses are unsupported by the exact-byte capture policy"
        )
    if "content-range" in response.headers:
        raise AcquisitionError("Partial HTTP response")
    length = response.headers.get("content-length")
    if length is not None and not re.fullmatch(r"[0-9]+", length):
        raise AcquisitionError("Invalid HTTP Content-Length")
    if length is not None and (len(length) > 20 or int(length) > max_bytes):
        raise AcquisitionError(f"Response exceeds maximum limit of {max_bytes} bytes")

    content = bytearray()
    for chunk in response.iter_raw():
        if len(content) + len(chunk) > max_bytes:
            raise AcquisitionError(f"Response exceeds maximum limit of {max_bytes} bytes")
        content.extend(chunk)
    if length is not None and len(content) != int(length):
        raise AcquisitionError("Incomplete HTTP response")
    return bytes(content)


def _fetch(
    url: str, artifact_type: ArtifactType, max_bytes: int, timeout_seconds: float
) -> tuple[bytes, str, HttpAcquisitionEvidence]:
    import httpx

    history: list[str] = []
    expected_type = _CONTENT_TYPES[artifact_type]
    current_url = url
    try:
        # Keep certificate trust, proxy configuration and redirect control inside the boundary.
        with httpx.Client(verify=True, trust_env=False, timeout=timeout_seconds) as client:
            while True:
                _require_approved_url(current_url)
                with client.stream(
                    "GET",
                    current_url,
                    follow_redirects=False,
                    headers={"Accept-Encoding": "identity", "Accept": expected_type},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise AcquisitionError("Redirect missing Location header")
                        if len(history) == _MAX_REDIRECTS:
                            raise AcquisitionError("Exceeded maximum redirects")
                        # urljoin() can discard control characters before URL validation.
                        if any(
                            ord(char) <= 32 or ord(char) >= 127 or char == "\\" for char in location
                        ):
                            raise SecurityPolicyViolationError("Invalid HTTP redirect location")
                        history.append(current_url)
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code == 403:
                        raise AcquisitionError(
                            f"HTTP 403 Forbidden: {UNRETRIEVED_UPSTREAM_PDF_GATE}"
                        )
                    if response.status_code != 200:
                        raise AcquisitionError(f"Unexpected HTTP status {response.status_code}")
                    content_types = response.headers.get_list("content-type")
                    mime = (
                        content_types[0].split(";", 1)[0].strip().lower()
                        if len(content_types) == 1
                        else ""
                    )
                    if mime != expected_type:
                        raise AcquisitionError(
                            f"Expected Content-Type {expected_type}, got {mime!r}"
                        )
                    fingerprint = _peer_fingerprint(response)
                    content = _read_response(response, max_bytes)
                    evidence = HttpAcquisitionEvidence(
                        final_url=current_url,
                        redirect_history=tuple(history),
                        status_code=200,
                        content_type=expected_type,
                        response_headers={
                            key: response.headers[key]
                            for key in _HEADERS
                            if key in response.headers
                        },
                    )
                    return content, fingerprint, evidence
    except (httpx.HTTPError, httpx.InvalidURL, ssl.SSLError) as exc:
        raise AcquisitionError(f"HTTPS acquisition failed: {exc}") from exc


def _validate_artifact(content: bytes, artifact_type: ArtifactType) -> int | None:
    if artifact_type == "raw_pdf":
        import pymupdf

        if not re.match(rb"%PDF-[12]\.[0-9][\r\n]", content) or not content.rstrip().endswith(
            b"%%EOF"
        ):
            raise AcquisitionError("Missing PDF header or end-of-file marker")
        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if (
                    document.is_pdf
                    and not document.is_repaired
                    and not document.is_encrypted
                    and document.page_count
                ):
                    return int(document.page_count)
        except (ValueError, RuntimeError) as exc:
            raise AcquisitionError("Malformed PDF") from exc
        raise AcquisitionError("Malformed, encrypted or empty PDF")

    if artifact_type == "raw_html":
        from lxml import etree

        head = content[:2048].removeprefix(b"\xef\xbb\xbf").lstrip()
        if not re.match(rb"(?:<!doctype\s+html\b[^>]*>\s*)?<html(?:\s|>)", head, re.I):
            raise AcquisitionError("Missing HTML document start")
        if not re.search(rb"</html\s*>\s*$", content[-2048:], re.I):
            raise AcquisitionError("Missing HTML document end")
        # These are portal challenge markers. Shape checks establish no legal-content identity.
        if re.search(
            rb"cf-chl-|/cdn-cgi/challenge-platform/|<title>\s*(?:just a moment|access denied|attention required)",
            content,
            re.I,
        ):
            raise AcquisitionError("HTML challenge or access error page")
        try:
            parsed = etree.HTML(content, etree.HTMLParser(no_network=True))
        except etree.LxmlError as exc:
            raise AcquisitionError("Malformed HTML document") from exc
        if parsed is None or parsed.tag != "html" or parsed.find("body") is None:
            raise AcquisitionError("Malformed HTML document")
        return None

    raise ValueError(f"Unsupported artifact_type: {artifact_type}")


def _accept(
    content: bytes,
    root: Path,
    *,
    source_id: str,
    document_id: str,
    language: Literal["en", "ar"],
    publisher: str,
    url: str,
    is_authoritative: bool,
    artifact_type: ArtifactType,
    source_role: SourceRole,
    clearance: RedistributionClearance,
    attestation: ManualAttestation | None = None,
    fingerprint: str | None = None,
    evidence: HttpAcquisitionEvidence | None = None,
) -> LegalSource:
    digest = hashlib.sha256(content).hexdigest()
    if attestation is not None and digest != attestation.captured_sha256.lower():
        raise SnapshotIntegrityError("Checksum mismatch: file does not match the attested SHA-256")
    pages = _validate_artifact(content, artifact_type)
    path = compute_snapshot_path(root, digest, "pdf" if artifact_type == "raw_pdf" else "html")
    source = LegalSource(
        source_id=source_id,
        document_id=document_id,
        language=language,
        publisher=publisher,
        source_url=url,
        local_path=path.relative_to(root.resolve()).as_posix(),
        retrieved_at=attestation.acquired_at_utc if attestation else datetime.now(timezone.utc),
        sha256=digest,
        page_count=pages,
        is_authoritative=is_authoritative,
        artifact_type=artifact_type,
        source_role=source_role,
        extraction_method="manual"
        if attestation
        else ("pdf_text" if artifact_type == "raw_pdf" else "html_dom"),
        authenticity_tier=AuthenticityTier.OPERATOR_ATTESTED_ONLY
        if attestation
        else AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        attestation_id=attestation.attestation_id if attestation else None,
        tls_cert_fingerprint=fingerprint,
        http_capture=evidence,
        redistribution_clearance=clearance,
    )
    return persist_acquisition(root, source, content, attestation)


def acquire_from_url(
    source_id: str,
    document_id: str,
    url: str,
    language: Literal["en", "ar"],
    is_authoritative: bool,
    snapshot_dir: Path,
    *,
    artifact_type: ArtifactType = "raw_pdf",
    source_role: SourceRole = "extraction_primary",
    redistribution_clearance: RedistributionClearance = RedistributionClearance.PENDING_REVIEW,
    publisher: str = "UAE Official Portal",
    max_bytes: int = MAX_ACQUISITION_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> LegalSource:
    """Capture bytes and certificate evidence from a verified, owned HTTPS transport."""
    _require_approved_url(url)
    _validate_staging(source_id, document_id, redistribution_clearance)
    if (
        not 0 < max_bytes <= MAX_ACQUISITION_BYTES
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("Acquisition needs a positive finite timeout and a bounded size limit")
    if is_authoritative and language != "ar":
        raise ValueError("Authoritative legal sources must be Arabic ('ar')")
    content, fingerprint, evidence = _fetch(url, artifact_type, max_bytes, timeout_seconds)
    return _accept(
        content,
        snapshot_dir,
        source_id=source_id,
        document_id=document_id,
        language=language,
        publisher=publisher,
        url=url,
        is_authoritative=is_authoritative,
        artifact_type=artifact_type,
        source_role=source_role,
        clearance=redistribution_clearance,
        fingerprint=fingerprint,
        evidence=evidence,
    )


def _read_local_file(path: Path, limit: int, label: str) -> bytes:
    try:
        if not path.is_file():
            raise AcquisitionError(f"{label} does not exist or is not a regular file: {path}")
        with path.open("rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise AcquisitionError(f"{label} is not a regular file: {path}")
            if info.st_size <= limit:
                content = file.read(limit + 1)
                if len(content) <= limit:
                    return content
    except OSError as exc:
        raise AcquisitionError(f"Could not read {label.lower()}: {exc}") from exc
    raise AcquisitionError(f"{label} exceeds maximum size of {limit} bytes")


def ingest_local(
    file_path: Path,
    attestation: ManualAttestation,
    document_id: str,
    language: Literal["en", "ar"],
    publisher: str,
    is_authoritative: bool,
    snapshot_dir: Path,
    *,
    artifact_type: ArtifactType = "raw_pdf",
    source_role: SourceRole = "extraction_primary",
    redistribution_clearance: RedistributionClearance = RedistributionClearance.PENDING_REVIEW,
) -> LegalSource:
    """Bind exact input bytes to an operator's attestation and staging record."""
    # Revalidate the record at the trust boundary, including model_copy() callers.
    attestation = ManualAttestation.model_validate(attestation.model_dump())
    _require_approved_url(attestation.claimed_source_url)
    _validate_staging(attestation.source_id, document_id, redistribution_clearance)
    if attestation.attestation_id.upper().startswith("SYNTHETIC_TEST_ONLY"):
        raise SecurityPolicyViolationError(
            "Synthetic attestations cannot enter acquisition staging"
        )
    if attestation.document_id != document_id:
        raise SecurityPolicyViolationError(
            "Attested document_id contradicts the requested document"
        )
    if is_authoritative and language != "ar":
        raise ValueError("Authoritative legal sources must be Arabic ('ar')")

    content = _read_local_file(file_path, MAX_ACQUISITION_BYTES, "Local file")

    # Operator-supplied corroboration is kept as an assertion for later review.
    return _accept(
        content,
        snapshot_dir,
        source_id=attestation.source_id,
        document_id=document_id,
        language=language,
        publisher=publisher,
        url=attestation.claimed_source_url,
        is_authoritative=is_authoritative,
        artifact_type=artifact_type,
        source_role=source_role,
        clearance=redistribution_clearance,
        attestation=attestation,
    )


def build_cli_parser() -> argparse.ArgumentParser:
    """Build CLI parser for corpus acquisition workflows."""
    parser = argparse.ArgumentParser(
        prog="python -m uae_lexagent.corpus",
        description="Secure official source acquisition and snapshot management.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # acquire subcommand
    acquire_parser = subparsers.add_parser("acquire", help="Acquire source from live HTTPS URL")
    acquire_parser.add_argument("--source-id", required=True, help="Unique source identifier")
    acquire_parser.add_argument(
        "--document-id", required=True, help="Canonical document identifier"
    )
    acquire_parser.add_argument(
        "--url", required=True, help="HTTPS URL on approved government portal"
    )
    acquire_parser.add_argument(
        "--lang", choices=["ar", "en"], required=True, help="Source language"
    )
    acquire_parser.add_argument(
        "--publisher", default="UAE Official Portal", help="Publisher entity"
    )
    acquire_parser.add_argument(
        "--authoritative", action="store_true", help="Mark as authoritative Arabic text"
    )
    acquire_parser.add_argument(
        "--artifact-type",
        choices=["raw_pdf", "raw_html"],
        default="raw_pdf",
        help="Artifact type to acquire",
    )
    acquire_parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_STAGING_DIR,
        help="Private staging directory for snapshots and acquisition evidence",
    )

    # ingest-local subcommand
    ingest_parser = subparsers.add_parser(
        "ingest-local", help="Ingest local file with ManualAttestation"
    )
    ingest_parser.add_argument("--file", type=Path, required=True, help="Path to local file")
    ingest_parser.add_argument(
        "--attestation",
        type=Path,
        required=True,
        help="Path to ManualAttestation JSON (64 KiB maximum)",
    )
    ingest_parser.add_argument("--document-id", required=True, help="Canonical document identifier")
    ingest_parser.add_argument(
        "--lang", choices=["ar", "en"], required=True, help="Source language"
    )
    ingest_parser.add_argument("--publisher", required=True, help="Publisher entity")
    ingest_parser.add_argument(
        "--authoritative", action="store_true", help="Mark as authoritative Arabic text"
    )
    ingest_parser.add_argument(
        "--artifact-type",
        choices=["raw_pdf", "raw_html"],
        default="raw_pdf",
        help="Artifact type",
    )
    ingest_parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_STAGING_DIR,
        help="Private staging directory for snapshots and acquisition evidence",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for acquisition tools."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    try:
        if args.subcommand == "acquire":
            source = acquire_from_url(
                source_id=args.source_id,
                document_id=args.document_id,
                url=args.url,
                language=args.lang,
                is_authoritative=args.authoritative,
                snapshot_dir=args.snapshot_dir,
                artifact_type=args.artifact_type,
                publisher=args.publisher,
            )
            print(
                f"[SUCCESS] Acquired source {source.source_id} -> {source.local_path} (SHA: {source.sha256})"
            )
            return 0

        elif args.subcommand == "ingest-local":
            attestation_bytes = _read_local_file(
                args.attestation, MAX_ATTESTATION_BYTES, "Attestation file"
            )
            attestation = ManualAttestation.model_validate_json(attestation_bytes)

            source = ingest_local(
                file_path=args.file,
                attestation=attestation,
                document_id=args.document_id,
                language=args.lang,
                publisher=args.publisher,
                is_authoritative=args.authoritative,
                snapshot_dir=args.snapshot_dir,
                artifact_type=args.artifact_type,
            )
            print(
                f"[SUCCESS] Ingested source {source.source_id} -> {source.local_path} (Tier: {source.authenticity_tier.value})"
            )
            return 0

    except Exception as exc:
        print(f"[ERROR] Acquisition command failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
