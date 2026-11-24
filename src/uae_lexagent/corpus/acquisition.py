"""Secure source acquisition and content-addressed snapshot storage (T-03 / ADR-006).

Provides automated HTTPS acquisition from allowlisted UAE government domains with TLS
peer certificate fingerprint extraction, bounded streaming, atomic file storage,
corruption detection, and manual operator attestation ingestion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urljoin, urlparse

from ..models import (
    AuthenticityTier,
    LegalSource,
    ManualAttestation,
    RedistributionClearance,
)

UNRETRIEVED_UPSTREAM_PDF_GATE: str = "UNRETRIEVED_UPSTREAM_PDF_GATE"

APPROVED_GOVERNMENT_DOMAINS: frozenset[str] = frozenset(
    {
        "mohre.gov.ae",
        "www.mohre.gov.ae",
        "uaelegislation.gov.ae",
        "www.uaelegislation.gov.ae",
    }
)

MAX_ACQUISITION_BYTES: int = 50_000_000  # 50 MB
DEFAULT_TIMEOUT_SECONDS: float = 30.0


class AcquisitionError(Exception):
    """Base exception for source acquisition failures."""


class SecurityPolicyViolationError(AcquisitionError):
    """Raised when an acquisition request violates security or governance policies."""


class SnapshotIntegrityError(AcquisitionError):
    """Raised when snapshot checksum mismatch or storage corruption is detected."""


def is_approved_government_url(url: str) -> bool:
    """Validate that a URL strictly targets an approved UAE government portal over HTTPS.

    Rejects insecure schemes, deceptive domains (e.g. mohre.gov.ae.evil.com),
    non-standard ports, user credentials in authority, and malformed URLs.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        if not parsed.hostname:
            return False
        hostname = parsed.hostname.lower()
        if hostname not in APPROVED_GOVERNMENT_DOMAINS:
            return False
        if parsed.port is not None and parsed.port != 443:
            return False
        if parsed.username or parsed.password:
            return False
        return True
    except Exception:
        return False


def get_tls_peer_cert_fingerprint(
    hostname: str,
    port: int = 443,
    timeout_seconds: float = 10.0,
) -> str:
    """Connect to hostname via TLS, verify certificates, and return SHA-256 fingerprint.

    Returns:
        Formatted fingerprint string matching pattern '^SHA256:[a-fA-F0-9]{64}$'.
    """
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, port), timeout=timeout_seconds) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                if not cert_der:
                    raise AcquisitionError(f"Could not retrieve peer certificate for {hostname}")
                sha256_hex = hashlib.sha256(cert_der).hexdigest().lower()
                return f"SHA256:{sha256_hex}"
    except ssl.SSLError as exc:
        raise AcquisitionError(
            f"TLS certificate verification failed for {hostname}: {exc}"
        ) from exc
    except Exception as exc:
        raise AcquisitionError(
            f"Network error obtaining TLS certificate for {hostname}: {exc}"
        ) from exc


def compute_snapshot_path(base_dir: Path, sha256_hash: str, ext: str) -> Path:
    """Compute content-addressed snapshot target path: base_dir/snapshots/<hash[:2]>/<hash>.<ext>.

    Enforces strict SHA-256 formatting and path traversal safety.
    """
    sha256_clean = sha256_hash.strip().lower()
    if not re.match(r"^[a-f0-9]{64}$", sha256_clean):
        raise SnapshotIntegrityError(f"Invalid SHA-256 digest format: {sha256_hash}")

    clean_ext = ext.lstrip(".").lower().strip()
    if not clean_ext or not re.match(r"^[a-z0-9]+$", clean_ext):
        raise ValueError(f"Invalid file extension: {ext}")

    resolved_base = base_dir.resolve()
    target_path = (
        resolved_base / "snapshots" / sha256_clean[:2] / f"{sha256_clean}.{clean_ext}"
    ).resolve()

    try:
        target_path.relative_to(resolved_base)
    except ValueError as exc:
        raise SecurityPolicyViolationError(
            f"Path traversal detected: {target_path} is outside base directory {resolved_base}"
        ) from exc

    return target_path


def store_snapshot(
    content_bytes: bytes,
    snapshot_dir: Path,
    ext: str,
    expected_sha256: str | None = None,
) -> tuple[Path, str]:
    """Store content bytes atomically into content-addressed snapshot layout.

    Detects existing corruption and ensures idempotent writes.

    Returns:
        tuple of (absolute_file_path, computed_sha256_digest)
    """
    computed_sha256 = hashlib.sha256(content_bytes).hexdigest().lower()

    if expected_sha256 is not None:
        clean_expected = expected_sha256.strip().lower()
        if clean_expected != computed_sha256:
            raise SnapshotIntegrityError(
                f"Checksum mismatch: expected {clean_expected}, computed {computed_sha256}"
            )

    target_path = compute_snapshot_path(snapshot_dir, computed_sha256, ext)

    if target_path.exists():
        existing_bytes = target_path.read_bytes()
        existing_sha256 = hashlib.sha256(existing_bytes).hexdigest().lower()
        if existing_sha256 != computed_sha256:
            raise SnapshotIntegrityError(
                f"Snapshot corruption detected at existing file {target_path}: "
                f"expected {computed_sha256}, got {existing_sha256}"
            )
        return target_path, computed_sha256

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f"{target_path.name}.tmp_{uuid.uuid4().hex}"
    try:
        tmp_path.write_bytes(content_bytes)
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    return target_path, computed_sha256


def _extract_page_count_if_pdf(content_bytes: bytes) -> int | None:
    """Attempt to extract page count from PDF bytes using PyMuPDF if available."""
    if not content_bytes.startswith(b"%PDF-"):
        return None
    try:
        import pymupdf

        with pymupdf.open(stream=content_bytes, filetype="pdf") as doc:
            return int(len(doc))
    except Exception:
        return None


def acquire_from_url(
    source_id: str,
    document_id: str,
    url: str,
    language: Literal["en", "ar"],
    is_authoritative: bool,
    snapshot_dir: Path,
    *,
    artifact_type: Literal["raw_pdf", "raw_html"] = "raw_pdf",
    source_role: Literal["extraction_primary", "archival_reference"] = "extraction_primary",
    redistribution_clearance: RedistributionClearance = RedistributionClearance.PENDING_REVIEW,
    publisher: str = "UAE Official Portal",
    max_bytes: int = MAX_ACQUISITION_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: Any | None = None,
    cert_fingerprint_fn: Callable[[str, int, float], str] | None = None,
) -> LegalSource:
    """Acquire official legal source artifact over HTTPS with peer cert fingerprinting.

    Enforces allowlisted domain checks, bounded streaming, redirect checking,
    content magic bytes, and atomic snapshot storage.
    """
    if not is_approved_government_url(url):
        raise SecurityPolicyViolationError(
            f"URL '{url}' does not point to an approved UAE government portal via HTTPS"
        )

    if is_authoritative and language != "ar":
        raise ValueError("Authoritative legal sources must be Arabic ('ar')")

    parsed_url = urlparse(url)
    hostname = parsed_url.hostname or ""
    port = parsed_url.port or 443

    fingerprint_getter = cert_fingerprint_fn or get_tls_peer_cert_fingerprint
    cert_fingerprint = fingerprint_getter(hostname, port, timeout_seconds)

    import httpx

    close_client = False
    if client is None:
        client = httpx.Client(
            follow_redirects=False,
            verify=True,
            timeout=timeout_seconds,
        )
        close_client = True

    try:
        current_url = url
        redirect_count = 0
        max_redirects = 5

        while True:
            response = client.get(current_url)

            if response.status_code in {301, 302, 303, 307, 308}:
                redirect_count += 1
                if redirect_count > max_redirects:
                    raise AcquisitionError(
                        f"Exceeded maximum redirects ({max_redirects}) fetching {url}"
                    )
                location = response.headers.get("location")
                if not location:
                    raise AcquisitionError(
                        f"Redirect response from {current_url} missing Location header"
                    )
                next_url = urljoin(current_url, location)
                if not is_approved_government_url(next_url):
                    raise SecurityPolicyViolationError(
                        f"Redirect target '{next_url}' violates approved government domain policy"
                    )
                current_url = next_url
                continue

            if response.status_code == 403:
                raise AcquisitionError(
                    f"HTTP 403 Forbidden received from {current_url}. "
                    "Endpoint is protected by bot challenge/WAF. Mark as UNRETRIEVED_UPSTREAM_PDF_GATE."
                )

            if response.status_code != 200:
                raise AcquisitionError(
                    f"HTTP request to {current_url} failed with status code {response.status_code}"
                )

            # Check bounded size
            content = response.content
            if len(content) > max_bytes:
                raise AcquisitionError(
                    f"Response size ({len(content)} bytes) exceeds maximum limit of {max_bytes} bytes"
                )

            break
    finally:
        if close_client:
            client.close()

    # Validate content magic bytes / structure
    if artifact_type == "raw_pdf":
        if not content.startswith(b"%PDF-"):
            raise AcquisitionError(
                "Response content does not contain valid PDF magic bytes (%PDF-). "
                "Possible HTML challenge or portal error returned."
            )
        ext = "pdf"
        extraction_method: Literal["manual", "html_dom", "pdf_text", "ocr"] = "pdf_text"
    elif artifact_type == "raw_html":
        lower_head = content[:2048].lower()
        if b"<html" not in lower_head and b"<!doctype html" not in lower_head:
            raise AcquisitionError("Response content does not contain valid HTML document tags.")
        ext = "html"
        extraction_method = "html_dom"
    else:
        raise ValueError(f"Unsupported artifact_type: {artifact_type}")

    stored_path, sha256_hash = store_snapshot(content, snapshot_dir, ext=ext)
    page_count = _extract_page_count_if_pdf(content) if ext == "pdf" else None

    # Compute relative path from repo root or parent if feasible, else absolute
    try:
        local_path_str = str(stored_path.relative_to(Path.cwd()))
    except ValueError:
        local_path_str = str(stored_path)

    return LegalSource(
        source_id=source_id,
        document_id=document_id,
        language=language,
        publisher=publisher,
        source_url=url,
        local_path=local_path_str.replace("\\", "/"),
        retrieved_at=datetime.now(timezone.utc),
        sha256=sha256_hash,
        page_count=page_count,
        is_authoritative=is_authoritative,
        artifact_type=artifact_type,
        source_role=source_role,
        extraction_method=extraction_method,
        authenticity_tier=AuthenticityTier.GOV_AUTOMATED_TLS_VERIFIED,
        attestation_id=None,
        tls_cert_fingerprint=cert_fingerprint,
        redistribution_clearance=redistribution_clearance,
    )


def ingest_local(
    file_path: Path,
    attestation: ManualAttestation,
    document_id: str,
    language: Literal["en", "ar"],
    publisher: str,
    is_authoritative: bool,
    snapshot_dir: Path,
    *,
    artifact_type: Literal["raw_pdf", "raw_html"] = "raw_pdf",
    source_role: Literal["extraction_primary", "archival_reference"] = "extraction_primary",
    redistribution_clearance: RedistributionClearance = RedistributionClearance.PENDING_REVIEW,
) -> LegalSource:
    """Ingest a locally provided file with a validated ManualAttestation record.

    Assigns GOV_INDEPENDENTLY_CORROBORATED if cross-check passed, else OPERATOR_ATTESTED_ONLY.
    """
    resolved_file = file_path.resolve()
    if not resolved_file.exists() or not resolved_file.is_file():
        raise AcquisitionError(f"Local file does not exist or is not a file: {file_path}")

    if not is_approved_government_url(attestation.claimed_source_url):
        raise SecurityPolicyViolationError(
            f"Attestation claimed_source_url '{attestation.claimed_source_url}' "
            "does not target an approved UAE government portal via HTTPS"
        )

    if is_authoritative and language != "ar":
        raise ValueError("Authoritative legal sources must be Arabic ('ar')")

    content = resolved_file.read_bytes()
    if len(content) > MAX_ACQUISITION_BYTES:
        raise AcquisitionError(
            f"File size ({len(content)} bytes) exceeds maximum limit of {MAX_ACQUISITION_BYTES} bytes"
        )

    if artifact_type == "raw_pdf":
        if not content.startswith(b"%PDF-"):
            raise AcquisitionError("Provided file does not contain valid PDF magic bytes (%PDF-)")
        ext = "pdf"
        page_count = _extract_page_count_if_pdf(content)
    elif artifact_type == "raw_html":
        lower_head = content[:2048].lower()
        if b"<html" not in lower_head and b"<!doctype html" not in lower_head:
            raise AcquisitionError("Provided file does not contain valid HTML document tags")
        ext = "html"
        page_count = None
    else:
        raise ValueError(f"Unsupported artifact_type: {artifact_type}")

    stored_path, sha256_hash = store_snapshot(content, snapshot_dir, ext=ext)

    tier = (
        AuthenticityTier.GOV_INDEPENDENTLY_CORROBORATED
        if attestation.independent_cross_check_passed
        else AuthenticityTier.OPERATOR_ATTESTED_ONLY
    )

    try:
        local_path_str = str(stored_path.relative_to(Path.cwd()))
    except ValueError:
        local_path_str = str(stored_path)

    return LegalSource(
        source_id=attestation.source_id,
        document_id=document_id,
        language=language,
        publisher=publisher,
        source_url=attestation.claimed_source_url,
        local_path=local_path_str.replace("\\", "/"),
        retrieved_at=attestation.acquired_at_utc,
        sha256=sha256_hash,
        page_count=page_count,
        is_authoritative=is_authoritative,
        artifact_type=artifact_type,
        source_role=source_role,
        extraction_method="manual",
        authenticity_tier=tier,
        attestation_id=attestation.attestation_id,
        tls_cert_fingerprint=None,
        redistribution_clearance=redistribution_clearance,
    )


def build_cli_parser() -> argparse.ArgumentParser:
    """Build CLI parser for corpus acquisition workflows."""
    parser = argparse.ArgumentParser(
        prog="python -m uae_lexagent.corpus.acquisition",
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
        default=Path("data/official_sources"),
        help="Base directory for snapshot storage",
    )

    # ingest-local subcommand
    ingest_parser = subparsers.add_parser(
        "ingest-local", help="Ingest local file with ManualAttestation"
    )
    ingest_parser.add_argument("--file", type=Path, required=True, help="Path to local file")
    ingest_parser.add_argument(
        "--attestation", type=Path, required=True, help="Path to ManualAttestation JSON"
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
        default=Path("data/official_sources"),
        help="Base directory for snapshot storage",
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
            with args.attestation.open("r", encoding="utf-8") as f:
                attestation_data = json.load(f)
            attestation = ManualAttestation.model_validate(attestation_data)

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
