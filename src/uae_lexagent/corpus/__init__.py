"""Corpus acquisition, extraction, normalization, and release management."""

from .acquisition import (
    UNRETRIEVED_UPSTREAM_PDF_GATE,
    AcquisitionError,
    SecurityPolicyViolationError,
    SnapshotIntegrityError,
    acquire_from_url,
    compute_snapshot_path,
    ingest_local,
    is_approved_government_url,
    store_snapshot,
)

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
