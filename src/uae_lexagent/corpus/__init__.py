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
from .extractor import (
    EXTRACTION_FIDELITY_THRESHOLD,
    GAZETTE_712_SOURCE_BLOCKER,
    ExtractionBundle,
    ExtractionError,
    extract_from_snapshot,
    extract_from_staged_source,
    extract_html_bytes,
    extract_pdf_bytes,
    persist_extraction,
    report_gazette_712_blocker,
)
from .normalizer import (
    DualTrackText,
    create_dual_track_text,
    normalize_arabic_search,
)

__all__ = [
    "EXTRACTION_FIDELITY_THRESHOLD",
    "GAZETTE_712_SOURCE_BLOCKER",
    "UNRETRIEVED_UPSTREAM_PDF_GATE",
    "AcquisitionError",
    "DualTrackText",
    "ExtractionBundle",
    "ExtractionError",
    "SecurityPolicyViolationError",
    "SnapshotIntegrityError",
    "acquire_from_url",
    "compute_snapshot_path",
    "create_dual_track_text",
    "extract_from_snapshot",
    "extract_from_staged_source",
    "extract_html_bytes",
    "extract_pdf_bytes",
    "ingest_local",
    "is_approved_government_url",
    "normalize_arabic_search",
    "persist_extraction",
    "report_gazette_712_blocker",
    "store_snapshot",
]
