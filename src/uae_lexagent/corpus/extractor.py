"""Deterministic HTML-first statutory extraction, PDF fallback, CMap gate, and review queue (T-04).

Lifecycle & Architectural Boundaries:
- Reads immutable staged snapshots with single-open exact SHA-256 verification (no TOCTOU reopen).
- HTML DOM extraction is primary (`ADR-002`); PDF extraction via PyMuPDF is secondary with
  conservative CMap corruption detection and mandatory human-review quarantine (`RV-03`).
- Produces deterministic intermediate `ExtractionBundle` artifacts stored in private staging
  (`data/.acquisition/extracted/`) without mutating `data/official_sources/` or auto-promoting
  any `publication_state` to `VERIFIED_PUBLISHABLE`.
- Enforces strict isolation between consolidated texts and historical Gazette 712 sources.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..models import AuthenticityTier, LegalSource, RedistributionClearance
from .acquisition import SourceRole
from .normalizer import ARABIC_NORMALIZER_VERSION, normalize_arabic_search
from .storage import (
    AcquisitionError,
    SecurityPolicyViolationError,
    SnapshotIntegrityError,
    _check_path,
    _manifest_lock,
    _private_root,
    _read_records,
    _staged_bytes,
    _sync_directory,
    compute_snapshot_path,
)

EXTRACTOR_VERSION = "1.0.0"
EXTRACTION_FIDELITY_THRESHOLD = 0.95
MAX_EXTRACTION_BYTES = 50_000_000  # 50 MB
MAX_PDF_PAGES = 500
MAX_PDF_EXTRACTED_TEXT_BYTES = 20_000_000  # 20 MB guard against PDF decompression bombs
MAX_DOM_DEPTH = 128
MAX_DOM_NODES = 100_000
GAZETTE_712_SOURCE_BLOCKER = "PENDING_GAZETTE_712_ORIGINAL_2021_ACQUISITION"

_ARTICLE_CONTAINERS_SELECTOR = ("section", "article", "div")
_STRIP_TAGS = frozenset(
    {"script", "style", "noscript", "template", "iframe", "svg", "object", "embed"}
)
_CHROME_TAGS = frozenset({"nav", "footer", "aside"})
_CHROME_MARKERS_RE = re.compile(
    r"(?:^|[\s_-])(?:cookie|banner|breadcrumb|sidebar|nav|navbar|menu|toolbar|social|share|pagination|modal|skip-link)(?:$|[\s_-])",
    re.I,
)
_CHALLENGE_HTML_RE = re.compile(
    rb"cf-chl-|/cdn-cgi/challenge-platform/|<title>\s*(?:just a moment|access denied|attention required)",
    re.I,
)

_ARTICLE_HEADING_RE = re.compile(
    r"^\s*(?:المادة|مادة|Article)\s*\(?\s*([0-9٠-٩]+(?:\s*(?:مكرر|bis|ter))?)\s*\)?\s*(?:[—\-:–]\s*(.+))?$",
    re.I,
)
_LEADING_CLAUSE_PREFIX_RE = re.compile(
    r"^\s*(?:\(([0-9٠-٩]+|[أ-يa-zA-Z])\)|([0-9٠-٩]+|[أ-يa-zA-Z])[.\-)])\s+(.+)$",
    re.S,
)
_ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class ExtractionError(AcquisitionError):
    """Raised when extraction input is malformed, truncated, or unsafe to parse."""


class ExtractionDiagnostic(BaseModel):
    """Structured diagnostic event separating direct observation from heuristic suspicion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    severity: Literal["warning", "error", "quarantine"]
    detection_basis: Literal["direct_observation", "heuristic_suspicion", "structural_check"]
    message: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    source_locator: str | None = None


class ExtractedClause(BaseModel):
    """Deterministic clause/subclause extracted within a statutory article."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_number: str | None = None
    subclause_number: str | None = None
    text: str = Field(min_length=1)
    normalized_text: str
    source_locator: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)


class ExtractedArticle(BaseModel):
    """Article-level intermediate extraction record (pre-versioning / pre-publication)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    article_number: str = Field(min_length=1)
    article_title: str | None = None
    text: str
    normalized_text: str
    clauses: tuple[ExtractedClause, ...] = ()
    source_locator: str = Field(min_length=1)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    extraction_method: Literal["html_dom", "pdf_text", "ocr"]
    cmap_corruption_detected: bool = False
    cmap_suspicion_detected: bool = False
    extraction_fidelity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    human_verification_status: Literal["unreviewed"] = "unreviewed"
    review_required: bool = False
    review_reasons: tuple[str, ...] = ()
    publication_eligibility: Literal["UNREVIEWED_STAGED", "QUARANTINED_REVIEW_REQUIRED"] = (
        "UNREVIEWED_STAGED"
    )


class PageExtraction(BaseModel):
    """Per-page PDF extraction boundary and diagnostics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    raw_text: str
    char_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    drawing_count: int = Field(ge=0)
    reading_order_ambiguous: bool = False
    cmap_corruption_detected: bool = False
    cmap_suspicion_detected: bool = False
    ocr_required: bool = False
    diagnostics: tuple[ExtractionDiagnostic, ...] = ()


class ReviewQueueItem(BaseModel):
    """Deterministic human-review quarantine record for RV-03 / S-06."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    page_number: int | None = Field(default=None, ge=1)
    segment_id: str | None = None
    diagnostic_codes: tuple[str, ...] = Field(min_length=1)
    diagnostic_summary: str = Field(min_length=1)
    extraction_method: Literal["html_dom", "pdf_text", "ocr"]
    review_status: Literal["pending_human_review"] = "pending_human_review"
    source_locator: str = Field(min_length=1)
    quarantined_publication_state: Literal["BLOCKED_LEGAL_TOPIC"] = "BLOCKED_LEGAL_TOPIC"


class ExtractionBundle(BaseModel):
    """Deterministic intermediate extraction artifact stored in private staging."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "5.2.2-intermediate.1"
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    language: Literal["ar", "en"]
    is_authoritative: bool
    authenticity_tier: AuthenticityTier
    redistribution_clearance: RedistributionClearance
    artifact_type: Literal["raw_html", "raw_pdf"]
    extraction_method: Literal["html_dom", "pdf_text", "ocr"]
    source_role: SourceRole
    is_historical_unamended_source: bool = False
    historical_source_edition: str | None = None
    is_synthetic_fixture: bool = False
    title: str | None = None
    headings: tuple[str, ...] = ()
    articles: tuple[ExtractedArticle, ...] = ()
    pages: tuple[PageExtraction, ...] = ()
    diagnostics: tuple[ExtractionDiagnostic, ...] = ()
    review_queue: tuple[ReviewQueueItem, ...] = ()
    cmap_corruption_detected: bool = False
    cmap_suspicion_detected: bool = False
    ocr_required: bool = False
    requires_human_review: bool = False
    extraction_fidelity_score_summary: float | None = Field(default=None, ge=0.0, le=1.0)
    canonical_output_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class HistoricalBlockerRecord(BaseModel):
    """Explicit unresolved blocker record for Gazette No. 712 archival acquisition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocker_code: str = GAZETTE_712_SOURCE_BLOCKER
    instrument_id: str = "FDL_33_2021"
    affected_articles: tuple[str, ...] = ("8(3)", "54", "60")
    target_gazette: str = "Official Gazette No. 712 (Supplement) dated 2021-09-26"
    acquired: bool = False
    derived_from_consolidated_forbidden: bool = True
    reason: str = (
        "Original 2021 unamended text of Articles 8(3), 54, and 60 from Gazette 712 has not been "
        "independently acquired and cannot be reverse-derived from consolidated portal text."
    )


def report_gazette_712_blocker() -> HistoricalBlockerRecord:
    """Return explicit unresolved historical acquisition status for Gazette 712."""
    return HistoricalBlockerRecord()


def get_toolchain_metadata() -> dict[str, str]:
    """Return operational dependency versions separate from deterministic canonical hashes."""
    import lxml
    import pymupdf

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "normalizer_version": ARABIC_NORMALIZER_VERSION,
        "lxml_version": str(getattr(lxml, "__version__", "unknown")),
        "pymupdf_version": str(getattr(pymupdf, "__version__", "unknown")),
    }


def _compute_bundle_canonical_hash(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("canonical_output_sha256", None)
    import json

    serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _deterministic_review_id(
    source_id: str, snapshot_sha256: str, codes: tuple[str, ...], locator: str
) -> str:
    seed = f"{source_id}|{snapshot_sha256}|{','.join(sorted(codes))}|{locator}"
    return "rev-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _normalize_article_num(raw_num: str) -> str:
    return re.sub(r"\s+", " ", raw_num.translate(_ARABIC_DIGITS_TRANS)).strip()


def _clean_dom_text(raw: str) -> str:
    """Reconstruct normalized readable text from decoded DOM text nodes.

    Explicit reconstruction contract:
    - Converts HTML `&nbsp;` (`\\u00a0`) and inline space/tab runs into single spaces.
    - Normalizes line endings (`\\r\\n`/`\\r` -> `\\n`).
    - Preserves Arabic diacritics, punctuation, digits, and exact wording verbatim
      (never performs search normalization here); result is DOM reconstruction, NOT raw HTML bytes.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [
        re.sub(r"[ \t\u2000-\u200a\u202f\u205f\u3000]+", " ", ln).strip() for ln in text.split("\n")
    ]
    return "\n".join(ln for ln in lines if ln).strip()


def _validate_synthetic_boundary(source_id: str, document_id: str, allow_synthetic: bool) -> bool:
    is_synthetic = any(
        val.upper().startswith("SYNTHETIC_TEST_ONLY") for val in (source_id, document_id)
    )
    if is_synthetic and not allow_synthetic:
        raise SecurityPolicyViolationError(
            "Synthetic test fixtures cannot be extracted without explicit allow_synthetic=True"
        )
    return is_synthetic


def _read_snapshot_single_open(path: Path, *, expected_sha256: str, max_bytes: int) -> bytes:
    """Open target snapshot once, enforce size & regular-file checks, and verify exact SHA-256."""
    _check_path(path)
    try:
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ExtractionError(f"Snapshot is not a regular file: {path}")
            if info.st_size > max_bytes:
                raise ExtractionError(
                    f"Snapshot exceeds maximum extraction limit of {max_bytes} bytes"
                )
            content = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ExtractionError(f"Could not read snapshot file {path}: {exc}") from exc

    if len(content) > max_bytes:
        raise ExtractionError(f"Snapshot exceeds maximum extraction limit of {max_bytes} bytes")

    actual = hashlib.sha256(content).hexdigest().lower()
    if actual != expected_sha256.lower():
        raise SnapshotIntegrityError(
            f"Snapshot SHA-256 checksum mismatch: expected {expected_sha256}, computed {actual}"
        )
    return content


def _enforce_dom_limits(root: Any, *, max_depth: int, max_nodes: int) -> None:
    """Iterative DOM tree walk verifying depth and total node count without recursion."""
    stack: list[tuple[Any, int]] = [(root, 1)]
    node_count = 0
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > max_nodes:
            raise ExtractionError(f"HTML DOM exceeds maximum node count limit ({max_nodes})")
        if depth > max_depth:
            raise ExtractionError(f"HTML DOM exceeds maximum nesting depth limit ({max_depth})")
        for child in node:
            stack.append((child, depth + 1))


def _is_chrome_element(elem: Any) -> bool:
    tag = elem.tag if isinstance(elem.tag, str) else ""
    if tag.lower() in _CHROME_TAGS:
        return True
    role = (elem.get("role") or "").strip().lower()
    if role in {"navigation", "banner", "contentinfo", "complementary", "dialog"}:
        return True
    cls_and_id = f"{elem.get('class') or ''} {elem.get('id') or ''}"
    # Never treat explicit law-article containers as chrome
    if "law-article" in cls_and_id or elem.get("data-article") is not None:
        return False
    return bool(_CHROME_MARKERS_RE.search(cls_and_id))


def _strip_chrome_iteratively(root: Any) -> None:
    """Remove scripts, styles, navigation, cookie banners, and portal chrome deterministically."""
    stack: list[Any] = [root]
    while stack:
        parent = stack.pop()
        children = list(parent)
        for child in children:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag.lower() in _STRIP_TAGS or _is_chrome_element(child):
                parent.remove(child)
            else:
                stack.append(child)


def _node_text(elem: Any) -> str:
    return _clean_dom_text("".join(elem.itertext()))


def _extract_clauses_from_article_elements(
    elements: list[Any], article_locator: str
) -> tuple[list[ExtractedClause], list[ExtractionDiagnostic], list[str]]:
    """Extract deterministic clauses, nested subclauses, and full body text parts."""
    clauses: list[ExtractedClause] = []
    diagnostics: list[ExtractionDiagnostic] = []
    body_parts: list[str] = []

    list_counter = 0
    for elem in elements:
        tag = elem.tag.lower() if isinstance(elem.tag, str) else ""
        if tag in {"h1", "h2", "h3", "h4"} or (
            isinstance(elem.get("class"), str)
            and any(c in elem.get("class", "") for c in ("article-title", "article-number"))
        ):
            continue

        if tag in {"ol", "ul"}:
            if tag == "ul":
                diagnostics.append(
                    ExtractionDiagnostic(
                        code="AMBIGUOUS_ARTICLE_STRUCTURE",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message="Unordered <ul> clause list encountered; ordering ambiguity requires review.",
                        source_locator=f"{article_locator}/ul",
                    )
                )
            li_items = [
                child for child in elem if isinstance(child.tag, str) and child.tag.lower() == "li"
            ]
            for idx, li in enumerate(li_items, start=1):
                list_counter += 1
                clause_num = str(li.get("data-clause") or idx)
                # Direct text of <li> excluding nested <ol>/<ul>
                nested_lists = [
                    c for c in li if isinstance(c.tag, str) and c.tag.lower() in {"ol", "ul"}
                ]
                lead_fragments: list[str] = []
                if li.text:
                    lead_fragments.append(li.text)
                for c in li:
                    ctag = c.tag.lower() if isinstance(c.tag, str) else ""
                    if ctag not in {"ol", "ul"}:
                        lead_fragments.append("".join(c.itertext()))
                    if c.tail:
                        lead_fragments.append(c.tail)
                lead_text = _clean_dom_text(" ".join(lead_fragments))
                clause_loc = f"{article_locator}/{tag}/li[{idx}]"
                if lead_text:
                    clauses.append(
                        ExtractedClause(
                            clause_number=clause_num,
                            subclause_number=None,
                            text=lead_text,
                            normalized_text=normalize_arabic_search(lead_text),
                            source_locator=clause_loc,
                        )
                    )
                    body_parts.append(lead_text)

                for n_idx, nested in enumerate(nested_lists, start=1):
                    nested_tag = nested.tag.lower()
                    if nested_tag == "ul":
                        diagnostics.append(
                            ExtractionDiagnostic(
                                code="AMBIGUOUS_ARTICLE_STRUCTURE",
                                severity="quarantine",
                                detection_basis="structural_check",
                                message="Unordered nested <ul> subclauses require review.",
                                source_locator=f"{clause_loc}/ul[{n_idx}]",
                            )
                        )
                    sub_items = [
                        sc for sc in nested if isinstance(sc.tag, str) and sc.tag.lower() == "li"
                    ]
                    for s_idx, sub_li in enumerate(sub_items, start=1):
                        # Reject depth > 2 nested lists as ambiguous
                        if any(
                            isinstance(dc.tag, str) and dc.tag.lower() in {"ol", "ul"}
                            for dc in sub_li.iter()
                            if dc is not sub_li
                        ):
                            diagnostics.append(
                                ExtractionDiagnostic(
                                    code="AMBIGUOUS_ARTICLE_STRUCTURE",
                                    severity="quarantine",
                                    detection_basis="structural_check",
                                    message="Deeply nested clause (>2 levels) requires human review.",
                                    source_locator=f"{clause_loc}/{nested_tag}[{n_idx}]/li[{s_idx}]",
                                )
                            )
                        sub_text = _node_text(sub_li)
                        sub_num = str(sub_li.get("data-subclause") or s_idx)
                        m = _LEADING_CLAUSE_PREFIX_RE.match(sub_text)
                        if m:
                            marker = m.group(1) or m.group(2)
                            if marker:
                                sub_num = marker.translate(_ARABIC_DIGITS_TRANS)
                        if sub_text:
                            clauses.append(
                                ExtractedClause(
                                    clause_number=clause_num,
                                    subclause_number=sub_num,
                                    text=sub_text,
                                    normalized_text=normalize_arabic_search(sub_text),
                                    source_locator=f"{clause_loc}/{nested_tag}[{n_idx}]/li[{s_idx}]",
                                )
                            )
                            body_parts.append(sub_text)
        elif tag in {"p", "div"}:
            p_text = _node_text(elem)
            if p_text:
                m = _LEADING_CLAUSE_PREFIX_RE.match(p_text)
                clause_num = None
                if m:
                    marker = m.group(1) or m.group(2)
                    if marker:
                        clause_num = marker.translate(_ARABIC_DIGITS_TRANS)
                clauses.append(
                    ExtractedClause(
                        clause_number=clause_num,
                        subclause_number=None,
                        text=p_text,
                        normalized_text=normalize_arabic_search(p_text),
                        source_locator=f"{article_locator}/{tag}",
                    )
                )
                body_parts.append(p_text)

    return clauses, diagnostics, body_parts


def extract_html_bytes(
    content: bytes,
    *,
    source_id: str,
    document_id: str,
    language: Literal["ar", "en"],
    is_authoritative: bool,
    authenticity_tier: AuthenticityTier,
    redistribution_clearance: RedistributionClearance,
    source_role: SourceRole = "extraction_primary",
    historical_edition: str | None = None,
    allow_synthetic: bool = False,
    max_bytes: int = MAX_EXTRACTION_BYTES,
) -> ExtractionBundle:
    """Deterministically extract statutory structure from raw UTF-8 HTML snapshot bytes."""
    from lxml import etree

    is_synthetic = _validate_synthetic_boundary(source_id, document_id, allow_synthetic)
    if is_authoritative and language != "ar":
        raise ValueError("Authoritative sources must be Arabic ('ar')")
    if not content:
        raise ExtractionError("Empty HTML content")
    if len(content) > max_bytes:
        raise ExtractionError(f"HTML size exceeds maximum extraction limit of {max_bytes} bytes")
    if b"\x00" in content:
        raise ExtractionError("HTML content contains unexpected NUL bytes")
    if _CHALLENGE_HTML_RE.search(content):
        raise ExtractionError("HTML challenge or portal access error page cannot be extracted")

    snapshot_sha256 = hashlib.sha256(content).hexdigest().lower()
    try:
        text_html = content.removeprefix(b"\xef\xbb\xbf").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError(f"HTML snapshot is not valid UTF-8: {exc}") from exc

    parser = etree.HTMLParser(no_network=True, collect_ids=False, huge_tree=False)
    try:
        root = etree.HTML(text_html, parser)
    except etree.LxmlError as exc:
        raise ExtractionError(f"Malformed HTML document: {exc}") from exc

    if root is None or root.tag != "html" or root.find("body") is None:
        raise ExtractionError("Malformed HTML document missing <html> or <body>")

    _enforce_dom_limits(root, max_depth=MAX_DOM_DEPTH, max_nodes=MAX_DOM_NODES)

    title_el = root.find(".//title")
    title = _node_text(title_el) if title_el is not None else None
    body = root.find("body")
    _strip_chrome_iteratively(body)

    # Locate primary statutory container (Layout A/B root)
    primary_root = body.find(".//main")
    if primary_root is None:
        for candidate in body.iter(*_ARTICLE_CONTAINERS_SELECTOR):
            if candidate.get("id") == "legislation-content" or "legislation-body" in (
                candidate.get("class") or ""
            ):
                primary_root = candidate
                break
    if primary_root is None:
        primary_root = body

    headings = tuple(_node_text(h1) for h1 in primary_root.findall(".//h1") if _node_text(h1))
    if not title and headings:
        title = headings[0]

    # Layout A: explicit article container blocks (`section.law-article`, `article.law-article`, `[data-article]`)
    article_blocks: list[Any] = []
    for elem in primary_root.iter(*_ARTICLE_CONTAINERS_SELECTOR):
        cls = elem.get("class") or ""
        if (
            elem.get("data-article") is not None
            or "law-article" in cls.split()
            or "article-block" in cls.split()
            or elem.tag.lower() == "article"
        ):
            article_blocks.append(elem)

    diagnostics: list[ExtractionDiagnostic] = []
    raw_articles_data: list[dict[str, Any]] = []

    if article_blocks:
        # Detect substantive text or article headings sitting orphaned outside explicit containers
        article_block_set = set(article_blocks)
        for child in list(primary_root):
            if child in article_block_set:
                continue
            ctag = child.tag.lower() if isinstance(child.tag, str) else ""
            if ctag == "h1":
                continue
            # Check that none of child's descendants are an article block
            if any(desc in article_block_set for desc in child.iter()):
                continue
            orphan_text = _node_text(child)
            if orphan_text and (_ARTICLE_HEADING_RE.match(orphan_text) or len(orphan_text) >= 20):
                diagnostics.append(
                    ExtractionDiagnostic(
                        code="UNEXPECTED_ARTICLE_BOUNDARY",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message=(
                            "Orphaned statutory text or heading found outside article containers; "
                            "requires human boundary review."
                        ),
                        source_locator=f"orphan:{ctag}",
                    )
                )

        for idx, block in enumerate(article_blocks, start=1):
            locator = f"article[{idx}]"
            data_art = block.get("data-article")
            heading_el = None
            for htag in ("h2", "h3", "h4"):
                heading_el = block.find(f"./{htag}")
                if heading_el is not None:
                    break
            heading_text = _node_text(heading_el) if heading_el is not None else ""
            art_num: str | None = _normalize_article_num(data_art) if data_art else None
            art_title: str | None = None

            m = _ARTICLE_HEADING_RE.match(heading_text)
            if m:
                parsed_num = _normalize_article_num(m.group(1))
                if art_num is None:
                    art_num = parsed_num
                elif art_num != parsed_num:
                    diagnostics.append(
                        ExtractionDiagnostic(
                            code="AMBIGUOUS_ARTICLE_STRUCTURE",
                            severity="quarantine",
                            detection_basis="structural_check",
                            message=f"data-article '{art_num}' conflicts with heading '{parsed_num}'.",
                            source_locator=locator,
                        )
                    )
                if m.group(2):
                    art_title = _clean_dom_text(m.group(2))
            elif heading_text and art_num is None:
                diagnostics.append(
                    ExtractionDiagnostic(
                        code="AMBIGUOUS_ARTICLE_STRUCTURE",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message=f"Article container missing recognizable article number in heading '{heading_text}'.",
                        source_locator=locator,
                    )
                )

            if not art_num:
                # Never invent missing article numbers! Skip emitting unnumbered record and quarantine.
                diagnostics.append(
                    ExtractionDiagnostic(
                        code="MISSING_ARTICLE_IDENTIFIER",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message="Article block lacks verifiable article identifier; cannot invent number.",
                        source_locator=locator,
                    )
                )
                continue

            clauses, art_diags, body_parts = _extract_clauses_from_article_elements(
                list(block), f"{locator}[article={art_num}]"
            )
            full_text = "\n".join(body_parts).strip()
            if not full_text:
                art_diags.append(
                    ExtractionDiagnostic(
                        code="EMPTY_ARTICLE_BODY",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message=f"Article '{art_num}' has an empty body.",
                        source_locator=locator,
                    )
                )

            raw_articles_data.append(
                {
                    "article_number": art_num,
                    "article_title": art_title,
                    "text": full_text,
                    "clauses": tuple(clauses),
                    "source_locator": f"section[data-article='{art_num}']#{idx}",
                    "diags": art_diags,
                }
            )
    else:
        # Layout B: heading-delimited statutory sections (h2/h3/h4 headings followed by sibling <p>/<ol>)
        current_group: dict[str, Any] | None = None
        for idx, child in enumerate(list(primary_root), start=1):
            ctag = child.tag.lower() if isinstance(child.tag, str) else ""
            if ctag in {"h2", "h3", "h4"}:
                heading_text = _node_text(child)
                m = _ARTICLE_HEADING_RE.match(heading_text)
                if m:
                    if current_group is not None:
                        raw_articles_data.append(current_group)
                    art_num = _normalize_article_num(m.group(1))
                    art_title = _clean_dom_text(m.group(2)) if m.group(2) else None
                    current_group = {
                        "article_number": art_num,
                        "article_title": art_title,
                        "nodes": [],
                        "source_locator": f"{ctag}[article={art_num}]#{idx}",
                    }
                    continue
            if current_group is not None:
                current_group["nodes"].append(child)
        if current_group is not None:
            raw_articles_data.append(current_group)

        for grp in raw_articles_data:
            clauses, art_diags, body_parts = _extract_clauses_from_article_elements(
                grp.pop("nodes"), grp["source_locator"]
            )
            full_text = "\n".join(body_parts).strip()
            if not full_text:
                art_diags.append(
                    ExtractionDiagnostic(
                        code="EMPTY_ARTICLE_BODY",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message=f"Article '{grp['article_number']}' has an empty body.",
                        source_locator=grp["source_locator"],
                    )
                )
            grp["text"] = full_text
            grp["clauses"] = tuple(clauses)
            grp["diags"] = art_diags

    # Detect duplicate article numbers and out-of-order numeric boundaries (never merge silently!)
    counts: dict[str, int] = {}
    for item in raw_articles_data:
        counts[item["article_number"]] = counts.get(item["article_number"], 0) + 1

    prev_base_int: int | None = None
    for item in raw_articles_data:
        m_int = re.match(r"^([0-9]+)", item["article_number"])
        if m_int:
            cur_int = int(m_int.group(1))
            if prev_base_int is not None and cur_int < prev_base_int:
                diagnostics.append(
                    ExtractionDiagnostic(
                        code="UNEXPECTED_ARTICLE_BOUNDARY",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message=(
                            f"Out-of-order article sequence detected: Article '{item['article_number']}' "
                            f"follows Article '{prev_base_int}'."
                        ),
                        source_locator=item["source_locator"],
                    )
                )
            prev_base_int = cur_int

    articles: list[ExtractedArticle] = []
    review_queue: list[ReviewQueueItem] = []

    for item in raw_articles_data:
        art_num = item["article_number"]
        item_diags: list[ExtractionDiagnostic] = list(item["diags"])
        if counts[art_num] > 1:
            item_diags.append(
                ExtractionDiagnostic(
                    code="DUPLICATE_ARTICLE_ID",
                    severity="quarantine",
                    detection_basis="structural_check",
                    message=f"Duplicate article identifier '{art_num}' detected; never merged silently.",
                    source_locator=item["source_locator"],
                )
            )
        diagnostics.extend(item_diags)
        needs_review = any(d.severity == "quarantine" for d in item_diags)
        reasons = tuple(sorted({d.code for d in item_diags if d.severity == "quarantine"}))
        if needs_review:
            review_queue.append(
                ReviewQueueItem(
                    review_id=_deterministic_review_id(
                        source_id, snapshot_sha256, reasons, item["source_locator"]
                    ),
                    document_id=document_id,
                    source_id=source_id,
                    snapshot_sha256=snapshot_sha256,
                    segment_id=f"article:{art_num}",
                    diagnostic_codes=reasons,
                    diagnostic_summary="; ".join(
                        d.message for d in item_diags if d.severity == "quarantine"
                    ),
                    extraction_method="html_dom",
                    source_locator=item["source_locator"],
                )
            )

        articles.append(
            ExtractedArticle(
                article_number=art_num,
                article_title=item["article_title"],
                text=item["text"],
                normalized_text=normalize_arabic_search(item["text"]),
                clauses=item["clauses"],
                source_locator=item["source_locator"],
                extraction_method="html_dom",
                review_required=needs_review,
                review_reasons=reasons,
                publication_eligibility=(
                    "QUARANTINED_REVIEW_REQUIRED" if needs_review else "UNREVIEWED_STAGED"
                ),
            )
        )

    if not articles:
        has_body_text = bool(_node_text(primary_root))
        code = "UNSUPPORTED_HTML_LAYOUT" if has_body_text else "MISSING_STATUTORY_STRUCTURE"
        diag = ExtractionDiagnostic(
            code=code,
            severity="quarantine",
            detection_basis="structural_check",
            message="No recognizable statutory article containers or headings found in HTML DOM.",
            source_locator="html/body",
        )
        diagnostics.append(diag)
        review_queue.append(
            ReviewQueueItem(
                review_id=_deterministic_review_id(
                    source_id, snapshot_sha256, (code,), "html/body"
                ),
                document_id=document_id,
                source_id=source_id,
                snapshot_sha256=snapshot_sha256,
                diagnostic_codes=(code,),
                diagnostic_summary=diag.message,
                extraction_method="html_dom",
                source_locator="html/body",
            )
        )

    doc_q_diags = [d for d in diagnostics if d.severity == "quarantine"]
    queued_codes = {code for item in review_queue for code in item.diagnostic_codes}
    missing_doc_diags = [d for d in doc_q_diags if d.code not in queued_codes]
    if missing_doc_diags:
        doc_codes = tuple(sorted({d.code for d in missing_doc_diags}))
        review_queue.append(
            ReviewQueueItem(
                review_id=_deterministic_review_id(
                    source_id, snapshot_sha256, doc_codes, "html:document"
                ),
                document_id=document_id,
                source_id=source_id,
                snapshot_sha256=snapshot_sha256,
                diagnostic_codes=doc_codes,
                diagnostic_summary="; ".join(d.message for d in missing_doc_diags),
                extraction_method="html_dom",
                source_locator=missing_doc_diags[0].source_locator or "html:document",
            )
        )

    requires_review = bool(review_queue) or bool(doc_q_diags)
    is_hist = historical_edition == "gazette_712_2021_original"

    bundle_dict: dict[str, Any] = {
        "schema_version": "5.2.2-intermediate.1",
        "document_id": document_id,
        "source_id": source_id,
        "snapshot_sha256": snapshot_sha256,
        "language": language,
        "is_authoritative": is_authoritative,
        "authenticity_tier": authenticity_tier,
        "redistribution_clearance": redistribution_clearance,
        "artifact_type": "raw_html",
        "extraction_method": "html_dom",
        "source_role": source_role,
        "is_historical_unamended_source": is_hist,
        "historical_source_edition": historical_edition,
        "is_synthetic_fixture": is_synthetic,
        "title": title,
        "headings": headings,
        "articles": tuple(articles),
        "pages": (),
        "diagnostics": tuple(diagnostics),
        "review_queue": tuple(review_queue),
        "cmap_corruption_detected": False,
        "cmap_suspicion_detected": False,
        "ocr_required": False,
        "requires_human_review": requires_review,
        "extraction_fidelity_score_summary": None,
        "canonical_output_sha256": "0" * 64,
    }
    temp_bundle = ExtractionBundle(**bundle_dict)
    digest = _compute_bundle_canonical_hash(temp_bundle.model_dump(mode="json"))
    return temp_bundle.model_copy(update={"canonical_output_sha256": digest})


def analyze_pdf_page_cmap(
    page_number: int,
    raw_text: str,
    *,
    language: Literal["ar", "en"],
    image_count: int = 0,
    drawing_count: int = 0,
    reading_order_ambiguous: bool = False,
) -> tuple[bool, bool, bool, list[ExtractionDiagnostic]]:
    """Conservatively analyze extracted PDF page text for CMap corruption & OCR triggers.

    Distinguishes:
    - Direct observation (`detection_basis="direct_observation"`): replacement chars (U+FFFD),
      Private Use Area glyphs, NUL/control characters, or contextual Arabic Presentation Forms.
    - Heuristic suspicion (`detection_basis="heuristic_suspicion"`): empty text on image/vector pages,
      abnormal char distribution on Arabic pages, or ambiguous multi-column reading order.

    Note: Clean heuristic analysis never mathematically proves absence of subtle CMap swaps.
    """
    diagnostics: list[ExtractionDiagnostic] = []
    direct_corruption = False
    suspicion = False
    ocr_required = False
    locator = f"pdf:page={page_number}"

    # 1. Directly observed corruption markers
    if "\ufffd" in raw_text:
        direct_corruption = True
        diagnostics.append(
            ExtractionDiagnostic(
                code="CMAP_REPLACEMENT_CHAR",
                severity="quarantine",
                detection_basis="direct_observation",
                message=f"Unicode replacement character (U+FFFD) observed on page {page_number}.",
                page_number=page_number,
                source_locator=locator,
            )
        )

    if any(0xE000 <= ord(ch) <= 0xF8FF or 0xF0000 <= ord(ch) <= 0x10FFFF for ch in raw_text):
        direct_corruption = True
        diagnostics.append(
            ExtractionDiagnostic(
                code="CMAP_PUA_GLYPH",
                severity="quarantine",
                detection_basis="direct_observation",
                message=f"Private Use Area (PUA) glyph codepoint observed on page {page_number}.",
                page_number=page_number,
                source_locator=locator,
            )
        )

    if any((ord(ch) < 32 and ch not in ("\n", "\r", "\t")) or ord(ch) == 127 for ch in raw_text):
        direct_corruption = True
        diagnostics.append(
            ExtractionDiagnostic(
                code="CMAP_CONTROL_CHAR_CORRUPTION",
                severity="quarantine",
                detection_basis="direct_observation",
                message=f"Unexpected ASCII control characters in PDF text on page {page_number}.",
                page_number=page_number,
                source_locator=locator,
            )
        )

    pres_b_count = sum(1 for ch in raw_text if 0xFE70 <= ord(ch) <= 0xFEFF)
    pres_a_count = sum(
        1 for ch in raw_text if 0xFB50 <= ord(ch) <= 0xFDFF and ch not in ("\ufd3e", "\ufd3f")
    )
    if pres_b_count >= 1 or pres_a_count >= 3:
        direct_corruption = True
        diagnostics.append(
            ExtractionDiagnostic(
                code="CMAP_PRESENTATION_FORM_CORRUPTION",
                severity="quarantine",
                detection_basis="direct_observation",
                message=f"Arabic contextual presentation-form glyphs observed on page {page_number} (ToUnicode CMap defect).",
                page_number=page_number,
                source_locator=locator,
            )
        )

    # 2. Heuristic checks (scanned page -> OCR, mojibake distribution, column overlap)
    stripped = raw_text.strip()
    if not stripped and (image_count > 0 or drawing_count > 0):
        ocr_required = True
        suspicion = True
        diagnostics.append(
            ExtractionDiagnostic(
                code="OCR_REQUIRED",
                severity="quarantine",
                detection_basis="heuristic_suspicion",
                message=f"Page {page_number} contains visual/image objects with empty extracted text layer; OCR required.",
                page_number=page_number,
                source_locator=locator,
            )
        )
    elif language == "ar" and len(stripped) >= 10:
        non_ws = [ch for ch in stripped if not ch.isspace()]
        arabic_count = sum(1 for ch in non_ws if 0x0600 <= ord(ch) <= 0x06FF)
        latin1_mojibake = sum(1 for ch in non_ws if 0x00C0 <= ord(ch) <= 0x00FF)
        symbol_count = sum(
            1
            for ch in non_ws
            if not (ch.isalnum() or 0x0600 <= ord(ch) <= 0x06FF or ch in ".,;:()-/\"'،؛؟٪")
        )
        # Distinguish clean bilingual pages (real ASCII words) from broken CMap garbled noise
        english_words = re.findall(r"[A-Za-z]{2,}", stripped)
        if latin1_mojibake >= 3 and (latin1_mojibake / max(len(non_ws), 1)) > 0.15:
            suspicion = True
            diagnostics.append(
                ExtractionDiagnostic(
                    code="CMAP_ABNORMAL_CHAR_DISTRIBUTION",
                    severity="quarantine",
                    detection_basis="heuristic_suspicion",
                    message=f"High Latin-1 mojibake ratio detected in Arabic PDF page {page_number}.",
                    page_number=page_number,
                    source_locator=locator,
                )
            )
        elif arabic_count < 3 and not english_words and (symbol_count / max(len(non_ws), 1)) > 0.35:
            suspicion = True
            diagnostics.append(
                ExtractionDiagnostic(
                    code="CMAP_ABNORMAL_CHAR_DISTRIBUTION",
                    severity="quarantine",
                    detection_basis="heuristic_suspicion",
                    message=f"Abnormal symbol/noise ratio without valid Arabic or English text on page {page_number}.",
                    page_number=page_number,
                    source_locator=locator,
                )
            )

    if reading_order_ambiguous:
        suspicion = True
        diagnostics.append(
            ExtractionDiagnostic(
                code="CMAP_UNSUPPORTED_READING_ORDER",
                severity="quarantine",
                detection_basis="heuristic_suspicion",
                message=f"Ambiguous multi-column reading order detected on page {page_number}.",
                page_number=page_number,
                source_locator=locator,
            )
        )

    return direct_corruption, suspicion, ocr_required, diagnostics


def _page_has_ambiguous_columns(page_dict: dict[str, Any]) -> bool:
    """Detect horizontally disjoint lines sharing vertical bands (multi-column reading risk)."""
    line_boxes: list[tuple[float, float, float, float]] = []
    for block in page_dict.get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            spans = line.get("spans", ())
            text = "".join(str(sp.get("text", "")) for sp in spans).strip()
            bbox = line.get("bbox")
            if text and bbox and len(bbox) == 4:
                line_boxes.append((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])))

    for i, (x0_1, y0_1, x1_1, y1_1) in enumerate(line_boxes):
        h1 = max(y1_1 - y0_1, 1.0)
        for x0_2, y0_2, x1_2, y1_2 in line_boxes[i + 1 :]:
            overlap_y = min(y1_1, y1_2) - max(y0_1, y0_2)
            # Same vertical band AND horizontally disjoint with wide gap (> 40pt)
            if overlap_y > 0.5 * h1 and (x0_2 - x1_1 > 40 or x0_1 - x1_2 > 40):
                return True
    return False


def extract_pdf_bytes(
    content: bytes,
    *,
    source_id: str,
    document_id: str,
    language: Literal["ar", "en"],
    is_authoritative: bool,
    authenticity_tier: AuthenticityTier,
    redistribution_clearance: RedistributionClearance,
    source_role: SourceRole = "extraction_primary",
    historical_edition: str | None = None,
    measured_fidelity_score: float | None = None,
    ocr_text_override: dict[int, str] | None = None,
    allow_synthetic: bool = False,
    max_bytes: int = MAX_EXTRACTION_BYTES,
) -> ExtractionBundle:
    """Fallback PDF extraction via PyMuPDF with CMap corruption and OCR review gating."""
    import pymupdf

    is_synthetic = _validate_synthetic_boundary(source_id, document_id, allow_synthetic)
    if is_authoritative and language != "ar":
        raise ValueError("Authoritative sources must be Arabic ('ar')")
    if not content:
        raise ExtractionError("Empty PDF content")
    if len(content) > max_bytes:
        raise ExtractionError(f"PDF size exceeds maximum extraction limit of {max_bytes} bytes")
    if not re.match(rb"%PDF-[12]\.[0-9][\r\n]", content) or not content.rstrip().endswith(b"%%EOF"):
        raise ExtractionError("Malformed or truncated PDF missing header or %%EOF trailer")

    if measured_fidelity_score is not None and not (0.0 <= measured_fidelity_score <= 1.0):
        raise ValueError("measured_fidelity_score must lie in [0.0, 1.0]")

    snapshot_sha256 = hashlib.sha256(content).hexdigest().lower()

    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except (ValueError, RuntimeError) as exc:
        raise ExtractionError(f"Malformed PDF document: {exc}") from exc

    pages_out: list[PageExtraction] = []
    all_diagnostics: list[ExtractionDiagnostic] = []
    review_queue: list[ReviewQueueItem] = []
    any_corrupt = False
    any_suspect = False
    any_ocr_req = False
    ocr_used = False
    total_text_bytes = 0

    with doc:
        if not doc.is_pdf or doc.is_encrypted or doc.page_count == 0:
            raise ExtractionError("Malformed, encrypted, or empty PDF document")
        if doc.page_count > MAX_PDF_PAGES:
            raise ExtractionError(f"PDF exceeds maximum page limit ({MAX_PDF_PAGES})")

        for page_index in range(doc.page_count):
            page_num = page_index + 1
            page = doc[page_index]
            raw_text = str(page.get_text("text"))
            total_text_bytes += len(raw_text.encode("utf-8"))
            if total_text_bytes > MAX_PDF_EXTRACTED_TEXT_BYTES:
                raise ExtractionError(
                    f"Extracted PDF text exceeds maximum limit ({MAX_PDF_EXTRACTED_TEXT_BYTES} bytes)"
                )

            image_count = len(page.get_images(full=False))
            drawing_count = len(page.get_drawings())
            ambiguous_cols = _page_has_ambiguous_columns(page.get_text("dict"))

            corrupt, suspect, ocr_req, page_diags = analyze_pdf_page_cmap(
                page_num,
                raw_text,
                language=language,
                image_count=image_count,
                drawing_count=drawing_count,
                reading_order_ambiguous=ambiguous_cols,
            )

            # Check explicit caller-supplied OCR output if scanned page had no text layer
            effective_text = raw_text
            if ocr_req and ocr_text_override and page_num in ocr_text_override:
                ocr_used = True
                ocr_req = False
                effective_text = ocr_text_override[page_num]
                # Replace OCR_REQUIRED with OCR_USED_REVIEW_REQUIRED (still strictly quarantined!)
                page_diags = [d for d in page_diags if d.code != "OCR_REQUIRED"]
                page_diags.append(
                    ExtractionDiagnostic(
                        code="OCR_USED_REVIEW_REQUIRED",
                        severity="quarantine",
                        detection_basis="structural_check",
                        message=f"OCR text supplied for scanned page {page_num}; mandatory RV-03 human review required.",
                        page_number=page_num,
                        source_locator=f"pdf:page={page_num}",
                    )
                )

            any_corrupt = any_corrupt or corrupt
            any_suspect = any_suspect or suspect
            any_ocr_req = any_ocr_req or ocr_req
            all_diagnostics.extend(page_diags)

            pages_out.append(
                PageExtraction(
                    page_number=page_num,
                    raw_text=effective_text,
                    char_count=len(effective_text),
                    image_count=image_count,
                    drawing_count=drawing_count,
                    reading_order_ambiguous=ambiguous_cols,
                    cmap_corruption_detected=corrupt,
                    cmap_suspicion_detected=suspect,
                    ocr_required=ocr_req,
                    diagnostics=tuple(page_diags),
                )
            )

    if (
        measured_fidelity_score is not None
        and measured_fidelity_score < EXTRACTION_FIDELITY_THRESHOLD
    ):
        all_diagnostics.append(
            ExtractionDiagnostic(
                code="LOW_EXTRACTION_FIDELITY",
                severity="quarantine",
                detection_basis="structural_check",
                message=(
                    f"Measured extraction fidelity score {measured_fidelity_score:.3f} is below "
                    f"RV-03 threshold {EXTRACTION_FIDELITY_THRESHOLD:.2f}."
                ),
                source_locator="pdf:document",
            )
        )

    extraction_method: Literal["pdf_text", "ocr"] = "ocr" if ocr_used else "pdf_text"
    quarantine_codes = tuple(
        sorted({d.code for d in all_diagnostics if d.severity == "quarantine"})
    )
    needs_review = bool(quarantine_codes) or ocr_used or any_corrupt or any_suspect or any_ocr_req

    # Build conservative article/segment records from extracted page text (without inventing numbers)
    articles: list[ExtractedArticle] = []
    for pg in pages_out:
        cleaned = _clean_dom_text(pg.raw_text)
        if not cleaned:
            continue
        lines = cleaned.split("\n")
        current_art: str | None = None
        current_title: str | None = None
        buf: list[str] = []

        def _flush(art_no: str, art_t: str | None, body_lines: list[str], p_no: int) -> None:
            txt = "\n".join(body_lines).strip()
            pg_codes = tuple(
                sorted(
                    {d.code for d in pg.diagnostics if d.severity == "quarantine"}
                    | set(quarantine_codes)
                )
            )
            seg_review = bool(pg_codes) or ocr_used
            articles.append(
                ExtractedArticle(
                    article_number=art_no,
                    article_title=art_t,
                    text=txt,
                    normalized_text=normalize_arabic_search(txt),
                    clauses=(),
                    source_locator=f"pdf:page={p_no}:article={art_no}",
                    page_start=p_no,
                    page_end=p_no,
                    extraction_method=extraction_method,
                    cmap_corruption_detected=pg.cmap_corruption_detected,
                    cmap_suspicion_detected=pg.cmap_suspicion_detected,
                    extraction_fidelity_score=measured_fidelity_score,
                    review_required=seg_review,
                    review_reasons=pg_codes,
                    publication_eligibility=(
                        "QUARANTINED_REVIEW_REQUIRED" if seg_review else "UNREVIEWED_STAGED"
                    ),
                )
            )

        for ln in lines:
            m = _ARTICLE_HEADING_RE.match(ln)
            if m:
                if current_art is not None:
                    _flush(current_art, current_title, buf, pg.page_number)
                current_art = _normalize_article_num(m.group(1))
                current_title = _clean_dom_text(m.group(2)) if m.group(2) else None
                buf = []
            elif current_art is not None:
                buf.append(ln)
        if current_art is not None:
            _flush(current_art, current_title, buf, pg.page_number)

    if needs_review:
        for pg in pages_out:
            pg_q = tuple(sorted({d.code for d in pg.diagnostics if d.severity == "quarantine"}))
            if pg_q:
                loc = f"pdf:page={pg.page_number}"
                review_queue.append(
                    ReviewQueueItem(
                        review_id=_deterministic_review_id(source_id, snapshot_sha256, pg_q, loc),
                        document_id=document_id,
                        source_id=source_id,
                        snapshot_sha256=snapshot_sha256,
                        page_number=pg.page_number,
                        diagnostic_codes=pg_q,
                        diagnostic_summary="; ".join(
                            d.message for d in pg.diagnostics if d.severity == "quarantine"
                        ),
                        extraction_method=extraction_method,
                        source_locator=loc,
                    )
                )
        if not review_queue:
            loc = "pdf:document"
            review_queue.append(
                ReviewQueueItem(
                    review_id=_deterministic_review_id(
                        source_id, snapshot_sha256, quarantine_codes, loc
                    ),
                    document_id=document_id,
                    source_id=source_id,
                    snapshot_sha256=snapshot_sha256,
                    diagnostic_codes=quarantine_codes,
                    diagnostic_summary="; ".join(
                        d.message for d in all_diagnostics if d.severity == "quarantine"
                    ),
                    extraction_method=extraction_method,
                    source_locator=loc,
                )
            )

    is_hist = historical_edition == "gazette_712_2021_original"
    bundle_dict: dict[str, Any] = {
        "schema_version": "5.2.2-intermediate.1",
        "document_id": document_id,
        "source_id": source_id,
        "snapshot_sha256": snapshot_sha256,
        "language": language,
        "is_authoritative": is_authoritative,
        "authenticity_tier": authenticity_tier,
        "redistribution_clearance": redistribution_clearance,
        "artifact_type": "raw_pdf",
        "extraction_method": extraction_method,
        "source_role": source_role,
        "is_historical_unamended_source": is_hist,
        "historical_source_edition": historical_edition,
        "is_synthetic_fixture": is_synthetic,
        "title": None,
        "headings": (),
        "articles": tuple(articles),
        "pages": tuple(pages_out),
        "diagnostics": tuple(all_diagnostics),
        "review_queue": tuple(review_queue),
        "cmap_corruption_detected": any_corrupt,
        "cmap_suspicion_detected": any_suspect,
        "ocr_required": any_ocr_req,
        "requires_human_review": needs_review,
        "extraction_fidelity_score_summary": measured_fidelity_score,
        "canonical_output_sha256": "0" * 64,
    }
    temp_bundle = ExtractionBundle(**bundle_dict)
    digest = _compute_bundle_canonical_hash(temp_bundle.model_dump(mode="json"))
    return temp_bundle.model_copy(update={"canonical_output_sha256": digest})


def extract_from_snapshot(
    source: LegalSource,
    snapshot_root: Path,
    *,
    historical_edition: str | None = None,
    measured_fidelity_score: float | None = None,
    ocr_text_override: dict[int, str] | None = None,
    allow_synthetic: bool = False,
    max_bytes: int = MAX_EXTRACTION_BYTES,
) -> ExtractionBundle:
    """Resolve snapshot path from LegalSource, verify exact bytes once, and extract deterministically."""
    source = LegalSource.model_validate(source.model_dump())
    _validate_synthetic_boundary(source.source_id, source.document_id, allow_synthetic)

    if source.artifact_type not in ("raw_html", "raw_pdf"):
        raise ExtractionError(
            f"Extraction requires raw_html or raw_pdf snapshot, got {source.artifact_type!r}"
        )

    ext = "html" if source.artifact_type == "raw_html" else "pdf"
    expected_path = compute_snapshot_path(snapshot_root, source.sha256, ext)
    expected_rel = expected_path.relative_to(snapshot_root.resolve()).as_posix()
    if source.local_path.replace("\\", "/") != expected_rel:
        raise SnapshotIntegrityError(
            f"LegalSource local_path '{source.local_path}' mismatches canonical snapshot '{expected_rel}'"
        )

    # Single-open read & checksum verification avoids check-then-reopen TOCTOU gaps
    content = _read_snapshot_single_open(
        expected_path, expected_sha256=source.sha256, max_bytes=max_bytes
    )

    if source.artifact_type == "raw_html":
        return extract_html_bytes(
            content,
            source_id=source.source_id,
            document_id=source.document_id,
            language=source.language,
            is_authoritative=source.is_authoritative,
            authenticity_tier=source.authenticity_tier,
            redistribution_clearance=source.redistribution_clearance,
            source_role=source.source_role,
            historical_edition=historical_edition,
            allow_synthetic=allow_synthetic,
            max_bytes=max_bytes,
        )

    return extract_pdf_bytes(
        content,
        source_id=source.source_id,
        document_id=source.document_id,
        language=source.language,
        is_authoritative=source.is_authoritative,
        authenticity_tier=source.authenticity_tier,
        redistribution_clearance=source.redistribution_clearance,
        source_role=source.source_role,
        historical_edition=historical_edition,
        measured_fidelity_score=measured_fidelity_score,
        ocr_text_override=ocr_text_override,
        allow_synthetic=allow_synthetic,
        max_bytes=max_bytes,
    )


def extract_from_staged_source(
    staging_root: Path,
    source_id: str,
    *,
    document_id: str | None = None,
    historical_edition: str | None = None,
    measured_fidelity_score: float | None = None,
    allow_synthetic: bool = False,
) -> ExtractionBundle:
    """Resolve an accepted LegalSource from private staging manifest.json and extract it."""
    root = _private_root(staging_root)
    sources = _read_records(root / "manifest.json", TypeAdapter(list[LegalSource]))
    source = next((s for s in sources if s.source_id == source_id), None)
    if source is None:
        raise ExtractionError(f"Accepted source_id '{source_id}' not found in staging manifest")
    if document_id is not None and source.document_id != document_id:
        raise SnapshotIntegrityError(
            f"Staged source '{source_id}' binds document_id '{source.document_id}', not '{document_id}'"
        )
    return extract_from_snapshot(
        source,
        root,
        historical_edition=historical_edition,
        measured_fidelity_score=measured_fidelity_score,
        allow_synthetic=allow_synthetic,
    )


_BUNDLE_ADAPTER = TypeAdapter(ExtractionBundle)
_REVIEW_QUEUE_ADAPTER = TypeAdapter(tuple[ReviewQueueItem, ...])


def persist_extraction(staging_root: Path, bundle: ExtractionBundle) -> Path:
    """Atomically store an intermediate ExtractionBundle inside private staging, enforcing isolation.

    Never writes to production `data/official_sources/`.
    Prevents historical unamended Gazette 712 artifacts from mixing with consolidated sources.
    """
    root = _private_root(staging_root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    extracted_dir = root / "extracted"
    review_dir = root / "review_queue"
    extracted_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_path(extracted_dir, directory=True)

    target = extracted_dir / f"{bundle.source_id}.{bundle.snapshot_sha256[:12]}.extraction.json"
    _check_path(target)

    with _manifest_lock(root):
        # Enforce historical vs consolidated isolation across all staged extraction bundles
        for existing_file in sorted(extracted_dir.glob("*.extraction.json")):
            _check_path(existing_file)
            existing = _BUNDLE_ADAPTER.validate_json(existing_file.read_bytes())
            if (
                existing.source_id == bundle.source_id
                and existing.snapshot_sha256 == bundle.snapshot_sha256
                and existing.canonical_output_sha256 == bundle.canonical_output_sha256
                and existing.is_historical_unamended_source == bundle.is_historical_unamended_source
                and existing.document_id == bundle.document_id
            ):
                return existing_file

            if (
                existing.document_id == bundle.document_id
                and existing.is_historical_unamended_source != bundle.is_historical_unamended_source
            ):
                raise SecurityPolicyViolationError(
                    f"Historical source isolation violation: document_id '{bundle.document_id}' "
                    "cannot mix historical unamended and consolidated editions"
                )
            if (
                existing.snapshot_sha256 == bundle.snapshot_sha256
                and existing.is_historical_unamended_source != bundle.is_historical_unamended_source
            ):
                raise SecurityPolicyViolationError(
                    f"Historical source isolation violation: snapshot '{bundle.snapshot_sha256}' "
                    "cannot bind both historical unamended and consolidated sources"
                )
            if (
                existing.source_id == bundle.source_id
                and existing.is_historical_unamended_source != bundle.is_historical_unamended_source
            ):
                raise SecurityPolicyViolationError(
                    f"Historical source isolation violation: source_id '{bundle.source_id}' "
                    "cannot switch historical/consolidated classification"
                )

        bundle_bytes = _BUNDLE_ADAPTER.dump_json(bundle, indent=2) + b"\n"
        with _staged_bytes(target, bundle_bytes) as staged_bundle:
            os.replace(staged_bundle, target)

        if bundle.review_queue:
            review_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            _check_path(review_dir, directory=True)
            review_target = review_dir / f"{bundle.source_id}.review.json"
            _check_path(review_target)
            review_bytes = _REVIEW_QUEUE_ADAPTER.dump_json(bundle.review_queue, indent=2) + b"\n"
            with _staged_bytes(review_target, review_bytes) as staged_review:
                os.replace(staged_review, review_target)

        _sync_directory(extracted_dir)
        _sync_directory(root)

    return target
